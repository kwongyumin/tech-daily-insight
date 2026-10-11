# Flink 상태 저장소 RocksDB 튜닝과 실시간 집계

## 개요

Apache Flink는 대규모 스트림 처리에서 사실상 표준으로 자리 잡은 프레임워크다. 그 핵심 강점 중 하나는 **상태(State) 관리**인데, 특히 대용량 상태를 다룰 때 `RocksDB`를 백엔드로 사용하는 `EmbeddedRocksDBStateBackend`가 핵심 역할을 한다.

문제는 RocksDB가 기본 설정만으로는 실시간 집계 워크로드에서 예상치 못한 성능 병목을 유발한다는 점이다. Write Stall, 과도한 디스크 I/O, GC 압박 등이 대표적이다. 이 글에서는 Flink에서 RocksDB를 실무 수준으로 튜닝하고, 실시간 집계를 안정적으로 운영하기 위한 전략을 구체적인 설정과 코드 예제를 통해 다룬다.

---

## 핵심 개념

### RocksDB StateBackend 동작 원리

Flink의 상태 백엔드는 크게 두 가지로 나뉜다.

- **HashMapStateBackend**: JVM Heap에 상태를 저장. 빠르지만 메모리 한계 명확.
- **EmbeddedRocksDBStateBackend**: 로컬 디스크(+ 일부 메모리 캐시)에 상태 저장. 수십~수백 GB의 상태도 처리 가능.

RocksDB는 **LSM-Tree(Log-Structured Merge Tree)** 기반의 키-값 저장소다. 쓰기는 먼저 메모리의 MemTable에 기록되고, 일정 크기가 차면 디스크의 SSTable로 플러시된다. 이후 백그라운드 Compaction이 레벨별로 데이터를 병합한다.

```
Write → MemTable (RAM) → L0 SSTable → L1 → L2 → ... (Compaction)
Read  → Block Cache → MemTable → SSTable (Bloom Filter 활용)
```

실시간 집계 워크로드는 **소수의 키에 대한 빈번한 읽기/쓰기**가 특징이므로, 이 흐름의 각 단계를 최적화하는 것이 핵심이다.

### Write Stall의 이해

Write Stall은 RocksDB가 Compaction을 따라가지 못할 때 쓰기 요청을 의도적으로 늦추는 메커니즘이다. Flink 작업에서 처리 지연이 갑자기 급등한다면 Write Stall을 먼저 의심해야 한다.

주요 트리거 조건:
- L0 SSTable 파일 수가 `level0_slowdown_writes_trigger` 초과
- 보류 중인 Compaction 바이트가 `soft_pending_compaction_bytes_limit` 초과
- MemTable 수가 `max_write_buffer_number` 초과

---

## 실전 예제

### 1. RocksDB 옵션 튜닝 설정

Flink에서는 `RocksDBOptionsFactory`를 구현해 RocksDB 내부 옵션을 세밀하게 제어할 수 있다.

```java
public class CustomRocksDBOptionsFactory implements ConfigurableRocksDBOptionsFactory {

    @Override
    public DBOptions createDBOptions(DBOptions currentOptions, Collection<AutoCloseable> handlesToClose) {
        return currentOptions
            // 백그라운드 Compaction 스레드 수 (CPU 코어 수의 50% 권장)
            .setMaxBackgroundJobs(4)
            // Write Buffer Manager로 전체 MemTable 메모리 상한 설정
            .setWriteBufferManager(createWriteBufferManager(handlesToClose))
            // 통계 수집 활성화 (성능 분석용, 운영 중엔 오버헤드 감안)
            .setStatistics(createStatistics(handlesToClose));
    }

    @Override
    public ColumnFamilyOptions createColumnOptions(
            ColumnFamilyOptions currentOptions,
            Collection<AutoCloseable> handlesToClose) {

        // 압축 알고리즘: LZ4가 CPU/압축률 균형에서 일반적으로 우수
        final List<CompressionType> compressionPerLevel = Arrays.asList(
            CompressionType.NO_COMPRESSION,  // L0: 빠른 읽기 우선
            CompressionType.NO_COMPRESSION,  // L1
            CompressionType.LZ4_COMPRESSION, // L2 이상: 공간 절약
            CompressionType.LZ4_COMPRESSION,
            CompressionType.ZSTD_COMPRESSION // 가장 하위 레벨: 최대 압축
        );

        BlockBasedTableConfig tableConfig = new BlockBasedTableConfig()
            // Block Cache: 읽기 성능의 핵심. 전체 메모리의 30~50% 권장
            .setBlockCacheSize(512 * 1024 * 1024L) // 512MB
            .setBlockSize(16 * 1024L)              // 16KB (기본 4KB보다 큰 값이 집계에 유리)
            .setFilterPolicy(new BloomFilter(10, false)) // Bloom Filter로 불필요한 디스크 읽기 차단
            .setCacheIndexAndFilterBlocks(true)    // 인덱스/필터를 Block Cache에 포함
            .setPinL0FilterAndIndexBlocksInCache(true);

        return currentOptions
            .setTableFormatConfig(tableConfig)
            .setWriteBufferSize(64 * 1024 * 1024L)  // MemTable 크기: 64MB
            .setMaxWriteBufferNumber(3)               // 동시에 유지할 최대 MemTable 수
            .setMinWriteBufferNumberToMerge(2)        // 플러시 전 병합할 최소 MemTable 수
            .setLevel0FileNumCompactionTrigger(4)     // L0 파일 4개 시 Compaction 시작
            .setLevel0SlowdownWritesTrigger(16)       // Write Stall 경고 임계값
            .setLevel0StopWritesTrigger(32)           // Write 완전 중단 임계값
            .setMaxBytesForLevelBase(256 * 1024 * 1024L) // L1 최대 크기: 256MB
            .setCompressionPerLevel(compressionPerLevel);
    }

    private WriteBufferManager createWriteBufferManager(Collection<AutoCloseable> handlesToClose) {
        // 모든 Column Family의 MemTable 총합 상한을 설정해 OOM 방지
        Cache sharedCache = new LRUCache(1024 * 1024 * 1024L); // 1GB
        handlesToClose.add(sharedCache);
        WriteBufferManager manager = new WriteBufferManager(
            256 * 1024 * 1024L, // 256MB 상한
            sharedCache
        );
        handlesToClose.add(manager);
        return manager;
    }
}
```

### 2. Flink Job에 커스텀 옵션 적용

```java
StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();

EmbeddedRocksDBStateBackend rocksDBBackend = new EmbeddedRocksDBStateBackend(true);
rocksDBBackend.setRocksDBOptions(new CustomRocksDBOptionsFactory());

// 체크포인트 설정 (Incremental Checkpoint 활성화가 중요)
env.setStateBackend(rocksDBBackend);
env.getCheckpointConfig().setCheckpointInterval(60_000L);         // 1분
env.getCheckpointConfig().setMinPauseBetweenCheckpoints(30_000L); // 최소 간격
env.getCheckpointConfig().setCheckpointTimeout(120_000L);
env.getCheckpointConfig().setMaxConcurrentCheckpoints(1);
```

### 3. 실시간 집계 구현 예제

1분 윈도우 내 이벤트 수와 합계를 집계하는 실전 예제다.

```java
// 이벤트 스트림 정의
DataStream<UserEvent> eventStream = env
    .addSource(new KafkaSource<>(...))
    .keyBy(UserEvent::getUserId);

// 집계 결과 POJO
@Data
@AllArgsConstructor
public static class AggResult {
    private String userId;
    private long eventCount;
    private double totalAmount;
    private Instant windowEnd;
}

// Sliding Window 집계 (1분 윈도우, 30초 슬라이드)
DataStream<AggResult> aggregated = eventStream
    .window(SlidingEventTimeWindows.of(Time.minutes(1), Time.seconds(30)))
    .aggregate(new EventAggregateFunction(), new WindowResultFunction());

// AggregateFunction: 상태를 누적하는 핵심 로직
public static class EventAggregateFunction
        implements AggregateFunction<UserEvent, AggResult, AggResult> {

    @Override
    public AggResult createAccumulator() {
        return new AggResult("", 0L, 0.0, null);
    }

    @Override
    public AggResult add(UserEvent event, AggResult acc) {
        return new AggResult(
            event.getUserId(),
            acc.getEventCount() + 1,
            acc.getTotalAmount() + event.getAmount(),
            null
        );
    }

    @Override
    public AggResult getResult(AggResult acc) {
        return acc;
    }

    @Override
    public AggResult merge(AggResult a, AggResult b) {
        return new AggResult(
            a.getUserId(),
            a.getEventCount() + b.getEventCount(),
            a.getTotalAmount() + b.getTotalAmount(),
            null
        );
    }
}

// ProcessWindowFunction: 윈도우 메타정보 추가
public static class WindowResultFunction
        extends ProcessWindowFunction<AggResult, AggResult, String, TimeWindow> {

    @Override
    public void process(String key, Context ctx, Iterable<AggResult> elements, Collector<AggResult> out) {
        AggResult result = elements.iterator().next();
        result.setWindowEnd(Instant.ofEpochMilli(ctx.window().getEnd()));
        out.collect(result);
    }
}
```

### 4. 상태 크기 모니터링

RocksDB 상태 크기를 Flink 메트릭으로 노출하면 이상 징후를 조기에 탐지할 수 있다.

```java
// TaskManager 로그 또는 메트릭 시스템에서 확인
// flink.taskmanager.job.task.operator.rocksdb.estimate-num-keys
// flink.taskmanager.job.task.operator.rocksdb.size-all-mem-tables
// flink.taskmanager.job.task.operator.rocksdb.block-cache-hit
// flink.taskmanager.job.task.operator.rocksdb.block-cache-miss

// flink-conf.yaml 또는 코드에서 메트릭 Reporter 설정
configuration.setString("metrics.reporter.promgateway.class",
    "org.apache.flink.metrics.prometheus.PrometheusPushGatewayReporter");
```

---

## 주의사항 및 트레이드오프

### Block Cache vs. MemTable 메모리 배분

Block Cache와 MemTable은 모두 JVM 외부 메모리(Off-Heap)를 사용한다. TaskManager의 `taskmanager.memory.managed.size` 설정 내에서 경쟁하므로, 집계 패턴에 따라 전략적으로 배분해야 한다.

| 워크로드 특성 | Block Cache 비중 | MemTable 비중 |
|---|---|---|
| 읽기 빈번 (point lookup) | 높게 (60~70%) | 낮게 |
| 쓰기 빈번 (집계 업데이트) | 낮게 (30~40%) | 높게 |
| 균형형 | 50% | 50% |

### Compaction과 처리 지연의 관계

백그라운드 Compaction이 과도하게 리소스를 점유하면 처리 스레드와 I/O 경쟁이 발생한다. `setMaxBackgroundJobs`를 무작정 높이는 것은 역효과다. Compaction 스레드 수는 실제 디스크 처리량(throughput) 한계를 측정한 뒤 결정해야 한다.

### Incremental Checkpoint의 함정

`EmbeddedRocksDBStateBackend(true)`로 증분 체크포인트를 활성화하면 체크포인트 크기는 줄지만, 복구 시간은 증가할 수 있다. SSTable 파일이 누적될수록 복구 시 다운로드해야 할 파일이 많아지기 때문이다. 주기적으로 `savepoint`를 찍어 베이스라인을 갱신하는 운영 정책이 필요하다.

### Timer 남용 주의

실시간 집계에서 `ProcessFunction`의 타이머를 과도하게 등록하면 타이머 상태 자체가 RocksDB에 쌓여 성능이 저하된다. 가능하면 Window API를 활용하고, 커스텀 타이머는 필요한 최소한으로 제한한다.

### Late Data 처리 전략

이벤트 타임 기반 집계에서 늦게 도착하는 데이터는 정확도와 처리 비용 사이의 트레이드오프다. `allowedLateness`를 길게 설정하면 윈도우 상태를 오래 유지해야 해서 RocksDB 상태 크기가 증가한다.

```java
.window(TumblingEventTimeWindows.of(Time.minutes(1)))
.allowedLateness(Time.seconds(30))   // 30초까지만 허용 (비용과 정확도 균형)
.sideOutputLateData(lateOutputTag)   // 초과 지연 데이터는 사이드 아웃풋으로 별도 처리
```

---

## 정리

Flink와 RocksDB를 이용한 실시간 집계는 강력하지만, 기본 설정만으로는 운영 환경에서 반드시 한계에 부딪힌다. 핵심 포인트를 다시 정리하면 다음과 같다.

1. **Block Cache와 MemTable 크기**를 워크로드 특성에 맞게 명시적으로 설정한다.
2. **Bloom Filter**를 활성화해 불필요한 디스크 읽기를 차단한다.
3. **Write Stall 임계값**(`level0_slowdown_writes_trigger`)을 기본값보다 여유 있게 설정하고, 메트릭으로 모니터링한다.
4. **Incremental Checkpoint**는 활성화하되, savepoint 기반 베이스라인 갱신 정책을 함께 수립한다.
5. **Compaction 스레드 수**는 디스크 처리 한계를 측정한 뒤 조정하며, 과도한 설정은 피한다.
6. **Late Data 허용 범위**는 RocksDB 상태 크기와 직결되므로 비즈니스 요구사항과 함께 신중히 결정한다.

RocksDB 튜닝은 한 번에 끝나지 않는다. 워크로드가 변할 때마다 메트릭을 분석하고 점진적으로 파라미터를 조정하는 운영 문화가 안정적인 실시간 파이프라인의 기반이 된다.
