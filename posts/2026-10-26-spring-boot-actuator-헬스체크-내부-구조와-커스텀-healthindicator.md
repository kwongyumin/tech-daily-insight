# Spring Boot Actuator 헬스체크 내부 구조와 커스텀 HealthIndicator

## 개요

운영 환경에서 서비스의 상태를 외부에서 확인할 수 있는 헬스체크 엔드포인트는 이제 선택이 아닌 필수입니다. 쿠버네티스의 `livenessProbe`, `readinessProbe`, 로드 밸런서의 헬스체크, 모니터링 시스템의 상태 수집까지 — 수많은 인프라 컴포넌트가 `/actuator/health`에 의존합니다.

Spring Boot Actuator는 이 기능을 기본으로 제공하지만, 실무에서 기본 설정만으로는 부족한 경우가 많습니다. DB 연결 상태는 알 수 있어도, 외부 API 가용성, 메시지 큐 컨슈머 상태, 내부 캐시 유효성 같은 도메인 특화 지표는 직접 구현해야 합니다.

이 글에서는 Actuator 헬스체크의 내부 동작 원리를 살펴보고, 실무에서 바로 적용 가능한 커스텀 `HealthIndicator` 구현 방법과 운영 시 주의해야 할 트레이드오프를 다룹니다.

---

## 핵심 개념

### HealthEndpoint의 내부 구조

Actuator의 헬스 엔드포인트는 단순히 HTTP 200을 반환하는 것이 아닙니다. 내부적으로 여러 `HealthIndicator` 구현체를 수집하고 집계하는 구조를 가집니다.

```
HealthEndpoint
    └── HealthContributorRegistry
            ├── DiskSpaceHealthIndicator
            ├── DataSourceHealthIndicator
            ├── RedisHealthIndicator
            └── (커스텀 HealthIndicator들)
```

핵심 인터페이스는 다음 세 가지입니다.

- **`HealthIndicator`**: 단일 컴포넌트의 상태를 반환하는 인터페이스
- **`CompositeHealthContributor`**: 여러 `HealthContributor`를 그룹화하는 인터페이스
- **`HealthContributorRegistry`**: 모든 contributor를 관리하는 레지스트리

### Health 상태 집계 방식

여러 `HealthIndicator`의 결과는 `StatusAggregator`에 의해 하나의 상태로 집계됩니다. 기본 우선순위는 다음과 같습니다.

```
DOWN > OUT_OF_SERVICE > UP > UNKNOWN
```

즉, 하나라도 `DOWN`인 컴포넌트가 있으면 전체 상태가 `DOWN`이 되고, HTTP 상태 코드는 503이 반환됩니다. 이 동작은 `management.endpoint.health.status.http-mapping` 설정으로 변경할 수 있습니다.

### 보안과 노출 수준 설정

`/actuator/health`가 반환하는 상세 정보의 수준은 `show-details` 설정으로 제어합니다.

```yaml
management:
  endpoint:
    health:
      show-details: when-authorized  # never | always | when-authorized
      show-components: always
  endpoints:
    web:
      exposure:
        include: health, info, metrics
```

운영 환경에서 `always`로 설정하면 DB 연결 정보나 내부 시스템 구조가 외부에 노출될 수 있으므로 주의가 필요합니다.

---

## 실전 예제

### 기본 HealthIndicator 구현

가장 단순한 구현부터 시작합니다. 외부 결제 API의 가용성을 체크하는 예시입니다.

```java
@Component
public class PaymentApiHealthIndicator implements HealthIndicator {

    private final PaymentApiClient paymentApiClient;

    public PaymentApiHealthIndicator(PaymentApiClient paymentApiClient) {
        this.paymentApiClient = paymentApiClient;
    }

    @Override
    public Health health() {
        try {
            boolean isAvailable = paymentApiClient.ping();
            if (isAvailable) {
                return Health.up()
                        .withDetail("provider", "external-payment")
                        .withDetail("status", "reachable")
                        .build();
            }
            return Health.down()
                    .withDetail("provider", "external-payment")
                    .withDetail("reason", "ping returned false")
                    .build();
        } catch (Exception e) {
            return Health.down(e)
                    .withDetail("provider", "external-payment")
                    .withDetail("reason", e.getMessage())
                    .build();
        }
    }
}
```

`@Component`만 등록하면 Spring Boot가 자동으로 `HealthContributorRegistry`에 등록합니다. 빈 이름에서 `HealthIndicator` 접미사를 제거한 이름(`paymentApi`)이 응답 JSON의 키가 됩니다.

### AbstractHealthIndicator 활용

예외 처리 보일러플레이트를 줄이려면 `AbstractHealthIndicator`를 상속하는 방법이 더 깔끔합니다.

```java
@Component
public class MessageQueueHealthIndicator extends AbstractHealthIndicator {

    private final RabbitTemplate rabbitTemplate;
    private final QueueMetricsCollector metricsCollector;

    public MessageQueueHealthIndicator(RabbitTemplate rabbitTemplate,
                                       QueueMetricsCollector metricsCollector) {
        super("Message queue health check failed");
        this.rabbitTemplate = rabbitTemplate;
        this.metricsCollector = metricsCollector;
    }

    @Override
    protected void doHealthCheck(Health.Builder builder) {
        QueueMetrics metrics = metricsCollector.collect();

        long pendingMessages = metrics.getPendingCount();
        long consumerCount = metrics.getActiveConsumerCount();

        if (consumerCount == 0) {
            builder.down()
                    .withDetail("reason", "no active consumers")
                    .withDetail("pendingMessages", pendingMessages);
            return;
        }

        if (pendingMessages > 10_000) {
            builder.status("WARN")
                    .withDetail("pendingMessages", pendingMessages)
                    .withDetail("activeConsumers", consumerCount)
                    .withDetail("warning", "queue backlog is high");
            return;
        }

        builder.up()
                .withDetail("pendingMessages", pendingMessages)
                .withDetail("activeConsumers", consumerCount);
    }
}
```

`WARN`처럼 커스텀 상태를 사용할 때는 해당 상태의 HTTP 매핑도 명시해주는 것이 좋습니다.

```yaml
management:
  endpoint:
    health:
      status:
        http-mapping:
          WARN: 200
```

### CompositeHealthContributor로 그룹화

마이크로서비스가 여러 외부 의존성을 가질 때, 관련 체크를 하나의 그룹으로 묶어 관리하면 가독성이 높아집니다.

```java
@Component("externalDependencies")
public class ExternalDependenciesHealthContributor implements CompositeHealthContributor {

    private final Map<String, HealthContributor> contributors;

    public ExternalDependenciesHealthContributor(
            PaymentApiHealthIndicator paymentApi,
            ShippingApiHealthIndicator shippingApi,
            NotificationApiHealthIndicator notificationApi) {

        this.contributors = Map.of(
                "payment", paymentApi,
                "shipping", shippingApi,
                "notification", notificationApi
        );
    }

    @Override
    public HealthContributor getContributor(String name) {
        return contributors.get(name);
    }

    @Override
    public Iterator<NamedContributor<HealthContributor>> iterator() {
        return contributors.entrySet().stream()
                .map(entry -> NamedContributor.of(entry.getKey(), entry.getValue()))
                .iterator();
    }
}
```

이렇게 하면 응답 구조가 계층화됩니다.

```json
{
  "status": "UP",
  "components": {
    "externalDependencies": {
      "status": "UP",
      "components": {
        "payment": { "status": "UP" },
        "shipping": { "status": "UP" },
        "notification": { "status": "DOWN" }
      }
    }
  }
}
```

### Liveness와 Readiness 분리

쿠버네티스 환경에서는 Liveness와 Readiness를 분리하는 것이 매우 중요합니다.

```yaml
management:
  endpoint:
    health:
      probes:
        enabled: true
  health:
    livenessstate:
      enabled: true
    readinessstate:
      enabled: true
```

이 설정 후 `/actuator/health/liveness`와 `/actuator/health/readiness`가 별도로 활성화됩니다.

```java
@Component
public class CacheWarmupReadinessIndicator implements HealthIndicator {

    private final CacheWarmupService cacheWarmupService;

    public CacheWarmupReadinessIndicator(CacheWarmupService cacheWarmupService) {
        this.cacheWarmupService = cacheWarmupService;
    }

    @Override
    @ReadinessProbeIndicator  // Readiness 그룹에만 포함
    public Health health() {
        if (!cacheWarmupService.isCompleted()) {
            return Health.down()
                    .withDetail("reason", "cache warmup in progress")
                    .withDetail("progress", cacheWarmupService.getProgressPercent() + "%")
                    .build();
        }
        return Health.up().build();
    }
}
```

특정 `HealthIndicator`를 특정 그룹에만 포함시키려면 `application.yml`에서 명시적으로 설정할 수도 있습니다.

```yaml
management:
  endpoint:
    health:
      group:
        readiness:
          include: readinessState, cacheWarmupReadiness, db
        liveness:
          include: livenessState
```

---

## 주의사항 및 트레이드오프

### 1. 헬스체크 자체가 부하를 유발할 수 있다

외부 API 호출이나 DB 쿼리를 포함한 헬스체크는 주기적으로 실행되기 때문에, 대상 시스템에 불필요한 부하를 줄 수 있습니다. 특히 쿠버네티스에서 `periodSeconds`를 짧게 설정하면 더 심해집니다.

**해결 방법**: 결과를 일정 시간 캐싱하거나, 별도 백그라운드 스레드에서 주기적으로 상태를 갱신하는 방식을 사용합니다.

```java
@Component
public class CachedExternalApiHealthIndicator implements HealthIndicator {

    private volatile Health cachedHealth = Health.unknown().build();
    private final AtomicLong lastCheckedAt = new AtomicLong(0);
    private static final long CACHE_TTL_MS = 30_000; // 30초

    @Override
    public Health health() {
        long now = System.currentTimeMillis();
        if (now - lastCheckedAt.get() > CACHE_TTL_MS) {
            cachedHealth = checkActualHealth();
            lastCheckedAt.set(now);
        }
        return cachedHealth;
    }

    private Health checkActualHealth() {
        // 실제 외부 호출 로직
    }
}
```

### 2. DOWN이 곧 Pod 재시작을 의미할 수 있다

Liveness probe에 연결된 HealthIndicator가 잠깐이라도 `DOWN`을 반환하면 쿠버네티스가 Pod를 재시작합니다. 일시적인 외부 의존성 장애까지 Liveness에 포함하는 것은 위험합니다.

**원칙**: Liveness는 "앱이 살아있는가(데드락, OOM 등)"만 체크하고, 외부 의존성은 Readiness에서 체크합니다.

### 3. 민감한 정보 노출

`withDetail()`로 추가한 정보는 `show-details: always` 설정 시 인증 없이 외부에 노출됩니다. DB URL, 내부 호스트명, 에러 스택 트레이스 등이 포함되지 않도록 주의해야 합니다.

```java
// 나쁜 예
return Health.down(e)
        .withDetail("connectionUrl", datasource.getUrl())  // URL 노출 위험
        .withDetail("stackTrace", ExceptionUtils.getStackTrace(e))  // 스택 노출 위험
        .build();

// 좋은 예
return Health.down()
        .withDetail("errorType", e.getClass().getSimpleName())
        .withDetail("message", "Database connection failed")
        .build();
```

### 4. 헬스체크 타임아웃 설정

외부 호출을 포함한 HealthIndicator는 반드시 타임아웃을 설정해야 합니다. 헬스체크 자체가 블로킹되어 응답을 못 돌려주면 더 큰 문제가 됩니다. `RestTemplate`이나 `WebClient`의 커넥션/읽기 타임아웃을 헬스체크 전용으로 짧게 설정하는 것을 권장합니다.

---

## 정리

Spring Boot Actuator의 헬스체크는 단순한 HTTP 응답이 아닌, 서비스 운영의 가시성을 제공하는 핵심 인프라입니다.

| 구분 | 핵심 포인트 |
|---|---|
| 기본 구조 | `HealthIndicator` → `HealthContributorRegistry` → `StatusAggregator` |
| 그룹화 | `CompositeHealthContributor`로 관련 체크 묶기 |
| K8s 환경 | Liveness/Readiness 명확히 분리 |
| 성능 | 캐싱으로 불필요한 외부 호출 방지 |
| 보안 | `show-details` 설정과 민감 정보 노출 주의 |

커스텀 `HealthIndicator`를 잘 설계하면 인프라 팀, SRE, 개발팀 모두가 서비스 상태를 빠르게 파악할 수 있습니다. 다만 헬스체크 자체가 장애의 원인이 되지 않도록, 부하와 타임아웃에 대한 고려를 함께 해야 합니다.

실무에서 헬스체크 전략을 수립할 때는 "무엇을 체크할 것인가"와 함께 "어떤 상태가 재시작을 트리거해야 하는가"를 팀 내에서 명확히 합의하고 문서화하는 것을 강력히 권장합니다.
