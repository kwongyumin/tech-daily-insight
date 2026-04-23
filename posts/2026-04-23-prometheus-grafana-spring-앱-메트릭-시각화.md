# Prometheus + Grafana Spring 앱 메트릭 시각화

## 개요

프로덕션 환경에서 애플리케이션이 살아있다는 것을 어떻게 증명할 수 있을까? 단순한 헬스체크를 넘어, JVM 힙 사용량, HTTP 요청 처리 시간, DB 커넥션 풀 상태까지 실시간으로 파악할 수 있어야 진짜 운영이라고 부를 수 있다.

Prometheus + Grafana 스택은 현재 쿠버네티스 생태계에서 사실상 표준 모니터링 솔루션으로 자리잡았다. Spring Boot는 Micrometer를 통해 이 스택과 자연스럽게 통합되며, 설정 몇 줄로 수십 개의 메트릭을 자동으로 노출할 수 있다.

이 글에서는 Spring Boot 애플리케이션에 Prometheus 메트릭을 설정하고, Grafana 대시보드까지 연결하는 전 과정을 실전 중심으로 다룬다. 단순한 튜토리얼이 아니라, 실무에서 마주치는 커스텀 메트릭 정의, 레이블 설계, 알람 연동까지 포함한다.

---

## 핵심 개념

### Micrometer: Spring의 메트릭 추상화 레이어

Micrometer는 SLF4J의 메트릭 버전이라고 이해하면 된다. 코드에서는 Micrometer API를 사용하고, 실제 백엔드(Prometheus, Datadog, CloudWatch 등)는 런타임에 교체할 수 있다.

Spring Boot Actuator가 Micrometer와 통합되어, JVM, HTTP, DataSource 등 수십 가지 메트릭을 자동으로 수집한다.

### Prometheus의 Pull 모델

Prometheus는 대상 서버에서 메트릭을 **능동적으로 수집(scrape)**한다. Spring 앱은 `/actuator/prometheus` 엔드포인트를 통해 메트릭을 노출하고, Prometheus가 설정된 주기마다 해당 엔드포인트를 호출한다.

이 Pull 모델은 서비스 디스커버리와 결합할 때 강력하지만, 방화벽 환경에서는 Pushgateway를 통한 Push 모델을 고려해야 한다.

### 메트릭 타입

| 타입 | 설명 | 사용 예 |
|------|------|---------|
| Counter | 단조 증가 값 | HTTP 요청 수, 에러 수 |
| Gauge | 임의로 증감하는 값 | 현재 연결 수, 큐 사이즈 |
| Timer | 시간 측정 + 횟수 | API 응답 시간 |
| DistributionSummary | 값의 분포 | 요청 payload 크기 |

---

## 실전 예제

### 1. 의존성 설정

```gradle
// build.gradle
dependencies {
    implementation 'org.springframework.boot:spring-boot-starter-actuator'
    implementation 'org.springframework.boot:spring-boot-starter-web'
    implementation 'io.micrometer:micrometer-registry-prometheus'
    
    // JPA 사용 시 DataSource 메트릭 자동 수집
    implementation 'org.springframework.boot:spring-boot-starter-data-jpa'
}
```

### 2. application.yml 설정

```yaml
management:
  endpoints:
    web:
      exposure:
        include: health, info, prometheus, metrics
  endpoint:
    prometheus:
      enabled: true
  metrics:
    tags:
      # 모든 메트릭에 공통 레이블 추가 (환경 구분에 필수)
      application: ${spring.application.name}
      environment: ${APP_ENV:local}
    distribution:
      percentiles-histogram:
        http.server.requests: true  # HTTP 응답 시간 히스토그램 활성화
      percentiles:
        http.server.requests: 0.5, 0.90, 0.95, 0.99
      slo:
        http.server.requests: 100ms, 300ms, 500ms, 1s

spring:
  application:
    name: order-service
```

`percentiles-histogram: true` 설정은 Prometheus의 `histogram_quantile` 함수를 통한 정확한 백분위수 계산을 가능하게 한다. 단, 카디널리티가 높아지므로 필요한 메트릭에만 선택적으로 적용한다.

### 3. 커스텀 메트릭 정의

실무에서는 비즈니스 로직 수준의 메트릭이 더 중요한 경우가 많다. 주문 처리 수, 결제 실패율, 재고 소진 이벤트 등이 그 예다.

```java
@Service
@RequiredArgsConstructor
public class OrderService {

    private final MeterRegistry meterRegistry;
    private final OrderRepository orderRepository;

    // Counter: 주문 생성 이벤트 추적
    private Counter orderCreatedCounter;
    private Counter orderFailedCounter;
    
    // Timer: 주문 처리 시간 측정
    private Timer orderProcessingTimer;
    
    // Gauge: 현재 처리 중인 주문 수 (AtomicInteger로 관리)
    private final AtomicInteger pendingOrders = new AtomicInteger(0);

    @PostConstruct
    public void initMetrics() {
        orderCreatedCounter = Counter.builder("order.created.total")
            .description("Total number of orders created")
            .tag("region", "KR")
            .register(meterRegistry);

        orderFailedCounter = Counter.builder("order.failed.total")
            .description("Total number of failed orders")
            .register(meterRegistry);

        orderProcessingTimer = Timer.builder("order.processing.duration")
            .description("Time taken to process an order")
            .publishPercentiles(0.5, 0.95, 0.99)
            .register(meterRegistry);

        // Gauge는 레지스트리에 등록 시 참조를 유지해야 함
        Gauge.builder("order.pending.count", pendingOrders, AtomicInteger::get)
            .description("Number of orders currently being processed")
            .register(meterRegistry);
    }

    public Order createOrder(OrderRequest request) {
        pendingOrders.incrementAndGet();
        
        return orderProcessingTimer.record(() -> {
            try {
                Order order = processOrder(request);
                orderCreatedCounter.increment();
                return order;
            } catch (Exception e) {
                // 실패 원인을 tag로 구분하면 더 세밀한 분석 가능
                meterRegistry.counter("order.failed.total",
                    "reason", e.getClass().getSimpleName()
                ).increment();
                throw e;
            } finally {
                pendingOrders.decrementAndGet();
            }
        });
    }
}
```

### 4. Prometheus 설정 (prometheus.yml)

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'spring-order-service'
    metrics_path: '/actuator/prometheus'
    static_configs:
      - targets: ['order-service:8080']
    # 보안 헤더가 필요한 경우
    # authorization:
    #   credentials: 'your-token'

  # 쿠버네티스 환경에서는 서비스 디스커버리 활용
  - job_name: 'kubernetes-pods'
    kubernetes_sd_configs:
      - role: pod
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
        action: keep
        regex: "true"
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_path]
        action: replace
        target_label: __metrics_path__
        regex: (.+)
```

### 5. Docker Compose로 전체 스택 실행

```yaml
# docker-compose.yml
version: '3.8'

services:
  app:
    build: .
    ports:
      - "8080:8080"
    environment:
      APP_ENV: dev
    networks:
      - monitoring

  prometheus:
    image: prom/prometheus:v2.47.0
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus_data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.retention.time=15d'
      - '--web.enable-lifecycle'  # API를 통한 설정 리로드 허용
    ports:
      - "9090:9090"
    networks:
      - monitoring

  grafana:
    image: grafana/grafana:10.1.0
    volumes:
      - grafana_data:/var/lib/grafana
      - ./monitoring/grafana/provisioning:/etc/grafana/provisioning
    environment:
      GF_SECURITY_ADMIN_PASSWORD: secret
      GF_USERS_ALLOW_SIGN_UP: "false"
    ports:
      - "3000:3000"
    depends_on:
      - prometheus
    networks:
      - monitoring

volumes:
  prometheus_data:
  grafana_data:

networks:
  monitoring:
    driver: bridge
```

### 6. Grafana 대시보드 핵심 PromQL

Grafana에서 실제로 자주 사용하는 쿼리들이다.

```promql
# HTTP 요청 처리량 (RPS)
rate(http_server_requests_seconds_count{application="order-service"}[1m])

# P99 응답 시간 (히스토그램 기반)
histogram_quantile(0.99, 
  sum(rate(http_server_requests_seconds_bucket{application="order-service"}[5m])) 
  by (le, uri)
)

# 에러율 (5xx 비율)
sum(rate(http_server_requests_seconds_count{
  application="order-service", 
  status=~"5.."
}[5m])) 
/ 
sum(rate(http_server_requests_seconds_count{application="order-service"}[5m]))

# JVM 힙 사용률
jvm_memory_used_bytes{area="heap"} 
/ 
jvm_memory_max_bytes{area="heap"}

# DB 커넥션 풀 사용률
hikaricp_connections_active / hikaricp_connections_max

# 현재 처리 중인 주문 수 (커스텀 메트릭)
order_pending_count{application="order-service"}
```

### 7. Alertmanager 연동 (prometheus-rules.yml)

```yaml
groups:
  - name: spring-app-alerts
    rules:
      - alert: HighErrorRate
        expr: |
          sum(rate(http_server_requests_seconds_count{status=~"5.."}[5m]))
          /
          sum(rate(http_server_requests_seconds_count[5m])) > 0.05
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "High HTTP error rate detected"
          description: "Error rate is {{ $value | humanizePercentage }} over the last 5 minutes"

      - alert: HighP99Latency
        expr: |
          histogram_quantile(0.99, 
            sum(rate(http_server_requests_seconds_bucket[5m])) by (le)
          ) > 1.0
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "P99 latency exceeds 1 second"

      - alert: JvmHeapCritical
        expr: |
          jvm_memory_used_bytes{area="heap"} 
          / jvm_memory_max_bytes{area="heap"} > 0.85
        for: 3m
        labels:
          severity: critical
        annotations:
          summary: "JVM heap usage above 85%"
```

---

## 주의사항 및 트레이드오프

### 카디널리티 폭발 문제

메트릭의 레이블 값이 무한히 증가하면 Prometheus의 메모리 사용량이 폭발한다. **절대 고유값(userId, orderId, IP 등)을 레이블로 사용하지 말 것.**

```java
// ❌ 잘못된 예 - orderId가 레이블이 되면 시계열이 무한 증가
meterRegistry.counter("order.processed", "orderId", order.getId()).increment();

// ✅ 올바른 예 - 카디널리티가 낮은 값만 레이블로 사용
meterRegistry.counter("order.processed", 
    "type", order.getType().name(),
    "status", order.getStatus().name()
).increment();
```

### 보안 엔드포인트 노출

`/actuator/prometheus`는 내부 메트릭을 모두 노출한다. 프로덕션에서는 반드시 접근을 제한해야 한다.

```yaml
# Spring Security 설정 또는 별도 포트 분리
management:
  server:
    port: 8081  # 메트릭 전용 포트를 분리하고 내부 네트워크에서만 접근 허용
```

### 스토리지 리텐션 계획

Prometheus는 기본 15일 데이터를 로컬에 저장한다. 장기 보존이 필요하다면 Thanos나 Cortex 같은 장기 스토리지 솔루션을 검토해야 한다. 특히 컴플라이언스 요건이 있는 금융/의료 서비스라면 이 설계가 초기부터 필요하다.

### 스크레이프 주기와 정확도

기본 15초 스크레이프 간격은 초 단위 스파이크를 놓칠 수 있다. 반대로 간격을 줄이면 Prometheus 서버의 부하와 스토리지 사용량이 급증한다. 서비스 SLA에 맞게 균형점을 찾아야 한다.

### Micrometer의 Timer.record() 주의

`Timer.record()`는 예외 발생 시에도 시간을 기록한다. 에러 케이스를 별도로 추적해야 한다면 `tag("error", "true")` 처럼 명시적으로 구분해야 한다. 그렇지 않으면 에러로 인한 빠른 실패(fail-fast)가 정상적인 처리 시간 통계를 왜곡한다.

---

## 정리

Prometheus + Grafana + Micrometer 조합은 Spring 생태계에서 가장 성숙한 모니터링 스택이다. 핵심을 정리하면 다음과 같다.

1. **자동 메트릭은 시작점이다.** Spring Actuator가 제공하는 JVM, HTTP, DataSource 메트릭은 기본 건강 지표로 활용하고, 비즈니스 임팩트를 드러내는 커스텀 메트릭을 추가로 정의하라.

2. **레이블 설계가 가장 중요하다.** 카디널리티를 항상 고려하고, 운영 환경에서 메트릭을 필터링/집계할 때 필요한 차원(dimension)을 미리 설계하라.

3. **히스토그램 > 요약(Summary).** `percentiles-histogram: true`를 사용하면 Prometheus 측에서 집계가 가능해 다중 인스턴스 환경에서 정확한 백분위수를 얻을 수 있다. Summary는 클라이언트 사이드 계산이라 집계가 불가능하다.

4. **알람은 증상 기반으로.** CPU 사용량 같은 원인 기반 알람보다 에러율, 응답 시간 같은 사용자 체감 지표를 기반으로 알람을 설정하는 것이 실효성이 높다.

실무에서 모니터링은 개발 완료 후 붙이는 것이 아니라, 설계 단계에서 "이 기능이 정상 작동하는지 어떻게 측정할 것인가"를 함께 고민해야 한다. 메트릭 없는 프로덕션은 눈을 감고 운전하는 것과 같다.