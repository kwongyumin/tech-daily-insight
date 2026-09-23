# Apache Iceberg 데이터 레이크와 CDC 적재의 함정

## 개요

데이터 레이크 아키텍처가 성숙해지면서 Apache Iceberg는 사실상의 오픈 테이블 포맷 표준으로 자리잡았다. ACID 트랜잭션, 스키마 진화, 타임 트래블 같은 강력한 기능 덕분에 많은 팀이 기존 Hive 기반 레이크하우스를 Iceberg로 마이그레이션하고 있다.

여기에 CDC(Change Data Capture)를 결합하면 운영 데이터베이스의 변경사항을 실시간에 가깝게 데이터 레이크에 반영할 수 있다. 이론적으로는 완벽한 조합이다. 그러나 실무에서 이 두 기술을 연결할 때 예상치 못한 함정들이 기다리고 있다.

이 글에서는 CDC 이벤트를 Iceberg 테이블에 적재할 때 마주치는 핵심적인 문제들과, 각 문제에 대한 현실적인 해결책을 살펴본다.

---

## 핵심 개념

### Apache Iceberg의 파일 레이아웃

Iceberg는 데이터를 크게 세 가지 레이어로 관리한다.

```
s3://my-lake/
└── warehouse/
    └── orders/
        ├── metadata/
        │   ├── v1.metadata.json
        │   ├── v2.metadata.json        ← 현재 스냅샷 포인터
        │   └── snap-xxx.avro           ← 매니페스트 리스트
        └── data/
            ├── part-00001.parquet
            └── part-00002.parquet
```

`INSERT`는 새 데이터 파일을 추가하지만, `UPDATE`나 `DELETE`는 기존 파일을 직접 수정하지 않는다. 대신 **삭제 파일(Delete File)** 을 생성하여 논리적으로 레코드를 숨긴다. 이 메커니즘이 CDC 적재 패턴과 맞닿아 있어 복잡성을 만들어낸다.

### CDC 이벤트의 구조

Debezium 기준의 CDC 이벤트는 다음과 같은 형태를 갖는다.

```json
{
  "op": "u",
  "before": { "id": 1, "status": "PENDING", "amount": 100 },
  "after":  { "id": 1, "status": "SHIPPED", "amount": 100 },
  "ts_ms": 1718000000000,
  "source": { "table": "orders", "lsn": 12345 }
}
```

`op` 필드는 `c`(create), `u`(update), `d`(delete), `r`(read/snapshot) 네 가지 값을 갖는다. 이 이벤트들을 Iceberg에 반영하는 방법은 여러 가지가 있으며, 각각 심각한 트레이드오프를 동반한다.

---

## 실전 예제: CDC 적재 패턴과 함정

### 패턴 1: Append-Only (가장 흔한 실수)

가장 단순한 접근법은 모든 CDC 이벤트를 그대로 Iceberg에 Append하는 것이다.

```python
# Spark Structured Streaming 예시
df_cdc = (
    spark.readStream
    .format("kafka")
    .option("subscribe", "dbserver1.public.orders")
    .load()
)

df_parsed = df_cdc.select(
    from_json(col("value").cast("string"), cdc_schema).alias("data")
).select("data.*")

# 단순 Append - 이게 문제의 시작이다
df_parsed.writeStream \
    .format("iceberg") \
    .outputMode("append") \
    .option("path", "s3://my-lake/warehouse/orders_cdc") \
    .option("checkpointLocation", "/tmp/checkpoint") \
    .start()
```

이 방식의 **치명적인 문제점**:

- 테이블에 동일 `id`의 레코드가 여러 버전으로 공존한다.
- 쿼리 시 `ROW_NUMBER()`나 `LAST_VALUE()`로 최신 레코드를 직접 필터링해야 한다.
- 시간이 지날수록 조회 성능이 급격히 저하된다.
- 진정한 의미의 `DELETE`가 반영되지 않는다.

### 패턴 2: Upsert (MoR vs CoW의 선택)

Iceberg v2는 `MERGE INTO` 문법을 통한 Upsert를 지원한다. 이때 핵심은 **Merge-on-Read(MoR)** 와 **Copy-on-Write(CoW)** 중 무엇을 선택하느냐다.

```sql
-- Iceberg MERGE INTO 예시 (Spark SQL)
MERGE INTO prod.orders AS target
USING (
  SELECT
    after.id,
    after.status,
    after.amount,
    op,
    ts_ms
  FROM cdc_staging
  WHERE op IN ('c', 'u', 'd')
) AS source
ON target.id = source.id
WHEN MATCHED AND source.op = 'd' THEN DELETE
WHEN MATCHED AND source.op = 'u' THEN UPDATE SET
  target.status = source.status,
  target.amount = source.amount,
  target.updated_at = source.ts_ms
WHEN NOT MATCHED AND source.op IN ('c', 'r') THEN INSERT
  (id, status, amount, updated_at)
  VALUES (source.id, source.status, source.amount, source.ts_ms);
```

**테이블 속성으로 쓰기 전략 설정**:

```sql
-- Copy-on-Write: 읽기 성능 우선, 쓰기 비용 높음
ALTER TABLE prod.orders SET TBLPROPERTIES (
  'write.update.mode' = 'copy-on-write',
  'write.delete.mode' = 'copy-on-write',
  'write.merge.mode'  = 'copy-on-write'
);

-- Merge-on-Read: 쓰기 성능 우선, 읽기 시 오버헤드 발생
ALTER TABLE prod.orders SET TBLPROPERTIES (
  'write.update.mode' = 'merge-on-read',
  'write.delete.mode' = 'merge-on-read',
  'write.merge.mode'  = 'merge-on-read'
);
```

### 패턴 3: 순서 보장 문제 (Out-of-Order 이벤트)

Kafka 파티션이 여러 개일 때, 또는 재처리 시나리오에서 이벤트 순서가 뒤바뀔 수 있다.

```python
# 잘못된 예: ts_ms만 믿으면 안 된다
def process_event(event):
    if event.op == 'u':
        upsert(event.after)  # 오래된 이벤트가 나중에 도착하면 데이터 오염

# 올바른 예: LSN(Log Sequence Number) 또는 source.ts_ms + 시퀀스 조합
def process_event_safe(event):
    current = get_current_record(event.after['id'])
    if current is None or event.source['lsn'] > current['_lsn']:
        upsert_with_metadata(event.after, lsn=event.source['lsn'])
    else:
        # 오래된 이벤트 무시 또는 별도 로깅
        log_skipped_event(event)
```

Iceberg 테이블에 `_lsn`, `_source_ts` 같은 메타 컬럼을 추가하고 MERGE 조건에 반영하는 것이 핵심이다.

```sql
WHEN MATCHED AND source.lsn > target._lsn AND source.op = 'u' THEN UPDATE SET ...
```

---

## 주의사항 및 트레이드오프

### 함정 1: 소형 파일 폭발 (Small File Problem)

CDC는 본질적으로 고빈도 소량 쓰기 패턴이다. 5분마다 스트리밍 배치를 커밋하면, 하루 288번의 커밋이 발생하고, 각 커밋이 수십 개의 소형 파일을 만든다. 한 달이면 수만 개의 파일이 쌓인다.

**해결책: 정기적인 Compaction**

```python
# Spark에서 Iceberg Compaction 실행
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

# 소형 파일 병합 (target-file-size-bytes: 128MB 권장)
spark.sql("""
  CALL prod.system.rewrite_data_files(
    table => 'prod.orders',
    strategy => 'binpack',
    options => map(
      'target-file-size-bytes', '134217728',
      'min-file-size-bytes',    '67108864',
      'max-concurrent-file-group-rewrites', '5'
    )
  )
""")

# MoR 사용 시 삭제 파일도 함께 정리
spark.sql("""
  CALL prod.system.rewrite_position_delete_files(
    table => 'prod.orders',
    options => map('target-file-size-bytes', '134217728')
  )
""")
```

Compaction은 반드시 주기적으로 자동화해야 한다. 일반적으로 CDC가 활발한 테이블은 **6~24시간 주기**로 실행하는 것이 권장된다.

### 함정 2: 메타데이터 파일 누적

Iceberg는 각 커밋마다 새로운 메타데이터 파일을 생성한다. 이 파일들이 쌓이면 메타데이터 로딩 자체가 병목이 된다.

```sql
-- 스냅샷 만료 처리 (7일 이상 된 스냅샷 정리)
CALL prod.system.expire_snapshots(
  table => 'prod.orders',
  older_than => TIMESTAMP '2024-06-01 00:00:00',
  retain_last => 5
);

-- 고아 파일 정리 (메타데이터와 연결이 끊긴 데이터 파일)
CALL prod.system.remove_orphan_files(
  table => 'prod.orders',
  older_than => TIMESTAMP '2024-06-01 00:00:00'
);
```

**테이블 속성으로 자동 관리 활성화**:

```sql
ALTER TABLE prod.orders SET TBLPROPERTIES (
  'history.expire.min-snapshots-to-keep' = '5',
  'history.expire.max-snapshot-age-ms'   = '604800000'  -- 7일
);
```

### 함정 3: 정확히 한 번(Exactly-Once) 보장의 어려움

Kafka + Spark Streaming 조합에서 장애 발생 시 이벤트가 중복 처리될 수 있다. Iceberg는 원자적 커밋을 보장하지만, Kafka offset 커밋과 Iceberg 커밋이 원자적으로 묶이지는 않는다.

```python
# 멱등성 보장을 위한 접근: 이벤트 ID 기반 중복 제거
def write_with_dedup(df_batch, batch_id):
    # 배치 내 중복 제거 (같은 id, 같은 lsn)
    df_deduped = df_batch \
        .dropDuplicates(["id", "_lsn"]) \
        .filter(col("op") != "r")  # 초기 스냅샷 이벤트 제외

    # MERGE로 멱등성 보장 (같은 lsn이면 덮어써도 결과 동일)
    df_deduped.createOrReplaceTempView("cdc_batch")
    spark.sql("""
        MERGE INTO prod.orders t
        USING cdc_batch s ON t.id = s.id
        WHEN MATCHED AND s._lsn > t._lsn ...
    """)
```

### 트레이드오프 요약

| 항목 | Copy-on-Write | Merge-on-Read |
|---|---|---|
| 쓰기 비용 | 높음 (파일 재작성) | 낮음 (삭제 파일만 추가) |
| 읽기 비용 | 낮음 | 높음 (삭제 파일 조인) |
| Compaction 필요성 | 낮음 | 높음 |
| 실시간 CDC 적합성 | 낮음 | 높음 |
| 분석 쿼리 적합성 | 높음 | 보통 |

CDC 워크로드에서는 **MoR로 쓰고, 주기적으로 CoW로 Compaction하는 하이브리드 전략**이 현실적인 최선이다.

---

## 정리

Apache Iceberg와 CDC의 조합은 강력하지만, 단순히 이벤트를 스트리밍으로 흘려보내는 것만으로는 충분하지 않다.

**핵심 체크리스트**:

1. **Append-Only는 분석용 히스토리 테이블에만** — Upsert가 필요한 소스 미러링에는 반드시 MERGE INTO 사용
2. **LSN 기반 순서 보장** — `ts_ms`만으로 이벤트 순서를 신뢰하지 말 것
3. **MoR + 주기적 Compaction** — CDC 빈도에 따라 Compaction 주기 조정
4. **메타데이터 관리 자동화** — 스냅샷 만료와 고아 파일 정리를 파이프라인에 포함
5. **멱등성 설계** — 재처리를 항상 가정하고 MERGE 조건을 멱등적으로 작성

데이터 레이크는 "쌓아두는 곳"이 아니라 "관리되는 시스템"이다. CDC를 통해 실시간성을 확보하려면, 그에 상응하는 운영 복잡성도 함께 설계에 포함해야 한다. 처음부터 Compaction 전략과 메타데이터 관리 계획 없이 CDC 파이프라인을 구성하면, 수개월 후 조회 성능 저하와 운영 부채로 돌아온다.

Iceberg의 강점은 이 모든 문제를 해결할 수 있는 도구를 이미 제공한다는 점이다. 함정을 인식하고 올바른 도구를 적절한 시점에 사용하는 것, 그것이 데이터 엔지니어링의 핵심이다.
