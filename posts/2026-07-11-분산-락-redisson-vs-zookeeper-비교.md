# 분산 락 Redisson vs ZooKeeper 비교

## 개요

멀티 인스턴스 환경에서 공유 자원에 대한 동시 접근을 제어하는 것은 분산 시스템 설계의 핵심 과제 중 하나다. 단일 서버에서 `synchronized`나 `ReentrantLock`으로 해결하던 문제가, 수평 확장된 환경에서는 더 이상 통하지 않는다. 이를 해결하기 위해 **분산 락(Distributed Lock)** 이 등장했고, 실무에서 가장 많이 사용되는 두 가지 선택지가 바로 **Redisson**과 **ZooKeeper**다.

두 솔루션 모두 분산 락을 구현할 수 있지만, 내부 동작 원리와 적합한 유스케이스가 다르다. 이 글에서는 각각의 핵심 개념과 실전 예제를 비교하며, 실무에서 어떤 상황에 어떤 선택이 적합한지 살펴본다.

---

## 핵심 개념

### Redisson 기반 분산 락

Redisson은 Redis를 기반으로 동작하는 Java 클라이언트 라이브러리로, 단순한 캐시 클라이언트를 넘어 다양한 분산 자료구조와 락 메커니즘을 제공한다.

**동작 원리:**

- Redis의 `SET NX PX` 명령어와 Lua 스크립트를 활용해 원자적(atomic) 락 획득을 보장한다.
- **Watchdog 메커니즘**: 락을 획득한 클라이언트가 TTL 내에 작업을 완료하지 못할 경우, 백그라운드 스레드(Watchdog)가 자동으로 TTL을 갱신(renewal)하여 락이 중간에 해제되는 것을 방지한다.
- **Pub/Sub 기반 대기**: 락을 획득하지 못한 클라이언트는 Redis Pub/Sub 채널을 구독하여 락 해제 이벤트를 기다린다. 이는 불필요한 폴링을 줄여 성능을 높인다.
- **재진입 가능(Reentrant)**: 동일 스레드에서 중복으로 락을 획득할 수 있다.

**Redis Cluster 환경의 문제 (RedLock):**

단일 Redis 노드 장애 시 락의 안전성이 보장되지 않는다. 이를 위해 Martin Kleppmann이 지적한 RedLock 알고리즘의 한계를 인지해야 하며, Redisson은 RedLock 구현을 제공하지만 완벽하지 않다는 점을 알고 써야 한다.

---

### ZooKeeper 기반 분산 락

ZooKeeper는 Apache의 분산 코디네이션 서비스로, **ZAB(ZooKeeper Atomic Broadcast) 프로토콜**을 기반으로 강력한 일관성(Strong Consistency)을 보장한다.

**동작 원리:**

- **Ephemeral Sequential Node**: 락을 획득하려는 각 클라이언트는 ZooKeeper의 특정 경로 아래에 임시(ephemeral) + 순차(sequential) 노드를 생성한다.
- 가장 낮은 시퀀스 번호를 가진 노드가 락을 획득한다.
- 락을 획득하지 못한 클라이언트는 자신보다 낮은 번호의 노드에 **Watch**를 걸어 삭제 이벤트를 기다린다.
- 클라이언트가 비정상 종료되면 세션이 만료되면서 Ephemeral 노드가 자동 삭제되므로, **데드락(Deadlock)** 위험이 원천적으로 차단된다.

**Curator 라이브러리**: ZooKeeper의 낮은 수준 API를 직접 다루는 것은 복잡하므로, 실무에서는 Apache Curator 라이브러리를 사용해 분산 락을 구현하는 것이 일반적이다.

---

## 실전 예제

### Redisson 분산 락 구현

**의존성 추가 (build.gradle)**

```groovy
implementation 'org.redisson:redisson-spring-boot-starter:3.27.0'
```

**application.yml 설정**

```yaml
spring:
  redis:
    host: localhost
    port: 6379

redisson:
  single-server-config:
    address: "redis://localhost:6379"
    connection-pool-size: 10
    connection-minimum-idle-size: 5
```

**분산 락 서비스 구현**

```java
@Service
@RequiredArgsConstructor
@Slf4j
public class RedissonLockService {

    private final RedissonClient redissonClient;

    /**
     * Redisson 분산 락을 활용한 재고 감소 처리
     */
    public void decreaseStock(Long productId, int quantity) {
        String lockKey = "lock:product:" + productId;
        RLock lock = redissonClient.getLock(lockKey);

        try {
            // waitTime: 락 획득 대기 시간 (5초)
            // leaseTime: 락 점유 시간 (3초, -1이면 Watchdog 동작)
            boolean acquired = lock.tryLock(5, 3, TimeUnit.SECONDS);

            if (!acquired) {
                throw new RuntimeException("락 획득 실패: productId=" + productId);
            }

            log.info("락 획득 성공: {}", lockKey);
            // 실제 비즈니스 로직 수행
            processStockDecrease(productId, quantity);

        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("락 획득 중 인터럽트 발생", e);
        } finally {
            // 락을 획득한 스레드만 해제 가능 (isHeldByCurrentThread 체크)
            if (lock.isHeldByCurrentThread()) {
                lock.unlock();
                log.info("락 해제: {}", lockKey);
            }
        }
    }

    private void processStockDecrease(Long productId, int quantity) {
        // DB 업데이트 로직
        log.info("재고 감소 처리: productId={}, quantity={}", productId, quantity);
    }
}
```

**AOP 기반 분산 락 추상화**

```java
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
public @interface DistributedLock {
    String key();
    long waitTime() default 5L;
    long leaseTime() default 3L;
    TimeUnit timeUnit() default TimeUnit.SECONDS;
}

@Aspect
@Component
@RequiredArgsConstructor
@Slf4j
public class DistributedLockAspect {

    private final RedissonClient redissonClient;

    @Around("@annotation(distributedLock)")
    public Object lock(ProceedingJoinPoint joinPoint, DistributedLock distributedLock) throws Throwable {
        String lockKey = distributedLock.key();
        RLock lock = redissonClient.getLock(lockKey);

        try {
            boolean acquired = lock.tryLock(
                distributedLock.waitTime(),
                distributedLock.leaseTime(),
                distributedLock.timeUnit()
            );
            if (!acquired) {
                throw new RuntimeException("분산 락 획득 실패: " + lockKey);
            }
            return joinPoint.proceed();
        } finally {
            if (lock.isHeldByCurrentThread()) {
                lock.unlock();
            }
        }
    }
}

// 사용 예시
@DistributedLock(key = "lock:order:#{#orderId}")
public void processOrder(Long orderId) {
    // 비즈니스 로직
}
```

---

### ZooKeeper (Curator) 분산 락 구현

**의존성 추가 (build.gradle)**

```groovy
implementation 'org.apache.curator:curator-recipes:5.6.0'
implementation 'org.apache.curator:curator-framework:5.6.0'
```

**ZooKeeper 클라이언트 설정**

```java
@Configuration
public class ZooKeeperConfig {

    @Bean
    public CuratorFramework curatorFramework() {
        RetryPolicy retryPolicy = new ExponentialBackoffRetry(1000, 3);
        CuratorFramework client = CuratorFrameworkFactory.builder()
            .connectString("localhost:2181")
            .sessionTimeoutMs(60000)
            .connectionTimeoutMs(15000)
            .retryPolicy(retryPolicy)
            .namespace("myapp")  // 네임스페이스로 경로 격리
            .build();
        client.start();
        return client;
    }
}
```

**분산 락 서비스 구현**

```java
@Service
@RequiredArgsConstructor
@Slf4j
public class ZooKeeperLockService {

    private final CuratorFramework curatorFramework;

    public void decreaseStock(Long productId, int quantity) {
        String lockPath = "/locks/product/" + productId;
        InterProcessMutex lock = new InterProcessMutex(curatorFramework, lockPath);

        try {
            // 최대 10초 대기
            if (!lock.acquire(10, TimeUnit.SECONDS)) {
                throw new RuntimeException("ZooKeeper 락 획득 실패: " + lockPath);
            }

            log.info("ZooKeeper 락 획득 성공: {}", lockPath);
            processStockDecrease(productId, quantity);

        } catch (Exception e) {
            throw new RuntimeException("락 처리 중 예외 발생", e);
        } finally {
            try {
                if (lock.isAcquiredInThisProcess()) {
                    lock.release();
                    log.info("ZooKeeper 락 해제: {}", lockPath);
                }
            } catch (Exception e) {
                log.error("ZooKeeper 락 해제 실패", e);
            }
        }
    }

    /**
     * 읽기/쓰기 락이 필요한 경우 (다중 읽기, 단일 쓰기)
     */
    public void readOperation(Long resourceId) throws Exception {
        String lockPath = "/locks/resource/" + resourceId;
        InterProcessReadWriteLock rwLock =
            new InterProcessReadWriteLock(curatorFramework, lockPath);
        InterProcessMutex readLock = rwLock.readLock();

        readLock.acquire();
        try {
            // 읽기 작업 수행
        } finally {
            readLock.release();
        }
    }

    private void processStockDecrease(Long productId, int quantity) {
        log.info("재고 감소 처리: productId={}, quantity={}", productId, quantity);
    }
}
```

---

## 주의사항 및 트레이드오프

### 성능 비교

| 항목 | Redisson (Redis) | ZooKeeper (Curator) |
|------|-----------------|---------------------|
| 락 획득 속도 | 매우 빠름 (메모리 기반) | 상대적으로 느림 (디스크 동기화) |
| 처리량 | 높음 | 중간 |
| 네트워크 왕복 | 2~3회 | 더 많음 |
| 적합한 TPS | 수천~수만 | 수백~수천 |

### 일관성 및 안전성

**Redisson의 주의사항:**

1. **Clock Skew 문제**: Redis는 시간 기반 TTL에 의존하므로 서버 간 시간 차이가 락 안전성에 영향을 줄 수 있다.
2. **RedLock의 한계**: Redisson이 제공하는 `RedissonRedLock`은 Martin Kleppmann의 비판처럼, GC Pause나 네트워크 지연으로 인해 두 클라이언트가 동시에 락을 보유하는 상황이 이론적으로 가능하다. 금융 트랜잭션처럼 절대적 안전성이 필요한 경우엔 추가적인 **fencing token** 전략을 함께 사용해야 한다.
3. **Watchdog 오용**: `leaseTime`을 명시하면 Watchdog이 비활성화된다. 의도치 않은 락 만료를 막으려면 `-1`을 사용하거나 적절한 leaseTime을 설정해야 한다.

**ZooKeeper의 주의사항:**

1. **세션 만료 처리**: 네트워크 파티션 상황에서 세션이 만료되면 Ephemeral 노드가 삭제되어 락이 해제된다. 이 경우 진행 중인 작업이 롤백되지 않을 수 있어 멱등성(idempotency) 설계가 필수다.
2. **ZooKeeper 자체의 부하**: 모든 락 획득/해제가 ZooKeeper 리더 노드를 통해 처리되므로, 락 경합이 심한 환경에서 ZooKeeper가 병목이 될 수 있다.
3. **Herd Effect(양떼 효과)**: Watch를 잘못 설정하면 락 해제 시 모든 대기 클라이언트가 동시에 깨어나는 문제가 발생한다. Curator의 `InterProcessMutex`는 이를 내부적으로 방지한다.

### 운영 복잡도

- **Redisson**: 대부분의 팀이 이미 Redis를 캐시로 운영 중이라면 별도 인프라 없이 도입 가능하다. 운영 부담이 낮다.
- **ZooKeeper**: 별도 ZooKeeper 클러스터(최소 3노드 권장)를 운영해야 한다. Kafka나 HBase를 이미 사용 중이라면 기존 ZooKeeper를 재활용할 수 있다.

---

## 정리

| 비교 항목 | Redisson | ZooKeeper |
|-----------|----------|-----------|
| 기반 기술 | Redis | ZAB 프로토콜 |
| 일관성 모델 | 최종 일관성 (조건부) | 강한 일관성 |
| 성능 | 고성능 | 중간 |
| 운영 복잡도 | 낮음 | 높음 |
| 재진입 락 | 기본 지원 | Curator로 지원 |
| 읽기/쓰기 락 | 지원 | 지원 |
| 적합한 환경 | 고TPS, 캐시 인프라 보유 | 강한 일관성, 코디네이션 필요 |

**선택 가이드:**

- **Redisson을 선택해야 할 때**: 이미 Redis 인프라가 있고, 높은 TPS가 요구되며, 짧은 시간의 락이 주 사용 사례일 때. 일반적인 이커머스, API 서버의 중복 요청 방지 등에 적합하다.

- **ZooKeeper를 선택해야 할 때**: 강한 일관성이 절대적으로 필요하고, 이미 ZooKeeper 클러스터를 운영 중이며, 분산 코디네이션(리더 선출, 서비스 디스커버리 등)을 함께 활용할 때 유리하다.

분산 락은 도입 자체보다 **실패 시나리오를 얼마나 철저히 고려했는가**가 더 중요하다. 어떤 도구를 선택하든 멱등성 설계, 락 만료 후 처리, 예외 상황에서의 롤백 전략을 함께 설계해야 실무에서 안전하게 사용할 수 있다.