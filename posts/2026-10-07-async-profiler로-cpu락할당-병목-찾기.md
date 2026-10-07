# async-profiler로 CPU·락·할당 병목 찾기

## 개요

프로덕션 환경에서 성능 문제가 발생했을 때 "어디서 시간이 소비되는가"를 정확히 파악하는 것은 생각보다 어렵다. JVM 위에서 동작하는 Spring 애플리케이션이라면 더욱 그렇다. GC 압력, 락 경합, 핫한 메서드의 CPU 점유 등 원인이 다양하기 때문이다.

전통적인 JVM 프로파일러(JVisualVM, YourKit 등)는 **Safe Point 편향(safepoint bias)** 문제를 가지고 있다. JVM은 스레드를 Safe Point에서만 멈출 수 있어, 실제로 핫한 코드가 Safe Point 사이에 있다면 샘플에 잡히지 않는다.

**async-profiler**는 이 문제를 해결한다. Linux의 `perf_events`와 `AsyncGetCallTrace` API를 조합해 Safe Point와 무관하게 스택 트레이스를 수집한다. CPU·락·메모리 할당 세 가지 프로파일링 모드를 지원하며, 프로덕션에 붙여도 오버헤드가 낮은 것이 특징이다.

이 글에서는 async-profiler를 실무에서 활용하는 방법을 CPU·락·할당 병목 각각의 관점에서 살펴본다.

---

## 핵심 개념

### Safe Point 편향이란?

```
일반 프로파일러: 스레드 정지(Safe Point) → 스택 덤프 → 재개
                       ↑ 여기서만 샘플링 가능

async-profiler: OS 시그널(SIGPROF) → 언제든 스택 덤프
               ↑ Safe Point 무관
```

Safe Point는 JIT 컴파일된 코드에서 특정 지점(루프 백엣지, 메서드 진입 등)에만 삽입된다. 루프 내부의 긴 연산은 Safe Point 사이에 위치할 수 있어 기존 프로파일러로는 잡히지 않는다.

### async-profiler의 세 가지 모드

| 모드 | 이벤트 | 용도 |
|------|--------|------|
| `cpu` | `perf_events` SIGPROF | CPU 사용량이 높은 메서드 탐지 |
| `lock` | Java monitor 이벤트 | 락 경합 지점 탐지 |
| `alloc` | TLAB 할당 이벤트 | GC 압력 유발 객체 탐지 |

### 출력 형식

- **Flame Graph (SVG)**: 시각적으로 핫스팟 파악
- **JFR (Java Flight Recorder)**: IntelliJ, JMC와 연동
- **텍스트**: 자동화 파이프라인에 활용

---

## 실전 예제

### 환경 설정

```bash
# async-profiler 다운로드 (Linux x64 기준)
wget https://github.com/async-profiler/async-profiler/releases/download/v3.0/async-profiler-3.0-linux-x64.tar.gz
tar -xzf async-profiler-3.0-linux-x64.tar.gz
cd async-profiler-3.0-linux-x64

# kernel 파라미터 설정 (perf_events 권한)
echo 1 | sudo tee /proc/sys/kernel/perf_event_paranoid
echo 0 | sudo tee /proc/sys/kernel/kptr_restrict
```

### Spring Boot 애플리케이션에 붙이기

```bash
# 실행 중인 JVM PID 확인
jps -l

# 예: PID가 12345인 경우
PID=12345

# CPU 프로파일링 (30초)
./asprof -e cpu -d 30 -f /tmp/cpu-flamegraph.html $PID

# 락 프로파일링 (30초)
./asprof -e lock -d 30 -f /tmp/lock-flamegraph.html $PID

# 할당 프로파일링 (30초)
./asprof -e alloc -d 30 -f /tmp/alloc-flamegraph.html $PID
```

Spring Boot 시작 시 에이전트로 붙이는 방법도 있다:

```bash
java -agentpath:/path/to/libasyncProfiler.so=start,event=cpu,file=/tmp/profile.html \
     -jar your-application.jar
```

`application.yml`에서 제어하려면 JVM 옵션으로 전달하는 것이 일반적이다:

```yaml
# docker-compose.yml 예시
services:
  app:
    environment:
      JAVA_OPTS: >-
        -agentpath:/opt/async-profiler/libasyncProfiler.so=
        start,event=cpu,interval=10ms,file=/tmp/cpu.html
```

---

### 1. CPU 병목 찾기

CPU Flame Graph에서 넓고 평평한 막대를 찾는다. 이것이 핫스팟이다.

**예제: 불필요한 직렬화 비용**

```java
@RestController
@RequestMapping("/api/products")
public class ProductController {

    private final ObjectMapper objectMapper;
    private final ProductRepository repository;

    // 문제: 매 요청마다 JSON → 객체 → JSON 왕복
    @GetMapping
    public ResponseEntity<String> getProducts() throws JsonProcessingException {
        List<Product> products = repository.findAll();
        
        // CPU Flame Graph에서 이 부분이 넓게 잡힘
        String json = objectMapper.writeValueAsString(products);
        return ResponseEntity.ok(json);
    }
}
```

Flame Graph에서 `ObjectMapper.writeValueAsString` 계열 스택이 전체 CPU의 30% 이상을 차지한다면, 아래와 같이 개선을 고려한다:

```java
@GetMapping
public ResponseEntity<List<ProductResponse>> getProducts() {
    // Spring이 내부적으로 HttpMessageConverter를 통해 직렬화
    // ObjectMapper 재사용 + 필요한 필드만 포함
    return ResponseEntity.ok(
        repository.findAll().stream()
            .map(ProductResponse::from)
            .toList()
    );
}

// DTO로 필요한 필드만 노출
public record ProductResponse(Long id, String name, BigDecimal price) {
    public static ProductResponse from(Product p) {
        return new ProductResponse(p.getId(), p.getName(), p.getPrice());
    }
}
```

---

### 2. 락 경합 찾기

락 Flame Graph는 스레드가 모니터를 기다리는 시간 기준으로 스택을 집계한다.

**예제: 공유 캐시의 동기화 병목**

```java
@Service
public class PriceCacheService {

    // 문제: 단순 HashMap에 synchronized
    private final Map<Long, BigDecimal> cache = new HashMap<>();

    // Flame Graph에서 이 메서드의 monitor-enter가 상위에 잡힘
    public synchronized BigDecimal getPrice(Long productId) {
        return cache.computeIfAbsent(productId, this::fetchFromDB);
    }

    public synchronized void updatePrice(Long productId, BigDecimal price) {
        cache.put(productId, price);
    }
    
    private BigDecimal fetchFromDB(Long id) { /* ... */ }
}
```

`lock` 모드 Flame Graph에서 `PriceCacheService.getPrice`가 넓게 잡힌다면:

```java
@Service
public class PriceCacheService {

    // 개선 1: ConcurrentHashMap 사용
    private final Map<Long, BigDecimal> cache = new ConcurrentHashMap<>();

    public BigDecimal getPrice(Long productId) {
        // computeIfAbsent는 ConcurrentHashMap에서 세그먼트 락 사용
        return cache.computeIfAbsent(productId, this::fetchFromDB);
    }

    // 개선 2: Caffeine 캐시로 교체 (더 정교한 락 전략)
    // @Bean
    // public Cache<Long, BigDecimal> priceCache() {
    //     return Caffeine.newBuilder()
    //         .maximumSize(10_000)
    //         .expireAfterWrite(5, TimeUnit.MINUTES)
    //         .build();
    // }
}
```

**`@Transactional`의 숨겨진 락 경합**

Flame Graph에서 `AbstractPlatformTransactionManager`나 JDBC 커넥션 풀(`HikariPool`) 관련 스택이 락 상위에 잡히는 경우도 흔하다:

```java
// 문제: 불필요하게 넓은 트랜잭션 범위
@Transactional
public void processOrder(OrderRequest request) {
    validateOrder(request);          // 외부 API 호출 포함 - 트랜잭션 불필요
    enrichProductInfo(request);      // 외부 API 호출 - 트랜잭션 불필요
    Order order = saveOrder(request); // 여기만 트랜잭션 필요
    sendNotification(order);         // 이벤트 발행 - 트랜잭션 불필요
}

// 개선: 트랜잭션 범위 최소화
public void processOrder(OrderRequest request) {
    validateOrder(request);
    enrichProductInfo(request);
    Order order = saveOrderTransactional(request); // @Transactional 분리
    sendNotification(order);
}

@Transactional
protected Order saveOrderTransactional(OrderRequest request) {
    return orderRepository.save(Order.from(request));
}
```

---

### 3. 메모리 할당 병목 찾기

`alloc` 모드는 TLAB(Thread Local Allocation Buffer) 할당을 샘플링한다. GC가 자주 발생하거나 Old Gen이 빠르게 차오르는 경우 유용하다.

**예제: 루프 내 불필요한 객체 생성**

```java
@Service
public class ReportService {

    // Flame Graph에서 String[], char[] 등이 상위에 집계됨
    public List<String> generateReport(List<Order> orders) {
        List<String> lines = new ArrayList<>();
        for (Order order : orders) {
            // 문제: 루프마다 String 연결로 임시 객체 다량 생성
            String line = "Order#" + order.getId() 
                + " | " + order.getStatus() 
                + " | " + order.getAmount().toString();
            lines.add(line);
        }
        return lines;
    }
}
```

할당 Flame Graph에서 `char[]`나 `byte[]` 생성이 집중되어 있다면:

```java
public List<String> generateReport(List<Order> orders) {
    // 개선 1: StringBuilder 재사용
    StringBuilder sb = new StringBuilder(64);
    List<String> lines = new ArrayList<>(orders.size()); // 용량 사전 지정
    
    for (Order order : orders) {
        sb.setLength(0); // 초기화 후 재사용
        sb.append("Order#").append(order.getId())
          .append(" | ").append(order.getStatus())
          .append(" | ").append(order.getAmount());
        lines.add(sb.toString());
    }
    return lines;
}

// 개선 2: String.formatted() 또는 formatted record 활용
// Java 17+에서는 텍스트 블록이나 formatted 메서드가 내부적으로 최적화됨
public List<String> generateReportV2(List<Order> orders) {
    return orders.stream()
        .map(o -> "Order#%d | %s | %s"
            .formatted(o.getId(), o.getStatus(), o.getAmount()))
        .toList();
}
```

---

### JFR 형식으로 IntelliJ에서 분석하기

```bash
# JFR 형식으로 수집
./asprof -e cpu -d 60 -f /tmp/profile.jfr $PID

# 또는 alloc + cpu 동시에 (collapsed 형식)
./asprof -e cpu,alloc -d 30 -f /tmp/combined.jfr $PID
```

IntelliJ IDEA의 **Profiler** 탭에서 JFR 파일을 열면 Flame Graph, Call Tree, Method List를 GUI로 탐색할 수 있다. JetBrains IDE 없이도 [JDK Mission Control(JMC)](https://adoptium.net/jmc/)로 분석 가능하다.

---

## 주의사항 및 트레이드오프

### 프로덕션 적용 시 고려사항

**오버헤드**
- CPU 모드: 일반적으로 1~5% 수준. `interval`을 늘리면 오버헤드 감소, 해상도도 감소
- alloc 모드: TLAB 할당만 샘플링하므로 모든 할당을 잡지는 못함. 큰 객체(TLAB 외부)는 별도로 잡힘

```bash
# 오버헤드를 줄이려면 샘플링 간격 조정
./asprof -e cpu -i 20ms -d 30 -f /tmp/cpu.html $PID  # 기본 10ms → 20ms
```

**컨테이너 환경 제약**
- Docker에서는 `perf_events` 접근이 기본 차단됨
- `--cap-add SYS_ADMIN` 또는 `--privileged` 옵션 필요 (보안 검토 필수)
- Kubernetes에서는 DaemonSet으로 프로파일러를 배포하거나, sidecar 방식 고려

```yaml
# Kubernetes Pod 보안 컨텍스트 예시
securityContext:
  capabilities:
    add:
      - SYS_ADMIN  # 프로덕션에서는 최소 권한 원칙 검토 필요
```

**Alpine Linux 주의**
- Alpine은 `musl libc`를 사용해 `perf_events` 지원이 제한적
- `cpu` 이벤트 대신 `ctimer` 이벤트 사용 권장

```bash
./asprof -e ctimer -d 30 -f /tmp/cpu.html $PID  # Alpine 환경
```

### 샘플링 vs 계측

async-profiler는 **샘플링 기반**이므로 짧은 시간에 드물게 호출되는 메서드는 Flame Graph에 나타나지 않을 수 있다. 이 경우 APM 도구(Micrometer + Prometheus, Pinpoint 등)와 병행해 사용하는 것이 좋다.

### 해석 시 함정

- Flame Graph 너비는 **해당 스택이 샘플에 등장한 횟수** 비율이다. 절대적인 실행 시간이 아님
- `self` 시간(스택 최상단)과 `total` 시간을 구분해서 봐야 함
- I/O 대기 중인 스레드는 CPU 모드에서 잡히지 않음 → wall-clock 모드(`-e wall`) 활용

```bash
# wall-clock 모드: CPU 사용 여부 무관, 스레드가 살아있는 시간 기준 샘플링
./asprof -e wall -t -d 30 -f /tmp/wall.html $PID
# -t: 스레드별로 분리해서 보기
```

---

## 정리

async-profiler는 Safe Point 편향 없이 실제 JVM 동작을 관찰할 수 있는 강력한 도구다. 핵심 사용 흐름을 정리하면:

| 증상 | 프로파일링 모드 | 분석 포인트 |
|------|----------------|-------------|
| CPU 사용률이 높음 | `cpu` | Flame Graph 넓은 막대, 직렬화/연산 집중 구간 |
| 응답 지연, 스레드 대기 | `lock` | `monitor-enter` 스택, 트랜잭션 범위 |
| GC 빈도 높음, Old Gen 증가 | `alloc` | char[], byte[], 컬렉션 생성 패턴 |
| I/O 포함 전체 지연 | `wall` | 스레드별 대기 구간 |

가장 중요한 것은 **문제가 발생한 시점에 수집하는 것**이다. 부하 테스트(k6, Gatling 등) 중에 async-profiler를 동시에 실행해 재현 환경에서 데이터를 수집하거나, 프로덕션에서 낮은 오버헤드로 짧게 수집하는 방식 모두 유효하다.

Flame Graph를 처음 보면 낯설 수 있지만, "가장 넓은 막대"를 찾고 그 위의 호출 스택을 따라 올라가는 패턴만 익혀도 대부분의 병목은 빠르게 찾을 수 있다. 코드 리뷰와 직관에 의존하던 성능 분석을 데이터 기반으로 전환하는 첫 걸음으로 async-profiler를 적극 활용해보길 권장한다.
