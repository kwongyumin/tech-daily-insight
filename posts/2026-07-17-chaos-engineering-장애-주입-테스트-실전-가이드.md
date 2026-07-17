# Chaos Engineering 장애 주입 테스트 실전 가이드

## 개요

2011년 Netflix가 공개한 Chaos Monkey 이후, Chaos Engineering은 현대 분산 시스템의 신뢰성을 검증하는 필수 방법론으로 자리 잡았다. 단순히 "서버를 죽여보는 것"이 아니라, **통제된 환경에서 의도적으로 장애를 주입하여 시스템의 약점을 사전에 발견**하는 과학적 접근이다.

마이크로서비스 아키텍처가 보편화된 지금, 서비스 간 의존성이 복잡해지면서 예상치 못한 장애 시나리오가 폭발적으로 늘어났다. 단순한 유닛 테스트나 통합 테스트만으로는 프로덕션 환경의 카오스를 재현할 수 없다. 이 글에서는 Chaos Engineering의 핵심 개념부터 실제 Java/Spring 환경에서 적용할 수 있는 실전 예제까지 다룬다.

---

## 핵심 개념

### Chaos Engineering의 4단계 프로세스

Chaos Engineering은 과학적 실험 방법론을 따른다.

1. **정상 상태(Steady State) 정의**: 시스템이 "정상"임을 증명하는 측정 지표를 정의한다. (예: p99 응답시간 < 200ms, 에러율 < 0.1%)
2. **가설 수립**: "X가 발생해도 시스템은 정상 상태를 유지할 것이다"라는 가설을 세운다.
3. **실험 실행**: 프로덕션 환경과 유사한 조건에서 장애를 주입한다.
4. **결과 분석 및 개선**: 가설이 틀렸다면 시스템을 개선하고 반복한다.

### 주요 장애 유형

| 장애 유형 | 설명 | 도구 |
|-----------|------|------|
| **네트워크 지연** | 특정 서비스 간 레이턴시 증가 | tc, Toxiproxy |
| **서비스 다운** | 인스턴스 강제 종료 | Chaos Monkey, k6s |
| **CPU/메모리 과부하** | 리소스 고갈 시뮬레이션 | Chaos Blade, stress-ng |
| **디스크 장애** | I/O 에러 또는 디스크 풀 | Litmus Chaos |
| **DB 커넥션 고갈** | 커넥션 풀 소진 | Chaos Toolkit |

---

## 실전 예제

### 1. Chaos Monkey for Spring Boot 설정

Netflix OSS의 **Chaos Monkey for Spring Boot(CM4SB)** 를 사용하면 스프링 빈 레벨에서 장애를 주입할 수 있다.

**의존성 추가 (build.gradle)**

```groovy
dependencies {
    implementation 'de.codecentric:chaos-monkey-spring-boot:3.1.0'
    implementation 'org.springframework.boot:spring-boot-starter-actuator'
}
```

**application.yml 설정**

```yaml
spring:
  profiles:
    active: chaos-monkey

chaos:
  monkey:
    enabled: true
    watcher:
      service: true
      repository: true
      rest-controller: true
    assaults:
      level: 3                    # 1~10, 장애 발생 빈도
      latency-active: true
      latency-range-start: 1000   # ms
      latency-range-end: 3000
      exceptions-active: true
      kill-application-active: false  # 프로덕션에서는 false

management:
  endpoints:
    web:
      exposure:
        include: health, info, chaosmonkey
```

**Runtime API로 동적 장애 주입**

```bash
# 레이턴시 어설트 활성화
curl -X POST http://localhost:8080/actuator/chaosmonkey/assaults \
  -H "Content-Type: application/json" \
  -d '{
    "level": 5,
    "latencyActive": true,
    "latencyRangeStart": 500,
    "latencyRangeEnd": 2000,
    "exceptionsActive": false
  }'

# 현재 설정 확인
curl http://localhost:8080/actuator/chaosmonkey/assaults
```

### 2. Resilience4j와 함께하는 Circuit Breaker 검증

장애를 주입한 후 Circuit Breaker가 올바르게 동작하는지 검증하는 것이 핵심이다.

```java
@Service
@Slf4j
public class OrderService {

    private final PaymentClient paymentClient;
    private final MeterRegistry meterRegistry;

    public OrderService(PaymentClient paymentClient, MeterRegistry meterRegistry) {
        this.paymentClient = paymentClient;
        this.meterRegistry = meterRegistry;
    }

    @CircuitBreaker(name = "paymentService", fallbackMethod = "paymentFallback")
    @TimeLimiter(name = "paymentService")
    @Retry(name = "paymentService")
    public CompletableFuture<PaymentResponse> processPayment(PaymentRequest request) {
        log.info("Processing payment for orderId: {}", request.getOrderId());
        return CompletableFuture.supplyAsync(() -> paymentClient.pay(request));
    }

    private CompletableFuture<PaymentResponse> paymentFallback(
            PaymentRequest request, Exception ex) {
        log.warn("Payment fallback triggered for orderId: {}, reason: {}",
                request.getOrderId(), ex.getMessage());

        // Fallback 발생 횟수 메트릭 기록
        meterRegistry.counter("payment.fallback.count",
                "orderId", request.getOrderId()).increment();

        // 결제 대기 큐에 넣고 나중에 재처리
        return CompletableFuture.completedFuture(
                PaymentResponse.pending(request.getOrderId())
        );
    }
}
```

**Resilience4j 설정**

```yaml
resilience4j:
  circuitbreaker:
    instances:
      paymentService:
        sliding-window-type: COUNT_BASED
        sliding-window-size: 10
        failure-rate-threshold: 50        # 50% 실패 시 Open
        wait-duration-in-open-state: 10s
        permitted-number-of-calls-in-half-open-state: 3
        register-health-indicator: true
  timelimiter:
    instances:
      paymentService:
        timeout-duration: 2s
  retry:
    instances:
      paymentService:
        max-attempts: 3
        wait-duration: 500ms
        retry-exceptions:
          - java.io.IOException
          - java.util.concurrent.TimeoutException
```

### 3. Toxiproxy를 이용한 네트워크 장애 시뮬레이션

Toxiproxy는 TCP 프록시를 통해 네트워크 레벨의 장애를 세밀하게 제어할 수 있다.

**Docker Compose 설정**

```yaml
version: '3.8'
services:
  toxiproxy:
    image: ghcr.io/shopify/toxiproxy:2.5.0
    ports:
      - "8474:8474"   # API 포트
      - "5433:5433"   # PostgreSQL 프록시 포트
    networks:
      - chaos-net

  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: orderdb
      POSTGRES_USER: user
      POSTGRES_PASSWORD: password
    networks:
      - chaos-net

networks:
  chaos-net:
    driver: bridge
```

**Java 테스트 코드 (Testcontainers + Toxiproxy)**

```java
@SpringBootTest
@Testcontainers
class DatabaseChaosTest {

    @Container
    static ToxiproxyContainer toxiproxy = new ToxiproxyContainer(
            DockerImageName.parse("ghcr.io/shopify/toxiproxy:2.5.0"))
            .withNetwork(network);

    private static ToxiproxyClient toxiproxyClient;
    private static Proxy postgresProxy;

    @BeforeAll
    static void setUp() throws Exception {
        toxiproxyClient = new ToxiproxyClient(
                toxiproxy.getHost(), toxiproxy.getControlPort());

        postgresProxy = toxiproxyClient.createProxy(
                "postgres",
                "0.0.0.0:8666",
                "postgres:5432"
        );
    }

    @Test
    @DisplayName("DB 응답 지연 시 Connection Pool 고갈 테스트")
    void shouldHandleConnectionPoolExhaustionUnderLatency() throws Exception {
        // 3초 레이턴시 주입
        postgresProxy.toxics()
                .latency("db-latency", ToxicDirection.DOWNSTREAM, 3000)
                .setJitter(500);

        long startTime = System.currentTimeMillis();

        // 동시 요청 시뮬레이션
        List<CompletableFuture<Void>> futures = IntStream.range(0, 20)
                .mapToObj(i -> CompletableFuture.runAsync(() -> {
                    try {
                        orderRepository.findById(Long.valueOf(i));
                    } catch (Exception e) {
                        log.warn("Request {} failed: {}", i, e.getMessage());
                    }
                }))
                .collect(Collectors.toList());

        CompletableFuture.allOf(futures.toArray(new CompletableFuture[0])).join();

        long elapsed = System.currentTimeMillis() - startTime;

        // Circuit Breaker가 동작하여 빠르게 실패해야 함
        assertThat(elapsed).isLessThan(5000L);

        // 정리
        postgresProxy.toxics().get("db-latency").remove();
    }

    @Test
    @DisplayName("DB 커넥션 끊김 시 Retry 메커니즘 검증")
    void shouldRetryOnConnectionReset() throws Exception {
        // 패킷 드롭으로 커넥션 강제 리셋
        postgresProxy.toxics()
                .resetPeer("connection-reset", ToxicDirection.DOWNSTREAM, 0);

        assertThatThrownBy(() -> orderRepository.findById(1L))
                .isInstanceOf(DataAccessException.class);

        // 장애 제거 후 정상 복구 확인
        postgresProxy.toxics().get("connection-reset").remove();

        assertThatNoException().isThrownBy(() -> orderRepository.findById(1L));
    }
}
```

### 4. Chaos 실험 결과 모니터링

Prometheus + Grafana 기반의 메트릭 수집으로 실험 전후 상태를 비교한다.

```java
@Component
@Slf4j
public class ChaosExperimentMonitor {

    private final MeterRegistry registry;

    // 실험 전 기준값 (Steady State)
    private static final double MAX_ERROR_RATE = 0.01;       // 1%
    private static final double MAX_P99_LATENCY_MS = 500.0;

    @Scheduled(fixedDelay = 10000)
    public void checkSteadyState() {
        double currentErrorRate = getCurrentErrorRate();
        double currentP99Latency = getCurrentP99Latency();

        boolean isHealthy = currentErrorRate <= MAX_ERROR_RATE
                && currentP99Latency <= MAX_P99_LATENCY_MS;

        registry.gauge("chaos.steady_state",
                Tags.of("status", isHealthy ? "normal" : "degraded"),
                isHealthy ? 1.0 : 0.0);

        if (!isHealthy) {
            log.error("[CHAOS] Steady state violated! errorRate={}, p99={}ms",
                    currentErrorRate, currentP99Latency);
            // 자동 롤백 트리거
            notifyExperimentViolation(currentErrorRate, currentP99Latency);
        }
    }

    private double getCurrentErrorRate() {
        // Micrometer를 통한 실시간 에러율 계산
        return registry.find("http.server.requests")
                .tag("status", "5xx")
                .timer()
                .map(t -> t.count() / (double) getTotalRequestCount())
                .orElse(0.0);
    }
}
```

---

## 주의사항 및 트레이드오프

### ⚠️ 실전 적용 시 반드시 지켜야 할 원칙

**1. 폭발 반경(Blast Radius) 최소화**

처음부터 프로덕션 전체에 적용하는 것은 금물이다. **카나리 배포 인스턴스**나 **특정 사용자 그룹**에만 먼저 적용하고 점진적으로 확장한다.

```yaml
# Feature Flag를 통한 트래픽 제어
chaos:
  monkey:
    enabled: ${CHAOS_ENABLED:false}   # 환경변수로 제어
    watcher:
      service: true
```

**2. 자동 중단(Kill Switch) 메커니즘 필수**

정상 상태(Steady State)를 벗어나면 즉시 실험을 중단할 수 있는 자동화된 메커니즘을 마련해야 한다.

**3. 팀 전체의 사전 인지**

카오스 실험은 반드시 On-call 엔지니어, 서비스 오너, 관련 팀에게 사전 공지해야 한다. 몰래 진행하는 것은 신뢰를 무너뜨린다.

**4. 데이터 무결성 보호**

데이터를 생성/수정하는 쓰기 경로(Write Path)에는 초반에 장애를 주입하지 않는다. 읽기 경로(Read Path)부터 시작하라.

### 트레이드오프 분석

| 항목 | 장점 | 단점 |
|------|------|------|
| **조기 장애 발견** | 프로덕션 사고 예방 | 실험 중 실제 서비스 영향 가능성 |
| **팀 역량 강화** | 장애 대응 훈련 효과 | 초기 학습 비용 높음 |
| **자동화** | 지속적 검증 가능 | 잘못된 설정 시 대규모 장애 위험 |
| **문화적 변화** | 신뢰성 중심 문화 형성 | 경영진/타팀 설득 필요 |

---

## 정리

Chaos Engineering은 단순한 도구가 아니라 **시스템에 대한 신뢰를 쌓아가는 실천 철학**이다. 핵심을 정리하면 다음과 같다.

1. **Steady State를 먼저 정의하라**: 정상이 무엇인지 모르면 이상도 감지할 수 없다.
2. **작게 시작하라**: 로컬 → 스테이징 → 카나리 → 프로덕션 순서로 점진적으로 확대한다.
3. **자동화하되, Kill Switch를 준비하라**: 지속적 카오스 실험은 강력하지만, 언제든 멈출 수 있어야 한다.
4. **결과를 문서화하고 공유하라**: 실험 결과는 런북(Runbook)에 반영하고 팀 전체의 지식으로 만든다.
5. **도구보다 문화가 먼저다**: CM4SB, Toxiproxy, Litmus Chaos 등 어떤 도구를 쓰든, 팀이 카오스를 두려워하지 않는 문화를 만드는 것이 선행되어야 한다.

시스템은 실패한다. 언제 실패할지 모른다면 장애가 곧 재난이 된다. Chaos Engineering은 실패를 **예측 가능한 이벤트**로 만드는 기술이다.

> "Hope is not a strategy. Chaos Engineering is." — Chaos Engineering Community