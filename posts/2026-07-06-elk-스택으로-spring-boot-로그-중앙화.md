# ELK 스택으로 Spring Boot 로그 중앙화

## 개요

마이크로서비스 아키텍처가 보편화되면서 로그 관리는 단순히 `tail -f` 명령어로 해결할 수 없는 문제가 됐다. 서비스가 10개, 20개로 늘어나고 각 서비스가 여러 인스턴스로 구동되는 환경에서는 **로그 중앙화**가 선택이 아닌 필수다.

ELK 스택(Elasticsearch + Logstash + Kibana)은 이런 분산 환경에서 로그를 수집·저장·시각화하는 데 가장 널리 사용되는 오픈소스 솔루션이다. 여기에 Filebeat를 더한 **ELKF 스택** 구성이 실무에서는 더 일반적이다. 이 글에서는 Spring Boot 애플리케이션의 로그를 ELK 스택으로 중앙화하는 전 과정을 실전 예제와 함께 다룬다.

---

## 핵심 개념

### ELK 스택의 역할 분담

```
Spring Boot App
     │
     ▼
  Filebeat (로그 파일 수집 및 전송)
     │
     ▼
  Logstash (파싱, 필터링, 변환)
     │
     ▼
Elasticsearch (저장 및 인덱싱)
     │
     ▼
  Kibana (시각화 및 검색)
```

| 컴포넌트 | 역할 |
|---|---|
| **Filebeat** | 경량 로그 수집기. 파일을 tail하여 Logstash 또는 Elasticsearch로 전송 |
| **Logstash** | 로그 파싱, 필터링, 변환 파이프라인. Grok 패턴으로 구조화 |
| **Elasticsearch** | 분산 검색 엔진. 로그를 JSON 문서로 인덱싱하여 빠른 검색 지원 |
| **Kibana** | Elasticsearch 데이터를 시각화하는 웹 UI |

### 구조화 로그(Structured Logging)의 중요성

JSON 형태의 구조화 로그는 ELK 스택에서 파싱 비용을 크게 줄인다. Spring Boot에서는 **Logback + Logstash Encoder**를 활용해 바로 JSON 로그를 출력할 수 있다.

---

## 실전 예제

### 1. Docker Compose로 ELK 환경 구성

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
      - ES_JAVA_OPTS=-Xms512m -Xmx512m
    ports:
      - "9200:9200"
    volumes:
      - es_data:/usr/share/elasticsearch/data
    networks:
      - elk

  logstash:
    image: docker.elastic.co/logstash/logstash:8.11.0
    container_name: logstash
    ports:
      - "5044:5044"
      - "9600:9600"
    volumes:
      - ./logstash/pipeline:/usr/share/logstash/pipeline
    depends_on:
      - elasticsearch
    networks:
      - elk

  kibana:
    image: docker.elastic.co/kibana/kibana:8.11.0
    container_name: kibana
    ports:
      - "5601:5601"
    environment:
      - ELASTICSEARCH_HOSTS=http://elasticsearch:9200
    depends_on:
      - elasticsearch
    networks:
      - elk

  filebeat:
    image: docker.elastic.co/beats/filebeat:8.11.0
    container_name: filebeat
    user: root
    volumes:
      - ./filebeat/filebeat.yml:/usr/share/filebeat/filebeat.yml:ro
      - ./logs:/var/log/springboot:ro
    depends_on:
      - logstash
    networks:
      - elk

volumes:
  es_data:

networks:
  elk:
    driver: bridge
```

### 2. Spring Boot 의존성 및 Logback 설정

**`build.gradle`**

```groovy
dependencies {
    implementation 'org.springframework.boot:spring-boot-starter-web'
    implementation 'net.logstash.logback:logstash-logback-encoder:7.4'
    
    // MDC 활용을 위한 슬루스 (Spring Boot 3.x는 Micrometer Tracing 사용)
    implementation 'io.micrometer:micrometer-tracing-bridge-brave'
}
```

**`src/main/resources/logback-spring.xml`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <springProperty scope="context" name="APP_NAME" source="spring.application.name"/>
    <springProperty scope="context" name="ACTIVE_PROFILE" source="spring.profiles.active" defaultValue="local"/>

    <!-- 로컬 환경: 콘솔 출력 -->
    <appender name="CONSOLE" class="ch.qos.logback.core.ConsoleAppender">
        <encoder>
            <pattern>%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger{36} - %msg%n</pattern>
        </encoder>
    </appender>

    <!-- 운영 환경: JSON 파일 출력 -->
    <appender name="JSON_FILE" class="ch.qos.logback.core.rolling.RollingFileAppender">
        <file>logs/application.log</file>
        <rollingPolicy class="ch.qos.logback.core.rolling.TimeBasedRollingPolicy">
            <fileNamePattern>logs/application.%d{yyyy-MM-dd}.%i.log.gz</fileNamePattern>
            <timeBasedFileNamingAndTriggeringPolicy class="ch.qos.logback.core.rolling.SizeAndTimeBasedFNATP">
                <maxFileSize>100MB</maxFileSize>
            </timeBasedFileNamingAndTriggeringPolicy>
            <maxHistory>30</maxHistory>
            <totalSizeCap>3GB</totalSizeCap>
        </rollingPolicy>
        <encoder class="net.logstash.logback.encoder.LogstashEncoder">
            <!-- 공통 필드 추가 -->
            <customFields>{"app_name":"${APP_NAME}","environment":"${ACTIVE_PROFILE}"}</customFields>
            <!-- 불필요한 필드 제거 -->
            <fieldNames>
                <timestamp>@timestamp</timestamp>
                <version>[ignore]</version>
            </fieldNames>
        </encoder>
    </appender>

    <springProfile name="local">
        <root level="INFO">
            <appender-ref ref="CONSOLE"/>
        </root>
    </springProfile>

    <springProfile name="prod,staging">
        <root level="INFO">
            <appender-ref ref="JSON_FILE"/>
        </root>
    </springProfile>
</configuration>
```

### 3. MDC를 활용한 요청 추적

분산 환경에서 특정 요청의 흐름을 추적하려면 `traceId`를 로그에 포함시켜야 한다.

```java
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class RequestLoggingFilter extends OncePerRequestFilter {

    private static final String TRACE_ID_HEADER = "X-Trace-Id";
    private static final String MDC_TRACE_KEY = "traceId";
    private static final String MDC_USER_KEY = "userId";

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {
        String traceId = Optional.ofNullable(request.getHeader(TRACE_ID_HEADER))
                .orElse(UUID.randomUUID().toString().substring(0, 8));

        try {
            MDC.put(MDC_TRACE_KEY, traceId);
            // 인증 정보가 있다면 userId도 추가
            // MDC.put(MDC_USER_KEY, getUserId());
            
            response.addHeader(TRACE_ID_HEADER, traceId);
            filterChain.doFilter(request, response);
        } finally {
            // 반드시 MDC 정리 (스레드 풀 재사용 문제 방지)
            MDC.clear();
        }
    }
}
```

**`logback-spring.xml`에 MDC 필드 포함 설정 추가:**

```xml
<encoder class="net.logstash.logback.encoder.LogstashEncoder">
    <customFields>{"app_name":"${APP_NAME}"}</customFields>
    <includeMdcKeyName>traceId</includeMdcKeyName>
    <includeMdcKeyName>userId</includeMdcKeyName>
</encoder>
```

### 4. Logstash 파이프라인 설정

```ruby
# logstash/pipeline/logstash.conf
input {
  beats {
    port => 5044
  }
}

filter {
  # JSON 파싱 (logstash-logback-encoder가 이미 JSON을 출력하므로)
  json {
    source => "message"
    skip_on_invalid_json => true
  }

  # 레벨별 태그 추가
  if [level] == "ERROR" {
    mutate { add_tag => ["error"] }
  }

  # 불필요한 Filebeat 메타데이터 제거
  mutate {
    remove_field => ["agent", "ecs", "input", "tags", "host", "log"]
  }

  # 날짜 파싱
  date {
    match => ["@timestamp", "ISO8601"]
    target => "@timestamp"
  }
}

output {
  elasticsearch {
    hosts => ["http://elasticsearch:9200"]
    # 일별 인덱스 생성 (예: springboot-logs-2024.01.15)
    index => "springboot-logs-%{+YYYY.MM.dd}"
    # ILM(Index Lifecycle Management) 적용 시
    # ilm_rollover_alias => "springboot-logs"
    # ilm_policy => "springboot-logs-policy"
  }

  # 디버깅용 (운영에서는 제거)
  stdout {
    codec => rubydebug
  }
}
```

### 5. Filebeat 설정

```yaml
# filebeat/filebeat.yml
filebeat.inputs:
  - type: log
    enabled: true
    paths:
      - /var/log/springboot/*.log
    # JSON 로그 파싱
    json.keys_under_root: true
    json.overwrite_keys: true
    json.add_error_key: true
    # 멀티라인 처리 (스택 트레이스 대응)
    multiline.pattern: '^\{'
    multiline.negate: true
    multiline.match: after
    fields:
      service: spring-boot-app
    fields_under_root: true

output.logstash:
  hosts: ["logstash:5044"]
  loadbalance: true

# Filebeat 자체 로그 레벨
logging.level: warning
```

### 6. Kibana Index Pattern 생성 (API 방식)

```bash
# Index Pattern 생성
curl -X POST "http://localhost:5601/api/saved_objects/index-pattern" \
  -H "kbn-xsrf: true" \
  -H "Content-Type: application/json" \
  -d '{
    "attributes": {
      "title": "springboot-logs-*",
      "timeFieldName": "@timestamp"
    }
  }'
```

---

## 주의사항 및 트레이드오프

### ⚠️ 성능 고려사항

**Logstash는 메모리를 많이 먹는다.** JVM 기반이라 기본 힙이 수백 MB를 넘기 쉽다. 트래픽이 많지 않다면 Logstash를 제거하고 **Filebeat → Elasticsearch** 직접 연결을 고려하라. 대신 파싱 로직을 Elasticsearch의 **Ingest Pipeline**으로 이전한다.

```json
// Ingest Pipeline 예시
PUT _ingest/pipeline/springboot-logs-pipeline
{
  "processors": [
    {
      "json": {
        "field": "message",
        "target_field": "parsed"
      }
    }
  ]
}
```

### ⚠️ 인덱스 관리 전략

로그는 시간이 지남에 따라 기하급수적으로 쌓인다. **ILM(Index Lifecycle Management)** 정책을 반드시 설정하라.

```json
PUT _ilm/policy/springboot-logs-policy
{
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
        "actions": { "forcemerge": { "max_num_segments": 1 } }
      },
      "delete": {
        "min_age": "30d",
        "actions": { "delete": {} }
      }
    }
  }
}
```

### ⚠️ 민감 정보 마스킹

로그에 개인정보(이메일, 전화번호, 카드번호 등)가 포함되지 않도록 **Logstash의 mutate 필터** 또는 **Logback의 MaskingMessageConverter**를 활용하라.

```ruby
# Logstash에서 카드번호 마스킹
filter {
  mutate {
    gsub => [
      "message", "\d{4}-\d{4}-\d{4}-\d{4}", "****-****-****-****"
    ]
  }
}
```

### ⚠️ 트레이드오프 정리

| 항목 | 장점 | 단점 |
|---|---|---|
| **Logstash 사용** | 강력한 파싱, 풍부한 플러그인 | 높은 메모리 사용, 복잡도 증가 |
| **Direct to ES** | 간단한 구성, 낮은 리소스 | 파싱 기능 제한 |
| **JSON 로그** | 파싱 불필요, 빠른 인덱싱 | 가독성 저하 (로컬 개발 시) |
| **일별 인덱스** | 관리 편의성 | 소규모 인덱스 과다 생성 |

---

## 정리

ELK 스택으로 Spring Boot 로그를 중앙화할 때 핵심 포인트를 정리하면 다음과 같다.

1. **구조화 로그부터 시작하라** — `logstash-logback-encoder`로 JSON 출력을 설정하면 파싱 파이프라인이 단순해진다.
2. **MDC로 요청을 추적하라** — `traceId`를 모든 로그에 포함시키면 분산 환경에서의 디버깅 시간이 극적으로 줄어든다.
3. **ILM 정책은 Day 1부터 설정하라** — 운영 후 뒤늦게 설정하면 이미 쌓인 인덱스 관리가 골치 아파진다.
4. **민감 정보 마스킹을 자동화하라** — 개발자 실수에 의존하지 말고 파이프라인 레벨에서 처리하라.
5. **Logstash가 부담이라면 Ingest Pipeline을 활용하라** — 간단한 파싱은 Elasticsearch만으로 충분히 처리 가능하다.

로그 중앙화는 단순한 인프라 설정이 아니라 **장애 대응 속도를 결정하는 핵심 운영 역량**이다. 초기에 제대로 된 구조를 잡아두면 나중에 되돌리기 어려운 기술 부채를 방지할 수 있다.