# OpenTelemetry 분산 트레이싱 Spring Boot 적용

## 개요

마이크로서비스 아키텍처가 보편화되면서 단일 요청이 수십 개의 서비스를 거치는 환경이 일반화되었다. 이런 환경에서 장애 원인을 파악하거나 성능 병목을 찾아내는 작업은 기존의 로그 기반 모니터링만으로는 한계가 명확하다. 분산 트레이싱(Distributed Tracing)은 이 문제를 해결하기 위한 핵심 도구로, 요청의 전체 흐름을 시각적으로 추적할 수 있게 해준다.

**OpenTelemetry(OTel)**는 CNCF(Cloud Native Computing Foundation)가 주도하는 오픈소스 관측 가능성(Observability) 표준 프레임워크다. 기존에 파편화되어 있던 Jaeger, Zipkin, Prometheus 등 다양한 벤더별 SDK를 하나의 통합 표준으로 제공하며, 현재 업계 표준으로 자리 잡아가고 있다.

이 글에서는 Spring Boot 3.x 환경에서 OpenTelemetry를 활용한 분산 트레이싱을 실전 수준으로 구성하는 방법을 다룬다.

---

## 핵심 개념

### Trace, Span, Context Propagation

OpenTelemetry의 핵심 구성 요소는 세 가지다.

- **Trace**: 하나의 요청이 여러 서비스를 거치는 전체 흐름. 고유한 `traceId`로 식별된다.
- **Span**: Trace를 구성하는 개별 작업 단위. 시작/종료 시간, 상태, 속성(Attributes) 등을 포함한다.
- **Context Propagation**: 서비스 간 Trace 컨텍스트를 전달하는 메커니즘. HTTP 헤더(`traceparent`, `tracestate`)를 통해 전파된다.

```
[Service A]          [Service B]          [Service C]
  Span A ──────────▶  Span B ──────────▶  Span C
  (traceId: abc)       (traceId: abc)       (traceId: abc)
```

### OpenTelemetry Architecture

OTel의 구성 요소는 크게 세 가지 레이어로 나뉜다.

1. **SDK/API**: 애플리케이션에 삽입되어 텔레메트리 데이터를 생성
2. **OpenTelemetry Collector**: 데이터를 수집, 처리, 라우팅하는 에이전트/게이트웨이
3. **Backend**: Jaeger, Zipkin, Tempo, Datadog 등 최종 저장소

```
App (SDK) → OTel Collector → Backend (Jaeger/Tempo)
                          → Prometheus (Metrics)
                          → Loki (Logs)
```

---

## 실전 예제

### 1. 의존성 설정

Spring Boot 3.x + Micrometer Tracing 조합이 현재 권장 방식이다.

```xml
<!-- pom.xml -->
<dependencies>
    <!-- Spring Boot Actuator -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-actuator</artifactId>
    </dependency>

    <!-- Micrometer Tracing Bridge for OTel -->
    <dependency>
        <groupId>io.micrometer</groupId>
        <artifactId>micrometer-tracing-bridge-otel</artifactId>
    </dependency>

    <!-- OpenTelemetry Exporter (OTLP) -->
    <dependency>
        <groupId>io.opentelemetry</groupId>
        <artifactId>opentelemetry-exporter-otlp</artifactId>
    </dependency>

    <!-- Spring WebClient / RestTemplate 자동 계측 -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-web</artifactId>
    </dependency>

    <!-- Feign Client 사용 시 -->
    <dependency>
        <groupId>io.github.openfeign</groupId>
        <artifactId>feign-micrometer</artifactId>
    </dependency>
</dependencies>
```

### 2. application.yml 설정

```yaml
spring:
  application:
    name: order-service

management:
  tracing:
    sampling:
      probability: 1.0  # 운영환경에서는 0.1~0.3 권장
  otlp:
    tracing:
      endpoint: http://otel-collector:4318/v1/traces

logging:
  pattern:
    level: "%5p [${spring.application.name:},%X{traceId:-},%X{spanId:-}]"
```

> `sampling.probability: 1.0`은 모든 요청을 트레이싱한다. 프로덕션에서는 트래픽에 따라 조정 필요.

### 3. 커스텀 Span 추가

자동 계측만으로는 비즈니스 로직 내부의 세부 동작을 추적하기 어렵다. 중요한 비즈니스 로직에는 수동으로 Span을 추가하는 것이 좋다.

```java
@Service
@RequiredArgsConstructor
public class OrderService {

    private final Tracer tracer;
    private final OrderRepository orderRepository;
    private final PaymentClient paymentClient;

    public OrderResult processOrder(OrderRequest request) {
        // 커스텀 Span 생성
        Span span = tracer.nextSpan()
                .name("order.process")
                .tag("order.customerId", request.getCustomerId())
                .tag("order.amount", String.valueOf(request.getTotalAmount()))
                .start();

        try (Tracer.SpanInScope ws = tracer.withSpan(span)) {
            // 1. 재고 확인 (별도 Span)
            validateStock(request);

            // 2. 결제 처리
            PaymentResult payment = paymentClient.processPayment(request);

            // 3. 주문 저장
            Order order = orderRepository.save(Order.from(request, payment));

            span.tag("order.id", order.getId().toString());
            span.event("order.completed");

            return OrderResult.success(order);

        } catch (InsufficientStockException e) {
            span.tag("error", "true");
            span.tag("error.message", e.getMessage());
            throw e;
        } finally {
            span.end();
        }
    }

    private void validateStock(OrderRequest request) {
        Span stockSpan = tracer.nextSpan()
                .name("order.validate-stock")
                .start();
        try (Tracer.SpanInScope ws = tracer.withSpan(stockSpan)) {
            // 재고 검증 로직
            request.getItems().forEach(item -> {
                stockSpan.tag("item.sku", item.getSku());
                // ... 검증 로직
            });
        } finally {
            stockSpan.end();
        }
    }
}
```

### 4. @NewSpan 어노테이션 활용

Spring AOP 기반의 `@NewSpan`을 활용하면 코드를 더 간결하게 유지할 수 있다.

```java
@Service
public class InventoryService {

    @NewSpan("inventory.check")
    public InventoryStatus checkInventory(
            @SpanTag("product.id") String productId,
            @SpanTag("quantity") int quantity) {

        // 내부 로직 자동으로 Span으로 래핑됨
        return queryInventoryDB(productId, quantity);
    }
}
```

### 5. WebClient Propagation 설정

서비스 간 HTTP 통신 시 Trace 컨텍스트가 자동으로 전파되려면 WebClient 빈 설정이 필요하다.

```java
@Configuration
public class WebClientConfig {

    @Bean
    public WebClient webClient(WebClient.Builder builder) {
        return builder
                .baseUrl("http://payment-service")
                .build();
        // Spring Boot 3.x에서는 Micrometer가 자동으로 ObservationFilter 적용
    }

    // RestTemplate 사용 시
    @Bean
    public RestTemplate restTemplate(RestTemplateBuilder builder) {
        return builder
                .additionalInterceptors(new ObservationRestClientHttpRequestInterceptor(
                        ObservationRegistry.create()))
                .build();
    }
}
```

### 6. OpenTelemetry Collector Docker Compose

로컬 개발 환경 구성 예시다.

```yaml
# docker-compose.yml
version: '3.8'
services:
  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.91.0
    ports:
      - "4317:4317"   # gRPC
      - "4318:4318"   # HTTP
      - "8888:8888"   # Prometheus metrics
    volumes:
      - ./otel-config.yaml:/etc/otel/config.yaml
    command: ["--config=/etc/otel/config.yaml"]

  jaeger:
    image: jaegertracing/all-in-one:1.52
    ports:
      - "16686:16686"  # Jaeger UI
      - "14250:14250"  # gRPC
```

```yaml
# otel-config.yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    timeout: 1s
    send_batch_size: 1024
  memory_limiter:
    limit_mib: 512

exporters:
  jaeger:
    endpoint: jaeger:14250
    tls:
      insecure: true
  logging:
    loglevel: debug

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [jaeger, logging]
```

### 7. Trace ID와 로그 연동

로그와 트레이스를 연동하면 문제 상황에서 빠른 컨텍스트 전환이 가능하다.

```java
@Slf4j
@RestController
@RequestMapping("/orders")
public class OrderController {

    @PostMapping
    public ResponseEntity<OrderResult> createOrder(@RequestBody OrderRequest request) {
        // Spring Sleuth/Micrometer가 MDC에 traceId, spanId 자동 삽입
        log.info("주문 요청 수신: customerId={}, amount={}",
                request.getCustomerId(), request.getTotalAmount());
        // 로그 출력: INFO [order-service,65f2a1b3c4d5e6f7,a1b2c3d4] 주문 요청 수신: ...

        OrderResult result = orderService.processOrder(request);
        return ResponseEntity.ok(result);
    }
}
```

---

## 주의사항 및 트레이드오프

### 성능 오버헤드

트레이싱은 무료가 아니다. Span 생성, 컨텍스트 전파, 네트워크 전송 모두 오버헤드가 존재한다.

- **Sampling 전략 필수**: 프로덕션에서 `probability: 1.0`은 고트래픽 서비스에서 심각한 부하를 줄 수 있다. 헤드 기반 샘플링보다는 **Tail-based Sampling**을 고려하라. OTel Collector의 `tailsampling` 프로세서를 활용하면 에러가 발생한 Trace만 전체 보존하는 전략이 가능하다.

```yaml
# Tail-based Sampling 예시
processors:
  tail_sampling:
    decision_wait: 10s
    policies:
      - name: errors-policy
        type: status_code
        status_code: { status_codes: [ERROR] }
      - name: slow-traces
        type: latency
        latency: { threshold_ms: 1000 }
```

### Span Cardinality 관리

Span 태그(Attributes)에 고유값(userId, orderId 등)을 무분별하게 추가하면 백엔드 저장소에 높은 카디널리티 문제가 발생한다. 특히 Prometheus로 메트릭을 내보낼 때 메모리 폭증의 원인이 된다.

- **하지 말 것**: `span.tag("user.email", email)` — 이메일은 카디널리티가 무한함
- **할 것**: `span.tag("user.tier", "premium")` — 제한된 값 집합

### 비동기 처리 시 컨텍스트 전파

`@Async`, `CompletableFuture`, 메시지 큐 등 비동기 처리 환경에서는 컨텍스트 전파가 자동으로 되지 않는 경우가 있다.

```java
// 잘못된 예: 컨텍스트 유실 가능
CompletableFuture.runAsync(() -> {
    inventoryService.checkInventory(productId, qty); // traceId 없음
});

// 올바른 예: 컨텍스트 전파
Span currentSpan = tracer.currentSpan();
CompletableFuture.runAsync(() -> {
    try (Tracer.SpanInScope ws = tracer.withSpan(currentSpan)) {
        inventoryService.checkInventory(productId, qty);
    }
});
```

Kafka, RabbitMQ 등 메시지 브로커를 사용할 경우 메시지 헤더를 통해 컨텍스트를 직접 주입/추출하는 코드가 필요하다.

### Spring Boot 버전별 호환성

| Spring Boot | 트레이싱 모듈 | 비고 |
|---|---|---|
| 2.x | Spring Cloud Sleuth + Brave/OTel | Sleuth가 자동 설정 |
| 3.x | Micrometer Tracing + OTel Bridge | Sleuth 공식 지원 종료 |

Spring Boot 3.x로 마이그레이션 시 `spring-cloud-sleuth` 의존성을 모두 제거하고 `micrometer-tracing-bridge-otel`로 교체해야 한다.

---

## 정리

OpenTelemetry는 단순한 라이브러리가 아니라 관측 가능성 전략의 패러다임 전환이다. 핵심 포인트를 정리하면 다음과 같다.

1. **표준화**: OTel은 벤더 종속성을 제거하고, 인프라 교체 시에도 애플리케이션 코드 변경 최소화
2. **자동 계측 우선**: Spring Boot 3.x + Micrometer 조합은 HTTP, DB, 캐시 등 대부분의 계층을 자동 계측
3. **커스텀 Span은 신중하게**: 비즈니스 로직의 핵심 흐름에만 추가하고 카디널리티를 관리
4. **Collector 아키텍처 도입**: 애플리케이션과 백엔드를 분리하여 유연한 라우팅과 백압력(Backpressure) 처리
5. **샘플링 전략 수립**: 트래픽 규모에 맞는 샘플링 정책이 시스템 안정성의 핵심

분산 트레이싱은 도입 자체보다 **운영 전략**이 더 중요하다. 과도한 트레이싱은 오히려 시스템 부하를 높이고 노이즈를 증가시킬 수 있다. 작은 서비스부터 점진적으로 도입하고, 팀 내에서 태그 네이밍 컨벤션과 샘플링 정책을 사전에 합의하는 것을 강력히 권장한다.