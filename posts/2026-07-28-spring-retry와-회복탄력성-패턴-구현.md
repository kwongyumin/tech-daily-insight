# Spring Retry와 회복탄력성 패턴 구현

## 개요

분산 시스템 환경에서 외부 서비스 호출, 데이터베이스 연결, 메시지 브로커 통신은 언제든 실패할 수 있다. 네트워크 지연, 일시적인 서비스 불가, 타임아웃 등 **일시적 장애(Transient Fault)**는 잠깐 기다렸다가 재시도하면 해결되는 경우가 많다. 반면 무작정 재시도를 반복하면 시스템에 더 큰 부하를 주는 역효과가 발생하기도 한다.

Spring 생태계는 이러한 회복탄력성(Resilience) 문제를 해결하기 위해 **Spring Retry** 라이브러리를 제공한다. 단순 재시도를 넘어 지수 백오프(Exponential Backoff), 회로 차단기(Circuit Breaker), 폴백(Fallback) 등의 패턴을 선언적으로 구현할 수 있다. 이 글에서는 실무에서 바로 적용 가능한 수준으로 Spring Retry의 핵심 패턴을 깊이 있게 다룬다.

---

## 핵심 개념

### Retry Pattern

가장 기본적인 회복탄력성 패턴이다. 특정 예외 발생 시 지정한 횟수만큼 재시도한다. 핵심은 **어떤 예외에 대해**, **몇 번**, **어떤 간격으로** 재시도할지 명확히 정의하는 것이다.

### Exponential Backoff

재시도 간격을 지수적으로 늘려가는 전략이다. 서버가 과부하 상태일 때 빠른 재시도는 상황을 악화시킨다. 첫 재시도는 1초 후, 두 번째는 2초 후, 세 번째는 4초 후처럼 간격을 늘려 서버가 회복할 시간을 준다. 여기에 **Jitter(무작위 오프셋)**를 추가하면 다수의 클라이언트가 동시에 재시도하는 **Thundering Herd 문제**도 방지할 수 있다.

### Circuit Breaker

일정 횟수 이상 실패가 발생하면 이후 호출을 즉시 차단(Open)하고, 일정 시간 후 일부 요청을 허용(Half-Open)하며 복구를 확인하는 패턴이다. Spring Retry에도 상태 기반 재시도로 유사한 기능을 구현할 수 있다.

### Fallback

재시도가 모두 실패했을 때 대체 로직을 실행하는 패턴이다. 캐시에서 마지막 데이터를 반환하거나, 기본값을 내려주거나, 사용자에게 서비스 저하 알림을 보내는 방식으로 활용한다.

---

## 실전 예제

### 의존성 추가

```xml
<!-- Maven -->
<dependency>
    <groupId>org.springframework.retry</groupId>
    <artifactId>spring-retry</artifactId>
</dependency>
<dependency>
    <groupId>org.springframework</groupId>
    <artifactId>spring-aspects</artifactId>
</dependency>
```

```gradle
// Gradle
implementation 'org.springframework.retry:spring-retry'
implementation 'org.springframework:spring-aspects'
```

Spring Boot를 사용한다면 `spring-retry` 버전은 Boot BOM이 관리하므로 별도 버전 명시가 불필요하다.

---

### 기본 설정: @EnableRetry 활성화

```java
@Configuration
@EnableRetry
public class RetryConfig {
    // Spring Retry AOP 활성화
}
```

---

### 예제 1: 기본 @Retryable 적용

외부 결제 API를 호출하는 서비스를 예시로 든다.

```java
@Service
@Slf4j
public class PaymentService {

    private final PaymentApiClient paymentApiClient;

    public PaymentService(PaymentApiClient paymentApiClient) {
        this.paymentApiClient = paymentApiClient;
    }

    /**
     * 결제 API 호출 - 네트워크 오류 시 최대 3회 재시도
     * 재시도 간격: 초기 1초, 배수 2.0 (1s → 2s → 4s)
     */
    @Retryable(
        retryFor = {PaymentApiException.class, RestClientException.class},
        maxAttempts = 3,
        backoff = @Backoff(delay = 1000, multiplier = 2.0, random = true)
    )
    public PaymentResponse processPayment(PaymentRequest request) {
        log.info("결제 API 호출 시도 - orderId: {}", request.getOrderId());
        return paymentApiClient.call(request);
    }

    /**
     * 모든 재시도 실패 시 실행되는 폴백 메서드
     * 시그니처: 동일한 파라미터 + 마지막으로 발생한 예외
     */
    @Recover
    public PaymentResponse recoverPayment(Exception e, PaymentRequest request) {
        log.error("결제 API 최종 실패 - orderId: {}, error: {}", 
                  request.getOrderId(), e.getMessage());
        
        // 실패 이벤트 발행, 알림 발송, 보상 트랜잭션 등 처리
        return PaymentResponse.failed(request.getOrderId(), "결제 서비스 일시 불가");
    }
}
```

`random = true` 옵션은 Jitter를 적용하여 Thundering Herd를 방지한다. `@Recover` 메서드는 반드시 같은 클래스 내에 위치해야 하며, 반환 타입과 첫 번째 파라미터(Exception) 타입으로 매핑된다.

---

### 예제 2: RetryTemplate을 이용한 프로그래매틱 제어

어노테이션 방식이 제어권이 부족할 때는 `RetryTemplate`을 직접 구성한다.

```java
@Configuration
public class RetryTemplateConfig {

    @Bean
    public RetryTemplate paymentRetryTemplate() {
        return RetryTemplate.builder()
                .maxAttempts(3)
                .exponentialBackoff(
                    1000,   // 초기 대기 시간(ms)
                    2.0,    // 배수
                    10000,  // 최대 대기 시간(ms)
                    true    // jitter 적용
                )
                .retryOn(PaymentApiException.class)
                .retryOn(RestClientException.class)
                .notRetryOn(InvalidRequestException.class) // 비즈니스 예외는 재시도 제외
                .withListener(new RetryListenerSupport() {
                    @Override
                    public <T, E extends Throwable> void onError(
                            RetryContext context, RetryCallback<T, E> callback, Throwable throwable) {
                        log.warn("재시도 발생 - attempt: {}, error: {}", 
                                 context.getRetryCount(), throwable.getMessage());
                    }
                })
                .build();
    }
}
```

```java
@Service
@Slf4j
public class InventoryService {

    private final RetryTemplate paymentRetryTemplate;
    private final InventoryApiClient inventoryApiClient;

    public InventoryService(RetryTemplate paymentRetryTemplate, 
                            InventoryApiClient inventoryApiClient) {
        this.paymentRetryTemplate = paymentRetryTemplate;
        this.inventoryApiClient = inventoryApiClient;
    }

    public StockInfo getStockInfo(String productId) {
        return paymentRetryTemplate.execute(
            context -> {
                // 재시도 대상 로직
                log.debug("재고 조회 시도 #{} - productId: {}", 
                          context.getRetryCount() + 1, productId);
                return inventoryApiClient.fetchStock(productId);
            },
            context -> {
                // 폴백 로직 (RetryCallback 타입 일치 필요)
                log.error("재고 조회 최종 실패 - productId: {}", productId);
                return StockInfo.unavailable(productId);
            }
        );
    }
}
```

---

### 예제 3: 상태 기반 재시도 (Stateful Retry)로 Circuit Breaker 구현

Spring Retry의 `CircuitBreakerRetryPolicy`를 이용하면 Circuit Breaker 패턴을 구현할 수 있다.

```java
@Bean
public RetryTemplate circuitBreakerRetryTemplate() {
    // 5회 실패 시 Circuit Open, 10초 후 Half-Open 전환
    CircuitBreakerRetryPolicy circuitBreakerPolicy = new CircuitBreakerRetryPolicy(
            new SimpleRetryPolicy(5)
    );
    circuitBreakerPolicy.setOpenTimeout(10_000L);   // Open 상태 유지 시간(ms)
    circuitBreakerPolicy.setResetTimeout(20_000L);  // 완전 닫힘으로 초기화 시간(ms)

    FixedBackOffPolicy backOffPolicy = new FixedBackOffPolicy();
    backOffPolicy.setBackOffPeriod(2000L);

    RetryTemplate template = new RetryTemplate();
    template.setRetryPolicy(circuitBreakerPolicy);
    template.setBackOffPolicy(backOffPolicy);

    return template;
}
```

> **참고:** 프로덕션 환경에서 Circuit Breaker가 핵심 요구사항이라면 **Resilience4j**와 Spring Cloud Circuit Breaker를 함께 고려하는 것이 더 성숙한 선택이다. Spring Retry의 Circuit Breaker는 단일 인스턴스 내 메모리 기반이라 분산 환경에서의 상태 공유가 되지 않는다.

---

### 예제 4: RetryListener로 메트릭 수집

재시도 이벤트를 Micrometer와 연동해 모니터링한다.

```java
@Component
@Slf4j
public class MetricsRetryListener implements RetryListener {

    private final MeterRegistry meterRegistry;

    public MetricsRetryListener(MeterRegistry meterRegistry) {
        this.meterRegistry = meterRegistry;
    }

    @Override
    public <T, E extends Throwable> void onError(
            RetryContext context, RetryCallback<T, E> callback, Throwable throwable) {

        String serviceName = context.getAttribute(RetryContext.NAME) != null
                ? context.getAttribute(RetryContext.NAME).toString()
                : "unknown";

        meterRegistry.counter("retry.attempt",
                "service", serviceName,
                "exception", throwable.getClass().getSimpleName()
        ).increment();

        log.warn("[Retry] service={}, attempt={}, exception={}",
                serviceName, context.getRetryCount(), throwable.getClass().getSimpleName());
    }

    @Override
    public <T, E extends Throwable> void close(
            RetryContext context, RetryCallback<T, E> callback, Throwable throwable) {
        if (throwable != null) {
            meterRegistry.counter("retry.exhausted",
                    "service", context.getAttribute(RetryContext.NAME) != null
                            ? context.getAttribute(RetryContext.NAME).toString() : "unknown"
            ).increment();
        }
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. 멱등성(Idempotency) 확보가 선결 조건

재시도는 **같은 요청이 여러 번 전달되어도 결과가 동일**해야 안전하다. 결제 요청처럼 멱등하지 않은 API에 무분별하게 재시도를 적용하면 이중 결제가 발생한다. Idempotency Key 패턴으로 서버 측에서 중복 요청을 필터링하거나, GET/HEAD 같은 안전한 메서드에만 자동 재시도를 적용해야 한다.

### 2. 재시도할 예외를 엄격하게 선별

모든 예외에 재시도를 적용하면 안 된다.

| 예외 유형 | 재시도 여부 | 이유 |
|---|---|---|
| `ConnectTimeoutException` | ✅ | 일시적 네트워크 문제 |
| `ServiceUnavailableException (503)` | ✅ | 서버 일시적 과부하 |
| `BadRequestException (400)` | ❌ | 요청 자체가 잘못됨 |
| `UnauthorizedException (401)` | ❌ | 인증 로직 문제 |
| `DataIntegrityViolationException` | ❌ | 재시도해도 동일 오류 |

### 3. AOP 프록시 내부 호출 문제

`@Retryable`은 Spring AOP 기반이므로, **같은 클래스 내부에서 `this`로 호출하면 프록시를 거치지 않아 재시도가 동작하지 않는다.** 이 경우 `RetryTemplate`을 직접 사용하거나, 자기 자신을 빈으로 주입받는 방식으로 해결해야 한다.

### 4. 총 대기 시간(Worst Case Latency) 계산

지수 백오프 + maxAttempts 조합의 최악 시나리오를 반드시 계산해야 한다.

```
maxAttempts=3, delay=1000ms, multiplier=2.0
→ 1차 실패 즉시, 1초 대기 → 2차 실패, 2초 대기 → 3차 실패
→ 총 최소 대기: 3초 + 각 API 응답 타임아웃 × 3
```

HTTP 요청의 경우 타임아웃 설정과 재시도를 함께 고려하지 않으면 사용자 경험이 크게 저하된다. API Gateway나 로드밸런서의 타임아웃 설정과 맞물려야 한다.

### 5. Spring Retry vs Resilience4j

| 항목 | Spring Retry | Resilience4j |
|---|---|---|
| 학습 곡선 | 낮음 | 중간 |
| Circuit Breaker 완성도 | 기본 수준 | 프로덕션 수준 |
| 분산 환경 상태 공유 | ❌ | Redis 연동 가능 |
| Bulkhead 패턴 | ❌ | ✅ |
| 반응형(Reactive) 지원 | 제한적 | ✅ (Reactor) |
| 의존성 복잡도 | 낮음 | 중간 |

단순 재시도 요구사항은 Spring Retry로 충분하지만, 본격적인 회복탄력성 아키텍처가 필요하다면 Resilience4j를 도입하는 것이 합리적이다.

---

## 정리

Spring Retry는 **선언적이고 간결한 방식**으로 재시도 로직을 구현할 수 있는 강력한 도구다. 핵심 포인트를 정리하면 다음과 같다.

- `@Retryable` + `@Recover` 조합으로 빠르게 재시도 + 폴백 로직을 구성할 수 있다.
- `RetryTemplate`은 프로그래매틱 제어와 테스트 용이성이 필요할 때 선택한다.
- 지수 백오프와 Jitter는 서버 보호를 위해 항상 함께 적용한다.
- **멱등성 확보 없는 재시도는 약이 아닌 독이다.**
- 재시도 적용 전 예외의 성격(일시적 vs 영구적)을 반드시 분류한다.
- 모니터링(`RetryListener` + Micrometer)을 함께 구성해 재시도 패턴 데이터를 축적한다.

회복탄력성 패턴은 단순히 예외를 잡아서 다시 호출하는 기술이 아니다. **시스템 전체의 안정성과 사용자 경험을 지키기 위한 아키텍처 전략**이다. Spring Retry를 시작점으로, 서비스 복잡도에 따라 Resilience4j, Sentinel 등으로 확장하는 점진적 접근을 권장한다.