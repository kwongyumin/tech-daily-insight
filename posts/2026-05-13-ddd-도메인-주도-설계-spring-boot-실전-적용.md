# DDD 도메인 주도 설계 Spring Boot 실전 적용

## 개요

마이크로서비스 아키텍처가 대세가 되면서 DDD(Domain-Driven Design)가 다시 주목받고 있다. Eric Evans가 2003년에 제시한 이 개념은 단순한 설계 방법론을 넘어, 복잡한 비즈니스 도메인을 코드로 표현하는 철학에 가깝다.

하지만 많은 팀이 DDD를 도입하려다 좌절한다. "어디서부터 시작해야 하지?", "Entity와 Aggregate의 차이가 뭐야?", "Repository를 어떻게 나누지?" 같은 질문이 쏟아진다. 이 포스팅은 Spring Boot 실무 환경에서 DDD의 핵심 개념을 어떻게 코드로 옮기는지 구체적인 예제와 함께 설명한다.

---

## 핵심 개념

### 전략적 설계 vs 전술적 설계

DDD는 크게 두 층위로 나뉜다.

- **전략적 설계**: Bounded Context, Ubiquitous Language, Context Map
- **전술적 설계**: Entity, Value Object, Aggregate, Repository, Domain Service, Domain Event

실무에서 많은 팀이 전술적 설계(코드 패턴)만 도입하고 전략적 설계를 생략하는 실수를 한다. Bounded Context 없이 Aggregate를 잘라봤자 모듈 간 의존성이 엉켜버린다.

### Bounded Context와 패키지 구조

주문 시스템을 예로 들면, `Order`, `Catalog`, `Delivery`는 각각 독립된 Bounded Context다. Spring Boot에서는 이를 멀티 모듈이나 패키지 단위로 분리한다.

```
com.example
├── order
│   ├── domain
│   │   ├── model         # Entity, Value Object, Aggregate
│   │   ├── repository    # Repository 인터페이스
│   │   └── service       # Domain Service
│   ├── application       # Use Case, Application Service
│   ├── infrastructure    # JPA, 외부 API 구현체
│   └── interfaces        # REST Controller, DTO
├── catalog
│   └── ...
└── delivery
    └── ...
```

이 구조는 헥사고날 아키텍처(Ports & Adapters)와 자연스럽게 결합된다.

### Entity vs Value Object

| 구분 | Entity | Value Object |
|------|--------|--------------|
| 식별자 | 있음 (ID) | 없음 |
| 동등성 | ID 기반 | 속성값 기반 |
| 가변성 | 가변 | 불변(Immutable) |
| 예시 | Order, Customer | Money, Address |

---

## 실전 예제: 주문 도메인 구현

### Value Object 구현

Money는 대표적인 Value Object다. 불변으로 설계하고, 비즈니스 로직을 내부에 캡슐화한다.

```java
@Embeddable
public class Money {
    private final BigDecimal amount;
    private final String currency;

    protected Money() {} // JPA 기본 생성자

    public Money(BigDecimal amount, String currency) {
        if (amount.compareTo(BigDecimal.ZERO) < 0) {
            throw new IllegalArgumentException("금액은 0 이상이어야 합니다.");
        }
        this.amount = amount;
        this.currency = currency;
    }

    public Money add(Money other) {
        if (!this.currency.equals(other.currency)) {
            throw new IllegalArgumentException("통화 단위가 다릅니다.");
        }
        return new Money(this.amount.add(other.amount), this.currency);
    }

    public Money multiply(int quantity) {
        return new Money(this.amount.multiply(BigDecimal.valueOf(quantity)), this.currency);
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (!(o instanceof Money)) return false;
        Money money = (Money) o;
        return Objects.equals(amount, money.amount) &&
               Objects.equals(currency, money.currency);
    }

    @Override
    public int hashCode() {
        return Objects.hash(amount, currency);
    }
}
```

### Aggregate Root 구현

Order가 Aggregate Root이고, OrderItem은 그 안에 포함된다. **외부에서 OrderItem을 직접 조작하지 못하도록** 캡슐화하는 것이 핵심이다.

```java
@Entity
@Table(name = "orders")
public class Order {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String customerId;

    @Enumerated(EnumType.STRING)
    private OrderStatus status;

    @OneToMany(cascade = CascadeType.ALL, orphanRemoval = true, fetch = FetchType.LAZY)
    @JoinColumn(name = "order_id")
    private List<OrderItem> orderItems = new ArrayList<>();

    @Embedded
    private Money totalAmount;

    protected Order() {}

    public static Order create(String customerId) {
        Order order = new Order();
        order.customerId = customerId;
        order.status = OrderStatus.PENDING;
        order.totalAmount = new Money(BigDecimal.ZERO, "KRW");
        return order;
    }

    public void addItem(String productId, int quantity, Money unitPrice) {
        validateOrderStatus();
        OrderItem item = new OrderItem(productId, quantity, unitPrice);
        orderItems.add(item);
        recalculateTotalAmount();
    }

    public void removeItem(String productId) {
        validateOrderStatus();
        orderItems.removeIf(item -> item.getProductId().equals(productId));
        recalculateTotalAmount();
    }

    public void place() {
        if (orderItems.isEmpty()) {
            throw new IllegalStateException("주문 항목이 비어 있습니다.");
        }
        this.status = OrderStatus.PLACED;
        // 도메인 이벤트 발행 (후술)
    }

    public void cancel() {
        if (this.status != OrderStatus.PLACED) {
            throw new IllegalStateException("접수된 주문만 취소 가능합니다.");
        }
        this.status = OrderStatus.CANCELLED;
    }

    private void validateOrderStatus() {
        if (this.status != OrderStatus.PENDING) {
            throw new IllegalStateException("대기 중인 주문만 수정 가능합니다.");
        }
    }

    private void recalculateTotalAmount() {
        this.totalAmount = orderItems.stream()
            .map(OrderItem::getSubtotal)
            .reduce(new Money(BigDecimal.ZERO, "KRW"), Money::add);
    }

    // Getter만 공개 (Setter 없음)
    public Long getId() { return id; }
    public OrderStatus getStatus() { return status; }
    public Money getTotalAmount() { return totalAmount; }
    public List<OrderItem> getOrderItems() {
        return Collections.unmodifiableList(orderItems);
    }
}
```

### Repository 인터페이스 (도메인 계층)

Repository 인터페이스는 도메인 계층에 두고, 구현체는 인프라 계층에 둔다.

```java
// domain/repository/OrderRepository.java
public interface OrderRepository {
    Order save(Order order);
    Optional<Order> findById(Long id);
    List<Order> findByCustomerId(String customerId);
    void delete(Order order);
}

// infrastructure/persistence/JpaOrderRepository.java
@Repository
public class JpaOrderRepository implements OrderRepository {

    private final OrderJpaRepository jpaRepository;

    public JpaOrderRepository(OrderJpaRepository jpaRepository) {
        this.jpaRepository = jpaRepository;
    }

    @Override
    public Order save(Order order) {
        return jpaRepository.save(order);
    }

    @Override
    public Optional<Order> findById(Long id) {
        return jpaRepository.findById(id);
    }

    @Override
    public List<Order> findByCustomerId(String customerId) {
        return jpaRepository.findByCustomerId(customerId);
    }

    @Override
    public void delete(Order order) {
        jpaRepository.delete(order);
    }
}
```

### Application Service와 Domain Service 구분

비즈니스 로직이 단일 Aggregate 안에 있으면 **도메인 모델**에, 여러 Aggregate나 외부 시스템이 협력해야 하면 **도메인 서비스**에 둔다. 유스케이스 오케스트레이션은 **애플리케이션 서비스**가 담당한다.

```java
// application/OrderApplicationService.java
@Service
@Transactional
public class OrderApplicationService {

    private final OrderRepository orderRepository;
    private final ProductCatalogService productCatalogService; // 외부 Bounded Context
    private final ApplicationEventPublisher eventPublisher;

    public OrderApplicationService(OrderRepository orderRepository,
                                   ProductCatalogService productCatalogService,
                                   ApplicationEventPublisher eventPublisher) {
        this.orderRepository = orderRepository;
        this.productCatalogService = productCatalogService;
        this.eventPublisher = eventPublisher;
    }

    public Long createOrder(String customerId) {
        Order order = Order.create(customerId);
        return orderRepository.save(order).getId();
    }

    public void addItemToOrder(Long orderId, String productId, int quantity) {
        Order order = orderRepository.findById(orderId)
            .orElseThrow(() -> new EntityNotFoundException("주문을 찾을 수 없습니다: " + orderId));

        // 다른 Bounded Context에서 가격 조회
        Money unitPrice = productCatalogService.getPrice(productId);
        order.addItem(productId, quantity, unitPrice);

        orderRepository.save(order);
    }

    public void placeOrder(Long orderId) {
        Order order = orderRepository.findById(orderId)
            .orElseThrow(() -> new EntityNotFoundException("주문을 찾을 수 없습니다: " + orderId));

        order.place();
        orderRepository.save(order);

        // 도메인 이벤트 발행
        eventPublisher.publishEvent(new OrderPlacedEvent(order.getId(), order.getTotalAmount()));
    }
}
```

### Domain Event 처리

주문 완료 후 알림, 재고 차감 같은 사이드 이펙트는 Domain Event로 분리하면 결합도를 낮출 수 있다.

```java
// domain/event/OrderPlacedEvent.java
public class OrderPlacedEvent {
    private final Long orderId;
    private final Money totalAmount;
    private final LocalDateTime occurredAt;

    public OrderPlacedEvent(Long orderId, Money totalAmount) {
        this.orderId = orderId;
        this.totalAmount = totalAmount;
        this.occurredAt = LocalDateTime.now();
    }
    // Getters...
}

// application/event/OrderEventHandler.java
@Component
public class OrderEventHandler {

    private final NotificationService notificationService;
    private final InventoryService inventoryService;

    @EventListener
    @Async
    public void handleOrderPlaced(OrderPlacedEvent event) {
        notificationService.sendOrderConfirmation(event.getOrderId());
        inventoryService.decreaseStock(event.getOrderId());
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. Aggregate 경계를 너무 크게 잡지 마라

초기 설계 시 흔히 하는 실수는 Aggregate 안에 너무 많은 것을 넣는 것이다. Order 안에 Customer 전체 정보를 넣으면 트랜잭션 충돌과 성능 문제가 생긴다. **ID 참조**를 사용해 다른 Aggregate를 느슨하게 연결하라.

```java
// 나쁜 예
@ManyToOne
private Customer customer; // Aggregate 경계 침범

// 좋은 예
private String customerId; // ID로만 참조
```

### 2. CQRS와의 병행 적용

DDD를 적용하면 도메인 모델이 복잡해져 복잡한 조회 쿼리를 처리하기 어렵다. 이때 **CQRS(Command Query Responsibility Segregation)** 를 함께 도입하면 효과적이다. 조회(Query)는 JPA Entity가 아닌 DTO를 직접 반환하는 전용 쿼리 모델을 사용한다.

```java
// 조회 전용 - 도메인 모델 거치지 않음
@Repository
public class OrderQueryRepository {
    private final JPAQueryFactory queryFactory;

    public List<OrderSummaryDto> findOrderSummaries(String customerId) {
        return queryFactory
            .select(Projections.constructor(OrderSummaryDto.class,
                order.id, order.status, order.totalAmount.amount))
            .from(order)
            .where(order.customerId.eq(customerId))
            .fetch();
    }
}
```

### 3. Anemic Domain Model 경계하기

빈혈 도메인 모델(Anemic Domain Model)은 DDD의 가장 흔한 안티패턴이다. 도메인 객체가 getter/setter만 있고 비즈니스 로직이 전부 Service에 있다면 사실상 트랜잭션 스크립트와 다를 바 없다.

### 4. 팀 전체의 Ubiquitous Language 합의 필요

코드 변수명, 메서드명이 비즈니스 언어와 일치해야 한다. `status = 2` 대신 `status = OrderStatus.PLACED`처럼. 이를 위해 도메인 전문가와의 이벤트 스토밍(Event Storming) 세션을 권장한다.

### 5. DDD가 항상 정답은 아니다

CRUD 중심의 단순한 애플리케이션에 DDD를 적용하면 오히려 복잡도만 높아진다. 도메인의 복잡성이 충분히 높고, 장기적으로 유지보수가 필요한 시스템에 도입할 때 진가를 발휘한다.

---

## 정리

DDD를 Spring Boot에 실전 적용할 때 핵심은 다음과 같다.

| 레이어 | 역할 | 주요 구성요소 |
|--------|------|--------------|
| Domain | 비즈니스 핵심 규칙 | Entity, Value Object, Aggregate, Domain Event |
| Application | 유스케이스 오케스트레이션 | Application Service |
| Infrastructure | 기술적 구현 | JPA Repository, 외부 API 어댑터 |
| Interfaces | 외부와의 통신 | REST Controller, DTO |

DDD는 단순한 패키지 구조나 코딩 규칙이 아니다. **도메인 전문가와 개발자가 같은 언어를 쓰고, 비즈니스 핵심 로직이 코드에 명확히 드러나도록** 하는 설계 철학이다. 처음에는 작은 Bounded Context 하나에 적용하고, 팀이 개념에 익숙해지면 점진적으로 확장하는 전략이 실패 확률을 줄인다.

코드를 보면 비즈니스가 보이고, 비즈니스를 보면 코드가 보이는 시스템. 그것이 DDD가 지향하는 목표다.