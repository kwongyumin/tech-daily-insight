# Circuit Breaker 패턴과 Resilience4j 실전 가이드

## 개요

마이크로서비스 아키텍처가 보편화되면서 서비스 간 네트워크 호출은 피할 수 없는 현실이 되었다. 문제는 **하나의 서비스 장애가 연쇄적으로 전파되어 전체 시스템을 마비**시킬 수 있다는 점이다. 이를 "장애 전파(Cascading Failure)"라고 부르며, 실무에서 가장 골치 아픈 장애 유형 중 하나다.

Circuit Breaker 패턴은 이 문제를 전기 회로의 차단기(Circuit Breaker)에서 영감을 받아 해결한다. 장애가 감지되면 회로를 열어(Open) 추가적인 요청이 실패 서비스로 전달되지 않도록 차단하고, 시스템이 회복할 시간을 확보한다.

이 글에서는 Circuit Breaker의 핵심 개념을 정리하고, Java 생태계에서 사실상 표준으로 자리 잡은 **Resilience4j**를 활용한 실전 구현 방법을 다룬다.

---

## 핵심 개념

### Circuit Breaker의 세 가지 상태

Circuit Breaker는 세 가지 상태(State)를 기반으로 동작한다.

```
CLOSED → (실패율 임계치 초과) → OPEN → (대기 시간 경과) → HALF_OPEN → (성공) → CLOSED
                                                              ↓ (실패)
                                                            OPEN
```

| 상태 | 설명 |
|------|------|
| **CLOSED** | 정상 상태. 모든 요청이 통과된다. 실패율을 모니터링한다. |
| **OPEN** | 차단 상태. 모든 요청이 즉시 실패 처리된다(Fallback 실행). |
| **HALF_OPEN** | 회복 확인 상태. 제한된 수의 요청만 통과시켜 서비스 회복 여부를 탐색한다. |

### 슬라이딩 윈도우 방식

Resilience4j는 실패율을 측정하기 위해 두 가지 슬라이딩 윈도우 방식을 제공한다.

- **COUNT_BASED**: 최근 N번의 호출을 기준으로 실패율을 계산
- **TIME_BASED**: 최근 N초 동안의 호출을 기준으로 실패율을 계산

운영 환경의 트래픽 패턴에 따라 적합한 방식을 선택해야 한다. 트래픽이 불규칙하다면 `TIME_BASED`가 더 안정적인 측정 결과를 제공한다.

---

## 실전 예제

### 의존성 추가

```xml
<!-- pom.xml -->
<dependency>
    <groupId>io.github.resilience4j</groupId>
    <artifactId>resilience4j-spring-boot3</artifactId>
    <version>2.2.0</version>
</dependency>
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-aop</artifactId>
</dependency>
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-actuator</artifactId>
</dependency>
```

### application.yml 설정

```yaml
resilience4j:
  circuitbreaker:
    instances:
      paymentService:
        # 슬라이딩 윈도우 타입 (COUNT_BASED / TIME_BASED)
        slidingWindowType: COUNT_BASED
        # 슬라이딩 윈도우 크기 (최근 10번의 호출 기준)
        slidingWindowSize: 10
        # OPEN 상태로 전환되는 실패율 임계치 (50%)
        failureRateThreshold: 50
        # HALF_OPEN 상태에서 허용할 최대 요청 수
        permittedNumberOfCallsInHalfOpenState: 5
        # OPEN 상태 유지 시간 (ms)
        waitDurationInOpenState: 10000
        # 실패로 간주할 최소 호출 수 (윈도우가 채워지기 전까지는 계산 안 함)
        minimumNumberOfCalls: 5
        # 느린 호출 임계치 (2초 이상이면 느린 호출로 간주)
        slowCallDurationThreshold: 2000
        # 느린 호출 비율 임계치 (80% 이상이면 OPEN)
        slowCallRateThreshold: 80
        # 자동으로 HALF_OPEN으로 전환 여부
        automaticTransitionFromOpenToHalfOpenEnabled: true
        # 기록할 예외 (기본적으로 모든 예외가 실패로 기록됨)
        recordExceptions:
          - java.io.IOException
          - java.util.concurrent.TimeoutException
          - org.springframework.web.client.HttpServerErrorException
        # 실패로 기록하지 않을 예외
        ignoreExceptions:
          - com.example.exception.BusinessException

  retry:
    instances:
      paymentService:
        maxAttempts: 3
        waitDuration: 500
        retryExceptions:
          - java.io.IOException
          - java.util.concurrent.TimeoutException

  timelimiter:
    instances:
      paymentService:
        timeoutDuration: 3s
        cancelRunningFuture: true
```

### 서비스 레이어 구현

```java
@Service
@RequiredArgsConstructor
@Slf4j
public class PaymentService {

    private final PaymentGatewayClient paymentGatewayClient;

    @CircuitBreaker(name = "paymentService", fallbackMethod = "paymentFallback")
    @Retry(name = "paymentService")
    @TimeLimiter(name = "paymentService")
    public CompletableFuture<PaymentResponse> processPayment(PaymentRequest request) {
        log.info("결제 처리 시작: orderId={}", request.getOrderId());
        return CompletableFuture.supplyAsync(() ->
            paymentGatewayClient.charge(request)
        );
    }

    /**
     * Fallback 메서드 - Circuit Breaker가 OPEN 상태이거나 예외 발생 시 호출
     * 시그니처: 원본 메서드와 동일 + 마지막에 Throwable 파라미터 추가
     */
    private CompletableFuture<PaymentResponse> paymentFallback(
            PaymentRequest request, Throwable throwable) {
        log.warn("결제 서비스 Fallback 실행. orderId={}, reason={}",
                request.getOrderId(), throwable.getMessage());

        // 1. 큐에 적재하여 나중에 재처리
        // 2. 캐시된 결과 반환
        // 3. 대체 결제 수단으로 라우팅
        return CompletableFuture.completedFuture(
            PaymentResponse.pending(request.getOrderId(), "결제 서비스 일시 불가. 잠시 후 자동 처리됩니다.")
        );
    }
}
```

### Circuit Breaker 이벤트 모니터링

```java
@Component
@RequiredArgsConstructor
@Slf4j
public class CircuitBreakerEventListener {

    private final CircuitBreakerRegistry circuitBreakerRegistry;

    @PostConstruct
    public void registerEventListeners() {
        CircuitBreaker paymentCircuitBreaker =
            circuitBreakerRegistry.circuitBreaker("paymentService");

        // 상태 변경 이벤트 구독
        paymentCircuitBreaker.getEventPublisher()
            .onStateTransition(event -> {
                log.warn("[Circuit Breaker] 상태 변경: {} → {}",
                    event.getStateTransition().getFromState(),
                    event.getStateTransition().getToState());
                // 슬랙/PagerDuty 알림 발송
                alertService.sendAlert(buildAlertMessage(event));
            })
            .onCallNotPermitted(event ->
                log.warn("[Circuit Breaker] 요청 차단됨 (OPEN 상태)"))
            .onError(event ->
                log.error("[Circuit Breaker] 오류 발생: duration={}ms",
                    event.getElapsedDuration().toMillis()));
    }
}
```

### 수동 상태 제어 (운영 시 유용)

```java
@RestController
@RequiredArgsConstructor
@RequestMapping("/admin/circuit-breaker")
public class CircuitBreakerAdminController {

    private final CircuitBreakerRegistry circuitBreakerRegistry;

    @PostMapping("/{name}/open")
    public ResponseEntity<String> forceOpen(@PathVariable String name) {
        CircuitBreaker cb = circuitBreakerRegistry.circuitBreaker(name);
        cb.transitionToOpenState();
        return ResponseEntity.ok(name + " Circuit Breaker OPEN 상태로 전환");
    }

    @PostMapping("/{name}/close")
    public ResponseEntity<String> forceClose(@PathVariable String name) {
        CircuitBreaker cb = circuitBreakerRegistry.circuitBreaker(name);
        cb.transitionToClosedState();
        return ResponseEntity.ok(name + " Circuit Breaker CLOSED 상태로 전환");
    }

    @GetMapping("/{name}/metrics")
    public ResponseEntity<Map<String, Object>> getMetrics(@PathVariable String name) {
        CircuitBreaker cb = circuitBreakerRegistry.circuitBreaker(name);
        CircuitBreaker.Metrics metrics = cb.getMetrics();

        return ResponseEntity.ok(Map.of(
            "state", cb.getState(),
            "failureRate", metrics.getFailureRate(),
            "slowCallRate", metrics.getSlowCallRate(),
            "bufferedCalls", metrics.getNumberOfBufferedCalls(),
            "failedCalls", metrics.getNumberOfFailedCalls(),
            "successfulCalls", metrics.getNumberOfSuccessfulCalls()
        ));
    }
}
```

### Actuator를 통한 메트릭 노출

```yaml
management:
  endpoints:
    web:
      exposure:
        include: health, metrics, circuitbreakers, circuitbreakerevents
  endpoint:
    health:
      show-details: always
  health:
    circuitbreakers:
      enabled: true
```

`GET /actuator/circuitbreakers` 엔드포인트를 통해 모든 Circuit Breaker의 현재 상태와 메트릭을 확인할 수 있으며, 이를 Prometheus + Grafana 대시보드와 연동하면 시각적인 실시간 모니터링이 가능하다.

---

## 주의사항 및 트레이드오프

### 1. 애너테이션 적용 순서 문제

`@CircuitBreaker`, `@Retry`, `@TimeLimiter`를 함께 사용할 때 적용 순서가 중요하다. Resilience4j의 기본 우선순위는 다음과 같다.

```
TimeLimiter → CircuitBreaker → Retry → Bulkhead → RateLimiter
```

`@Retry`가 Circuit Breaker 바깥에서 동작하면, OPEN 상태에서도 재시도가 발생하여 **Fallback이 N번 호출**되는 비효율이 생긴다. 의도에 맞게 `@Order`를 명시하거나 설정을 통해 순서를 제어해야 한다.

### 2. Fallback의 함정 - Fallback도 실패할 수 있다

Fallback 로직 내에서 또 다른 외부 호출이나 DB 접근이 발생한다면, Fallback 자체도 실패할 수 있다. Fallback은 가능한 한 **단순하고 빠르게** 구성해야 한다. 캐시 조회나 기본값 반환 정도가 이상적이다.

### 3. 임계치 튜닝의 어려움

`failureRateThreshold`나 `slidingWindowSize` 같은 값은 트래픽 패턴에 따라 크게 달라진다. 너무 민감하게 설정하면 작은 오류에도 Circuit Breaker가 OPEN되어 **False Positive** 문제가 발생하고, 너무 둔감하게 설정하면 장애를 제때 차단하지 못한다. 운영 환경의 실제 데이터를 기반으로 점진적으로 튜닝해야 한다.

### 4. 분산 환경에서의 한계

Resilience4j는 **인메모리 기반**이므로, 여러 인스턴스가 뜬 클러스터 환경에서는 각 인스턴스가 독립적인 Circuit Breaker 상태를 유지한다. 즉, A 인스턴스의 Circuit Breaker가 OPEN이어도 B 인스턴스는 여전히 요청을 보낼 수 있다. 클러스터 전체에서 일관된 상태 관리가 필요하다면 Redis 기반의 외부 상태 저장소를 도입하거나, 서비스 메시(Istio 등)에서 제공하는 Circuit Breaker 기능을 활용하는 것이 더 적합하다.

### 5. 테스트 전략

```java
@Test
void circuitBreaker_shouldOpenAfterFailureThreshold() {
    CircuitBreakerConfig config = CircuitBreakerConfig.custom()
        .slidingWindowSize(5)
        .failureRateThreshold(50.0f)
        .minimumNumberOfCalls(5)
        .build();

    CircuitBreaker cb = CircuitBreaker.of("test", config);
    CheckedRunnable failingCall = CircuitBreaker.decorateCheckedRunnable(cb,
        () -> { throw new RuntimeException("외부 서비스 오류"); });

    // 5번 실패 유도
    IntStream.range(0, 5).forEach(i -> {
        assertThatThrownBy(failingCall::run);
    });

    // Circuit Breaker가 OPEN 상태인지 확인
    assertThat(cb.getState()).isEqualTo(CircuitBreaker.State.OPEN);

    // OPEN 상태에서 요청 시 CallNotPermittedException 발생 확인
    assertThatThrownBy(failingCall::run)
        .isInstanceOf(CallNotPermittedException.class);
}
```

---

## 정리

Circuit Breaker 패턴과 Resilience4j는 마이크로서비스 환경의 복잡한 장애 시나리오를 방어하는 데 필수적인 도구다. 핵심을 다시 정리하면 다음과 같다.

| 항목 | 요점 |
|------|------|
| **패턴 선택** | 단순 재시도가 아닌 장애 격리가 필요할 때 Circuit Breaker를 사용 |
| **설정 튜닝** | 실제 트래픽 데이터 기반으로 점진적 조정 필수 |
| **Fallback 설계** | 단순하고 빠르게, 외부 의존성 최소화 |
| **모니터링** | Actuator + Prometheus + Grafana 연동으로 실시간 가시성 확보 |
| **분산 환경** | 인메모리 한계 인지, 필요시 서비스 메시 또는 외부 상태 저장소 고려 |

실무에서 Circuit Breaker를 도입할 때 가장 중요한 것은 **기술 그 자체보다 어디에 적용할지 판단하는 것**이다. 모든 외부 호출에 적용하는 것이 능사가 아니며, 장애 허용 범위와 비즈니스 요구사항을 함께 고려한 설계가 선행되어야 한다. Resilience4j는 그 결정을 구현으로 옮기는 강력하고 유연한 도구다.