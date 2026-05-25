# Java 21 Virtual Threads (Project Loom) 실전 적용

## 개요

Java 21이 LTS(Long-Term Support) 버전으로 출시되면서 Project Loom의 핵심 기능인 **Virtual Threads**가 정식으로 도입되었습니다. 수년간 Preview 단계를 거친 끝에 프로덕션 환경에서 사용할 수 있게 된 이 기능은, Java 생태계의 동시성 프로그래밍 패러다임을 근본적으로 바꿀 잠재력을 가지고 있습니다.

기존의 Platform Thread(OS 스레드와 1:1로 매핑되는 전통적인 Java 스레드)는 생성 비용이 크고 메모리를 많이 소비하기 때문에, 높은 동시 요청을 처리하기 위해 Spring WebFlux나 Reactor 같은 리액티브 프로그래밍 모델이 주목받아 왔습니다. Virtual Thread는 이 문제를 근본적으로 해결하면서도 기존의 동기식 코드 스타일을 그대로 유지할 수 있게 해줍니다.

이 글에서는 Virtual Thread의 핵심 개념을 이해하고, Spring Boot 환경에서 실전에 바로 적용할 수 있는 예제를 통해 실무 도입 시 고려해야 할 사항들을 깊이 있게 다룹니다.

---

## 핵심 개념

### Platform Thread vs Virtual Thread

**Platform Thread**는 OS의 커널 스레드와 1:1로 매핑됩니다. 생성 비용이 크고(약 1MB 스택 메모리), 컨텍스트 스위칭 비용도 상당합니다. 일반적으로 수천 개 이상 생성하면 시스템 자원이 고갈됩니다.

**Virtual Thread**는 JVM이 관리하는 경량 스레드입니다. 소수의 Platform Thread(Carrier Thread라고 부릅니다) 위에서 수백만 개의 Virtual Thread가 스케줄링됩니다. I/O 블로킹 시 Carrier Thread를 점유하지 않고 `park` 상태로 전환되어 다른 Virtual Thread가 실행될 수 있습니다.

```
[Virtual Thread 1] ──┐
[Virtual Thread 2] ──┤──▶ [Carrier Thread (Platform Thread)] ──▶ [OS Thread]
[Virtual Thread N] ──┘
```

### Structured Concurrency (구조적 동시성)

Java 21에서 Preview로 포함된 `StructuredTaskScope`는 Virtual Thread와 함께 사용하면 더욱 강력합니다. 작업의 생명주기를 명확하게 관리하고 에러 처리를 간결하게 만들어줍니다.

### Pinning (피닝) 문제

Virtual Thread가 `synchronized` 블록이나 `native` 메서드 실행 중에 블로킹될 경우, Carrier Thread도 함께 블로킹됩니다. 이를 **Pinning**이라고 하며, Virtual Thread의 핵심 장점을 무력화할 수 있는 주요 주의사항입니다.

---

## 실전 예제

### 1. Spring Boot 3.x에서 Virtual Thread 활성화

Spring Boot 3.2 이상에서는 단 한 줄의 설정으로 Virtual Thread를 활성화할 수 있습니다.

```yaml
# application.yml
spring:
  threads:
    virtual:
      enabled: true
```

또는 Java Config로 직접 설정할 수도 있습니다.

```java
@Configuration
public class VirtualThreadConfig {

    @Bean
    public TomcatProtocolHandlerCustomizer<?> protocolHandlerCustomizer() {
        return protocolHandler -> {
            protocolHandler.setExecutor(
                Executors.newVirtualThreadPerTaskExecutor()
            );
        };
    }
}
```

### 2. Virtual Thread 기반 ExecutorService 활용

외부 API를 여러 건 병렬 호출하는 시나리오를 예로 들어보겠습니다. 기존 스레드 풀 방식과 Virtual Thread 방식을 비교합니다.

```java
@Service
@RequiredArgsConstructor
public class ProductAggregationService {

    private final ProductClient productClient;
    private final InventoryClient inventoryClient;
    private final ReviewClient reviewClient;

    // Virtual Thread 기반 병렬 집계
    public ProductDetailResponse aggregateProductDetail(Long productId) {
        try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
            Future<ProductInfo> productFuture =
                executor.submit(() -> productClient.getProduct(productId));

            Future<InventoryInfo> inventoryFuture =
                executor.submit(() -> inventoryClient.getInventory(productId));

            Future<List<Review>> reviewFuture =
                executor.submit(() -> reviewClient.getReviews(productId));

            return ProductDetailResponse.of(
                productFuture.get(),
                inventoryFuture.get(),
                reviewFuture.get()
            );
        } catch (InterruptedException | ExecutionException e) {
            Thread.currentThread().interrupt();
            throw new AggregationException("상품 정보 집계 실패", e);
        }
    }
}
```

### 3. StructuredTaskScope를 활용한 고급 패턴

`StructuredTaskScope`를 사용하면 하나의 작업이라도 실패하면 나머지를 취소하는 로직을 명확하게 작성할 수 있습니다.

```java
@Service
public class OrderProcessingService {

    public OrderResult processOrder(OrderRequest request) {
        // ShutdownOnFailure: 하나라도 실패하면 나머지 취소
        try (var scope = new StructuredTaskScope.ShutdownOnFailure()) {

            Subtask<PaymentResult> paymentTask =
                scope.fork(() -> processPayment(request.getPaymentInfo()));

            Subtask<InventoryResult> inventoryTask =
                scope.fork(() -> reserveInventory(request.getItems()));

            Subtask<ShippingResult> shippingTask =
                scope.fork(() -> scheduleShipping(request.getAddress()));

            // 모든 작업 완료 또는 실패 대기
            scope.join().throwIfFailed();

            return OrderResult.success(
                paymentTask.get(),
                inventoryTask.get(),
                shippingTask.get()
            );

        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new OrderProcessingException("주문 처리 인터럽트", e);
        } catch (ExecutionException e) {
            throw new OrderProcessingException("주문 처리 실패: " + e.getCause().getMessage(), e);
        }
    }

    // ShutdownOnSuccess: 하나라도 성공하면 나머지 취소 (경쟁 패턴)
    public String fetchFromFastestSource(String key) {
        try (var scope = new StructuredTaskScope.ShutdownOnSuccess<String>()) {

            scope.fork(() -> primaryCache.get(key));
            scope.fork(() -> secondaryCache.get(key));
            scope.fork(() -> database.get(key));  // 폴백

            scope.join();
            return scope.result();

        } catch (InterruptedException | ExecutionException e) {
            Thread.currentThread().interrupt();
            throw new CacheFetchException("데이터 조회 실패", e);
        }
    }
}
```

### 4. Virtual Thread와 데이터베이스 연결 풀 튜닝

Virtual Thread 환경에서는 데이터베이스 커넥션 풀 설정이 매우 중요합니다. 수백만 개의 Virtual Thread가 생성될 수 있지만, DB 커넥션은 여전히 제한적입니다.

```yaml
# application.yml - HikariCP 설정 (Virtual Thread 환경)
spring:
  datasource:
    hikari:
      # Virtual Thread는 많은 요청을 처리하지만 DB 커넥션은 제한
      # 커넥션 풀 크기는 DB 성능 기준으로 설정 (CPU 코어 수 * 2 + 유효 스핀들 수)
      maximum-pool-size: 20
      minimum-idle: 5
      # Virtual Thread에서 대기 시간이 길어질 수 있으므로 적절히 조정
      connection-timeout: 3000
      idle-timeout: 600000
      keepalive-time: 30000
```

```java
@Configuration
public class DataSourceConfig {

    // Virtual Thread 환경에서 Semaphore로 DB 접근 제어
    @Bean
    public DatabaseAccessLimiter databaseAccessLimiter(
            @Value("${spring.datasource.hikari.maximum-pool-size:20}") int poolSize) {
        return new DatabaseAccessLimiter(poolSize);
    }
}

@Component
@RequiredArgsConstructor
public class DatabaseAccessLimiter {

    private final Semaphore semaphore;

    public DatabaseAccessLimiter(int maxConcurrency) {
        this.semaphore = new Semaphore(maxConcurrency);
    }

    public <T> T executeWithLimit(Supplier<T> dbOperation) {
        try {
            semaphore.acquire();
            try {
                return dbOperation.get();
            } finally {
                semaphore.release();
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new DatabaseAccessException("DB 접근 대기 중 인터럽트");
        }
    }
}
```

### 5. 부하 테스트로 효과 확인하기

```java
@SpringBootTest
class VirtualThreadPerformanceTest {

    @Test
    void compareThreadPerformance() throws InterruptedException {
        int taskCount = 10_000;

        // Platform Thread Pool 방식
        long platformTime = measureExecutionTime(
            Executors.newFixedThreadPool(200),
            taskCount
        );

        // Virtual Thread 방식
        long virtualTime = measureExecutionTime(
            Executors.newVirtualThreadPerTaskExecutor(),
            taskCount
        );

        System.out.printf("Platform Thread: %dms%n", platformTime);
        System.out.printf("Virtual Thread:  %dms%n", virtualTime);
        System.out.printf("성능 향상: %.1fx%n", (double) platformTime / virtualTime);
    }

    private long measureExecutionTime(ExecutorService executor, int taskCount)
            throws InterruptedException {
        CountDownLatch latch = new CountDownLatch(taskCount);
        long start = System.currentTimeMillis();

        try (executor) {
            for (int i = 0; i < taskCount; i++) {
                executor.submit(() -> {
                    try {
                        // I/O 바운드 작업 시뮬레이션
                        Thread.sleep(Duration.ofMillis(100));
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                    } finally {
                        latch.countDown();
                    }
                });
            }
            latch.await();
        }

        return System.currentTimeMillis() - start;
    }
}
```

---

## 주의사항 및 트레이드오프

### ⚠️ Pinning 문제 감지 및 해결

`synchronized` 키워드 사용 시 Pinning이 발생합니다. JVM 옵션으로 감지할 수 있습니다.

```bash
# Pinning 발생 시 스택 트레이스 출력
-Djdk.tracePinnedThreads=full

# 또는 short (메서드 정보만 출력)
-Djdk.tracePinnedThreads=short
```

해결책은 `synchronized`를 `ReentrantLock`으로 교체하는 것입니다.

```java
// Before: Pinning 발생 가능
public synchronized void processData(Data data) {
    // 내부에서 블로킹 I/O 발생 시 Carrier Thread도 블로킹
    repository.save(data);
}

// After: Virtual Thread 친화적
private final ReentrantLock lock = new ReentrantLock();

public void processData(Data data) {
    lock.lock();
    try {
        repository.save(data);
    } finally {
        lock.unlock();
    }
}
```

### ⚠️ ThreadLocal 사용 주의

Virtual Thread는 수백만 개가 생성될 수 있으므로, `ThreadLocal`에 대용량 객체를 저장하면 메모리 문제가 발생합니다. Java 21에서는 `ScopedValue`(Preview)가 대안으로 제시되고 있습니다.

```java
// ScopedValue 사용 예 (Java 21 Preview)
public class RequestContext {
    public static final ScopedValue<UserInfo> CURRENT_USER = ScopedValue.newInstance();

    public void handleRequest(UserInfo user, Runnable handler) {
        ScopedValue.where(CURRENT_USER, user).run(handler);
    }

    public UserInfo getCurrentUser() {
        return CURRENT_USER.get();
    }
}
```

### ⚠️ CPU 바운드 작업에는 적합하지 않음

Virtual Thread는 **I/O 바운드** 작업에서 탁월한 성능을 발휘합니다. CPU를 집중적으로 사용하는 연산(이미지 처리, 암호화, 대용량 계산 등)에서는 ForkJoinPool이나 Platform Thread를 사용하는 것이 더 적합합니다.

| 작업 유형 | 권장 방식 |
|-----------|-----------|
| HTTP 요청/응답 처리 | Virtual Thread ✅ |
| DB 쿼리 및 결과 처리 | Virtual Thread ✅ |
| 파일 I/O | Virtual Thread ✅ |
| 대용량 수치 연산 | ForkJoinPool / Platform Thread |
| 영상/이미지 처리 | ForkJoinPool / Platform Thread |

### ⚠️ 서드파티 라이브러리 호환성

일부 구버전 라이브러리가 `synchronized`를 광범위하게 사용할 경우 Pinning 문제가 발생할 수 있습니다. 특히 JDBC 드라이버, 구버전 Hibernate(6.2 이전) 등은 확인이 필요합니다. Hibernate 6.2+, Spring Framework 6.x, Tomcat 10.x 등은 Virtual Thread를 충분히 고려하여 업데이트되었습니다.

---

## 정리

Java 21 Virtual Thread는 다음과 같은 상황에서 강력한 선택지가 됩니다.

- **높은 동시성이 필요한 I/O 바운드 서비스**: REST API 서버, 마이크로서비스 간 통신
- **리액티브 코드의 복잡성을 피하고 싶을 때**: 기존 동기식 코딩 스타일 유지
- **대규모 트래픽을 적은 자원으로 처리하고 싶을 때**: 스레드 풀 튜닝 없이 높은 처리량 달성

핵심 요약:
1. Spring Boot 3.2+에서 `spring.threads.virtual.enabled=true` 한 줄로 시작할 수 있습니다.
2. `synchronized` → `ReentrantLock` 교체로 Pinning 문제를 예방합니다.
3. DB 커넥션 풀은 Virtual Thread 수와 무관하게 DB 성능 기준으로 적절히 제한합니다.
4. CPU 바운드 작업과 I/O 바운드 작업을 구분하여 적절한 실행 모델을 선택합니다.
5. `StructuredTaskScope`로 복잡한 병렬 작업의 생명주기를 명확하게 관리합니다.

Virtual Thread는 은탄환이 아닙니다. 하지만 I/O 바운드 워크로드가 주를 이루는 일반적인 백엔드 서비스에서는 리액티브 프로그래밍의 복잡함 없이 높은 처리량을 달성할 수 있는 매우 현실적인 솔루션입니다. 지금 바로 개발/스테이징 환경에 적용하고 성능 지표를 비교해보세요.