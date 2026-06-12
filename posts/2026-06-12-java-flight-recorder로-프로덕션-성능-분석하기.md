# Java Flight Recorder로 프로덕션 성능 분석하기

## 개요

프로덕션 환경에서 성능 문제가 발생했을 때, 가장 곤혹스러운 상황 중 하나는 "재현이 안 된다"는 것입니다. 로컬이나 스테이징 환경에서는 멀쩡한데 프로덕션에서만 느리거나, 특정 시간대에만 CPU가 치솟는 경우가 대표적입니다. 이런 상황에서 기존의 프로파일링 도구들은 JVM에 상당한 오버헤드를 유발하기 때문에 실제 트래픽을 받는 서버에 붙이기가 두렵습니다.

**Java Flight Recorder(JFR)** 는 바로 이 문제를 해결하기 위해 설계된 도구입니다. Oracle이 JRockit JVM에서 가져온 이 기능은 JDK 11부터 완전히 오픈소스로 통합되었으며, **1~2% 미만의 오버헤드**로 JVM 내부 이벤트를 지속적으로 기록합니다. 프로덕션에서 안심하고 사용할 수 있는 "블랙박스 레코더"라고 생각하면 됩니다.

이 글에서는 JFR의 핵심 개념부터 실전에서 바로 활용할 수 있는 예제, 그리고 놓치기 쉬운 트레이드오프까지 다룹니다.

---

## 핵심 개념

### JFR 아키텍처

JFR은 JVM 내부에 **링 버퍼(Ring Buffer)** 형태로 이벤트를 쌓습니다. 이벤트가 임계치를 넘으면 디스크에 플러시하거나, 명시적으로 덤프를 떠서 `.jfr` 파일로 저장합니다. JFR이 수집하는 이벤트는 크게 세 가지입니다.

- **JVM 이벤트**: GC 활동, JIT 컴파일, 클래스 로딩, 스레드 상태
- **OS 이벤트**: CPU 사용률, 메모리 페이지, 파일/네트워크 I/O
- **애플리케이션 이벤트**: 커스텀 이벤트 (개발자가 직접 정의)

### JMC (Java Mission Control)

JFR 파일을 분석하는 GUI 도구입니다. JDK와 별도로 다운로드해야 하며, `.jfr` 파일을 열면 **Method Profiling**, **GC 분석**, **스레드 분석**, **힙 메모리 추이** 등을 시각적으로 확인할 수 있습니다. JMC 없이도 `jfr` CLI 명령어로 텍스트 파싱이 가능하지만, 복잡한 분석에는 GUI가 훨씬 효율적입니다.

---

## 실전 예제

### 1. JFR 활성화 방법

#### JVM 시작 옵션으로 활성화

가장 기본적인 방법입니다. 애플리케이션 시작 시 플래그를 추가합니다.

```bash
java -XX:+FlightRecorder \
     -XX:StartFlightRecording=duration=60s,filename=/tmp/myapp.jfr \
     -jar myapp.jar
```

`duration`을 지정하면 해당 시간 동안 기록 후 자동 종료됩니다. 프로덕션에서는 **연속 기록 모드(continuous)** 를 더 많이 씁니다.

```bash
java -XX:+FlightRecorder \
     -XX:StartFlightRecording=name=continuous,\
maxsize=512m,maxage=1h,\
settings=profile,\
filename=/var/log/jfr/myapp.jfr \
     -jar myapp.jar
```

- `maxsize`: 링 버퍼 최대 크기
- `maxage`: 최대 보관 시간 (이 시간을 넘긴 이벤트는 덮어씀)
- `settings`: `default`(낮은 오버헤드) 또는 `profile`(더 상세한 수집)

#### 실행 중인 프로세스에 동적으로 적용 (jcmd)

이미 실행 중인 서버에 영향을 최소화하며 덤프를 뜰 수 있습니다.

```bash
# PID 확인
jps -l

# 기록 시작
jcmd <PID> JFR.start name=profiling duration=120s settings=profile

# 기록 덤프 (파일로 저장)
jcmd <PID> JFR.dump name=profiling filename=/tmp/dump.jfr

# 기록 중지
jcmd <PID> JFR.stop name=profiling

# 현재 기록 상태 확인
jcmd <PID> JFR.check
```

`jcmd`를 이용하면 **배포 없이 프로덕션 서버에서 즉시 프로파일링**을 시작할 수 있습니다. 성능 이슈가 터진 순간 바로 붙여서 2분 치 데이터를 확보하는 것이 실무에서 가장 많이 쓰는 패턴입니다.

---

### 2. 커스텀 이벤트로 비즈니스 로직 추적

JFR의 진짜 강점 중 하나는 **애플리케이션 레벨의 커스텀 이벤트**를 정의할 수 있다는 것입니다. 예를 들어 특정 API 호출의 레이턴시를 JFR 이벤트로 기록하면, JVM 내부 이벤트와 함께 상관 분석이 가능합니다.

```java
import jdk.jfr.*;

@Name("com.mycompany.OrderProcessed")
@Label("Order Processed")
@Category({"Application", "Order"})
@Description("Tracks order processing latency")
public class OrderProcessedEvent extends Event {

    @Label("Order ID")
    private String orderId;

    @Label("Customer ID")
    private long customerId;

    @Label("Item Count")
    private int itemCount;

    @Label("Total Amount")
    @DataAmount
    private long totalAmountKRW;

    // Getter, Setter
    public void setOrderId(String orderId) { this.orderId = orderId; }
    public void setCustomerId(long customerId) { this.customerId = customerId; }
    public void setItemCount(int itemCount) { this.itemCount = itemCount; }
    public void setTotalAmountKRW(long totalAmountKRW) { this.totalAmountKRW = totalAmountKRW; }
}
```

```java
@Service
public class OrderService {

    public OrderResult processOrder(OrderRequest request) {
        OrderProcessedEvent event = new OrderProcessedEvent();
        event.begin(); // 타이머 시작

        try {
            // 실제 주문 처리 로직
            OrderResult result = doProcessOrder(request);

            event.setOrderId(result.getOrderId());
            event.setCustomerId(request.getCustomerId());
            event.setItemCount(request.getItems().size());
            event.setTotalAmountKRW(result.getTotalAmount());

            return result;
        } finally {
            event.commit(); // 이벤트 기록 (duration 자동 계산)
        }
    }
}
```

`event.begin()`과 `event.commit()` 사이의 시간이 자동으로 `duration` 필드에 기록됩니다. JMC에서 이 커스텀 이벤트를 필터링하면 "주문 처리가 느린 시점에 GC가 발생했는가?", "특정 고객의 주문만 느린가?" 같은 질문에 바로 답할 수 있습니다.

---

### 3. Spring Boot 환경에서 자동화된 JFR 덤프

장애 상황에서 자동으로 JFR 덤프를 남기려면 다음과 같이 설정할 수 있습니다.

```java
@Component
public class JfrDumpOnHighLatency {

    private static final Logger log = LoggerFactory.getLogger(JfrDumpOnHighLatency.class);
    private final AtomicBoolean isDumping = new AtomicBoolean(false);

    @Scheduled(fixedDelay = 30000) // 30초마다 체크
    public void checkAndDumpIfNeeded() {
        // 예: 응답 시간 메트릭이 임계치를 넘으면 덤프
        double p99Latency = getP99Latency(); // Micrometer 등에서 가져옴

        if (p99Latency > 2000 && isDumping.compareAndSet(false, true)) {
            log.warn("P99 latency {}ms exceeded threshold. Triggering JFR dump.", p99Latency);
            triggerJfrDump();
        }
    }

    private void triggerJfrDump() {
        try {
            String filename = String.format("/var/log/jfr/dump-%s.jfr",
                    LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss")));

            // JFR API를 직접 사용하는 방법
            for (Recording recording : FlightRecorder.getFlightRecorder().getRecordings()) {
                if (recording.getState() == RecordingState.RUNNING) {
                    recording.dump(Path.of(filename));
                    log.info("JFR dump saved to {}", filename);
                    break;
                }
            }
        } catch (Exception e) {
            log.error("Failed to dump JFR recording", e);
        } finally {
            isDumping.set(false);
        }
    }

    private double getP99Latency() {
        // Micrometer Timer에서 P99 가져오는 예시
        // return meterRegistry.get("http.server.requests").timer().percentile(0.99) / 1_000_000.0;
        return 0; // placeholder
    }
}
```

---

### 4. CLI로 빠르게 분석하기

JMC 없이 터미널에서 빠르게 분석이 필요할 때는 `jfr` 명령어를 활용합니다.

```bash
# .jfr 파일의 이벤트 목록 요약
jfr summary /tmp/dump.jfr

# 특정 이벤트 타입만 출력
jfr print --events jdk.GarbageCollection /tmp/dump.jfr

# CPU 샘플링 이벤트 출력 (메서드 핫스팟 분석)
jfr print --events jdk.ExecutionSample /tmp/dump.jfr

# JSON 형식으로 파싱
jfr print --json --events jdk.GarbageCollection /tmp/dump.jfr | jq '.recording.events[] | .values'

# 커스텀 이벤트 확인
jfr print --events "com.mycompany.OrderProcessed" /tmp/dump.jfr
```

---

## 주의사항 및 트레이드오프

### ⚠️ 오버헤드는 낮지만 0은 아니다

JFR 공식 문서는 "1% 미만 오버헤드"를 주장하지만, 이는 `default` 설정 기준입니다. `profile` 설정이나 커스텀 이벤트를 **매우 높은 빈도의 경로**에 넣으면 오버헤드가 늘어납니다. 특히 커스텀 이벤트는 `shouldCommit()` 체크를 활용해 불필요한 객체 생성을 줄이는 것이 좋습니다.

```java
// shouldCommit()으로 필터링하면 이벤트가 비활성화됐을 때 오버헤드 제거
if (event.shouldCommit()) {
    event.setOrderId(result.getOrderId());
    event.commit();
}
```

### ⚠️ 디스크 공간 관리

`maxsize`와 `maxage`를 반드시 설정하세요. 설정 없이 연속 기록하면 디스크를 모두 소진합니다. 프로덕션에서는 보통 `maxsize=256m~1g`, `maxage=1h~6h` 범위로 설정하고, 별도 로그 로테이션 정책과 함께 관리합니다.

### ⚠️ 보안 및 민감 정보

커스텀 이벤트에 PII(개인식별정보)나 민감한 비즈니스 데이터를 그대로 넣지 마세요. `.jfr` 파일은 암호화되지 않습니다. 고객 ID, 주문 금액 같은 데이터를 기록할 때는 마스킹 또는 해시 처리를 고려하세요.

### ⚠️ JDK 버전 호환성

- **JDK 8 (update 262+)**: JFR 사용 가능하지만 상용 기능 라이선스 이슈 있음 (OpenJDK 8u272+ 에서는 무료)
- **JDK 11+**: 완전 오픈소스, 추가 플래그 없이 사용 가능
- **JDK 14+**: `jdk.jfr.consumer` API가 강화되어 스트리밍 처리 가능

JDK 14 이상이라면 **JFR 이벤트 스트리밍**을 통해 실시간 모니터링 파이프라인도 구축할 수 있습니다.

```java
// JDK 14+ 스트리밍 API
try (var es = new RecordingStream()) {
    es.enable("jdk.GarbageCollection").withStackTrace();
    es.onEvent("jdk.GarbageCollection", event -> {
        System.out.println("GC occurred: " + event.getDuration());
    });
    es.startAsync();
}
```

### ⚠️ JFR != 트레이싱 도구

JFR은 **시스템 레벨 인사이트**에 탁월하지만, 분산 트레이싱(OpenTelemetry, Zipkin)을 대체하지는 않습니다. 마이크로서비스 환경에서 서비스 간 지연 분석은 여전히 트레이싱 도구가 필요합니다. 두 도구를 함께 쓰는 것이 이상적입니다.

---

## 정리

| 항목 | 내용 |
|------|------|
| **오버헤드** | `default` 설정 시 1% 미만 |
| **활성화 방법** | JVM 플래그, `jcmd` 동적 적용 |
| **분석 도구** | JMC (GUI), `jfr` CLI |
| **커스텀 이벤트** | `jdk.jfr.Event` 상속으로 구현 |
| **권장 JDK** | JDK 11 이상 (14+에서 스트리밍 지원) |

Java Flight Recorder는 **프로덕션에서 항상 켜두는** 것을 전제로 설계된 도구입니다. "문제가 생기면 그때 켜야지"가 아니라, 평소에 연속 기록 모드로 운영하다가 이슈 발생 시 즉시 덤프를 분석하는 것이 올바른 운용 방식입니다.

APM 도구(Datadog, New Relic 등)와 함께 사용하면 높은 수준의 메트릭은 APM으로 빠르게 파악하고, 근본 원인 분석(RCA)은 JFR로 깊이 파고드는 이상적인 관찰 가능성(Observability) 스택을 구축할 수 있습니다.

다음 장애 포스트모템에서 "재현이 안 돼서..."라는 말 대신, JFR 덤프 파일을 꺼내 드는 경험을 해보시길 권장합니다.