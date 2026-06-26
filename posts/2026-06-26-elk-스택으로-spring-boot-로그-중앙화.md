# ELK 스택으로 Spring Boot 로그 중앙화

## 개요

마이크로서비스 아키텍처가 보편화되면서 서비스 인스턴스가 수십 개를 넘어가는 환경이 흔해졌다. 이 상황에서 각 서버에 SSH로 접속해 로그를 grep하는 방식은 더 이상 실무에서 통하지 않는다. 장애가 발생했을 때 어느 인스턴스에서 문제가 시작됐는지 추적하려면, 로그가 한 곳에 모여 있어야 한다.

ELK 스택(Elasticsearch + Logstash + Kibana)은 이런 문제를 해결하는 대표적인 오픈소스 솔루션이다. 여기에 Filebeat를 더한 **ELKF 스택**이 실무에서는 더 일반적으로 사용된다. 이 글에서는 Spring Boot 애플리케이션의 로그를 ELK 스택으로 중앙화하는 전체 파이프라인을 구성하는 방법을 다룬다. 단순 설치 가이드가 아니라, 실제 운영 환경에서 고려해야 할 구조적 판단까지 함께 짚어보겠다.

---

## 핵심 개념

### ELK 스택 구성 요소

| 컴포넌트 | 역할 |
|---|---|
| **Filebeat** | 로그 파일을 읽어 Logstash 또는 Elasticsearch로 전송하는 경량 에이전트 |
| **Logstash** | 로그 수집, 파싱(필터), 변환 후 Elasticsearch로 전달 |
| **Elasticsearch** | 로그 데이터를 인덱싱하고 검색 가능하게 저장 |
| **Kibana** | Elasticsearch 데이터를 시각화하는 대시보드 |

### 데이터 흐름

```
Spring Boot App
    → (로그 파일 or stdout)
    → Filebeat (수집/전송)
    → Logstash (파싱/변환)
    → Elasticsearch (저장/인덱싱)
    → Kibana (시각화/검색)
```

소규모 환경에서는 Logstash를 생략하고 Filebeat에서 직접 Elasticsearch로 전송하기도 한다. 하지만 Grok 필터를 통한 구조화, 필드 추가/제거, 조건 분기 등이 필요하다면 Logstash는 필수다.

---

## 실전 예제

### 1. Spring Boot 로그 설정 (Logback + JSON)

Kibana에서 로그를 효율적으로 검색하려면 로그를 **JSON 형식**으로 출력하는 것이 핵심이다. 필드별로 인덱싱되기 때문에 `level:ERROR AND service:order-service` 같은 구조적 쿼리가 가능해진다.

`build.gradle`에 의존성 추가:

```groovy
dependencies {
    implementation 'net.logstash.logback:logstash-logback-encoder:7.4'
}
```

`logback-spring.xml` 설정:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <springProperty scope="context" name="APP_NAME" source="spring.application.name"/>
    <springProperty scope="context" name="ACTIVE_PROFILE" source="spring.profiles.active" defaultValue="local"/>

    <!-- 콘솔 출력 (로컬 개발용) -->
    <appender name="CONSOLE" class="ch.qos.logback.core.ConsoleAppender">
        <encoder>
            <pattern>%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger{36} - %msg%n</pattern>
        </encoder>
    </appender>

    <!-- JSON 파일 출력 (운영 환경) -->
    <appender name="FILE_JSON" class="ch.qos.logback.core.rolling.RollingFileAppender">
        <file>/var/log/app/${APP_NAME}.log</file>
        <rollingPolicy class="ch.qos.logback.core.rolling.TimeBasedRollingPolicy">
            <fileNamePattern>/var/log/app/${APP_NAME}.%d{yyyy-MM-dd}.log</fileNamePattern>
            <maxHistory>7</maxHistory>
            <totalSizeCap>3GB</totalSizeCap>
        </rollingPolicy>
        <encoder class="net.logstash.logback.encoder.LogstashEncoder">
            <!-- 커스텀 필드 추가 -->
            <customFields>{"service":"${APP_NAME}","environment":"${ACTIVE_PROFILE}"}</customFields>
            <!-- 스택트레이스를 한 줄로 -->
            <throwableConverter class="net.logstash.logback.stacktrace.ShortenedThrowableConverter">
                <maxDepthPerCause>10</maxDepthPerCause>
                <shortenedClassNameLength>20</shortenedClassNameLength>
                <rootCauseFirst>true</rootCauseFirst>
            </throwableConverter>
        </encoder>
    </appender>

    <springProfile name="local">
        <root level="INFO">
            <appender-ref ref="CONSOLE"/>
        </root>
    </springProfile>

    <springProfile name="prod">
        <root level="INFO">
            <appender-ref ref="FILE_JSON"/>
        </root>
    </springProfile>
</configuration>
```

MDC(Mapped Diagnostic Context)를 활용하면 요청 단위 트레이싱이 가능하다:

```java
@Component
@RequiredArgsConstructor
public class MdcLoggingFilter implements Filter {

    @Override
    public void doFilter(ServletRequest request, ServletResponse response, FilterChain chain)
            throws IOException, ServletException {
        HttpServletRequest httpRequest = (HttpServletRequest) request;
        try {
            String traceId = Optional.ofNullable(httpRequest.getHeader("X-Trace-Id"))
                    .orElse(UUID.randomUUID().toString().substring(0, 8));
            MDC.put("traceId", traceId);
            MDC.put("userId", resolveUserId(httpRequest));
            MDC.put("clientIp", httpRequest.getRemoteAddr());
            chain.doFilter(request, response);
        } finally {
            MDC.clear(); // 반드시 clear 필요
        }
    }

    private String resolveUserId(HttpServletRequest request) {
        // JWT 토큰에서 userId 추출 로직
        return Optional.ofNullable(request.getHeader("Authorization"))
                .map(this::extractUserIdFromToken)
                .orElse("anonymous");
    }
}
```

### 2. Docker Compose로 ELK 스택 구성

```yaml
# docker-compose.yml
version: '3.8'

services:
  elasticsearch:
    image: docker.elastic.co/elasticsearch/elasticsearch:8.11.0
    container_name: elasticsearch
    environment:
      - discovery.type=single-node
      - xpack.security.enabled=false
      - "ES_JAVA_OPTS=-Xms1g -Xmx1g"
    ports:
      - "9200:9200"
    volumes:
      - es_data:/usr/share/elasticsearch/data
    ulimits:
      memlock:
        soft: -1
        hard: -1

  logstash:
    image: docker.elastic.co/logstash/logstash:8.11.0
    container_name: logstash
    ports:
      - "5044:5044"
      - "9600:9600"
    volumes:
      - ./logstash/pipeline:/usr/share/logstash/pipeline
      - ./logstash/config/logstash.yml:/usr/share/logstash/config/logstash.yml
    depends_on:
      - elasticsearch

  kibana:
    image: docker.elastic.co/kibana/kibana:8.11.0
    container_name: kibana
    ports:
      - "5601:5601"
    environment:
      - ELASTICSEARCH_HOSTS=http://elasticsearch:9200
    depends_on:
      - elasticsearch

  filebeat:
    image: docker.elastic.co/beats/filebeat:8.11.0
    container_name: filebeat
    user: root
    volumes:
      - ./filebeat/filebeat.yml:/usr/share/filebeat/filebeat.yml:ro
      - /var/log/app:/var/log/app:ro
      - /var/lib/docker/containers:/var/lib/docker/containers:ro
    depends_on:
      - logstash

volumes:
  es_data:
    driver: local
```

### 3. Filebeat 설정

```yaml
# filebeat/filebeat.yml
filebeat.inputs:
  - type: log
    enabled: true
    paths:
      - /var/log/app/*.log
    json.keys_under_root: true
    json.add_error_key: true
    json.message_key: message
    fields:
      log_type: application
    fields_under_root: true
    multiline.type: pattern
    multiline.pattern: '^\{'
    multiline.negate: true
    multiline.match: after

output.logstash:
  hosts: ["logstash:5044"]
  loadbalance: true

processors:
  - add_host_metadata:
      when.not.contains.tags: forwarded
```

### 4. Logstash 파이프라인 설정

```ruby
# logstash/pipeline/spring-boot.conf
input {
  beats {
    port => 5044
  }
}

filter {
  # JSON 파싱이 안 된 경우 Grok으로 폴백
  if [message] =~ /^\{/ {
    json {
      source => "message"
      skip_on_invalid_json => true
    }
  } else {
    grok {
      match => {
        "message" => "%{TIMESTAMP_ISO8601:timestamp} \[%{DATA:thread}\] %{LOGLEVEL:level} %{DATA:logger} - %{GREEDYDATA:log_message}"
      }
    }
  }

  # 타임스탬프 파싱
  date {
    match => ["@timestamp", "ISO8601"]
    target => "@timestamp"
  }

  # 불필요한 필드 제거
  mutate {
    remove_field => ["agent", "ecs", "input", "host", "log", "tags"]
    add_field => {
      "[@metadata][index_prefix]" => "spring-logs"
    }
  }

  # 에러 레벨에 따른 태그 추가
  if [level] == "ERROR" {
    mutate {
      add_tag => ["alert_candidate"]
    }
  }
}

output {
  elasticsearch {
    hosts => ["elasticsearch:9200"]
    index => "%{[@metadata][index_prefix]}-%{[service]}-%{+YYYY.MM.dd}"
    # ILM(Index Lifecycle Management) 사용 시
    # ilm_rollover_alias => "spring-logs"
    # ilm_policy => "spring-logs-policy"
  }

  # 디버깅용 (운영에서는 비활성화)
  # stdout { codec => rubydebug }
}
```

### 5. Elasticsearch ILM 정책 설정

운영 환경에서는 인덱스 생명주기 관리(ILM)가 필수다:

```bash
curl -X PUT "localhost:9200/_ilm/policy/spring-logs-policy" \
  -H 'Content-Type: application/json' \
  -d '{
    "policy": {
      "phases": {
        "hot": {
          "actions": {
            "rollover": {
              "max_size": "10gb",
              "max_age": "1d"
            }
          }
        },
        "warm": {
          "min_age": "7d",
          "actions": {
            "shrink": { "number_of_shards": 1 },
            "forcemerge": { "max_num_segments": 1 }
          }
        },
        "delete": {
          "min_age": "30d",
          "actions": {
            "delete": {}
          }
        }
      }
    }
  }'
```

---

## 주의사항 및 트레이드오프

### ⚠️ 성능 영향 최소화

**비동기 Appender**를 사용하지 않으면 로그 I/O가 메인 스레드를 블로킹할 수 있다:

```xml
<appender name="ASYNC_FILE" class="ch.qos.logback.classic.AsyncAppender">
    <appender-ref ref="FILE_JSON"/>
    <queueSize>512</queueSize>
    <discardingThreshold>0</discardingThreshold> <!-- 0: 큐 꽉 차도 버리지 않음 -->
    <neverBlock>false</neverBlock>
</appender>
```

`discardingThreshold`를 0으로 설정하면 큐가 꽉 찼을 때 블로킹이 발생할 수 있다. 반대로 기본값(20%)으로 두면 WARN 이하 레벨 로그가 유실될 수 있다. 상황에 맞게 선택해야 한다.

### ⚠️ 민감 정보 마스킹

로그에 개인정보나 인증 토큰이 포함되지 않도록 Logstash 필터에서 마스킹한다:

```ruby
# logstash 필터 내부
mutate {
  gsub => [
    "message", "\"password\"\s*:\s*\"[^\"]*\"", "\"password\":\"***\"",
    "message", "Authorization:\s*Bearer\s+\S+", "Authorization: Bearer ***"
  ]
}
```

### ⚠️ Logstash vs Filebeat 직접 전송

| 항목 | Logstash 경유 | Filebeat → ES 직접 |
|---|---|---|
| 파싱/변환 유연성 | 높음 | 낮음 |
| 리소스 사용 | 많음 (JVM) | 적음 |
| 운영 복잡도 | 높음 | 낮음 |
| 적합한 규모 | 대규모, 복잡한 파싱 | 소규모, 단순 전송 |

### ⚠️ Elasticsearch 메모리 설정

Elasticsearch는 JVM 힙을 물리 메모리의 50%를 넘지 않게 설정해야 한다. 나머지 50%는 Lucene의 파일 시스템 캐시가 사용하기 때문이다. 힙을 너무 크게 잡으면 오히려 검색 성능이 떨어진다.

### ⚠️ 로그 유실 방지

Filebeat는 처리한 파일 오프셋을 레지스트리에 저장하므로 재시작 시에도 중복/유실 없이 전송이 가능하다. 하지만 Logstash가 다운된 상태에서 Filebeat 큐가 가득 차면 유실이 발생할 수 있다. 중요한 시스템에서는 **Kafka를 중간 버퍼**로 두는 아키텍처를 고려하자.

```
Filebeat → Kafka → Logstash → Elasticsearch
```

---

## 정리

ELK 스택을 통한 로그 중앙화는 단순히 로그를 한 곳에 모으는 것 이상의 가치를 제공한다. 트레이스 ID 기반 요청 추적, 에러 패턴 시각화, 실시간 알림 연동까지 확장할 수 있다.

핵심 포인트를 정리하면:

1. **JSON 포맷 출력**은 협상 불가한 전제 조건이다. `LogstashEncoder`로 구조화된 로그를 만들어라.
2. **MDC**로 traceId, userId를 심어두면 분산 환경에서의 디버깅 효율이 몇 배나 높아진다.
3. **ILM 정책**은 처음부터 설정해야 한다. 나중에 수십 GB가 쌓인 뒤에 설정하면 고통스럽다.
4. **비동기 Appender**는 운영 환경에서 필수다. 로그가 서비스 성능을 잡아먹지 않도록 하라.
5. **민감 정보 마스킹**을 로그 파이프라인 어딘가에 반드시 포함시켜라.

로그는 장애 발생 후 가장 먼저 들여다보는 곳이다. 평소에 잘 구조화된 로그 파이프라인을 만들어 두면, 새벽 2시에 PagerDuty 알림을 받았을 때 몇 초 만에 문제를 찾을 수 있는 차이가 생긴다.