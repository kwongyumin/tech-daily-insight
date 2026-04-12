# Spring WebFlux와 리액티브 프로그래밍 패턴

## 개요

마이크로서비스 아키텍처가 보편화되면서 수천 개의 동시 요청을 효율적으로 처리하는 것이 백엔드 개발의 핵심 과제 중 하나가 됐다. 전통적인 Spring MVC의 스레드-퍼-요청(Thread-per-request) 모델은 I/O 바운드 작업이 많은 환경에서 스레드 고갈(Thread starvation)이라는 명확한 한계를 드러낸다.

Spring WebFlux는 이 문제를 **이벤트 루프(Event Loop)** 기반의 비동기 논블로킹(Non-blocking) 모델로 해결한다. Reactor 라이브러리를 기반으로 하며, Netty를 기본 서버로 사용해 적은 수의 스레드로 수만 개의 동시 연결을 처리할 수 있다.

이 글에서는 WebFlux의 핵심 개념부터 실무에서 마주치는 복잡한 리액티브 패턴까지, 실제 코드와 함께 깊이 있게 다룬다.

---

## 핵심 개념

### Mono와 Flux

WebFlux의 두 핵심 퍼블리셔(Publisher)를 먼저 명확히 이해해야 한다.

- **`Mono<T>`**: 0개 또는 1개의 아이템을 비동기적으로 발행하는 퍼블리셔
- **`Flux<T>`**: 0개에서 N개의 아이템을 비동기적으로 발행하는 퍼블리셔

둘 다 Project Reactor의 구현체이며, Reactive Streams 명세를 따른다. 중요한 것은 이들이 **선언적(Declarative)** 파이프라인을 구성한다는 점이다. 구독(subscribe)이 발생하기 전까지 어떠한 연산도 실행되지 않는다.

### 백프레셔(Backpressure)

리액티브 프로그래밍의 핵심 개념 중 하나다. 소비자(Subscriber)가 생산자(Publisher)에게 자신이 처리할 수 있는 데이터의 양을 요청(request)함으로써 흐름을 제어한다. 이를 통해 메모리 오버플로우나 시스템 과부하를 방지한다.

### 스케줄러(Scheduler)

Reactor는 어떤 스레드에서 연산을 실행할지 명시적으로 지정할 수 있다.

- `Schedulers.boundedElastic()`: I/O 바운드 블로킹 작업용 (동적으로 스레드 풀 확장)
- `Schedulers.parallel()`: CPU 바운드 연산용 (고정 크기 스레드 풀)
- `Schedulers.single()`: 단일 재사용 스레드

---

## 실전 예제

### 1. 기본 WebFlux 컨트롤러 구성

```java
@RestController
@RequestMapping("/api/v1/orders")
@RequiredArgsConstructor
public class OrderController {

    private final OrderService orderService;

    // 단건 조회 - Mono 반환
    @GetMapping("/{orderId}")
    public Mono<ResponseEntity<OrderResponse>> getOrder(@PathVariable String orderId) {
        return orderService.findById(orderId)
            .map(order -> ResponseEntity.ok(OrderResponse.from(order)))
            .defaultIfEmpty(ResponseEntity.notFound().build());
    }

    // 목록 조회 - Flux 반환 (Server-Sent Events 활용)
    @GetMapping(produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<OrderResponse> streamOrders(@RequestParam String userId) {
        return orderService.findByUserId(userId)
            .map(OrderResponse::from)
            .delayElements(Duration.ofMillis(100)); // 스트리밍 시뮬레이션
    }

    // 주문 생성
    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public Mono<OrderResponse> createOrder(@RequestBody @Valid Mono<CreateOrderRequest> requestMono) {
        return requestMono
            .flatMap(orderService::createOrder)
            .map(OrderResponse::from);
    }
}
```

### 2. 리액티브 서비스 레이어와 에러 처리

실무에서 가장 중요한 부분 중 하나는 에러 처리다. 명령형 코드의 try-catch 대신 연산자 체인으로 처리한다.

```java
@Service
@RequiredArgsConstructor
@Slf4j
public class OrderService {

    private final OrderRepository orderRepository;
    private final InventoryClient inventoryClient;
    private final PaymentClient paymentClient;

    public Mono<Order> createOrder(CreateOrderRequest request) {
        return inventoryClient.checkStock(request.getProductId(), request.getQuantity())
            // 재고 확인 실패 시 커스텀 예외로 변환
            .filter(StockResponse::isAvailable)
            .switchIfEmpty(Mono.error(new InsufficientStockException("재고가 부족합니다.")))
            
            // 결제 처리
            .flatMap(stock -> paymentClient.processPayment(request.getUserId(), request.getTotalAmount()))
            
            // 결제 실패 시 에러 처리 및 재시도 로직
            .retryWhen(Retry.backoff(3, Duration.ofSeconds(1))
                .filter(throwable -> throwable instanceof PaymentGatewayException)
                .onRetryExhaustedThrow((spec, signal) -> 
                    new PaymentFailedException("결제 처리에 실패했습니다.")))
            
            // 주문 저장
            .flatMap(payment -> {
                Order order = Order.create(request, payment.getTransactionId());
                return orderRepository.save(order);
            })
            
            // 에러 로깅 (사이드이펙트)
            .doOnError(e -> log.error("주문 생성 실패: userId={}, error={}", 
                request.getUserId(), e.getMessage()))
            
            // 특정 예외 타입별 처리
            .onErrorMap(WebClientResponseException.class, 
                e -> new ExternalServiceException("외부 서비스 오류: " + e.getStatusCode()));
    }
}
```

### 3. 병렬 처리와 데이터 조합

여러 외부 API를 동시에 호출하고 결과를 합치는 패턴은 실무에서 매우 자주 사용된다.

```java
public Mono<DashboardResponse> getDashboard(String userId) {
    // 세 가지 외부 API를 병렬로 호출
    Mono<UserProfile> userProfileMono = userClient.getProfile(userId)
        .subscribeOn(Schedulers.boundedElastic());
    
    Mono<List<Order>> recentOrdersMono = orderRepository.findRecentByUserId(userId, 5)
        .collectList();
    
    Mono<RecommendationList> recommendationsMono = recommendationClient.getRecommendations(userId)
        .onErrorReturn(RecommendationList.empty()); // 실패해도 빈 목록으로 진행
    
    // zip으로 세 결과를 하나로 조합
    return Mono.zip(userProfileMono, recentOrdersMono, recommendationsMono)
        .map(tuple -> DashboardResponse.builder()
            .profile(tuple.getT1())
            .recentOrders(tuple.getT2())
            .recommendations(tuple.getT3())
            .build());
}
```

### 4. 리액티브 WebClient 활용

`RestTemplate`의 리액티브 대안인 `WebClient` 설정과 활용 패턴이다.

```java
@Configuration
public class WebClientConfig {

    @Bean
    public WebClient inventoryWebClient(WebClient.Builder builder) {
        return builder
            .baseUrl("https://inventory-service.internal")
            .defaultHeader(HttpHeaders.CONTENT_TYPE, MediaType.APPLICATION_JSON_VALUE)
            .filter(ExchangeFilterFunction.ofRequestProcessor(request -> {
                log.debug("Request: {} {}", request.method(), request.url());
                return Mono.just(request);
            }))
            // 연결 타임아웃, 응답 타임아웃 설정
            .clientConnector(new ReactorClientHttpConnector(
                HttpClient.create()
                    .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, 3000)
                    .responseTimeout(Duration.ofSeconds(5))
            ))
            .build();
    }
}

@Component
@RequiredArgsConstructor
public class InventoryClient {

    private final WebClient inventoryWebClient;

    public Mono<StockResponse> checkStock(String productId, int quantity) {
        return inventoryWebClient.get()
            .uri("/api/stock/{productId}?quantity={quantity}", productId, quantity)
            .retrieve()
            // HTTP 4xx, 5xx 에러를 커스텀 예외로 변환
            .onStatus(HttpStatus::is4xxClientError, 
                response -> response.bodyToMono(ErrorResponse.class)
                    .map(err -> new InventoryClientException(err.getMessage())))
            .onStatus(HttpStatus::is5xxServerError, 
                response -> Mono.error(new InventoryServiceException("재고 서비스 장애")))
            .bodyToMono(StockResponse.class)
            .timeout(Duration.ofSeconds(3));
    }
}
```

### 5. R2DBC를 활용한 리액티브 데이터베이스 접근

WebFlux를 쓰면서 JPA(블로킹)를 그대로 사용하면 이벤트 루프를 블로킹시켜 성능 저하가 발생한다. R2DBC를 사용해야 진정한 논블로킹 스택이 완성된다.

```java
// Repository
public interface OrderRepository extends ReactiveCrudRepository<Order, String> {
    
    @Query("SELECT * FROM orders WHERE user_id = :userId ORDER BY created_at DESC LIMIT :limit")
    Flux<Order> findRecentByUserId(String userId, int limit);
    
    Flux<Order> findByStatusAndCreatedAtBetween(
        OrderStatus status, LocalDateTime from, LocalDateTime to);
}

// 배치 처리 예제 - Flux를 활용한 스트리밍 처리
@Service
@RequiredArgsConstructor
public class OrderBatchService {

    private final OrderRepository orderRepository;
    private final NotificationClient notificationClient;

    public Flux<String> processExpiredOrders() {
        LocalDateTime threshold = LocalDateTime.now().minusHours(24);
        
        return orderRepository.findByStatusAndCreatedAtBetween(
                OrderStatus.PENDING, LocalDateTime.now().minusDays(7), threshold)
            // 한 번에 20개씩 묶어서 처리 (배치)
            .buffer(20)
            .flatMap(batch -> Flux.fromIterable(batch)
                .flatMap(order -> orderRepository.save(order.markExpired())
                    .flatMap(saved -> notificationClient.sendExpiredNotification(saved.getUserId()))
                    .thenReturn(order.getId())
                ), 4) // 동시 실행 수 제한 (concurrency=4)
            .doOnComplete(() -> log.info("만료 주문 처리 완료"));
    }
}
```

---

## 주의사항 및 트레이드오프

### 블로킹 코드 혼용 금지

WebFlux 환경에서 가장 치명적인 실수는 이벤트 루프 스레드를 블로킹하는 코드를 혼용하는 것이다.

```java
// ❌ 절대 하면 안 되는 패턴
public Mono<Order> badExample(String orderId) {
    return Mono.fromCallable(() -> {
        // JDBC 블로킹 호출이 이벤트 루프를 점유
        return jdbcOrderRepository.findById(orderId); // 블로킹!
    });
}

// ✅ 불가피한 블로킹 코드는 반드시 별도 스케줄러로 격리
public Mono<Order> goodExample(String orderId) {
    return Mono.fromCallable(() -> jdbcOrderRepository.findById(orderId))
        .subscribeOn(Schedulers.boundedElastic()); // 블로킹 전용 스레드풀 사용
}
```

### Context 전파 문제

Spring Security의 `SecurityContext`, MDC 로깅 컨텍스트 등은 ThreadLocal 기반이라 리액티브 체인에서 자동으로 전파되지 않는다. Reactor의 `Context`를 사용해 명시적으로 처리해야 한다.

```java
// MDC 컨텍스트를 리액티브 체인에서 유지하는 패턴
public Mono<Order> findOrderWithContext(String orderId) {
    return Mono.deferContextual(ctx -> {
        String traceId = ctx.getOrDefault("traceId", "unknown");
        MDC.put("traceId", traceId);
        return orderRepository.findById(orderId);
    });
}
```

### 언제 WebFlux를 쓰지 말아야 하는가

WebFlux가 항상 정답은 아니다. 다음 상황에서는 오히려 Spring MVC가 적합할 수 있다.

| 상황 | 권장 선택 |
|------|----------|
| 팀의 리액티브 숙련도가 낮음 | Spring MVC |
| CPU 바운드 연산이 주를 이룸 | Spring MVC |
| 레거시 블로킹 라이브러리 의존 | Spring MVC |
| 수만 동시 연결, I/O 바운드 | WebFlux |
| 실시간 스트리밍, SSE | WebFlux |
| 마이크로서비스 간 API 연동이 많음 | WebFlux |

### 디버깅의 어려움

비동기 스택 트레이스는 기본적으로 추적이 매우 어렵다. 개발 환경에서는 반드시 `Hooks.onOperatorDebug()`를 활성화하거나, `ReactorDebugAgent`를 사용해야 한다.

```java
// 개발 환경에서만 활성화 (운영에서는 성능 영향)
@Profile("dev")
@Configuration
public class ReactorDebugConfig {
    @PostConstruct
    public void enableDebug() {
        Hooks.onOperatorDebug();
    }
}
```

---

## 정리

Spring WebFlux와 리액티브 프로그래밍은 높은 동시성을 요구하는 I/O 바운드 시스템에서 강력한 무기가 된다. 하지만 러닝 커브가 가파르고, 팀 전체의 패러다임 전환이 필요하다는 비용도 분명히 존재한다.

핵심 포인트를 정리하면 다음과 같다.

- **`Mono`/`Flux`** 의 cold/hot 특성과 구독 시점을 명확히 이해하라
- **블로킹 코드는 `boundedElastic` 스케줄러로 반드시 격리**하라
- 에러 처리는 `onErrorMap`, `onErrorReturn`, `retryWhen` 등 연산자를 활용하라
- 진정한 논블로킹 스택을 위해 **R2DBC + WebClient** 조합을 사용하라
- WebFlux가 만능이 아님을 인지하고, 팀과 시스템 특성에 맞게 선택하라

리액티브 패러다임은 코드를 더 복잡하게 만드는 것이 아니라, 복잡한 비동기 흐름을 **선언적이고 조합 가능한 방식으로 표현**하는 방법론이다. 이 관점으로 접근할 때 비로소 WebFlux의 진가를 발휘할 수 있다.