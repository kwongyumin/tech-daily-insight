# Java Records와 Sealed Classes 모던 자바 문법

## 개요

Java 16에서 정식 도입된 **Records**와 Java 17에서 표준화된 **Sealed Classes**는 모던 자바의 핵심 문법 변화 중 하나입니다. 두 기능 모두 장황한 보일러플레이트 코드를 줄이고, 타입 시스템을 더 명확하게 표현할 수 있도록 설계되었습니다.

특히 Spring 생태계에서 DTO 설계, 도메인 모델링, API 응답 타입 처리 등에 Records와 Sealed Classes를 활용하면 코드 가독성과 유지보수성이 크게 향상됩니다. 이 글에서는 각 기능의 핵심 개념을 살펴보고, 실무에서 바로 적용 가능한 예제를 통해 실용적인 활용법을 소개합니다.

---

## 핵심 개념

### Java Records

Records는 **불변(immutable) 데이터 캐리어**를 간결하게 표현하기 위한 특수 클래스입니다. 기존에 Lombok의 `@Value`나 `@Data`로 처리하던 작업을 언어 차원에서 지원합니다.

```java
// 기존 방식 (Lombok 없이)
public final class UserDto {
    private final Long id;
    private final String name;
    private final String email;

    public UserDto(Long id, String name, String email) {
        this.id = id;
        this.name = name;
        this.email = email;
    }

    public Long getId() { return id; }
    public String getName() { return name; }
    public String getEmail() { return email; }

    @Override
    public boolean equals(Object o) { /* ... */ }
    @Override
    public int hashCode() { /* ... */ }
    @Override
    public String toString() { /* ... */ }
}

// Record 방식
public record UserDto(Long id, String name, String email) {}
```

컴파일러는 Record 선언으로부터 다음을 자동 생성합니다.

- 모든 필드에 대한 `private final` 선언
- Canonical Constructor (정규 생성자)
- 각 컴포넌트에 대한 접근자 메서드 (`id()`, `name()`, `email()`)
- `equals()`, `hashCode()`, `toString()` 구현

Records의 핵심 특성은 다음과 같습니다.

- **불변성**: 모든 필드는 묵시적으로 `final`
- **상속 불가**: 다른 클래스를 `extends` 할 수 없음 (단, `implements`는 가능)
- **컴팩트 생성자**: 유효성 검사 로직을 간결하게 추가 가능

### Sealed Classes

Sealed Classes는 **클래스 계층 구조를 명시적으로 제한**하는 기능입니다. `sealed` 키워드로 선언하고, `permits` 절에 허용된 하위 타입을 명시합니다.

```java
public sealed interface Shape
    permits Circle, Rectangle, Triangle {}

public record Circle(double radius) implements Shape {}
public record Rectangle(double width, double height) implements Shape {}
public record Triangle(double base, double height) implements Shape {}
```

하위 클래스는 반드시 `final`, `sealed`, `non-sealed` 중 하나로 선언해야 합니다.

- `final`: 더 이상 확장 불가
- `sealed`: 다시 sealed 계층으로 제한
- `non-sealed`: 제한 없이 확장 허용

---

## 실전 예제

### 1. Spring API 응답 처리 — Sealed Classes + Records

API 응답의 성공/실패를 타입 안전하게 표현하는 패턴입니다.

```java
// 봉인된 응답 타입 정의
public sealed interface ApiResponse<T>
    permits ApiResponse.Success, ApiResponse.Failure {

    record Success<T>(T data, String message) implements ApiResponse<T> {}
    record Failure<T>(String errorCode, String errorMessage, int statusCode)
        implements ApiResponse<T> {}
}
```

```java
// Service 레이어
@Service
@RequiredArgsConstructor
public class UserService {

    private final UserRepository userRepository;

    public ApiResponse<UserDto> findUser(Long id) {
        return userRepository.findById(id)
            .map(user -> new ApiResponse.Success<>(
                new UserDto(user.getId(), user.getName(), user.getEmail()),
                "사용자 조회 성공"
            ))
            .orElse(new ApiResponse.Failure<>(
                "USER_NOT_FOUND",
                "해당 사용자를 찾을 수 없습니다.",
                404
            ));
    }
}
```

```java
// Controller 레이어 — Pattern Matching과 결합
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/users")
public class UserController {

    private final UserService userService;

    @GetMapping("/{id}")
    public ResponseEntity<?> getUser(@PathVariable Long id) {
        ApiResponse<UserDto> response = userService.findUser(id);

        return switch (response) {
            case ApiResponse.Success<UserDto> s ->
                ResponseEntity.ok(s);
            case ApiResponse.Failure<UserDto> f ->
                ResponseEntity.status(f.statusCode()).body(f);
        };
    }
}
```

이 패턴의 핵심 장점은 `switch` 표현식에서 **컴파일러가 모든 케이스 처리를 강제**한다는 점입니다. 새로운 응답 타입이 추가될 경우, 처리하지 않은 곳에서 컴파일 에러가 발생합니다.

### 2. 도메인 이벤트 모델링

Event-Driven 아키텍처에서 도메인 이벤트를 타입 안전하게 정의하는 예제입니다.

```java
// 봉인된 도메인 이벤트 계층
public sealed interface OrderEvent
    permits OrderEvent.OrderPlaced,
            OrderEvent.OrderShipped,
            OrderEvent.OrderCancelled,
            OrderEvent.OrderDelivered {

    record OrderPlaced(
        String orderId,
        String customerId,
        List<OrderItem> items,
        LocalDateTime placedAt
    ) implements OrderEvent {}

    record OrderShipped(
        String orderId,
        String trackingNumber,
        LocalDateTime shippedAt
    ) implements OrderEvent {}

    record OrderCancelled(
        String orderId,
        String reason,
        LocalDateTime cancelledAt
    ) implements OrderEvent {}

    record OrderDelivered(
        String orderId,
        LocalDateTime deliveredAt
    ) implements OrderEvent {}
}
```

```java
// 이벤트 핸들러 — 완전한 패턴 매칭 보장
@Component
public class OrderEventHandler {

    public void handle(OrderEvent event) {
        switch (event) {
            case OrderEvent.OrderPlaced placed -> {
                log.info("주문 생성: {}, 고객: {}", placed.orderId(), placed.customerId());
                sendOrderConfirmationEmail(placed);
            }
            case OrderEvent.OrderShipped shipped -> {
                log.info("배송 시작: {}, 운송장: {}", shipped.orderId(), shipped.trackingNumber());
                sendShippingNotification(shipped);
            }
            case OrderEvent.OrderCancelled cancelled -> {
                log.warn("주문 취소: {}, 사유: {}", cancelled.orderId(), cancelled.reason());
                processRefund(cancelled);
            }
            case OrderEvent.OrderDelivered delivered -> {
                log.info("배송 완료: {}", delivered.orderId());
                requestReview(delivered);
            }
        }
    }
}
```

### 3. Record의 Compact Constructor를 활용한 유효성 검증

```java
public record CreateUserRequest(
    String name,
    String email,
    int age
) {
    // Compact Constructor: 유효성 검사
    public CreateUserRequest {
        if (name == null || name.isBlank()) {
            throw new IllegalArgumentException("이름은 필수입니다.");
        }
        if (email == null || !email.contains("@")) {
            throw new IllegalArgumentException("유효하지 않은 이메일 형식입니다.");
        }
        if (age < 0 || age > 150) {
            throw new IllegalArgumentException("나이는 0~150 사이여야 합니다.");
        }
        // 정규화
        name = name.trim();
        email = email.toLowerCase();
    }
}
```

```java
// Jackson 역직렬화와 함께 사용
@RestController
@RequestMapping("/api/users")
public class UserController {

    @PostMapping
    public ResponseEntity<UserDto> createUser(
        @RequestBody @Valid CreateUserRequest request
    ) {
        // Record는 생성 시점에 유효성 검사 완료
        // request 객체가 존재하면 항상 유효한 상태
        UserDto created = userService.createUser(request);
        return ResponseEntity.status(HttpStatus.CREATED).body(created);
    }
}
```

> **참고**: Spring Boot 3.x (Spring Framework 6.x)부터 Records에 대한 Jackson 역직렬화가 별도 설정 없이 정상 동작합니다.

### 4. 계층형 Sealed Class — 복잡한 비즈니스 로직

```java
// 결제 수단 계층 구조
public sealed interface PaymentMethod
    permits PaymentMethod.CardPayment,
            PaymentMethod.BankTransfer,
            PaymentMethod.DigitalWallet {

    sealed interface CardPayment extends PaymentMethod
        permits CardPayment.CreditCard, CardPayment.DebitCard {

        record CreditCard(
            String cardNumber, String holderName, YearMonth expiry, int installments
        ) implements CardPayment {}

        record DebitCard(
            String cardNumber, String holderName, String bankCode
        ) implements CardPayment {}
    }

    record BankTransfer(
        String bankCode, String accountNumber, String accountHolder
    ) implements PaymentMethod {}

    record DigitalWallet(
        String provider, String walletId, String token
    ) implements PaymentMethod {}
}
```

```java
// 수수료 계산 서비스
@Service
public class FeeCalculationService {

    public BigDecimal calculateFee(PaymentMethod method, BigDecimal amount) {
        return switch (method) {
            case PaymentMethod.CardPayment.CreditCard cc ->
                amount.multiply(BigDecimal.valueOf(0.015))
                      .multiply(BigDecimal.valueOf(cc.installments()));
            case PaymentMethod.CardPayment.DebitCard dc ->
                amount.multiply(BigDecimal.valueOf(0.005));
            case PaymentMethod.BankTransfer bt ->
                BigDecimal.valueOf(500); // 고정 수수료
            case PaymentMethod.DigitalWallet dw ->
                calculateWalletFee(dw.provider(), amount);
        };
    }
}
```

---

## 주의사항 및 트레이드오프

### Records 사용 시 주의사항

**1. 가변 컬렉션의 불변성 문제**

```java
// 위험: List가 외부에서 변경될 수 있음
public record OrderItems(List<String> items) {}

// 안전: 방어적 복사
public record OrderItems(List<String> items) {
    public OrderItems {
        items = List.copyOf(items); // 불변 리스트로 복사
    }
}
```

**2. JPA Entity로 사용 불가**

Records는 기본 생성자가 없고 `final` 필드를 가지기 때문에 JPA 엔티티로 직접 사용할 수 없습니다. 엔티티는 기존 클래스로 유지하고, DTO 레이어에서만 Records를 활용하는 것이 권장됩니다.

**3. Lombok과의 충돌**

Records와 Lombok을 함께 사용할 때 일부 어노테이션(`@Builder`, `@With` 등)이 정상 동작하지 않을 수 있습니다. 필요 시 Records 내에 직접 커스텀 메서드를 추가하거나 별도 빌더 클래스를 작성하세요.

### Sealed Classes 사용 시 주의사항

**1. 같은 패키지(또는 모듈) 내 정의 필수**

`permits`에 명시된 클래스는 동일 컴파일 단위에 있어야 합니다. 외부 라이브러리에서 하위 타입을 정의하려는 경우 설계를 재검토해야 합니다.

**2. 과도한 계층 설계 주의**

Sealed Classes는 계층 구조를 명시적으로 고정합니다. 확장 가능성이 요구되는 도메인(플러그인 아키텍처 등)에는 오히려 걸림돌이 될 수 있습니다. "이 타입의 모든 변형이 완전히 알려져 있는가?"를 기준으로 적용 여부를 판단하세요.

**3. 직렬화 고려**

Records는 기본적으로 `Serializable`을 구현하지 않습니다. 네트워크 전송이나 캐시 직렬화가 필요한 경우 명시적으로 `implements Serializable`을 선언하거나 Jackson/Protobuf 등의 직렬화 라이브러리를 활용하세요.

---

## 정리

| 기능 | 도입 버전 | 주요 용도 | 제한 사항 |
|------|-----------|-----------|-----------|
| Records | Java 16 (정식) | 불변 DTO, 값 객체 | JPA Entity 불가, 상속 불가 |
| Sealed Classes | Java 17 (정식) | 제한된 타입 계층, ADT | 같은 패키지 내 정의 필요 |
| Pattern Matching (switch) | Java 21 (정식) | Sealed 타입 분기 처리 | Java 21 이상 필요 |

Java Records와 Sealed Classes는 단순히 문법 설탕(Syntactic Sugar)을 넘어, **타입 시스템 수준에서 도메인을 더 정확하게 표현**하는 도구입니다. Records는 불변 데이터 구조를 표현할 때, Sealed Classes는 닫힌 타입 계층을 모델링할 때 강력한 힘을 발휘합니다.

특히 Java 21의 Pattern Matching for switch와 결합하면, Haskell이나 Scala의 대수적 데이터 타입(ADT)에 가까운 표현력을 Java에서도 누릴 수 있습니다. Spring Boot 3.x 기반의 신규 프로젝트라면 DTO 설계와 도메인 이벤트 모델링에 적극적으로 도입을 검토해볼 만합니다.