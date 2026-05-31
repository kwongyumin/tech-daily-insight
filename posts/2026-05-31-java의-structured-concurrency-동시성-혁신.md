# Java의 Structured Concurrency 동시성 혁신

## 개요

Java 21에서 Preview로 등장해 Java 23에서 두 번째 Preview를 거친 **Structured Concurrency(구조적 동시성)** 는 기존 비동기 프로그래밍의 복잡성을 근본적으로 해결하는 새로운 패러다임입니다. `ExecutorService`, `CompletableFuture`로 대표되는 기존 방식의 고질적인 문제—누수되는 스레드, 흩어진 에러 처리, 디버깅 어려움—를 코드 구조 자체로 강제 해결합니다.

Structured Concurrency는 **"동시 작업의 생명주기가 코드 블록의 스코프를 벗어나지 않아야 한다"** 는 단순하지만 강력한 원칙 위에 설계되었습니다. 이는 마치 구조적 프로그래밍이 `GOTO`문의 혼돈을 제거했던 것처럼, 비동기 프로그래밍 세계에서도 동일한 혁신을 가져오려는 시도입니다.

이 글에서는 Structured Concurrency의 핵심 개념부터 실무 적용 방법, 그리고 트레이드오프까지 시니어 개발자 관점에서 심도 있게 다루겠습니다.

---

## 핵심 개념

### 기존 방식의 문제점

먼저 기존 `ExecutorService` 방식으로 여러 작업을 동시에 실행하는 코드를 살펴보겠습니다.

```java
// 기존 방식 - 문제가 많다
ExecutorService executor = Executors.newFixedThreadPool(10);

Future<User> userFuture = executor.submit(() -> fetchUser(userId));
Future<Order> orderFuture = executor.submit(() -> fetchOrder(orderId));

try {
    User user = userFuture.get(5, TimeUnit.SECONDS);
    Order order = orderFuture.get(5, TimeUnit.SECONDS);
    return new UserOrderInfo(user, order);
} catch (Exception e) {
    // fetchUser가 실패해도 fetchOrder는 계속 실행 중!
    // 취소 처리를 별도로 해야 함
    userFuture.cancel(true);
    orderFuture.cancel(true);
    throw new RuntimeException(e);
}
```

이 코드의 문제점은 명확합니다:
- 하나의 작업이 실패해도 나머지 작업이 계속 실행됩니다.
- 취소 로직을 개발자가 직접 구현해야 합니다.
- 스레드 누수가 발생할 수 있습니다.
- 스택 트레이스가 실제 문제 원인을 숨깁니다.

### StructuredTaskScope 소개

Structured Concurrency의 핵심 클래스는 `StructuredTaskScope`입니다. 이 클래스는 `AutoCloseable`을 구현하여 `try-with-resources` 패턴으로 사용할 수 있으며, 내부에서 실행된 모든 작업은 스코프가 닫힐 때 반드시 완료(또는 취소)됩니다.

```java
// Structured Concurrency 방식
try (var scope = new StructuredTaskScope.ShutdownOnFailure()) {
    Subtask<User> userTask = scope.fork(() -> fetchUser(userId));
    Subtask<Order> orderTask = scope.fork(() -> fetchOrder(orderId));

    scope.join()           // 두 작업이 완료될 때까지 대기
         .throwIfFailed(); // 실패 시 예외 전파

    return new UserOrderInfo(userTask.get(), orderTask.get());
}
// 스코프를 벗어나면 모든 작업이 완료 또는 취소 보장
```

단 몇 줄의 차이지만, 동작은 근본적으로 다릅니다. 하나가 실패하면 나머지 작업은 자동으로 취소되고, 스코프가 닫힐 때 모든 자원이 정리됩니다.

### 두 가지 기본 정책

Java는 두 가지 내장 정책을 제공합니다:

**ShutdownOnFailure**: 하나의 작업이라도 실패하면 나머지 모두 취소
```java
// 모든 작업이 성공해야 할 때
StructuredTaskScope.ShutdownOnFailure
```

**ShutdownOnSuccess**: 하나라도 성공하면 나머지 모두 취소
```java
// 여러 소스 중 가장 빠른 응답을 원할 때
StructuredTaskScope.ShutdownOnSuccess
```

---

## 실전 예제

### 예제 1: 마이크로서비스 데이터 집계

실제 서비스에서 자주 만나는 패턴—여러 마이크로서비스에서 데이터를 동시에 수집해 집계하는 시나리오입니다.

```java
import java.util.concurrent.StructuredTaskScope;
import java.util.concurrent.StructuredTaskScope.Subtask;

public record DashboardData(
    UserProfile profile,
    List<Order> recentOrders,
    List<Notification> notifications,
    AccountBalance balance
) {}

public DashboardData buildDashboard(String userId) throws Exception {
    try (var scope = new StructuredTaskScope.ShutdownOnFailure()) {
        
        Subtask<UserProfile> profileTask = 
            scope.fork(() -> userService.getProfile(userId));
        
        Subtask<List<Order>> ordersTask = 
            scope.fork(() -> orderService.getRecentOrders(userId, 10));
        
        Subtask<List<Notification>> notificationsTask = 
            scope.fork(() -> notificationService.getUnread(userId));
        
        Subtask<AccountBalance> balanceTask = 
            scope.fork(() -> accountService.getBalance(userId));

        // 모든 작업 완료 대기 (타임아웃 포함)
        scope.joinUntil(Instant.now().plus(Duration.ofSeconds(3)))
             .throwIfFailed();

        return new DashboardData(
            profileTask.get(),
            ordersTask.get(),
            notificationsTask.get(),
            balanceTask.get()
        );
    }
}
```

### 예제 2: 커스텀 ShutdownOnSuccess - 멀티 소스 검색

여러 검색 제공자 중 가장 빠른 응답을 반환하는 패턴입니다.

```java
public SearchResult searchFromFastestProvider(String query) throws Exception {
    try (var scope = new StructuredTaskScope.ShutdownOnSuccess<SearchResult>()) {
        
        scope.fork(() -> elasticsearchProvider.search(query));
        scope.fork(() -> opensearchProvider.search(query));
        scope.fork(() -> legacyDatabaseProvider.search(query));

        scope.join(); // 가장 빠른 성공 결과를 기다림
        
        return scope.result(); // 첫 번째 성공 결과 반환
    }
}
```

### 예제 3: 커스텀 StructuredTaskScope 구현

내장 정책으로는 부족한 경우 커스텀 스코프를 구현할 수 있습니다. 예를 들어 N개 중 K개 이상 성공해야 하는 경우입니다.

```java
public class ShutdownOnKSuccess<T> extends StructuredTaskScope<T> {
    
    private final int requiredSuccesses;
    private final List<T> results = new CopyOnWriteArrayList<>();
    private final AtomicInteger successCount = new AtomicInteger(0);

    public ShutdownOnKSuccess(int requiredSuccesses) {
        this.requiredSuccesses = requiredSuccesses;
    }

    @Override
    protected void handleComplete(Subtask<? extends T> subtask) {
        if (subtask.state() == Subtask.State.SUCCESS) {
            results.add(subtask.get());
            if (successCount.incrementAndGet() >= requiredSuccesses) {
                shutdown(); // K개 성공 시 나머지 취소
            }
        }
    }

    public List<T> results() {
        super.ensureOwnerAndJoined();
        if (results.size() < requiredSuccesses) {
            throw new IllegalStateException(
                "필요한 성공 수에 도달하지 못했습니다: " + results.size()
            );
        }
        return Collections.unmodifiableList(results);
    }
}

// 사용 예시: 3개의 레플리카 노드 중 2개 이상에서 확인
public List<WriteResult> writeWithQuorum(WriteRequest request) throws Exception {
    try (var scope = new ShutdownOnKSuccess<WriteResult>(2)) {
        scope.fork(() -> replicaNode1.write(request));
        scope.fork(() -> replicaNode2.write(request));
        scope.fork(() -> replicaNode3.write(request));
        
        scope.join();
        return scope.results();
    }
}
```

### 예제 4: Spring Boot 서비스에서의 통합

Spring Boot 환경에서 Virtual Thread와 함께 사용하는 방법입니다.

```java
@Service
@RequiredArgsConstructor
public class ProductAggregationService {

    private final ProductRepository productRepository;
    private final ReviewService reviewService;
    private final InventoryService inventoryService;
    private final PricingService pricingService;

    public ProductDetail getProductDetail(Long productId) {
        try (var scope = new StructuredTaskScope.ShutdownOnFailure()) {
            
            var productTask = scope.fork(() ->
                productRepository.findById(productId)
                    .orElseThrow(() -> new ProductNotFoundException(productId))
            );
            
            var reviewsTask = scope.fork(() ->
                reviewService.getTopReviews(productId, 5)
            );
            
            var inventoryTask = scope.fork(() ->
                inventoryService.getStock(productId)
            );
            
            var priceTask = scope.fork(() ->
                pricingService.getCurrentPrice(productId)
            );

            scope.join().throwIfFailed(e -> 
                new ProductAggregationException("상품 정보 수집 실패", e)
            );

            return ProductDetail.builder()
                .product(productTask.get())
                .reviews(reviewsTask.get())
                .stockInfo(inventoryTask.get())
                .price(priceTask.get())
                .build();

        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ProductAggregationException("처리 중단", e);
        }
    }
}
```

`application.properties`에서 Virtual Thread를 활성화하면 Structured Concurrency의 효과가 극대화됩니다.

```properties
# Spring Boot 3.2+
spring.threads.virtual.enabled=true
```

---

## 주의사항 및 트레이드오프

### 1. 아직 Preview API

Java 21~23에서 여전히 Preview 상태입니다. 프로덕션 코드에 사용하려면 컴파일 및 런타임 옵션이 필요합니다.

```xml
<!-- Maven -->
<plugin>
    <groupId>org.apache.maven.plugins</groupId>
    <artifactId>maven-compiler-plugin</artifactId>
    <configuration>
        <compilerArgs>
            <arg>--enable-preview</arg>
        </compilerArgs>
        <release>23</release>
    </configuration>
</plugin>
```

```bash
# 실행 시
java --enable-preview -jar myapp.jar
```

API가 변경될 수 있으므로 버전 업그레이드 시 마이그레이션 비용을 고려해야 합니다.

### 2. ThreadLocal과의 호환성

기존 코드에서 `ThreadLocal`을 많이 사용한다면 주의가 필요합니다. Structured Concurrency는 Virtual Thread와 함께 사용하는 경우가 많고, 이때 **ScopedValue**를 `ThreadLocal` 대신 사용하는 것을 권장합니다.

```java
// ThreadLocal 대신 ScopedValue 사용 권장
private static final ScopedValue<SecurityContext> SECURITY_CONTEXT = ScopedValue.newInstance();

ScopedValue.where(SECURITY_CONTEXT, context).run(() -> {
    try (var scope = new StructuredTaskScope.ShutdownOnFailure()) {
        scope.fork(() -> {
            // 자식 스레드에서도 ScopedValue 접근 가능
            SecurityContext ctx = SECURITY_CONTEXT.get();
            return processWithContext(ctx);
        });
        scope.join().throwIfFailed();
    }
});
```

### 3. 복잡한 의존성 체인

작업 간에 의존성이 있는 경우 스코프를 중첩해야 합니다. 이 경우 스코프 설계를 신중하게 해야 합니다.

```java
// 중첩 스코프 - 1단계 결과를 기반으로 2단계 병렬 실행
public FinalResult processWithDependency(String id) throws Exception {
    // 1단계: 기본 데이터 수집
    InitialData initialData;
    try (var scope = new StructuredTaskScope.ShutdownOnFailure()) {
        var t1 = scope.fork(() -> fetchPrimary(id));
        var t2 = scope.fork(() -> fetchSecondary(id));
        scope.join().throwIfFailed();
        initialData = new InitialData(t1.get(), t2.get());
    }
    
    // 2단계: 1단계 결과 기반 처리
    try (var scope = new StructuredTaskScope.ShutdownOnFailure()) {
        var t3 = scope.fork(() -> processWithPrimary(initialData));
        var t4 = scope.fork(() -> processWithSecondary(initialData));
        scope.join().throwIfFailed();
        return new FinalResult(t3.get(), t4.get());
    }
}
```

### 4. 성능 고려사항

- `StructuredTaskScope`는 내부적으로 Virtual Thread를 생성합니다. 매우 빈번하게 호출되는 짧은 작업의 경우 오히려 오버헤드가 있을 수 있습니다.
- CPU-bound 작업보다 I/O-bound 작업에서 효과가 극대화됩니다.
- 기존 `CompletableFuture` 체인보다 가독성은 높지만, 함수형 스타일의 체이닝은 지원하지 않습니다.

---

## 정리

Structured Concurrency는 Java 동시성 프로그래밍의 패러다임 전환을 의미합니다. 핵심 이점을 정리하면:

| 항목 | 기존 방식 | Structured Concurrency |
|------|-----------|------------------------|
| 작업 취소 | 수동 구현 필요 | 스코프 종료 시 자동 처리 |
| 에러 전파 | 복잡한 try-catch 체인 | `throwIfFailed()`로 일관된 처리 |
| 스레드 누수 | 발생 가능 | 구조적으로 방지 |
| 디버깅 | 스택 트레이스 파편화 | 일관된 스코프 구조 |
| 가독성 | 콜백 지옥 | 순차적 코드 스타일 |

아직 Preview 상태라는 점에서 즉시 프로덕션 적용에는 신중함이 필요하지만, 새 프로젝트나 마이크로서비스의 신규 모듈에서는 충분히 도입을 검토할 만합니다. 특히 Virtual Thread와 결합했을 때 발휘되는 **단순하면서도 고성능의 비동기 처리**는 기존 어떤 방식과도 비교할 수 없는 개발 경험을 제공합니다.

Java 생태계가 Project Loom을 통해 오랜 시간 준비해온 동시성 혁신의 퍼즐 조각들—Virtual Thread, ScopedValue, Structured Concurrency—이 점차 완성되어 가고 있습니다. 지금부터 이 변화에 적응하는 것이 앞으로의 Java 백엔드 개발에서 경쟁력이 될 것입니다.