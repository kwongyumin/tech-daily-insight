# 대규모 MSA 환경의 Observability 설계

## 개요

마이크로서비스 아키텍처(MSA)가 보편화되면서 시스템을 "이해"하는 일이 점점 어려워졌다. 모놀리식 환경에서는 로그 파일 하나, APM 하나로 대부분의 문제를 추적할 수 있었지만, 수십~수백 개의 서비스가 비동기로 상호작용하는 MSA에서는 단순한 모니터링만으로는 장애 원인을 파악하기 어렵다.

이 포스팅에서는 대규모 MSA 환경에서 **Observability(관찰 가능성)**를 어떻게 설계하고 구현하는지를 실무 관점에서 다룬다. 단순히 도구 소개에 그치지 않고, **왜 그 선택을 해야 하는지**, **어떤 트레이드오프가 있는지**를 중심으로 설명한다.

---

## Observability란 무엇인가

Observability는 시스템의 외부 출력(Output)만으로 내부 상태를 얼마나 잘 추론할 수 있는가를 의미한다. 이는 단순한 "모니터링"과 다르다. 모니터링은 미리 알고 있는 문제를 감지하는 것(known unknowns)이고, Observability는 미리 알지 못했던 문제도 진단할 수 있는 능력(unknown unknowns)이다.

Observability의 세 가지 핵심 기둥은 다음과 같다.

| 구성 요소 | 역할 | 대표 도구 |
|---|---|---|
| **Metrics** | 수치 기반의 시스템 상태 측정 | Prometheus, Micrometer |
| **Logs** | 이벤트 발생 기록 | ELK Stack, Loki |
| **Traces** | 요청의 흐름 추적 | Jaeger, Zipkin, Tempo |

MSA에서는 이 세 가지가 **서로 연결**되어야 비로소 실질적인 Observability가 완성된다. 예를 들어 메트릭에서 이상 수치를 발견했을 때, 해당 시점의 트레이스를 조회하고, 그 트레이스에서 로그로 바로 이동할 수 있어야 한다.

---

## 핵심 개념 설계

### 1. Distributed Tracing과 Correlation ID

MSA에서 하나의 사용자 요청은 여러 서비스를 거친다. 이 흐름을 하나의 단위로 묶어주는 것이 **Distributed Tracing**이다.

- **Trace**: 전체 요청의 생애주기
- **Span**: 각 서비스에서 처리되는 단위 작업
- **Context Propagation**: HTTP 헤더(W3C TraceContext 표준 기준 `traceparent`)나 메시지 브로커 헤더를 통한 컨텍스트 전달

Spring Boot 3.x 환경에서는 **Micrometer Tracing**이 표준으로 자리잡았다. 이전의 Spring Cloud Sleuth를 대체한다.

```xml
<!-- pom.xml -->
<dependency>
    <groupId>io.micrometer</groupId>
    <artifactId>micrometer-tracing-bridge-otel</artifactId>
</dependency>
<dependency>
    <groupId>io.opentelemetry</groupId>
    <artifactId>opentelemetry-exporter-otlp</artifactId>
</dependency>
```

```yaml
# application.yml
management:
  tracing:
    sampling:
      probability: 0.1  # 10% 샘플링 (운영 환경)
  otlp:
    tracing:
      endpoint: http://otel-collector:4318/v1/traces
```

### 2. 구조화된 로깅 (Structured Logging)

로그는 "읽기 위한" 것이 아니라 "검색하고 집계하기 위한" 것이어야 한다. JSON 형태의 구조화 로그는 Elasticsearch나 Loki에서 필드 기반 쿼리를 가능하게 한다.

```java
// Logback + Logstash Encoder 설정
// logback-spring.xml
```

```xml
<configuration>
    <appender name="JSON" class="ch.qos.logback.core.ConsoleAppender">
        <encoder class="net.logstash.logback.encoder.LogstashEncoder">
            <includeMdcKeyName>traceId</includeMdcKeyName>
            <includeMdcKeyName>spanId</includeMdcKeyName>
            <includeMdcKeyName>userId</includeMdcKeyName>
            <includeMdcKeyName>requestId</includeMdcKeyName>
        </encoder>
    </appender>
</configuration>
```

Micrometer Tracing을 사용하면 `traceId`와 `spanId`가 MDC에 자동으로 주입된다. 여기에 비즈니스 컨텍스트(`userId`, `orderId` 등)를 추가로 넣어두면 로그와 트레이스를 연결하는 것이 훨씬 쉬워진다.

```java
@Service
public class OrderService {

    private static final Logger log = LoggerFactory.getLogger(OrderService.class);

    public OrderResponse createOrder(OrderRequest request) {
        // 비즈니스 컨텍스트를 MDC에 추가
        MDC.put("userId", request.getUserId());
        MDC.put("orderId", request.getOrderId());

        try {
            log.info("주문 생성 시작");
            // ... 비즈니스 로직
            log.info("주문 생성 완료");
            return response;
        } finally {
            MDC.remove("userId");
            MDC.remove("orderId");
        }
    }
}
```

### 3. Metrics 설계 원칙 - RED Method

마이크로서비스 메트릭 설계에는 **RED Method**가 유용하다.

- **R**ate: 초당 요청 수
- **E**rrors: 오류율
- **D**uration: 요청 처리 시간(레이턴시)

```java
@RestController
@RequiredArgsConstructor
public class OrderController {

    private final MeterRegistry meterRegistry;
    private final OrderService orderService;

    @PostMapping("/orders")
    public ResponseEntity<OrderResponse> createOrder(@RequestBody OrderRequest request) {
        Timer.Sample sample = Timer.start(meterRegistry);

        try {
            OrderResponse response = orderService.createOrder(request);

            sample.stop(Timer.builder("order.creation.duration")
                .tag("status", "success")
                .tag("region", request.getRegion())
                .register(meterRegistry));

            meterRegistry.counter("order.creation.count",
                "status", "success").increment();

            return ResponseEntity.ok(response);

        } catch (Exception e) {
            sample.stop(Timer.builder("order.creation.duration")
                .tag("status", "error")
                .tag("error_type", e.getClass().getSimpleName())
                .register(meterRegistry));

            meterRegistry.counter("order.creation.count",
                "status", "error").increment();

            throw e;
        }
    }
}
```

---

## 실전 구성: OpenTelemetry Collector 중심 아키텍처

대규모 환경에서는 각 서비스가 직접 백엔드(Prometheus, Jaeger 등)로 데이터를 전송하는 것보다, **OpenTelemetry Collector**를 중간에 두는 것이 훨씬 유연하다.

```
[서비스 A] ──┐
[서비스 B] ──┤──▶ [OTel Collector] ──▶ Metrics: Prometheus
[서비스 C] ──┘          │           ──▶ Traces:  Tempo / Jaeger
                        └───────────▶ Logs:    Loki
```

```yaml
# otel-collector-config.yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    timeout: 5s
    send_batch_size: 1024
  # 민감 정보 필터링
  attributes:
    actions:
      - key: http.request.header.authorization
        action: delete

exporters:
  prometheusremotewrite:
    endpoint: http://prometheus:9090/api/v1/write
  otlp/tempo:
    endpoint: http://tempo:4317
  loki:
    endpoint: http://loki:3100/loki/api/v1/push

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch, attributes]
      exporters: [otlp/tempo]
    metrics:
      receivers: [otlp]
      processors: [batch]
      exporters: [prometheusremotewrite]
    logs:
      receivers: [otlp]
      processors: [batch, attributes]
      exporters: [loki]
```

이 구성의 핵심 장점은 **백엔드를 교체해도 애플리케이션 코드를 변경하지 않아도 된다**는 점이다. Jaeger에서 Tempo로 마이그레이션하거나, 클라우드 벤더의 관리형 서비스로 전환할 때 Collector 설정만 바꾸면 된다.

---

## 주의사항 및 트레이드오프

### 샘플링 전략: 비용과 정확성의 균형

트레이스 전체를 수집하면 스토리지와 처리 비용이 폭발적으로 증가한다. 샘플링 전략은 크게 두 가지다.

**Head-based Sampling**: 요청 진입 시점에 샘플링 여부 결정
- 구현 단순, 낮은 오버헤드
- 단점: 오류가 발생한 요청도 샘플링 밖으로 빠질 수 있음

**Tail-based Sampling**: 요청이 완료된 후 결과를 보고 샘플링 결정
- 오류 요청, 느린 요청을 반드시 포함 가능
- 단점: OTel Collector에 메모리 부담, 구성 복잡

```yaml
# Tail-based Sampling 예시 (OTel Collector)
processors:
  tail_sampling:
    decision_wait: 10s
    policies:
      - name: errors-policy
        type: status_code
        status_code: { status_codes: [ERROR] }
      - name: slow-traces-policy
        type: latency
        latency: { threshold_ms: 1000 }
      - name: probabilistic-policy
        type: probabilistic
        probabilistic: { sampling_percentage: 5 }
```

### 카디널리티 폭발 주의

메트릭 레이블(태그)에 **사용자 ID, 주문 ID처럼 카디널리티가 높은 값**을 넣으면 Prometheus의 시계열 수가 기하급수적으로 늘어나 OOM을 유발할 수 있다. 레이블 값은 항상 **유한하고 예측 가능한 집합**이어야 한다.

```java
// ❌ 잘못된 예 - 카디널리티 폭발
meterRegistry.counter("api.call", "userId", userId).increment();

// ✅ 올바른 예 - 유한한 카디널리티
meterRegistry.counter("api.call", "endpoint", "/orders", "method", "POST").increment();
```

### Observability 데이터의 보안

로그와 트레이스에는 **PII(개인식별정보)**가 포함될 수 있다. OTel Collector의 `attributes` 프로세서나 애플리케이션 레벨의 마스킹을 통해 민감 데이터가 수집 파이프라인에 흘러들어가지 않도록 설계해야 한다.

### 운영 오버헤드

Observability 인프라 자체가 하나의 복잡한 시스템이 된다. Prometheus, Loki, Tempo, Grafana, OTel Collector를 모두 직접 운영하는 것은 상당한 부담이다. 팀의 규모와 역량에 따라 **Grafana Cloud, Datadog, AWS X-Ray** 같은 관리형 서비스와의 트레이드오프를 현실적으로 고려해야 한다.

---

## 정리

대규모 MSA의 Observability는 단순히 도구를 도입하는 문제가 아니다. 다음 원칙을 중심으로 설계 방향을 잡아야 한다.

1. **세 기둥(Metrics, Logs, Traces)의 연결**: `traceId`를 공통 키로 삼아 세 데이터 소스를 연결하라.
2. **OpenTelemetry를 표준으로**: 벤더 종속을 피하고 OTel SDK + Collector 구조를 기반으로 설계하라.
3. **구조화 로깅은 선택이 아닌 필수**: 텍스트 로그는 규모가 커질수록 검색과 분석이 불가능해진다.
4. **샘플링 전략은 명시적으로**: 비용과 정확성 사이의 균형을 의식적으로 결정하라.
5. **카디널리티를 항상 의식하라**: 메트릭 레이블 설계 실수는 운영 장애로 직결된다.

Observability는 "문제가 생겼을 때 빠르게 원인을 찾는" 능력이다. 이것을 제대로 갖춰두면 새벽 알람을 받고도 10분 안에 원인을 파악하는 팀이 될 수 있다. 아키텍처 초기부터 Observability를 설계에 포함시키는 것이, 나중에 레트로핏하는 것보다 훨씬 적은 비용으로 높은 효과를 거둘 수 있는 길이다.
