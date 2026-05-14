# Spring Boot Actuator와 Micrometer 모니터링 설정

## 개요

프로덕션 환경에서 애플리케이션을 운영하다 보면 "지금 서버가 제대로 동작하고 있는가?"라는 질문은 단순한 궁금증이 아니라 서비스 안정성과 직결된 핵심 질문이 된다. Spring Boot Actuator와 Micrometer는 이 질문에 체계적으로 답할 수 있는 강력한 모니터링 솔루션이다.

Spring Boot Actuator는 애플리케이션의 상태, 메트릭, 환경 정보 등을 HTTP 엔드포인트로 노출하는 프레임워크이고, Micrometer는 JVM 기반 애플리케이션을 위한 메트릭 계측(instrumentation) 퍼사드로서 Prometheus, Datadog, InfluxDB 등 다양한 모니터링 시스템과 통합을 지원한다.

이 글에서는 실무 환경에서 바로 적용 가능한 설정 방법과 함께, 주의해야 할 트레이드오프까지 깊이 있게 다룬다.

---

## 핵심 개념

### Spring Boot Actuator

Actuator는 `spring-boot-actuator` 모듈을 통해 제공되며, 다음과 같은 내장 엔드포인트를 제공한다.

| 엔드포인트 | 설명 |
|---|---|
| `/actuator/health` | 애플리케이션 헬스 상태 |
| `/actuator/metrics` | 메트릭 정보 |
| `/actuator/info` | 애플리케이션 정보 |
| `/actuator/env` | 환경 변수 및 설정 |
| `/actuator/loggers` | 로거 레벨 조회/변경 |
| `/actuator/threaddump` | 스레드 덤프 |
| `/actuator/prometheus` | Prometheus 포맷 메트릭 |

### Micrometer 아키텍처

Micrometer는 벤더 중립적인 메트릭 API를 제공한다. 핵심 인터페이스는 `MeterRegistry`이며, 여기에 다양한 `Meter` 타입을 등록한다.

- **Counter**: 단조 증가 값 (요청 수, 에러 수)
- **Gauge**: 임의의 순간 값 (현재 연결 수, 메모리 사용량)
- **Timer**: 이벤트 지속 시간과 빈도 측정
- **DistributionSummary**: 이벤트 크기 분포 측정
- **LongTaskTimer**: 장시간 실행 작업 모니터링

---

## 실전 예제

### 1. 의존성 설정

```xml
<!-- pom.xml -->
<dependencies>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-actuator</artifactId>
    </dependency>
    <!-- Micrometer Prometheus Registry -->
    <dependency>
        <groupId>io.micrometer</groupId>
        <artifactId>micrometer-registry-prometheus</artifactId>
    </dependency>
</dependencies>
```

```gradle
// build.gradle
dependencies {
    implementation 'org.springframework.boot:spring-boot-starter-actuator'
    implementation 'io.micrometer:micrometer-registry-prometheus'
}
```

### 2. 기본 설정 (application.yml)

```yaml
management:
  endpoints:
    web:
      exposure:
        include: health, info, metrics, prometheus, loggers, threaddump
      base-path: /actuator
  endpoint:
    health:
      show-details: when-authorized  # 인증된 사용자에게만 상세 정보 노출
      probes:
        enabled: true  # Kubernetes liveness/readiness probe 활성화
    prometheus:
      enabled: true
  metrics:
    tags:
      application: ${spring.application.name}  # 모든 메트릭에 공통 태그 추가
      environment: ${spring.profiles.active:default}
    distribution:
      percentiles-histogram:
        http.server.requests: true  # HTTP 요청에 대한 히스토그램 활성화
      percentiles:
        http.server.requests: 0.5, 0.90, 0.95, 0.99
      slo:
        http.server.requests: 10ms, 50ms, 100ms, 200ms, 500ms

spring:
  application:
    name: my-service

info:
  app:
    name: ${spring.application.name}
    version: '@project.version@'
    description: My Spring Boot Service
```

### 3. 커스텀 헬스 인디케이터

데이터베이스, 외부 API, 캐시 등의 상태를 커스텀하게 체크하려면 `HealthIndicator`를 구현한다.

```java
@Component
@RequiredArgsConstructor
public class ExternalApiHealthIndicator implements HealthIndicator {

    private final ExternalApiClient externalApiClient;
    private final MeterRegistry meterRegistry;

    @Override
    public Health health() {
        Timer.Sample sample = Timer.start(meterRegistry);
        try {
            boolean isHealthy = externalApiClient.ping();
            sample.stop(Timer.builder("external.api.health.check")
                    .tag("status", "success")
                    .register(meterRegistry));

            if (isHealthy) {
                return Health.up()
                        .withDetail("service", "External Payment API")
                        .withDetail("status", "reachable")
                        .build();
            }
            return Health.down()
                    .withDetail("service", "External Payment API")
                    .withDetail("reason", "ping failed")
                    .build();

        } catch (Exception e) {
            sample.stop(Timer.builder("external.api.health.check")
                    .tag("status", "error")
                    .register(meterRegistry));

            return Health.down(e)
                    .withDetail("service", "External Payment API")
                    .withDetail("error", e.getMessage())
                    .build();
        }
    }
}
```

### 4. 커스텀 메트릭 등록

비즈니스 메트릭을 직접 계측하는 방법이다. 주문 처리 서비스를 예시로 살펴보자.

```java
@Service
@RequiredArgsConstructor
public class OrderService {

    private final OrderRepository orderRepository;
    private final MeterRegistry meterRegistry;

    // Counter: 주문 생성 횟수
    private Counter orderCreatedCounter;
    // Counter: 주문 실패 횟수
    private Counter orderFailedCounter;
    // Timer: 주문 처리 시간
    private Timer orderProcessingTimer;
    // Gauge용 AtomicInteger: 현재 처리 중인 주문 수
    private final AtomicInteger activeOrders = new AtomicInteger(0);

    @PostConstruct
    public void initMetrics() {
        orderCreatedCounter = Counter.builder("order.created.total")
                .description("Total number of orders created")
                .tag("service", "order")
                .register(meterRegistry);

        orderFailedCounter = Counter.builder("order.failed.total")
                .description("Total number of failed orders")
                .tag("service", "order")
                .register(meterRegistry);

        orderProcessingTimer = Timer.builder("order.processing.duration")
                .description("Order processing duration")
                .publishPercentiles(0.5, 0.95, 0.99)
                .publishPercentileHistogram()
                .register(meterRegistry);

        // Gauge는 값을 외부에서 관찰하는 방식으로 등록
        Gauge.builder("order.active.count", activeOrders, AtomicInteger::get)
                .description("Number of currently active orders")
                .register(meterRegistry);
    }

    @Transactional
    public Order createOrder(OrderRequest request) {
        activeOrders.incrementAndGet();
        return orderProcessingTimer.record(() -> {
            try {
                Order order = Order.from(request);
                Order saved = orderRepository.save(order);
                orderCreatedCounter.increment();
                // 태그를 동적으로 붙이는 경우
                meterRegistry.counter("order.created.by.type",
                        "type", request.getOrderType().name()).increment();
                return saved;
            } catch (Exception e) {
                orderFailedCounter.increment();
                throw e;
            } finally {
                activeOrders.decrementAndGet();
            }
        });
    }
}
```

### 5. @Timed 어노테이션 활용

AOP 기반으로 메서드 실행 시간을 자동으로 측정하고 싶다면 `@Timed`와 `TimedAspect` 빈을 활용한다.

```java
// TimedAspect 빈 등록
@Configuration
public class MetricsConfig {

    @Bean
    public TimedAspect timedAspect(MeterRegistry registry) {
        return new TimedAspect(registry);
    }

    // MeterFilter: 특정 메트릭 필터링 및 공통 태그 추가
    @Bean
    public MeterFilter commonTagsMeterFilter(
            @Value("${spring.application.name}") String appName) {
        return MeterFilter.commonTags(
                Tags.of("app", appName, "region", "ap-northeast-2")
        );
    }
}
```

```java
@Service
public class UserService {

    @Timed(value = "user.profile.fetch", 
           description = "Time taken to fetch user profile",
           percentiles = {0.5, 0.95, 0.99},
           histogram = true)
    public UserProfile getUserProfile(Long userId) {
        // 비즈니스 로직
        return userRepository.findById(userId)
                .map(UserProfile::from)
                .orElseThrow(() -> new UserNotFoundException(userId));
    }
}
```

### 6. Prometheus + Grafana 연동

Prometheus 설정 파일에 Spring Boot 앱을 타겟으로 추가한다.

```yaml
# prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'spring-boot-app'
    metrics_path: '/actuator/prometheus'
    static_configs:
      - targets: ['host.docker.internal:8080']
    relabel_configs:
      - source_labels: [__address__]
        target_label: instance
```

Grafana에서 자주 쓰이는 PromQL 쿼리 예시:

```promql
# HTTP 요청 처리량 (RPS)
rate(http_server_requests_seconds_count{application="my-service"}[1m])

# 95th 퍼센타일 응답 시간
histogram_quantile(0.95, 
  sum(rate(http_server_requests_seconds_bucket{application="my-service"}[5m])) 
  by (le, uri))

# JVM 힙 사용률
jvm_memory_used_bytes{area="heap"} / jvm_memory_max_bytes{area="heap"} * 100

# 주문 생성 비율
rate(order_created_total[5m])
```

### 7. Actuator 보안 설정

```java
@Configuration
@EnableWebSecurity
@RequiredArgsConstructor
public class ActuatorSecurityConfig {

    @Bean
    public SecurityFilterChain actuatorSecurityFilterChain(HttpSecurity http) throws Exception {
        http
            .securityMatcher(EndpointRequest.toAnyEndpoint())
            .authorizeHttpRequests(auth -> auth
                // health, info는 모두 허용 (k8s probe용)
                .requestMatchers(EndpointRequest.to(HealthEndpoint.class, InfoEndpoint.class))
                    .permitAll()
                // prometheus는 모니터링 서버 IP 범위만 허용
                .requestMatchers(EndpointRequest.to(PrometheusScrapeEndpoint.class))
                    .hasIpAddress("10.0.0.0/8")
                // 나머지는 ADMIN 역할 필요
                .anyRequest().hasRole("ADMIN")
            )
            .httpBasic(Customizer.withDefaults());
        return http.build();
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. 카디널리티(Cardinality) 폭발 문제

Micrometer에서 가장 흔한 실수는 태그 값에 동적인 고유 값(userId, orderId 등)을 사용하는 것이다.

```java
// ❌ 절대 금지: userId를 태그로 사용 시 수백만 개의 시계열 생성
meterRegistry.counter("user.action", "userId", userId.toString()).increment();

// ✅ 올바른 방법: 카테고리, 타입 등 낮은 카디널리티 값만 태그로 사용
meterRegistry.counter("user.action", "action", "LOGIN", "role", userRole).increment();
```

### 2. 민감 정보 노출 위험

`/actuator/env`와 `/actuator/configprops` 엔드포인트는 환경 변수, 설정값 등 민감한 정보를 노출할 수 있다. 프로덕션 환경에서는 반드시 `exposure.include` 목록을 최소화하고 인증을 적용해야 한다.

```yaml
management:
  endpoints:
    web:
      exposure:
        # 프로덕션에서 env, configprops는 제외
        include: health, prometheus, info
```

### 3. 성능 오버헤드

`histogram = true` 옵션이나 `publishPercentileHistogram()`을 남발하면 메모리와 CPU 오버헤드가 발생한다. 히스토그램은 꼭 필요한 핵심 메트릭에만 적용하라. 특히 타이머에 높은 해상도 히스토그램을 모든 엔드포인트에 적용하면 메모리 사용량이 크게 증가할 수 있다.

### 4. Gauge와 WeakReference

`Gauge.builder()`로 객체를 참조할 때 Micrometer는 기본적으로 WeakReference를 사용한다. 참조 대상 객체가 GC되면 Gauge가 NaN을 반환하게 되므로, 서비스 빈처럼 라이프사이클이 보장된 객체를 참조하거나 `strongReference(true)` 옵션을 명시적으로 사용해야 한다.

### 5. Kubernetes 환경에서의 Probe 설정

```yaml
# Kubernetes Deployment 예시
livenessProbe:
  httpGet:
    path: /actuator/health/liveness
    port: 8080
  initialDelaySeconds: 30
  periodSeconds: 10
readinessProbe:
  httpGet:
    path: /actuator/health/readiness
    port: 8080
  initialDelaySeconds: 20
  periodSeconds: 5
```

Spring Boot 2.3+에서는 `management.endpoint.health.probes.enabled=true` 설정 시 `/liveness`와 `/readiness` 경로가 자동으로 활성화된다.

---

## 정리

Spring Boot Actuator와 Micrometer는 단순히 "잘 되고 있는지 보는 도구"가 아니라 서비스의 내부 상태를 외부로 투명하게 노출하는 **Observability**의 핵심 기반이다.

실무 적용 시 핵심 체크리스트를 정리하면 다음과 같다.

- **최소 노출 원칙**: 필요한 엔드포인트만 `exposure.include`에 등록하라.
- **보안 필수**: Actuator 엔드포인트에 반드시 IP 제한 또는 인증을 적용하라.
- **카디널리티 관리**: 태그 값은 항상 낮은 카디널리티(10개 미만의 고유 값)를 유지하라.
- **공통 태그 활용**: `application`, `environment`, `region` 등의 공통 태그를 MeterFilter로 일괄 적용하면 Grafana 대시보드 구성이 훨씬 편해진다.
- **SLO 기반 알람**: `slo` 설정으로 SLA 임계값 기반 버킷을 만들고, 이를 Alertmanager 규칙과 연동하라.

Micrometer는 Prometheus 외에도 Datadog, CloudWatch, InfluxDB 등 다양한 레지스트리를 지원하므로, 인프라 환경에 맞는 레지스트리 의존성 하나만 교체해도 동일한 계측 코드로 여러 플랫폼에 적용할 수 있다는 점도 큰 장점이다.

모니터링은 사후 대응이 아닌 선제적 운영의 도구다. Actuator와 Micrometer를 올바르게 활용해 서비스의 가시성을 높이고, 장애 발생 전에 이상 징후를 포착하는 운영 문화를 만들어 나가길 권장한다.