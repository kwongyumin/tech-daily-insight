# Spring Boot 트랜잭션 전파 옵션 완전 정리

## 개요

트랜잭션 전파(Transaction Propagation)는 Spring의 선언적 트랜잭션 관리에서 가장 중요하면서도 개발자들이 자주 실수하는 영역 중 하나다. 단순히 `@Transactional`을 붙이는 것에서 그치지 않고, **"호출된 메서드가 기존 트랜잭션에 어떻게 참여할 것인가"** 를 세밀하게 제어하는 것이 실무에서의 핵심이다.

잘못된 전파 옵션 선택은 데이터 정합성 문제, 예상치 못한 롤백, 성능 저하로 이어질 수 있다. 이 글에서는 Spring이 제공하는 7가지 전파 옵션 전부를 실전 예제와 함께 정리한다.

---

## 핵심 개념

### 트랜잭션 전파란?

Spring의 `@Transactional`은 `propagation` 속성을 통해 트랜잭션 경계를 어떻게 설정할지 결정한다. 즉, **이미 트랜잭션이 존재하는 상황에서 새 메서드가 호출될 때 어떻게 동작할 것인가**를 정의한다.

```java
@Transactional(propagation = Propagation.REQUIRED)
public void someMethod() {
    // ...
}
```

Spring은 `PlatformTransactionManager`를 통해 트랜잭션을 관리하며, 내부적으로 `TransactionSynchronizationManager`가 현재 스레드에 바인딩된 트랜잭션 컨텍스트를 유지한다.

### 7가지 전파 옵션 한눈에 보기

| 옵션 | 기존 트랜잭션 있음 | 기존 트랜잭션 없음 |
|------|-------------------|-------------------|
| `REQUIRED` | 기존 트랜잭션 참여 | 새 트랜잭션 생성 |
| `REQUIRES_NEW` | 기존 일시 중단, 새 트랜잭션 생성 | 새 트랜잭션 생성 |
| `NESTED` | 중첩 트랜잭션 생성 (savepoint) | 새 트랜잭션 생성 |
| `SUPPORTS` | 기존 트랜잭션 참여 | 트랜잭션 없이 실행 |
| `NOT_SUPPORTED` | 기존 트랜잭션 일시 중단 | 트랜잭션 없이 실행 |
| `MANDATORY` | 기존 트랜잭션 참여 | 예외 발생 |
| `NEVER` | 예외 발생 | 트랜잭션 없이 실행 |

---

## 실전 예제

### 1. REQUIRED (기본값)

가장 많이 사용되는 옵션이다. 기존 트랜잭션이 있으면 참여하고, 없으면 새로 생성한다.

```java
@Service
@RequiredArgsConstructor
public class OrderService {

    private final OrderRepository orderRepository;
    private final PaymentService paymentService;

    @Transactional // REQUIRED가 기본값
    public void placeOrder(OrderRequest request) {
        Order order = orderRepository.save(Order.from(request));
        // PaymentService의 메서드도 같은 트랜잭션에 참여
        paymentService.processPayment(order);
        // 어느 한 곳에서 예외 발생 시 전체 롤백
    }
}

@Service
public class PaymentService {

    @Transactional(propagation = Propagation.REQUIRED)
    public void processPayment(Order order) {
        // 동일한 트랜잭션 내에서 실행
        // OrderService에서 시작된 트랜잭션에 합류
    }
}
```

> **주의**: `REQUIRED`에서 내부 메서드가 `RuntimeException`을 던지면, 상위 트랜잭션 전체가 롤백 마킹(`rollback-only`)된다. 이를 모르고 내부 예외를 catch해도 이미 롤백이 예약된 상태라 `UnexpectedRollbackException`이 발생한다.

### 2. REQUIRES_NEW

독립적인 트랜잭션이 필요할 때 사용한다. 기존 트랜잭션을 일시 중단하고 새로운 트랜잭션을 시작한다.

```java
@Service
@RequiredArgsConstructor
public class AuditService {

    private final AuditLogRepository auditLogRepository;

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void saveAuditLog(String action, String userId) {
        // 부모 트랜잭션의 성패와 무관하게 독립적으로 커밋/롤백
        AuditLog log = AuditLog.builder()
                .action(action)
                .userId(userId)
                .createdAt(LocalDateTime.now())
                .build();
        auditLogRepository.save(log);
    }
}

@Service
@RequiredArgsConstructor
public class UserService {

    private final UserRepository userRepository;
    private final AuditService auditService;

    @Transactional
    public void updateUser(Long userId, UserUpdateRequest request) {
        User user = userRepository.findById(userId)
                .orElseThrow(() -> new UserNotFoundException(userId));
        user.update(request);

        // 감사 로그는 별도 트랜잭션으로 반드시 저장
        auditService.saveAuditLog("UPDATE_USER", userId.toString());

        // 여기서 예외 발생해도 감사 로그는 이미 커밋됨
        if (request.isInvalid()) {
            throw new InvalidRequestException("잘못된 요청입니다.");
        }
    }
}
```

> **주의**: `REQUIRES_NEW`는 새 DB 커넥션을 획득하므로 커넥션 풀 고갈 위험이 있다. 중첩 호출이 깊어지면 커넥션을 여러 개 점유하게 된다.

### 3. NESTED

JDBC Savepoint를 이용한 중첩 트랜잭션이다. 부모 트랜잭션 안에서 독립적으로 롤백 가능한 구간을 만들 때 유용하다.

```java
@Service
@RequiredArgsConstructor
public class BatchProcessService {

    private final ItemRepository itemRepository;
    private final FailureLogRepository failureLogRepository;

    @Transactional
    public BatchResult processBatch(List<Item> items) {
        int successCount = 0;
        int failCount = 0;

        for (Item item : items) {
            try {
                processItem(item); // NESTED 트랜잭션
                successCount++;
            } catch (Exception e) {
                // 개별 아이템 실패 시 해당 savepoint만 롤백
                // 전체 배치는 계속 진행
                failCount++;
                failureLogRepository.save(FailureLog.of(item, e));
            }
        }

        return new BatchResult(successCount, failCount);
    }

    @Transactional(propagation = Propagation.NESTED)
    public void processItem(Item item) {
        // 실패 시 이 메서드의 변경사항만 롤백
        itemRepository.save(item.process());
    }
}
```

> **주의**: `NESTED`는 JPA/Hibernate 환경에서 제대로 동작하지 않을 수 있다. `DataSourceTransactionManager`를 사용하는 순수 JDBC 환경에서 권장된다.

### 4. SUPPORTS

트랜잭션이 있으면 참여하고, 없으면 트랜잭션 없이 실행한다. 읽기 작업처럼 트랜잭션이 있어도 되고 없어도 되는 경우에 적합하다.

```java
@Service
public class ProductQueryService {

    @Transactional(propagation = Propagation.SUPPORTS, readOnly = true)
    public List<Product> findProducts(ProductSearchCondition condition) {
        // 트랜잭션 컨텍스트가 있으면 참여, 없으면 그냥 실행
        return productRepository.search(condition);
    }
}
```

### 5. NOT_SUPPORTED

트랜잭션 없이 실행해야 하는 로직에 사용한다. 기존 트랜잭션이 있으면 일시 중단한다.

```java
@Service
public class ExternalApiService {

    @Transactional(propagation = Propagation.NOT_SUPPORTED)
    public ApiResponse callExternalApi(String payload) {
        // 외부 API 호출은 트랜잭션 자원을 점유하지 않도록 함
        // 오래 걸리는 외부 통신 중 DB 커넥션을 잡아두지 않기 위해
        return httpClient.post(payload);
    }
}
```

### 6. MANDATORY

반드시 기존 트랜잭션 안에서만 호출되어야 하는 메서드에 사용한다. 트랜잭션 없이 호출 시 `IllegalTransactionStateException`을 던진다.

```java
@Service
public class InventoryService {

    @Transactional(propagation = Propagation.MANDATORY)
    public void decreaseStock(Long productId, int quantity) {
        // 이 메서드는 반드시 트랜잭션 내에서 호출되어야 함
        // 단독으로 호출하면 예외 발생 → 실수 방지
        Product product = productRepository.findByIdWithLock(productId);
        product.decreaseStock(quantity);
    }
}
```

### 7. NEVER

트랜잭션이 존재하면 예외를 발생시킨다. 트랜잭션과 절대 함께 실행되어서는 안 되는 작업에 사용한다.

```java
@Service
public class ReportGenerationService {

    @Transactional(propagation = Propagation.NEVER)
    public Report generateHeavyReport(ReportRequest request) {
        // 수분이 걸릴 수 있는 리포트 생성
        // 트랜잭션 내에서 호출하면 즉시 예외 발생 → 커넥션 점유 방지
        return reportBuilder.build(request);
    }
}
```

---

## 주의사항 및 트레이드오프

### Self-Invocation 문제

Spring AOP 프록시 기반이기 때문에 같은 클래스 내에서 메서드를 직접 호출하면 전파가 적용되지 않는다.

```java
@Service
public class OrderService {

    @Transactional
    public void placeOrder(OrderRequest request) {
        // 직접 호출 → 프록시를 거치지 않아 REQUIRES_NEW가 무시됨!
        this.saveAuditLog(request);
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void saveAuditLog(OrderRequest request) {
        // 실제로는 placeOrder의 트랜잭션 안에서 실행됨
    }
}
```

**해결 방법**: 별도 빈으로 분리하거나, `ApplicationContext`에서 빈을 주입받아 호출한다.

```java
@Service
@RequiredArgsConstructor
public class OrderService {

    private final AuditService auditService; // 별도 빈으로 분리

    @Transactional
    public void placeOrder(OrderRequest request) {
        auditService.saveAuditLog(request); // 프록시를 통한 호출
    }
}
```

### rollback-only 함정

`REQUIRED`로 참여한 하위 트랜잭션에서 예외가 발생하면, 상위 트랜잭션도 롤백 마킹된다.

```java
@Transactional
public void parentMethod() {
    try {
        childMethod(); // REQUIRED - 예외 발생
    } catch (Exception e) {
        // 예외를 잡아도 이미 rollback-only 마킹됨
        // 이후 커밋 시도 시 UnexpectedRollbackException 발생
        log.error("처리 중 오류", e);
    }
    // 커밋 시도 → UnexpectedRollbackException!
}
```

이 경우 `REQUIRES_NEW` 또는 `NESTED`를 사용해 독립적인 롤백 범위를 만들어야 한다.

### 성능 트레이드오프

| 옵션 | 커넥션 사용 | 주요 위험 |
|------|-------------|-----------|
| `REQUIRED` | 1개 공유 | rollback-only 전파 |
| `REQUIRES_NEW` | 추가 커넥션 획득 | 커넥션 풀 고갈 |
| `NESTED` | 1개 공유 | JPA 호환성 |
| `NOT_SUPPORTED` | 트랜잭션 중단 | 데이터 정합성 |

### 읽기 전용 트랜잭션 최적화

`readOnly = true` 설정은 Hibernate의 flush mode를 `NEVER`로 설정하고, DB 드라이버 레벨에서 최적화 힌트를 제공하므로 조회 전용 로직에는 반드시 적용하는 것이 좋다.

```java
@Transactional(propagation = Propagation.REQUIRED, readOnly = true)
public UserDto getUserDetail(Long userId) {
    return userRepository.findById(userId)
            .map(UserDto::from)
            .orElseThrow();
}
```

---

## 정리

트랜잭션 전파 옵션을 선택할 때는 다음 질문을 스스로에게 던져보자.

1. **이 작업은 기존 트랜잭션과 운명을 같이해야 하는가?** → `REQUIRED`
2. **이 작업은 반드시 성공해야 하고, 다른 실패에 영향받아서는 안 되는가?** → `REQUIRES_NEW`
3. **개별 실패를 허용하면서 전체를 진행해야 하는가?** → `NESTED` (JDBC 환경)
4. **이 메서드는 반드시 트랜잭션 안에서만 호출되어야 하는가?** → `MANDATORY`
5. **트랜잭션 없이 실행해야 하는가?** → `NOT_SUPPORTED` 또는 `NEVER`

실무에서는 `REQUIRED`와 `REQUIRES_NEW`를 가장 많이 사용하지만, 각각의 트레이드오프를 명확히 이해하고 사용해야 한다. Self-Invocation 함정과 rollback-only 전파 문제는 운영 환경에서 디버깅하기 매우 까다로운 문제이므로, 코드 리뷰 시 트랜잭션 경계를 반드시 함께 검토하는 습관을 들이길 권장한다.