# Hexagonal Architecture (포트와 어댑터) 패턴 실전

## 개요

마이크로서비스 전환이 보편화되고, 도메인 복잡도가 높아지면서 **Hexagonal Architecture(육각형 아키텍처)**, 일명 **포트와 어댑터(Ports and Adapters)** 패턴이 다시 주목받고 있다. 2005년 Alistair Cockburn이 제안한 이 패턴은 애플리케이션의 핵심 비즈니스 로직을 외부 시스템(DB, 메시지 큐, HTTP, UI 등)으로부터 완전히 격리하는 것을 목표로 한다.

전통적인 레이어드 아키텍처(Controller → Service → Repository)는 설계가 단순하지만, 시스템이 커질수록 상위 레이어가 하위 레이어의 구현에 강하게 결합되는 문제가 생긴다. 예를 들어 JPA를 Mybatis로 교체하거나, REST API를 gRPC로 전환하려 할 때 도메인 코드까지 수정해야 하는 상황이 벌어진다.

Hexagonal Architecture는 이러한 결합 문제를 **포트(인터페이스)**와 **어댑터(구현체)**의 분리로 해결한다.

---

## 핵심 개념

### 구조 개요

```
[ 외부 세계 ]
  ├── Driving Side (Primary / Inbound)
  │     └── REST Controller, CLI, gRPC Handler
  │           └── [Primary Adapter]
  │                 └── [Inbound Port (Interface)]
  │                       └── [ Application Core / Domain ]
  │                 ┌── [Outbound Port (Interface)]
  │           ┌── [Secondary Adapter]
  ├── Driven Side (Secondary / Outbound)
        └── JPA Repository, Kafka Producer, External API Client
```

### 1. 포트 (Port)

포트는 애플리케이션 경계에 위치한 **인터페이스**다. 두 가지 방향이 있다.

- **Inbound Port (Driving Port)**: 외부에서 애플리케이션 코어를 호출할 때 사용하는 인터페이스. Use Case 인터페이스가 대표적이다.
- **Outbound Port (Driven Port)**: 애플리케이션 코어가 외부 시스템을 호출할 때 사용하는 인터페이스. Repository, MessagePublisher 등이 해당된다.

### 2. 어댑터 (Adapter)

어댑터는 포트의 **구현체**로, 특정 기술에 종속된 코드를 담는다.

- **Primary Adapter (Inbound Adapter)**: REST Controller, GraphQL Resolver, Kafka Consumer 등
- **Secondary Adapter (Outbound Adapter)**: JPA Repository 구현체, Redis 클라이언트, 외부 API 호출 클라이언트 등

### 3. 의존성 방향

핵심 원칙은 **모든 의존성이 도메인 코어를 향해야 한다**는 것이다. 도메인 코어는 외부 기술을 전혀 알지 못한다.

---

## 실전 예제: 주문 처리 서비스 (Spring Boot + Java)

### 패키지 구조

```
com.example.order
├── domain
│   ├── model
│   │   └── Order.java
│   └── service
│       └── OrderService.java
├── application
│   ├── port
│   │   ├── in
│   │   │   └── PlaceOrderUseCase.java
│   │   └── out
│   │       ├── OrderRepository.java
│   │       └── PaymentPort.java
│   └── service
│       └── PlaceOrderService.java
├── adapter
│   ├── in
│   │   └── web
│   │       └── OrderController.java
│   └── out
│       ├── persistence
│       │   ├── OrderJpaRepository.java
│       │   └── OrderPersistenceAdapter.java
│       └── payment
│           └── PaymentApiAdapter.java
```

### 1. 도메인 모델

```java
// domain/model/Order.java
@Getter
public class Order {
    private final OrderId id;
    private final CustomerId customerId;
    private final List<OrderItem> items;
    private OrderStatus status;

    public static Order create(CustomerId customerId, List<OrderItem> items) {
        if (items == null || items.isEmpty()) {
            throw new IllegalArgumentException("주문 항목은 최소 1개 이상이어야 합니다.");
        }
        return new Order(OrderId.generate(), customerId, items, OrderStatus.PENDING);
    }

    public Money calculateTotalPrice() {
        return items.stream()
            .map(OrderItem::getSubtotal)
            .reduce(Money.ZERO, Money::add);
    }

    public void confirm() {
        if (this.status != OrderStatus.PENDING) {
            throw new IllegalStateException("대기 상태의 주문만 확정할 수 있습니다.");
        }
        this.status = OrderStatus.CONFIRMED;
    }
}
```

비즈니스 규칙이 도메인 모델 안에 응집되어 있다. JPA 어노테이션도, Spring 의존성도 없다.

### 2. Inbound Port (Use Case 인터페이스)

```java
// application/port/in/PlaceOrderUseCase.java
public interface PlaceOrderUseCase {
    OrderResult placeOrder(PlaceOrderCommand command);

    record PlaceOrderCommand(
        String customerId,
        List<OrderItemDto> items
    ) {
        public PlaceOrderCommand {
            Objects.requireNonNull(customerId, "customerId는 필수입니다.");
            Objects.requireNonNull(items, "items는 필수입니다.");
        }
    }
}
```

### 3. Outbound Port (Repository, 외부 시스템 인터페이스)

```java
// application/port/out/OrderRepository.java
public interface OrderRepository {
    Order save(Order order);
    Optional<Order> findById(OrderId orderId);
}

// application/port/out/PaymentPort.java
public interface PaymentPort {
    PaymentResult requestPayment(OrderId orderId, Money amount, CustomerId customerId);
}
```

도메인 코어는 `PaymentPort`라는 인터페이스만 알 뿐, 실제로 PG사 API를 호출하는지 카카오페이인지 알지 못한다.

### 4. Application Service (Use Case 구현체)

```java
// application/service/PlaceOrderService.java
@Service
@RequiredArgsConstructor
@Transactional
public class PlaceOrderService implements PlaceOrderUseCase {

    private final OrderRepository orderRepository;
    private final PaymentPort paymentPort;

    @Override
    public OrderResult placeOrder(PlaceOrderCommand command) {
        // 도메인 객체 생성
        List<OrderItem> items = command.items().stream()
            .map(dto -> new OrderItem(dto.productId(), dto.quantity(), dto.price()))
            .toList();

        Order order = Order.create(
            new CustomerId(command.customerId()),
            items
        );

        // 결제 요청 (Outbound Port 호출)
        PaymentResult paymentResult = paymentPort.requestPayment(
            order.getId(),
            order.calculateTotalPrice(),
            order.getCustomerId()
        );

        if (paymentResult.isSuccess()) {
            order.confirm();
        }

        // 저장 (Outbound Port 호출)
        Order savedOrder = orderRepository.save(order);
        return OrderResult.from(savedOrder);
    }
}
```

### 5. Primary Adapter: REST Controller

```java
// adapter/in/web/OrderController.java
@RestController
@RequestMapping("/api/v1/orders")
@RequiredArgsConstructor
public class OrderController {

    private final PlaceOrderUseCase placeOrderUseCase;

    @PostMapping
    public ResponseEntity<OrderResponse> placeOrder(
        @RequestBody @Valid PlaceOrderRequest request
    ) {
        PlaceOrderUseCase.PlaceOrderCommand command = new PlaceOrderUseCase.PlaceOrderCommand(
            request.customerId(),
            request.items().stream()
                .map(i -> new OrderItemDto(i.productId(), i.quantity(), i.price()))
                .toList()
        );

        OrderResult result = placeOrderUseCase.placeOrder(command);
        return ResponseEntity.status(HttpStatus.CREATED)
            .body(OrderResponse.from(result));
    }
}
```

Controller는 `PlaceOrderUseCase` 인터페이스에만 의존한다. 비즈니스 로직이 변경되어도 Controller는 수정할 필요가 없다.

### 6. Secondary Adapter: JPA 영속성 어댑터

```java
// adapter/out/persistence/OrderPersistenceAdapter.java
@Component
@RequiredArgsConstructor
public class OrderPersistenceAdapter implements OrderRepository {

    private final OrderJpaRepository jpaRepository;
    private final OrderMapper orderMapper;

    @Override
    public Order save(Order order) {
        OrderEntity entity = orderMapper.toEntity(order);
        OrderEntity saved = jpaRepository.save(entity);
        return orderMapper.toDomain(saved);
    }

    @Override
    public Optional<Order> findById(OrderId orderId) {
        return jpaRepository.findById(orderId.getValue())
            .map(orderMapper::toDomain);
    }
}
```

JPA Entity와 도메인 모델 사이에 **Mapper**를 두어 두 모델이 서로 오염되지 않도록 한다. 이 점이 레이어드 아키텍처와의 큰 차이 중 하나다.

### 7. Secondary Adapter: 외부 결제 API

```java
// adapter/out/payment/PaymentApiAdapter.java
@Component
@RequiredArgsConstructor
public class PaymentApiAdapter implements PaymentPort {

    private final PaymentApiClient apiClient;

    @Override
    public PaymentResult requestPayment(OrderId orderId, Money amount, CustomerId customerId) {
        PaymentApiRequest request = PaymentApiRequest.builder()
            .orderId(orderId.getValue())
            .amount(amount.getValue())
            .currency(amount.getCurrency())
            .userId(customerId.getValue())
            .build();

        try {
            PaymentApiResponse response = apiClient.pay(request);
            return response.isApproved()
                ? PaymentResult.success(response.getTransactionId())
                : PaymentResult.failure(response.getErrorMessage());
        } catch (PaymentApiException e) {
            return PaymentResult.failure("결제 서버 오류: " + e.getMessage());
        }
    }
}
```

---

## 테스트 용이성

Hexagonal Architecture의 가장 큰 이점 중 하나는 **테스트 용이성**이다.

```java
// PlaceOrderServiceTest.java
class PlaceOrderServiceTest {

    private final OrderRepository orderRepository = mock(OrderRepository.class);
    private final PaymentPort paymentPort = mock(PaymentPort.class);
    private final PlaceOrderService sut = new PlaceOrderService(orderRepository, paymentPort);

    @Test
    void 결제_성공_시_주문이_확정된다() {
        // given
        given(paymentPort.requestPayment(any(), any(), any()))
            .willReturn(PaymentResult.success("TXN-001"));
        given(orderRepository.save(any()))
            .willAnswer(invocation -> invocation.getArgument(0));

        var command = new PlaceOrderUseCase.PlaceOrderCommand(
            "CUSTOMER-1",
            List.of(new OrderItemDto("PROD-1", 2, Money.of(5000)))
        );

        // when
        OrderResult result = sut.placeOrder(command);

        // then
        assertThat(result.status()).isEqualTo(OrderStatus.CONFIRMED);
        verify(orderRepository).save(argThat(order ->
            order.getStatus() == OrderStatus.CONFIRMED
        ));
    }
}
```

Spring Context 없이, DB 없이, 외부 API 없이 순수 Java로 핵심 비즈니스 로직을 검증할 수 있다.

---

## 주의사항 및 트레이드오프

### ⚠️ 복잡도 증가

단순한 CRUD 서비스에 Hexagonal Architecture를 적용하면 파일 수와 클래스 수가 급격히 늘어난다. **도메인 복잡도가 낮은 서비스에는 과설계(over-engineering)가 될 수 있다.**

### ⚠️ 매핑 비용

도메인 모델, JPA Entity, DTO를 별도로 관리하면 매핑 코드가 늘어난다. MapStruct 같은 매핑 라이브러리를 적극 활용하거나, 팀 컨벤션으로 어떤 레이어에서 어떤 모델을 사용할지 명확히 정해야 한다.

### ⚠️ 팀 학습 곡선

레이어드 아키텍처에 익숙한 팀원들에게는 초반에 혼란을 줄 수 있다. 포트와 어댑터의 개념, 의존성 방향에 대한 충분한 팀 교육과 코드 리뷰 문화가 병행되어야 한다.

### ✅ 언제 적용하면 좋은가?

| 상황 | 적합 여부 |
|---|---|
| 도메인 로직이 복잡하고 지속적으로 변화하는 서비스 | ✅ 적합 |
| 여러 외부 시스템(DB, MQ, API)과 연동하는 서비스 | ✅ 적합 |
| 장기 운영 예정이며 테스트 커버리지가 중요한 서비스 | ✅ 적합 |
| 단순 CRUD 관리자 페이지 백엔드 | ❌ 과설계 가능성 |
| 프로토타입 또는 단기 프로젝트 | ❌ 과설계 가능성 |

---

## 정리

Hexagonal Architecture는 **"비즈니스 로직을 기술로부터 보호한다"** 는 단 하나의 철학에서 출발한다. 포트라는 계약을 통해 도메인 코어와 외부 세계를 분리하고, 어댑터를 교체함으로써 기술 변화에 유연하게 대응할 수 있다.

실제 프로젝트에서는 처음부터 완벽한 Hexagonal Architecture를 구현하려 하기보다, **핵심 Use Case를 인터페이스로 정의하고 도메인 모델에서 JPA 어노테이션을 제거하는 것**부터 시작하는 것을 추천한다. 점진적인 적용이 팀 수용성과 코드 품질 모두를 높이는 가장 현실적인 방법이다.

좋은 아키텍처는 변화에 열려 있어야 한다. Hexagonal Architecture는 그 철학을 코드 구조로 구현한 실용적인 도구다.