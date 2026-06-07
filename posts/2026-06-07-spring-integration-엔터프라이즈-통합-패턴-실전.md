# Spring Integration 엔터프라이즈 통합 패턴 실전

## 개요

엔터프라이즈 환경에서 시스템 간 통합은 피할 수 없는 과제다. 레거시 시스템과 신규 서비스, 외부 API, 메시지 큐, 파일 시스템이 복잡하게 얽힌 환경에서 개발자는 안정적이고 유지보수 가능한 통합 솔루션을 구축해야 한다.

**Spring Integration**은 Gregor Hohpe와 Bobby Woolf의 명저 *Enterprise Integration Patterns (EIP)* 를 Java/Spring 생태계에서 구현한 프레임워크다. 단순한 메시지 전달을 넘어, 라우팅, 변환, 필터링, 집계 등 복잡한 통합 시나리오를 선언적이고 일관된 방식으로 처리할 수 있게 해준다.

이 글에서는 실무에서 자주 마주치는 통합 시나리오를 중심으로 Spring Integration의 핵심 패턴과 예제 코드를 살펴본다.

---

## 핵심 개념

Spring Integration의 세계를 이해하려면 세 가지 핵심 구성 요소를 먼저 파악해야 한다.

### Message

모든 데이터는 `Message<T>` 객체로 캡슐화된다. 메시지는 **페이로드(payload)** 와 **헤더(headers)** 로 구성되며, 헤더에는 라우팅 정보, 우선순위, 상관관계 ID 등 메타데이터를 담는다.

### Message Channel

메시지가 이동하는 파이프라인이다. `DirectChannel`(동기, 단일 구독자), `PublishSubscribeChannel`(발행-구독), `QueueChannel`(비동기, 버퍼링) 등 다양한 채널 타입이 있다.

### Message Endpoint

채널에서 메시지를 소비하거나 처리하는 컴포넌트다. `Transformer`, `Router`, `Filter`, `Splitter`, `Aggregator`, `ServiceActivator` 등이 여기에 해당한다.

---

## 실전 예제

### 시나리오: 주문 처리 파이프라인

다음과 같은 요구사항을 구현해 보자.

1. HTTP 요청으로 주문이 들어온다.
2. 주문 유효성을 검사하고 유효하지 않은 주문은 오류 채널로 분기한다.
3. 주문 금액에 따라 일반/VIP 처리 경로로 라우팅한다.
4. 처리된 주문을 외부 시스템에 전송한다.

#### 의존성 설정

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-integration</artifactId>
</dependency>
<dependency>
    <groupId>org.springframework.integration</groupId>
    <artifactId>spring-integration-http</artifactId>
</dependency>
<dependency>
    <groupId>org.springframework.integration</groupId>
    <artifactId>spring-integration-amqp</artifactId>
</dependency>
```

#### 도메인 모델

```java
@Data
@Builder
public class Order {
    private String orderId;
    private String customerId;
    private BigDecimal amount;
    private OrderStatus status;
    private List<OrderItem> items;

    public enum OrderStatus {
        PENDING, VALID, INVALID, PROCESSING, COMPLETED
    }
}
```

#### Integration Flow 구성

Spring Integration 5.x 이후부터는 Java DSL을 통해 Flow를 선언적으로 구성하는 것이 주류다.

```java
@Configuration
@EnableIntegration
public class OrderIntegrationConfig {

    @Bean
    public MessageChannel orderInputChannel() {
        return new DirectChannel();
    }

    @Bean
    public MessageChannel validOrderChannel() {
        return new DirectChannel();
    }

    @Bean
    public MessageChannel invalidOrderChannel() {
        return new QueueChannel(100); // 비동기 처리, 최대 100개 버퍼
    }

    @Bean
    public MessageChannel vipOrderChannel() {
        return new DirectChannel();
    }

    @Bean
    public MessageChannel standardOrderChannel() {
        return new DirectChannel();
    }

    @Bean
    public IntegrationFlow orderProcessingFlow(
            OrderValidationService validationService,
            OrderRouter orderRouter,
            VipOrderProcessor vipProcessor,
            StandardOrderProcessor standardProcessor) {

        return IntegrationFlow.from(orderInputChannel())
            // 1. 유효성 검사 (Filter 패턴)
            .filter(Order.class,
                order -> validationService.isValid(order),
                e -> e.discardChannel(invalidOrderChannel()))
            // 2. 메시지 헤더에 처리 시각 추가 (Header Enricher 패턴)
            .enrichHeaders(h -> h
                .header("processedAt", Instant.now().toString())
                .headerExpression("orderPriority",
                    "payload.amount.compareTo(new java.math.BigDecimal('100000')) >= 0 ? 'VIP' : 'STANDARD'"))
            // 3. 금액 기반 라우팅 (Content-Based Router 패턴)
            .route(Order.class,
                order -> order.getAmount().compareTo(new BigDecimal("100000")) >= 0
                    ? "vipOrderChannel"
                    : "standardOrderChannel")
            .get();
    }

    @Bean
    public IntegrationFlow vipOrderFlow(VipOrderProcessor vipProcessor) {
        return IntegrationFlow.from(vipOrderChannel())
            .handle(vipProcessor, "process")
            .channel("orderOutputChannel")
            .get();
    }

    @Bean
    public IntegrationFlow standardOrderFlow(StandardOrderProcessor standardProcessor) {
        return IntegrationFlow.from(standardOrderChannel())
            .handle(standardProcessor, "process")
            .channel("orderOutputChannel")
            .get();
    }
}
```

#### Splitter & Aggregator 패턴

대량 주문을 배치로 받아 개별 처리 후 결과를 집계하는 패턴이다.

```java
@Bean
public IntegrationFlow batchOrderFlow() {
    return IntegrationFlow.from("batchOrderInputChannel")
        // Splitter: 배치 주문을 개별 주문으로 분리
        .split(Order.class, order -> order.getItems())
        // 각 아이템 병렬 처리
        .channel(c -> c.executor(Executors.newFixedThreadPool(5)))
        .handle(this::processOrderItem)
        // Aggregator: 동일 상관관계 ID를 가진 메시지 집계
        .aggregate(a -> a
            .correlationExpression("headers['correlationId']")
            .releaseStrategy(g -> g.size() == g.getMessages().stream()
                .findFirst()
                .map(m -> (Integer) m.getHeaders().get("batchSize"))
                .orElse(0))
            .outputProcessor(g -> g.getMessages().stream()
                .map(Message::getPayload)
                .collect(Collectors.toList())))
        .handle(this::completeBatchProcess)
        .get();
}

private OrderItem processOrderItem(OrderItem item, MessageHeaders headers) {
    // 재고 확인, 가격 계산 등 개별 처리
    item.setProcessed(true);
    return item;
}
```

#### 오류 처리 및 재시도 (Retry & Dead Letter)

```java
@Bean
public IntegrationFlow errorHandlingFlow() {
    return IntegrationFlow.from(invalidOrderChannel())
        .log(LoggingHandler.Level.WARN, "INVALID_ORDER",
            m -> "Invalid order received: " + m.getPayload())
        .handle(errorNotificationService, "notifyInvalidOrder")
        .get();
}

@Bean
public IntegrationFlow externalSystemFlow() {
    return IntegrationFlow.from("orderOutputChannel")
        .handle(Http.outboundGateway("${external.system.url}/orders")
                .httpMethod(HttpMethod.POST)
                .expectedResponseType(String.class)
                .requestFactory(retryableRequestFactory()),
            e -> e.advice(retryAdvice()))
        .get();
}

@Bean
public RequestHandlerRetryAdvice retryAdvice() {
    RequestHandlerRetryAdvice advice = new RequestHandlerRetryAdvice();
    RetryTemplate retryTemplate = RetryTemplate.builder()
        .maxAttempts(3)
        .exponentialBackoff(1000, 2, 10000)
        .retryOn(RestClientException.class)
        .build();
    advice.setRetryTemplate(retryTemplate);
    // 최종 실패 시 Dead Letter 채널로 전송
    advice.setRecoveryCallback(ctx -> {
        Message<?> failed = (Message<?>) ctx.getAttribute(RetryMessageHandlerAdvice.FAILED_MESSAGE_CONTEXT_KEY);
        deadLetterChannel().send(failed);
        return null;
    });
    return advice;
}
```

#### Gateway 인터페이스로 깔끔한 진입점 제공

```java
@MessagingGateway(defaultRequestChannel = "orderInputChannel")
public interface OrderGateway {

    @Gateway(requestChannel = "orderInputChannel",
             replyChannel = "orderReplyChannel",
             requestTimeout = 5000,
             replyTimeout = 10000)
    OrderResult submitOrder(Order order);

    @Gateway(requestChannel = "batchOrderInputChannel")
    void submitBatchOrders(List<Order> orders);
}

// 사용 측 코드 - Spring Integration을 전혀 모르는 서비스처럼 사용 가능
@Service
@RequiredArgsConstructor
public class OrderService {

    private final OrderGateway orderGateway;

    public OrderResult processOrder(OrderRequest request) {
        Order order = OrderMapper.toOrder(request);
        return orderGateway.submitOrder(order);
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. 채널 타입 선택의 중요성

`DirectChannel`은 호출 스레드에서 동기적으로 처리되어 트랜잭션 전파가 가능하지만, 처리 지연이 발생하면 호출자가 블로킹된다. `QueueChannel`은 비동기이지만 트랜잭션 경계가 달라진다. **트랜잭션이 필요한 구간에는 반드시 `DirectChannel`을 사용해야 한다.**

```java
// 잘못된 예: QueueChannel 너머로 트랜잭션이 전파되지 않음
@Transactional
public void processWithTransaction(Order order) {
    queueChannel.send(MessageBuilder.withPayload(order).build());
    // 여기서 예외 발생해도 이미 채널에 넣어진 메시지는 롤백 안 됨
}
```

### 2. 메모리 누수와 QueueChannel 크기 제한

`QueueChannel`에 용량 제한 없이 메시지를 계속 넣으면 OOM이 발생할 수 있다. 반드시 용량을 지정하고 백프레셔 전략을 마련해야 한다.

```java
// 권장: 명시적 용량 제한
@Bean
public MessageChannel bufferingChannel() {
    return new QueueChannel(500); // 500개 초과 시 send() 블로킹 또는 실패
}
```

### 3. 디버깅의 어려움

파이프라인 형태의 흐름은 런타임 에러 추적이 까다롭다. `WireTap` 채널을 활용해 중간 단계를 관찰하거나, Spring Integration의 Integration Graph(`/actuator/integrationgraph`)를 활용하면 전체 플로우를 시각화할 수 있다.

```java
.wireTap(wt -> wt
    .channel(c -> c.executor(Executors.newSingleThreadExecutor()))
    .handle(m -> log.debug("Message snapshot: {}", m)))
```

### 4. 복잡성 관리

EIP 패턴은 강력하지만, 과도하게 사용하면 플로우가 너무 복잡해져 유지보수가 어려워진다. 간단한 동기 처리라면 Spring Integration보다 직접적인 서비스 호출이 더 나을 수 있다. **비동기 처리, 다양한 프로토콜 통합, 복잡한 라우팅이 필요한 경우에만 도입을 검토하라.**

---

## 정리

Spring Integration은 엔터프라이즈 통합 시나리오에서 강력한 도구다. 핵심을 다시 정리하면:

| 패턴 | 구성 요소 | 사용 시나리오 |
|------|-----------|---------------|
| Content-Based Router | `route()` | 조건에 따른 경로 분기 |
| Filter | `filter()` | 유효하지 않은 메시지 제거 |
| Splitter | `split()` | 배치 메시지 개별 분리 |
| Aggregator | `aggregate()` | 분리된 메시지 집계 |
| Header Enricher | `enrichHeaders()` | 메타데이터 추가 |
| Dead Letter Channel | `RecoveryCallback` | 처리 실패 메시지 격리 |

**Messaging Gateway**로 진입점을 추상화하고, **Java DSL**로 플로우를 선언적으로 정의하며, **채널 타입**을 적재적소에 선택하는 것이 Spring Integration을 잘 활용하는 핵심이다.

복잡한 외부 시스템 통합, 비동기 파이프라인, 다양한 프로토콜 어댑터가 필요한 프로젝트라면 Spring Integration은 충분한 투자 가치가 있다. 단, 팀 전체가 EIP 패턴에 익숙해지는 러닝 커브와 디버깅 복잡성을 충분히 고려한 뒤 도입 결정을 내리길 권한다.