# Java 패턴 매칭과 Switch 표현식 완벽 이해

## 개요

Java는 오랫동안 "너무 verbose하다"는 비판을 받아왔다. 타입 체크 후 캐스팅, 복잡한 조건 분기, 장황한 switch 문법은 코드 가독성을 떨어뜨리는 주범이었다. 하지만 Java 14부터 시작된 **패턴 매칭(Pattern Matching)** 과 **Switch 표현식(Switch Expression)** 의 진화는 이러한 비판에 정면으로 응답하고 있다.

Java 21에서 정식 릴리즈된 패턴 매칭 for switch(JEP 441)와 함께, 이제 Java는 Kotlin이나 Scala에 버금가는 표현력을 갖추게 되었다. 이 글에서는 실무 현장에서 바로 적용할 수 있도록 개념부터 실전 예제, 주의사항까지 체계적으로 다룬다.

---

## 핵심 개념

### 1. Switch 표현식 (Java 14 정식 도입)

기존 switch 문(statement)과 switch 표현식(expression)의 가장 큰 차이는 **값을 반환할 수 있다**는 점이다.

```java
// 기존 switch 문 (statement)
String result;
switch (day) {
    case MONDAY:
    case FRIDAY:
        result = "Weekday";
        break;
    case SATURDAY:
    case SUNDAY:
        result = "Weekend";
        break;
    default:
        result = "Unknown";
}

// switch 표현식 (expression) - Java 14+
String result = switch (day) {
    case MONDAY, FRIDAY -> "Weekday";
    case SATURDAY, SUNDAY -> "Weekend";
    default -> "Unknown";
};
```

`->` 화살표 레이블을 사용하면 fall-through가 발생하지 않고, 여러 case를 쉼표로 묶을 수 있다. `yield` 키워드를 사용하면 블록 내에서 값을 반환할 수도 있다.

```java
int numLetters = switch (day) {
    case MONDAY, FRIDAY, SUNDAY -> 6;
    case TUESDAY -> 7;
    case THURSDAY, SATURDAY -> 8;
    case WEDNESDAY -> {
        System.out.println("Wednesday is the longest!");
        yield 9; // 블록에서 값 반환 시 yield 사용
    }
};
```

### 2. 패턴 매칭 for instanceof (Java 16 정식 도입)

타입 체크와 캐스팅을 한 번에 처리한다.

```java
// Before Java 16
if (obj instanceof String) {
    String s = (String) obj;
    System.out.println(s.length());
}

// Java 16+
if (obj instanceof String s) {
    System.out.println(s.length()); // s는 자동으로 String으로 바인딩
}
```

바인딩된 변수 `s`는 패턴 매칭이 성공한 스코프에서만 유효하며, 컴파일러가 타입 안정성을 보장한다.

### 3. 패턴 매칭 for Switch (Java 21 정식 도입)

`instanceof`의 패턴 매칭을 switch로 확장한 것이 핵심이다. 이를 통해 타입별 분기 처리를 선언적으로 작성할 수 있다.

```java
// Java 21+
static String formatValue(Object obj) {
    return switch (obj) {
        case Integer i -> "Integer: " + i;
        case Long l    -> "Long: " + l;
        case Double d  -> "Double: " + d;
        case String s  -> "String: " + s;
        case null      -> "null";
        default        -> "Unknown: " + obj;
    };
}
```

### 4. Guarded Pattern (보호 패턴)

`when` 키워드를 활용해 패턴에 추가 조건을 명시할 수 있다.

```java
static String classify(Object obj) {
    return switch (obj) {
        case Integer i when i < 0  -> "Negative integer";
        case Integer i when i == 0 -> "Zero";
        case Integer i             -> "Positive integer";
        case String s when s.isBlank() -> "Blank string";
        case String s              -> "Non-blank string: " + s;
        default                    -> "Other";
    };
}
```

---

## 실전 예제

### 예제 1: API 응답 처리 (Sealed Interface + Pattern Matching)

Spring 애플리케이션에서 외부 API 결과를 타입 안전하게 처리하는 패턴이다.

```java
// Sealed interface로 가능한 결과 타입을 제한
public sealed interface ApiResult<T>
    permits ApiResult.Success, ApiResult.Failure, ApiResult.Empty {

    record Success<T>(T data, int statusCode) implements ApiResult<T> {}
    record Failure<T>(String message, int statusCode, Throwable cause) implements ApiResult<T> {}
    record Empty<T>() implements ApiResult<T> {}
}
```

```java
// Service Layer에서 패턴 매칭으로 처리
@Service
public class OrderService {

    public ResponseEntity<OrderDto> processApiResult(ApiResult<Order> result) {
        return switch (result) {
            case ApiResult.Success<Order> s when s.statusCode() == 200 ->
                ResponseEntity.ok(OrderDto.from(s.data()));

            case ApiResult.Success<Order> s ->
                ResponseEntity.status(s.statusCode()).build();

            case ApiResult.Failure<Order> f when f.statusCode() == 404 ->
                ResponseEntity.notFound().build();

            case ApiResult.Failure<Order> f -> {
                log.error("API call failed: {}", f.message(), f.cause());
                yield ResponseEntity.internalServerError().build();
            }

            case ApiResult.Empty<Order> ignored ->
                ResponseEntity.noContent().build();
        };
        // sealed interface이므로 default 불필요 - 컴파일러가 완전성 체크
    }
}
```

Sealed interface와 패턴 매칭의 조합은 컴파일러가 모든 케이스를 처리했는지 보장한다. 새로운 구현체가 추가될 경우 컴파일 에러가 발생하므로, 런타임 버그를 사전에 방지할 수 있다.

### 예제 2: 도메인 이벤트 처리

이벤트 기반 아키텍처에서 다양한 도메인 이벤트를 타입 안전하게 디스패치하는 예제다.

```java
public sealed interface DomainEvent
    permits OrderCreated, OrderCancelled, PaymentCompleted, PaymentFailed {}

public record OrderCreated(String orderId, String userId, BigDecimal amount) implements DomainEvent {}
public record OrderCancelled(String orderId, String reason) implements DomainEvent {}
public record PaymentCompleted(String paymentId, String orderId) implements DomainEvent {}
public record PaymentFailed(String paymentId, String errorCode) implements DomainEvent {}
```

```java
@Component
public class DomainEventHandler {

    public void handle(DomainEvent event) {
        switch (event) {
            case OrderCreated e -> {
                log.info("Order created: {}", e.orderId());
                notificationService.sendOrderConfirmation(e.userId(), e.orderId());
                inventoryService.reserve(e.orderId());
            }
            case OrderCancelled e when e.reason().equals("USER_REQUEST") -> {
                log.info("Order cancelled by user: {}", e.orderId());
                refundService.processRefund(e.orderId());
            }
            case OrderCancelled e -> {
                log.warn("Order cancelled due to: {}", e.reason());
                alertService.sendAlert(e.orderId(), e.reason());
            }
            case PaymentCompleted e -> {
                log.info("Payment completed: {}", e.paymentId());
                orderService.confirmPayment(e.orderId());
            }
            case PaymentFailed e -> {
                log.error("Payment failed: {} - {}", e.paymentId(), e.errorCode());
                orderService.cancelDueToPaymentFailure(e.paymentId());
            }
        }
    }
}
```

### 예제 3: JSON 파싱 결과 처리

Jackson의 JsonNode를 파싱하는 유틸리티에 패턴 매칭을 적용한 예제다.

```java
public class JsonValueExtractor {

    public static Object extractValue(JsonNode node) {
        return switch (node) {
            case TextNode t   -> t.asText();
            case IntNode i    -> i.asInt();
            case LongNode l   -> l.asLong();
            case DoubleNode d -> d.asDouble();
            case BooleanNode b -> b.asBoolean();
            case ArrayNode a  -> StreamSupport.stream(a.spliterator(), false)
                                    .map(JsonValueExtractor::extractValue)
                                    .collect(Collectors.toList());
            case NullNode ignored -> null;
            default -> node.toString();
        };
    }
}
```

```java
// 실무에서 자주 쓰이는 타입 기반 변환 처리
public static <T> T convertValue(Object value, Class<T> targetType) {
    return switch (value) {
        case null -> null;
        case String s when targetType == Integer.class ->
            targetType.cast(Integer.parseInt(s));
        case String s when targetType == Boolean.class ->
            targetType.cast(Boolean.parseBoolean(s));
        case Number n when targetType == Long.class ->
            targetType.cast(n.longValue());
        case Number n when targetType == Double.class ->
            targetType.cast(n.doubleValue());
        default when targetType.isInstance(value) ->
            targetType.cast(value);
        default ->
            throw new IllegalArgumentException(
                "Cannot convert " + value.getClass() + " to " + targetType);
    };
}
```

---

## 주의사항 및 트레이드오프

### 1. 패턴 순서가 중요하다

패턴은 위에서 아래로 평가된다. 더 구체적인 패턴(guarded pattern)은 반드시 일반 패턴보다 앞에 위치해야 한다. 그렇지 않으면 컴파일 에러가 발생한다.

```java
// 컴파일 에러: case Integer i가 이미 모든 Integer를 처리하므로
// case Integer i when i > 0은 도달 불가
static String wrong(Object obj) {
    return switch (obj) {
        case Integer i         -> "Any integer";
        case Integer i when i > 0 -> "Positive"; // 컴파일 에러!
        default -> "Other";
    };
}

// 올바른 순서
static String correct(Object obj) {
    return switch (obj) {
        case Integer i when i > 0 -> "Positive";
        case Integer i when i < 0 -> "Negative";
        case Integer i            -> "Zero";
        default                   -> "Other";
    };
}
```

### 2. null 처리

기존 switch는 null이 입력되면 `NullPointerException`을 던졌다. 패턴 매칭 switch에서는 `case null`을 명시적으로 처리할 수 있다. 하지만 `case null`을 작성하지 않으면 여전히 NPE가 발생하므로 주의가 필요하다.

```java
// case null 명시 필요
return switch (obj) {
    case null -> "null value";
    case String s -> "string: " + s;
    default -> "other";
};
```

### 3. Sealed Interface 없이는 default 필수

switch 표현식에서 sealed interface나 enum이 아닌 일반 타입을 사용하면 컴파일러가 완전성을 보장할 수 없으므로 `default` 케이스가 반드시 있어야 한다. 이는 향후 타입이 추가되어도 컴파일 에러가 발생하지 않는다는 의미이기도 하므로, 가능하면 sealed 계층을 활용하는 것이 안전하다.

### 4. 성능 고려사항

패턴 매칭 switch는 내부적으로 `invokedynamic`을 사용하여 효율적으로 구현되어 있다. 단순한 타입 체크보다 오버헤드가 있을 수 있지만, 대부분의 실무 시나리오에서는 무시할 수준이다. 단, 매우 고빈도 핫패스(hot path)에서는 벤치마크를 통해 확인하는 것이 좋다.

### 5. Java 버전 호환성

| 기능 | 도입 버전 | 정식 릴리즈 |
|------|----------|------------|
| Switch 표현식 | Java 12 (Preview) | Java 14 |
| Pattern Matching for instanceof | Java 14 (Preview) | Java 16 |
| Pattern Matching for switch | Java 17 (Preview) | Java 21 |
| Record Patterns | Java 19 (Preview) | Java 21 |

팀 내 Java 버전이 통일되어 있지 않다면 도입 전 버전 정책을 반드시 확인해야 한다.

---

## 정리

Java의 패턴 매칭과 Switch 표현식은 단순한 문법 설탕(syntactic sugar)이 아니다. Sealed interface와 결합하면 **컴파일 타임 완전성 검증**, **타입 안전한 분기 처리**, **가독성 높은 선언적 코드** 세 가지를 동시에 달성할 수 있다.

실무에서 적용할 수 있는 핵심 포인트를 정리하면 다음과 같다.

- **복잡한 instanceof + casting 체인**은 패턴 매칭 for instanceof로 교체하라.
- **타입별 분기가 많은 핸들러/디스패처**는 패턴 매칭 for switch로 리팩터링하라.
- **유한한 상태/결과 타입**은 sealed interface + record를 활용하면 컴파일러가 변경 안전망이 되어준다.
- **when 절(Guarded Pattern)** 을 적극 활용해 조건 분기를 한 곳에서 선언적으로 표현하라.

Java 21을 사용할 수 있는 환경이라면 더 이상 망설일 이유가 없다. 기존의 복잡한 분기 로직을 패턴 매칭으로 리팩터링하는 것만으로도 코드 리뷰 부담이 현저히 줄어들고, 유지보수성이 크게 향상될 것이다.