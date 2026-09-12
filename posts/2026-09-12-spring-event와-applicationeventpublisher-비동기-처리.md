# Spring Event와 ApplicationEventPublisher 비동기 처리

## 개요

마이크로서비스나 모놀리식 애플리케이션을 막론하고, 도메인 간 결합도를 낮추면서 특정 행위에 반응하는 사이드 이펙트를 처리해야 하는 상황은 매우 흔하다. 예를 들어 회원가입 완료 후 이메일 발송, 주문 완료 후 포인트 적립, 결제 완료 후 알림 전송 같은 케이스들이다.

이런 요구사항을 단순하게 처리하면 서비스 레이어가 점점 비대해지고, 서로 무관한 도메인 로직이 강하게 결합된다. Spring의 이벤트 메커니즘은 이 문제를 옵저버 패턴 기반으로 우아하게 해결해준다. 특히 `@Async`와 결합하면 비동기 처리까지 자연스럽게 녹여낼 수 있다.

이 글에서는 `ApplicationEventPublisher`를 활용한 이벤트 발행/구독 패턴의 핵심 개념부터, 실무에서 바로 적용할 수 있는 비동기 처리 예제, 그리고 트랜잭션 경계와 관련된 주의사항까지 상세히 다룬다.

---

## 핵심 개념

### Spring Event 구조

Spring의 이벤트 시스템은 크게 세 가지 요소로 구성된다.

- **Event**: 발행할 이벤트 객체. `ApplicationEvent`를 상속하거나, Spring 4.2 이후부터는 **POJO**로도 작성 가능하다.
- **Publisher**: `ApplicationEventPublisher`를 통해 이벤트를 발행한다. `ApplicationContext` 자체도 `ApplicationEventPublisher`를 구현한다.
- **Listener**: `@EventListener` 어노테이션 또는 `ApplicationListener<T>` 인터페이스를 구현하여 이벤트를 수신한다.

기본적으로 Spring 이벤트는 **동기(Synchronous)** 방식으로 동작한다. 즉, 이벤트를 발행한 스레드에서 리스너도 함께 실행된다. 비동기 처리를 위해서는 `@Async`를 리스너 메서드에 추가하거나, `ApplicationEventMulticaster`를 커스터마이징해야 한다.

### 동기 vs 비동기 이벤트 처리

| 구분 | 동기 | 비동기 |
|---|---|---|
| 실행 스레드 | 발행자와 동일 | 별도 스레드 풀 |
| 트랜잭션 공유 | 가능 | 불가 |
| 예외 전파 | 발행자까지 전파 | 전파되지 않음 |
| 처리 순서 | 보장 | 보장되지 않음 |
| 사용 시나리오 | 트랜잭션 내 처리 필요 | 이메일, 알림, 로그 등 |

---

## 실전 예제

### 1. 이벤트 객체 정의

Spring 4.2 이후에는 `ApplicationEvent`를 상속하지 않아도 된다. 불변 객체로 설계하는 것이 좋다.

```java
// 주문 완료 이벤트
public class OrderCompletedEvent {

    private final Long orderId;
    private final Long userId;
    private final BigDecimal totalAmount;
    private final LocalDateTime occurredAt;

    public OrderCompletedEvent(Long orderId, Long userId, BigDecimal totalAmount) {
        this.orderId = orderId;
        this.userId = userId;
        this.totalAmount = totalAmount;
        this.occurredAt = LocalDateTime.now();
    }

    public Long getOrderId() { return orderId; }
    public Long getUserId() { return userId; }
    public BigDecimal getTotalAmount() { return totalAmount; }
    public LocalDateTime getOccurredAt() { return occurredAt; }
}
```

### 2. 이벤트 발행 (Publisher)

`ApplicationEventPublisher`를 주입받아 이벤트를 발행한다. 서비스 레이어의 핵심 비즈니스 로직이 완료된 후 이벤트를 발행하는 것이 일반적인 패턴이다.

```java
@Service
@RequiredArgsConstructor
public class OrderService {

    private final OrderRepository orderRepository;
    private final ApplicationEventPublisher eventPublisher;

    @Transactional
    public Order completeOrder(Long orderId) {
        Order order = orderRepository.findById(orderId)
                .orElseThrow(() -> new OrderNotFoundException(orderId));

        order.complete(); // 도메인 로직 처리
        orderRepository.save(order);

        // 이벤트 발행 - 트랜잭션이 커밋되기 전 시점
        eventPublisher.publishEvent(
            new OrderCompletedEvent(order.getId(), order.getUserId(), order.getTotalAmount())
        );

        return order;
    }
}
```

### 3. 비동기 이벤트 리스너 구현

`@Async`를 활성화하려면 설정 클래스에 `@EnableAsync`를 추가해야 한다. 스레드 풀도 함께 커스터마이징하는 것을 권장한다.

```java
@Configuration
@EnableAsync
public class AsyncConfig {

    @Bean(name = "eventTaskExecutor")
    public Executor eventTaskExecutor() {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(5);
        executor.setMaxPoolSize(20);
        executor.setQueueCapacity(100);
        executor.setThreadNamePrefix("event-async-");
        executor.setRejectedExecutionHandler(new ThreadPoolExecutor.CallerRunsPolicy());
        executor.initialize();
        return executor;
    }
}
```

이제 리스너를 구현한다. 각 리스너는 단일 책임을 갖도록 분리하는 것이 유지보수 측면에서 유리하다.

```java
// 포인트 적립 리스너
@Component
@Slf4j
@RequiredArgsConstructor
public class PointAccumulationEventListener {

    private final PointService pointService;

    @Async("eventTaskExecutor")
    @EventListener
    public void handleOrderCompleted(OrderCompletedEvent event) {
        log.info("포인트 적립 처리 시작 - orderId: {}, userId: {}",
                event.getOrderId(), event.getUserId());
        try {
            pointService.accumulate(event.getUserId(), event.getTotalAmount());
        } catch (Exception e) {
            log.error("포인트 적립 실패 - orderId: {}", event.getOrderId(), e);
            // 별도의 보상 트랜잭션 또는 재시도 처리
        }
    }
}

// 이메일 알림 리스너
@Component
@Slf4j
@RequiredArgsConstructor
public class OrderNotificationEventListener {

    private final EmailService emailService;

    @Async("eventTaskExecutor")
    @EventListener
    public void handleOrderCompleted(OrderCompletedEvent event) {
        log.info("주문 완료 알림 발송 시작 - orderId: {}", event.getOrderId());
        emailService.sendOrderCompletionEmail(event.getUserId(), event.getOrderId());
    }
}
```

### 4. 트랜잭션 커밋 후 이벤트 발행 - `@TransactionalEventListener`

실무에서 가장 중요한 패턴 중 하나다. 기본 `@EventListener`는 트랜잭션 커밋 전에 실행되기 때문에, DB 롤백이 발생해도 이미 이메일이 발송되는 문제가 생길 수 있다. `@TransactionalEventListener`를 사용하면 트랜잭션의 특정 단계에 바인딩할 수 있다.

```java
@Component
@Slf4j
@RequiredArgsConstructor
public class OrderCompletedTransactionalListener {

    private final ExternalNotificationClient notificationClient;

    // 트랜잭션 커밋 성공 후에만 실행
    @Async("eventTaskExecutor")
    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
    public void handleAfterCommit(OrderCompletedEvent event) {
        log.info("트랜잭션 커밋 후 외부 알림 전송 - orderId: {}", event.getOrderId());
        notificationClient.sendPushNotification(event.getUserId(), event.getOrderId());
    }

    // 트랜잭션 롤백 시 보상 처리
    @TransactionalEventListener(phase = TransactionPhase.AFTER_ROLLBACK)
    public void handleAfterRollback(OrderCompletedEvent event) {
        log.warn("트랜잭션 롤백 감지 - orderId: {}", event.getOrderId());
        // 보상 로직 또는 알림
    }
}
```

`TransactionPhase` 옵션은 다음과 같다.

| Phase | 설명 |
|---|---|
| `BEFORE_COMMIT` | 커밋 직전 (트랜잭션 내부) |
| `AFTER_COMMIT` | 커밋 성공 후 (기본값) |
| `AFTER_ROLLBACK` | 롤백 후 |
| `AFTER_COMPLETION` | 커밋 또는 롤백 상관없이 완료 후 |

### 5. 이벤트 우선순위 지정

여러 리스너가 동일한 이벤트를 처리할 때 실행 순서가 중요한 경우 `@Order`를 사용한다. 단, 비동기 처리 시에는 순서가 보장되지 않으므로 동기 처리에서만 유효하다.

```java
@Component
public class FirstListener {

    @EventListener
    @Order(1)
    public void handle(OrderCompletedEvent event) {
        // 가장 먼저 실행
    }
}

@Component
public class SecondListener {

    @EventListener
    @Order(2)
    public void handle(OrderCompletedEvent event) {
        // 두 번째로 실행
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. 비동기 리스너에서 트랜잭션이 전파되지 않는다

`@Async`를 사용하면 별도 스레드에서 실행되므로, 발행자의 트랜잭션 컨텍스트가 전파되지 않는다. 리스너 내에서 DB 작업이 필요하다면 `@Transactional`을 명시적으로 선언해야 한다.

```java
@Async("eventTaskExecutor")
@EventListener
@Transactional // 별도 트랜잭션 시작
public void handle(OrderCompletedEvent event) {
    // 이 메서드는 새로운 트랜잭션으로 실행됨
    someRepository.save(...);
}
```

### 2. `@TransactionalEventListener` + `@Async` 조합의 함정

`@TransactionalEventListener`에 `@Async`를 함께 사용할 때, `AFTER_COMMIT` 페이즈에서 비동기로 실행되는 리스너 내부에서 새로운 트랜잭션을 시작하면 **이미 커밋된 트랜잭션의 데이터**를 읽게 된다. 이 경우 `@Transactional(propagation = Propagation.REQUIRES_NEW)`를 사용해야 한다.

```java
@Async("eventTaskExecutor")
@TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
@Transactional(propagation = Propagation.REQUIRES_NEW) // 반드시 명시
public void handleAfterCommit(OrderCompletedEvent event) {
    // 새로운 트랜잭션으로 데이터 처리
}
```

### 3. 예외 처리와 이벤트 유실

비동기 리스너에서 발생한 예외는 발행자에게 전파되지 않는다. 따라서 리스너 내부에서 반드시 예외를 핸들링해야 한다. 이벤트 유실이 허용되지 않는 중요한 사이드 이펙트라면 메시지 큐(Kafka, RabbitMQ 등)를 고려해야 한다.

```java
@Async("eventTaskExecutor")
@EventListener
public void handle(OrderCompletedEvent event) {
    try {
        // 처리 로직
    } catch (Exception e) {
        log.error("이벤트 처리 실패", e);
        // Dead Letter Queue 저장 또는 재시도 스케줄링
        retryScheduler.schedule(event);
    }
}
```

### 4. 이벤트 기반 아키텍처의 한계

Spring 내부 이벤트는 **애플리케이션 프로세스 내**에서만 동작한다. 서버 재시작 시 처리 중인 이벤트는 유실되고, 멀티 인스턴스 환경에서는 다른 인스턴스에 이벤트가 전달되지 않는다. 이런 경우에는 외부 메시지 브로커와 연동하거나, Transactional Outbox 패턴을 도입하는 것이 적절하다.

### 5. 순환 이벤트 발행 주의

리스너 내에서 또 다른 이벤트를 발행하는 경우, 잘못 설계하면 이벤트 루프가 발생할 수 있다. 이벤트 흐름을 명확하게 문서화하고, 리스너 내에서의 이벤트 발행은 최소화해야 한다.

---

## 정리

Spring Event와 `ApplicationEventPublisher`는 도메인 간 결합도를 낮추고 단일 책임 원칙을 지키는 데 매우 효과적인 도구다. 핵심 포인트를 정리하면 다음과 같다.

- **기본 이벤트**는 동기 처리이며, `@Async`를 통해 비동기로 전환할 수 있다.
- **`@TransactionalEventListener`** 는 트랜잭션 커밋 성공 이후에만 처리해야 하는 사이드 이펙트에 필수적이다.
- 비동기 리스너는 트랜잭션 컨텍스트를 공유하지 않으므로 **명시적 `@Transactional` 선언**이 필요하다.
- 이벤트 유실이 허용되지 않는 중요한 처리는 **메시지 큐 또는 Outbox 패턴**으로 보완해야 한다.
- 스레드 풀은 반드시 **명시적으로 커스터마이징**하여 서비스 목적에 맞게 설정해야 한다.

Spring Event는 빠르게 적용할 수 있는 경량 이벤트 메커니즘이지만, 복잡한 분산 시스템에서는 외부 메시지 브로커와 함께 사용하는 전략이 더 견고한 아키텍처를 만든다. 팀의 운영 복잡도와 요구사항에 맞게 적절한 수준의 솔루션을 선택하는 것이 중요하다.
