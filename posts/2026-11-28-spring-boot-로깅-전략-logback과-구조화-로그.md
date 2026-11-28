# Spring Boot 로깅 전략 Logback과 구조화 로그

## 개요

애플리케이션이 프로덕션 환경에서 문제를 일으킬 때, 개발자가 가장 먼저 손에 쥐는 도구는 로그다. 그런데 수백만 줄의 텍스트 로그를 grep으로 뒤지거나, 여러 서비스에 흩어진 로그를 머릿속으로 조합하는 경험을 해본 적이 있다면 그 고통을 잘 알 것이다.

Spring Boot는 기본적으로 Logback을 로깅 구현체로 사용한다. 단순히 `application.properties`에서 로그 레벨 몇 줄 조정하는 수준을 넘어, **구조화 로그(Structured Logging)** 와 **컨텍스트 전파**, **환경별 전략** 까지 갖추면 운영 효율이 크게 달라진다. 이 글에서는 Logback의 핵심 구조를 짚고, 실무에서 바로 활용할 수 있는 JSON 구조화 로그 설정과 MDC 활용 패턴을 다룬다.

---

## 핵심 개념

### Logback 아키텍처

Logback은 세 가지 핵심 컴포넌트로 구성된다.

- **Logger**: 실제 로그를 기록하는 주체. 계층 구조(패키지 단위)를 가진다.
- **Appender**: 로그를 어디에 출력할지 결정한다. (콘솔, 파일, 원격 등)
- **Encoder/Layout**: 로그 메시지를 어떤 형식으로 직렬화할지 결정한다.

Spring Boot는 `logback-spring.xml`을 사용할 경우 `<springProfile>` 태그를 활용해 프로파일별 설정이 가능하다. 반드시 `logback.xml`이 아닌 `logback-spring.xml`을 사용해야 Spring의 Environment를 활용할 수 있다.

### 구조화 로그란?

전통적인 텍스트 로그는 사람이 읽기 쉽지만 기계가 파싱하기 어렵다.

```
2024-01-15 10:23:45.123 ERROR [payment-service] PaymentService - Payment failed for userId=1234, orderId=5678, amount=99000
```

반면 구조화 로그는 JSON과 같은 형식으로 각 필드를 명시적으로 분리한다.

```json
{
  "timestamp": "2024-01-15T10:23:45.123Z",
  "level": "ERROR",
  "service": "payment-service",
  "logger": "PaymentService",
  "message": "Payment failed",
  "userId": "1234",
  "orderId": "5678",
  "amount": 99000,
  "traceId": "abc123def456"
}
```

Elasticsearch, Loki, CloudWatch Logs Insights 같은 로그 집계 시스템에서 필드 기반 검색과 집계가 가능해지므로, 대규모 분산 시스템에서는 사실상 필수 전략이 된다.

### MDC (Mapped Diagnostic Context)

MDC는 현재 스레드에 key-value 형태의 컨텍스트 정보를 붙이는 메커니즘이다. 요청 단위로 `traceId`, `userId`, `requestId` 등을 MDC에 설정해두면, 해당 스레드에서 발생하는 모든 로그에 자동으로 그 값이 포함된다.

---

## 실전 예제

### 의존성 추가

JSON 구조화 로그를 위해 `logstash-logback-encoder`를 추가한다.

```xml
<!-- pom.xml -->
<dependency>
    <groupId>net.logstash.logback</groupId>
    <artifactId>logstash-logback-encoder</artifactId>
    <version>7.4</version>
</dependency>
```

```gradle
// build.gradle
implementation 'net.logstash.logback:logstash-logback-encoder:7.4'
```

### logback-spring.xml 설정

```xml
<?xml version="1.0" encoding="UTF-8"?>
<configuration>

    <springProperty scope="context" name="APP_NAME" source="spring.application.name" defaultValue="app"/>
    <springProperty scope="context" name="ACTIVE_PROFILE" source="spring.profiles.active" defaultValue="default"/>

    <!-- 로컬 개발: 사람이 읽기 쉬운 텍스트 출력 -->
    <springProfile name="local">
        <appender name="CONSOLE" class="ch.qos.logback.core.ConsoleAppender">
            <encoder>
                <pattern>%d{HH:mm:ss.SSS} [%thread] %-5level %logger{36} - %msg%n</pattern>
            </encoder>
        </appender>

        <root level="DEBUG">
            <appender-ref ref="CONSOLE"/>
        </root>
    </springProfile>

    <!-- 프로덕션: JSON 구조화 로그 -->
    <springProfile name="prod,staging">
        <appender name="JSON_CONSOLE" class="ch.qos.logback.core.ConsoleAppender">
            <encoder class="net.logstash.logback.encoder.LogstashEncoder">
                <customFields>{"service":"${APP_NAME}","env":"${ACTIVE_PROFILE}"}</customFields>
                <fieldNames>
                    <timestamp>timestamp</timestamp>
                    <message>message</message>
                    <logger>logger</logger>
                    <thread>thread</thread>
                    <levelValue>[ignore]</levelValue>
                </fieldNames>
                <throwableConverter class="net.logstash.logback.stacktrace.ShortenedThrowableConverter">
                    <maxDepthPerCause>20</maxDepthPerCause>
                    <shortenedClassNameLength>20</shortenedClassNameLength>
                    <rootCauseFirst>true</rootCauseFirst>
                </throwableConverter>
            </encoder>
        </appender>

        <!-- 비동기 Appender로 I/O 블로킹 최소화 -->
        <appender name="ASYNC_JSON" class="ch.qos.logback.classic.AsyncAppender">
            <appender-ref ref="JSON_CONSOLE"/>
            <queueSize>1024</queueSize>
            <discardingThreshold>0</discardingThreshold>
            <includeCallerData>false</includeCallerData>
        </appender>

        <root level="INFO">
            <appender-ref ref="ASYNC_JSON"/>
        </root>

        <!-- 외부 라이브러리 로그 레벨 제한 -->
        <logger name="org.springframework" level="WARN"/>
        <logger name="org.hibernate" level="WARN"/>
        <logger name="com.zaxxer.hikari" level="INFO"/>
    </springProfile>

</configuration>
```

### MDC 기반 요청 컨텍스트 필터

```java
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class RequestLoggingFilter extends OncePerRequestFilter {

    private static final String TRACE_ID_HEADER = "X-Trace-Id";

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {
        String traceId = Optional.ofNullable(request.getHeader(TRACE_ID_HEADER))
                .filter(id -> !id.isBlank())
                .orElse(UUID.randomUUID().toString());

        try {
            MDC.put("traceId", traceId);
            MDC.put("method", request.getMethod());
            MDC.put("uri", request.getRequestURI());
            MDC.put("clientIp", getClientIp(request));

            response.setHeader(TRACE_ID_HEADER, traceId);
            filterChain.doFilter(request, response);

        } finally {
            // 반드시 MDC를 정리해야 스레드 풀 재사용 시 오염 방지
            MDC.clear();
        }
    }

    private String getClientIp(HttpServletRequest request) {
        String forwardedFor = request.getHeader("X-Forwarded-For");
        if (forwardedFor != null && !forwardedFor.isBlank()) {
            return forwardedFor.split(",")[0].trim();
        }
        return request.getRemoteAddr();
    }
}
```

### 구조화 로그 필드 추가 활용

```java
@Service
@RequiredArgsConstructor
@Slf4j
public class PaymentService {

    public PaymentResult processPayment(PaymentRequest request) {
        // StructuredArguments로 필드를 명시적으로 로그에 포함
        log.info("Payment processing started",
                StructuredArguments.keyValue("userId", request.getUserId()),
                StructuredArguments.keyValue("orderId", request.getOrderId()),
                StructuredArguments.keyValue("amount", request.getAmount()));

        try {
            PaymentResult result = executePayment(request);

            log.info("Payment completed successfully",
                    StructuredArguments.keyValue("userId", request.getUserId()),
                    StructuredArguments.keyValue("orderId", request.getOrderId()),
                    StructuredArguments.keyValue("pgTransactionId", result.getTransactionId()),
                    StructuredArguments.keyValue("durationMs", result.getDurationMs()));

            return result;

        } catch (PaymentException e) {
            log.error("Payment failed",
                    StructuredArguments.keyValue("userId", request.getUserId()),
                    StructuredArguments.keyValue("orderId", request.getOrderId()),
                    StructuredArguments.keyValue("errorCode", e.getErrorCode()),
                    e);
            throw e;
        }
    }
}
```

### 비동기 환경에서 MDC 전파

Virtual Thread나 `@Async`, `CompletableFuture`를 사용하는 경우 MDC는 자동으로 전파되지 않는다.

```java
@Configuration
public class AsyncConfig implements AsyncConfigurer {

    @Override
    public Executor getAsyncExecutor() {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(10);
        executor.setMaxPoolSize(50);
        executor.setQueueCapacity(100);
        executor.setTaskDecorator(new MdcTaskDecorator()); // MDC 전파 핵심
        executor.initialize();
        return executor;
    }
}

public class MdcTaskDecorator implements TaskDecorator {

    @Override
    public Runnable decorate(Runnable runnable) {
        // 호출 시점의 MDC 컨텍스트를 캡처
        Map<String, String> contextMap = MDC.getCopyOfContextMap();

        return () -> {
            try {
                if (contextMap != null) {
                    MDC.setContextMap(contextMap);
                }
                runnable.run();
            } finally {
                MDC.clear();
            }
        };
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. 비동기 Appender의 큐 오버플로우

`AsyncAppender`는 기본적으로 큐가 80% 이상 차면 `WARN` 레벨 이하 로그를 버린다(`discardingThreshold` 기본값 20%). 트래픽 폭증 시 중요한 로그가 유실될 수 있으므로, 프로덕션에서는 `discardingThreshold`를 `0`으로 설정하고 큐 사이즈를 충분히 확보해야 한다. 단, 큐가 가득 차면 블로킹이 발생할 수 있으므로 모니터링과 함께 운영해야 한다.

### 2. 민감 정보 로깅

구조화 로그는 필드가 명확히 분리되어 오히려 민감 정보를 실수로 로깅할 위험이 높다. 카드번호, 비밀번호, 개인식별정보는 로그에 절대 포함되어선 안 된다. 커스텀 `Converter`나 직렬화 단계에서 마스킹 처리를 적용하거나, 코드 리뷰 단계에서 체크리스트로 관리해야 한다.

### 3. 로그 볼륨과 비용

JSON 로그는 텍스트 로그보다 용량이 크다. `traceId`, `service`, `env` 같은 공통 필드가 모든 로그 라인에 반복되기 때문이다. 로그 집계 플랫폼의 비용이 인입 볼륨 기반이라면, 불필요한 `DEBUG` 로그가 프로덕션에서 활성화되지 않도록 주의해야 한다. 로그 레벨을 런타임에 동적으로 변경하려면 Spring Boot Actuator의 `/actuator/loggers` 엔드포인트를 활용하면 된다.

```bash
# 런타임 로그 레벨 변경
curl -X POST http://localhost:8080/actuator/loggers/com.example.payment \
  -H 'Content-Type: application/json' \
  -d '{"configuredLevel": "DEBUG"}'
```

### 4. Caller Data 비용

`includeCallerData`를 `true`로 설정하면 클래스명, 메서드명, 라인 번호가 로그에 포함되지만, 스택 트레이스 추출 비용이 발생한다. 성능이 중요한 경로에서는 비활성화하고, 에러 로그에만 선택적으로 적용하는 것을 권장한다.

### 5. 로컬 파일 로테이션 전략

컨테이너 환경(Kubernetes)에서는 로그를 파일로 쓰는 것보다 표준 출력(stdout)으로 내보내고 사이드카 또는 DaemonSet이 수집하는 패턴이 일반적이다. 반면 VM 환경이나 레거시 환경에서는 `RollingFileAppender`와 `SizeAndTimeBasedRollingPolicy`를 함께 사용해 일별 로테이션과 최대 보관 용량을 함께 설정해야 한다.

---

## 정리

| 항목 | 로컬/개발 | 프로덕션 |
|------|-----------|----------|
| 출력 형식 | 텍스트 패턴 | JSON 구조화 |
| Appender | ConsoleAppender | AsyncAppender + JSON |
| 로그 레벨 | DEBUG | INFO (WARN for 외부 라이브러리) |
| MDC | 선택적 | 필수 (traceId 포함) |
| Caller Data | 활성화 가능 | 비활성화 권장 |

Spring Boot에서 로깅 전략을 제대로 갖추는 것은 단순한 설정 작업이 아니다. **관찰 가능성(Observability)** 의 핵심 축 중 하나이며, 장애 대응 시간과 직결된다.

핵심을 정리하면 다음과 같다.

- `logback-spring.xml`을 사용해 프로파일별 전략을 분리한다.
- 프로덕션에서는 `logstash-logback-encoder`로 JSON 구조화 로그를 출력한다.
- MDC 필터로 요청 단위 `traceId`를 모든 로그에 자동 포함시킨다.
- 비동기 처리 시 `TaskDecorator`로 MDC를 명시적으로 전파한다.
- 민감 정보, 로그 볼륨, 큐 오버플로우에 대한 운영 정책을 사전에 수립한다.

로그는 작성할 때보다 **읽을 때** 가치가 결정된다. 미래의 나 또는 팀원이 새벽 3시에 장애를 디버깅할 때 고맙다는 말을 들을 수 있는 로깅 전략을 갖추길 권한다.
