# ClickHouse 실시간 분석 쿼리 최적화 전략

## 개요

ClickHouse는 Yandex가 개발한 컬럼 지향(Column-Oriented) OLAP 데이터베이스로, 초당 수십억 행을 처리할 수 있는 압도적인 분석 성능으로 많은 기업의 실시간 분석 인프라에 자리 잡고 있습니다. Cloudflare, Uber, Criteo 같은 글로벌 기업들이 로그 분석, 사용자 행동 분석, 광고 성과 집계 등에 활용하고 있으며, 국내에서도 대규모 이벤트 로그 처리 시스템에 빠르게 도입되고 있습니다.

그러나 ClickHouse의 잠재력을 제대로 끌어내려면 단순히 SQL을 날리는 것 이상이 필요합니다. 테이블 엔진 선택, 파티션 설계, 인덱스 전략, 쿼리 작성 패턴 등 여러 레이어에서의 최적화가 맞물려야 실제 운영 환경에서 원하는 성능을 얻을 수 있습니다.

이 포스팅에서는 실무에서 ClickHouse를 운영하면서 체득한 쿼리 최적화 전략을 핵심 개념부터 실전 예제까지 구체적으로 다루겠습니다.

---

## 핵심 개념

### 1. 컬럼 지향 스토리지의 이해

ClickHouse는 데이터를 행 단위가 아닌 컬럼 단위로 저장합니다. `SELECT user_id, event_type FROM events`를 실행할 때 나머지 컬럼(`timestamp`, `properties`, `session_id` 등)은 디스크에서 아예 읽지 않습니다. 이는 분석 쿼리에서 전체 테이블 스캔처럼 보이더라도 실제 I/O는 필요한 컬럼에만 집중된다는 의미입니다.

**핵심 시사점**: `SELECT *`는 절대 금지입니다. 필요한 컬럼만 명시적으로 지정하세요.

### 2. MergeTree 엔진과 Primary Key

ClickHouse의 기본 엔진인 `MergeTree`는 데이터를 **파트(Part)** 단위로 관리하고, 백그라운드에서 지속적으로 머지합니다. Primary Key는 데이터의 물리적 정렬 순서를 결정하며, 이 순서가 쿼리 성능에 직접적인 영향을 줍니다.

```sql
CREATE TABLE events
(
    event_date   Date,
    event_time   DateTime,
    user_id      UInt64,
    event_type   LowCardinality(String),
    properties   String
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(event_date)
ORDER BY (event_type, user_id, event_time)  -- Primary Key이자 정렬 키
PRIMARY KEY (event_type, user_id);          -- 인덱스 범위 (정렬 키의 prefix)
```

`ORDER BY`로 지정한 컬럼이 **Sparse Index**의 기준이 됩니다. ClickHouse는 기본적으로 8,192행마다 하나의 인덱스 마크(mark)를 생성하고, 이 마크를 통해 관련 없는 그래뉼(granule)을 스킵합니다.

### 3. 파티션 프루닝 (Partition Pruning)

파티션은 물리적으로 데이터를 분리하는 단위입니다. `WHERE event_date = '2024-01-15'` 조건이 있다면 해당 파티션만 스캔합니다. 파티션 설계는 쿼리 패턴에 맞게 결정해야 합니다.

- **월별 파티션**: 장기 보관 + 월 단위 분석
- **일별 파티션**: 단기 고빈도 쿼리, 빠른 TTL 관리
- **시간별 파티션**: 극히 빈번한 소규모 조회 (파트 수 폭증 주의)

---

## 실전 예제

### 예제 1: 정렬 키 설계와 쿼리 매칭

가장 흔한 실수 중 하나는 쿼리 WHERE 절의 컬럼 순서와 ORDER BY가 맞지 않는 경우입니다.

```sql
-- 테이블 정의
CREATE TABLE user_events
(
    event_date  Date,
    user_id     UInt64,
    event_type  LowCardinality(String),
    event_time  DateTime,
    duration_ms UInt32
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(event_date)
ORDER BY (user_id, event_type, event_time);

-- ✅ 좋은 쿼리: 정렬 키 prefix와 일치
SELECT 
    event_type,
    count() AS cnt,
    avg(duration_ms) AS avg_duration
FROM user_events
WHERE user_id = 12345
  AND event_date >= '2024-01-01'
  AND event_date < '2024-02-01'
GROUP BY event_type;

-- ❌ 나쁜 쿼리: 정렬 키 첫 번째 컬럼 건너뜀 → 전체 스캔
SELECT count()
FROM user_events
WHERE event_type = 'purchase'
  AND event_date = '2024-01-15';
```

두 번째 쿼리를 개선하려면 `event_type`을 ORDER BY의 앞쪽으로 옮기거나, `SKIP INDEX`를 추가해야 합니다.

### 예제 2: 스킵 인덱스(Skip Index) 활용

ClickHouse의 스킵 인덱스는 특정 컬럼에 대한 보조 인덱스로, 불필요한 그래뉼을 스킵하는 데 도움을 줍니다.

```sql
-- Bloom Filter 인덱스 추가 (고카디널리티 컬럼 필터링)
ALTER TABLE user_events
    ADD INDEX idx_event_type event_type TYPE bloom_filter(0.01) GRANULARITY 4;

-- set 인덱스 (저카디널리티 컬럼, 최대 100개 유니크 값)
ALTER TABLE user_events
    ADD INDEX idx_duration duration_ms TYPE minmax GRANULARITY 1;

-- 인덱스 적용을 위해 데이터 재구성
ALTER TABLE user_events MATERIALIZE INDEX idx_event_type;

-- 스킵 인덱스가 실제로 사용되는지 확인
EXPLAIN indexes = 1
SELECT count()
FROM user_events
WHERE event_type = 'purchase';
```

`minmax` 인덱스는 수치형 범위 쿼리에, `bloom_filter`는 특정 값 포함 여부 필터링에 효과적입니다.

### 예제 3: Materialized View로 집계 사전 계산

실시간 대시보드처럼 반복적으로 같은 집계를 계산하는 경우, Materialized View를 사용해 쓰기 시점에 미리 집계를 유지하는 전략이 강력합니다.

```sql
-- 집계 결과를 저장할 타겟 테이블 (AggregatingMergeTree 사용)
CREATE TABLE daily_event_stats
(
    event_date  Date,
    event_type  LowCardinality(String),
    user_cnt    AggregateFunction(uniq, UInt64),
    event_cnt   AggregateFunction(count, UInt64),
    avg_dur     AggregateFunction(avg, UInt32)
)
ENGINE = AggregatingMergeTree()
PARTITION BY toYYYYMM(event_date)
ORDER BY (event_date, event_type);

-- Materialized View: 원본 테이블에 INSERT될 때마다 자동 집계
CREATE MATERIALIZED VIEW mv_daily_event_stats
TO daily_event_stats
AS
SELECT
    event_date,
    event_type,
    uniqState(user_id)     AS user_cnt,
    countState()           AS event_cnt,
    avgState(duration_ms)  AS avg_dur
FROM user_events
GROUP BY event_date, event_type;

-- 집계 결과 조회 시 Merge 함수 사용
SELECT
    event_date,
    event_type,
    uniqMerge(user_cnt)   AS unique_users,
    countMerge(event_cnt) AS total_events,
    avgMerge(avg_dur)     AS avg_duration
FROM daily_event_stats
WHERE event_date >= today() - 30
GROUP BY event_date, event_type
ORDER BY event_date, total_events DESC;
```

이 패턴을 사용하면 원본 테이블 수백억 행을 스캔하는 대신, 집계 테이블의 수백만 행만 읽어 결과를 반환합니다.

### 예제 4: 쿼리 병렬성과 설정 튜닝

ClickHouse는 쿼리 수준에서 다양한 설정을 조절할 수 있습니다.

```sql
-- 쿼리 설정 예시
SELECT
    toStartOfHour(event_time) AS hour,
    event_type,
    count() AS cnt
FROM user_events
WHERE event_date >= today() - 7
GROUP BY hour, event_type
ORDER BY hour, cnt DESC
SETTINGS
    max_threads = 16,                    -- 병렬 처리 스레드 수
    max_memory_usage = 10000000000,      -- 최대 메모리 10GB
    max_bytes_before_external_group_by = 5000000000, -- GROUP BY 외부 정렬 임계값
    prefer_localhost_replica = 1;        -- 로컬 레플리카 우선 사용
```

```sql
-- 느린 쿼리 분석을 위한 EXPLAIN
EXPLAIN PIPELINE
SELECT user_id, count() AS cnt
FROM user_events
WHERE event_type = 'click'
  AND event_date = today()
GROUP BY user_id
HAVING cnt > 10;
```

### 예제 5: JOIN 최적화

ClickHouse에서 JOIN은 오른쪽 테이블을 메모리에 올리는 방식으로 동작합니다. 큰 테이블을 오른쪽에 두면 메모리 부족이 발생할 수 있습니다.

```sql
-- ✅ 작은 테이블을 오른쪽에 (Dictionary 테이블 활용도 검토)
SELECT
    e.event_type,
    u.user_segment,
    count() AS cnt
FROM user_events e
INNER JOIN (
    SELECT user_id, user_segment
    FROM users
    WHERE is_active = 1
) u ON e.user_id = u.user_id
WHERE e.event_date = today()
GROUP BY e.event_type, u.user_segment;

-- 반복 JOIN이 필요한 경우 Dictionary 사용 (메모리 로드, 초고속 조회)
CREATE DICTIONARY user_segment_dict
(
    user_id     UInt64,
    user_segment String
)
PRIMARY KEY user_id
SOURCE(CLICKHOUSE(TABLE 'users' DB 'analytics'))
LAYOUT(HASHED())
LIFETIME(MIN 300 MAX 600);

-- Dictionary를 활용한 JOIN 대체
SELECT
    event_type,
    dictGet('user_segment_dict', 'user_segment', user_id) AS segment,
    count() AS cnt
FROM user_events
WHERE event_date = today()
GROUP BY event_type, segment;
```

---

## 주의사항 및 트레이드오프

### 1. 파티션 수 관리

파티션을 너무 세분화하면 파트 수가 폭발적으로 증가하고 머지 부하가 커집니다. ClickHouse는 파티션당 파트 수가 300개를 초과하면 INSERT를 거부합니다(`Too many parts` 에러). 일별 파티션이 적당한 상황에서 시간별 파티션으로 전환하면 이 문제가 심화됩니다.

**권장**: 파티션 키 단위당 데이터 볼륨이 최소 수백 MB 이상이 되도록 설계하세요.

### 2. 카디널리티와 ORDER BY 순서

ORDER BY의 컬럼 순서는 **카디널리티가 낮은 것 → 높은 것** 순으로 배치하는 것이 일반적으로 유리하지만, 실제 쿼리 패턴에 따라 달라질 수 있습니다. 카디널리티가 낮은 컬럼을 앞에 두면 같은 값끼리 물리적으로 인접하게 배치되어 압축률이 높아지는 이점도 있습니다.

### 3. Materialized View의 일관성 주의

Materialized View는 INSERT 트랜잭션과 동기로 동작하지 않습니다. 원본 INSERT가 성공해도 MV 집계가 실패할 수 있으며, 부분 실패 시 데이터 불일치가 발생할 수 있습니다. 중요한 집계 지표는 주기적인 검증 로직을 별도로 구현해야 합니다.

### 4. 실시간 vs 배치 트레이드오프

ClickHouse는 소량의 INSERT를 매우 자주 실행하는 것을 권장하지 않습니다. 최소 1,000행 이상, 가능하면 10,000~100,000행 단위로 배치 INSERT하는 것이 파트 수 관리와 성능 양쪽에서 유리합니다. Kafka → ClickHouse 파이프라인을 구성할 때 Kafka Engine의 `kafka_poll_max_batch_size`와 Materialized View 조합을 활용하면 이를 자동으로 처리할 수 있습니다.

### 5. ReplicatedMergeTree와 분산 쿼리

클러스터 환경에서는 `Distributed` 테이블 엔진을 통해 쿼리가 각 샤드로 분산됩니다. 이때 샤딩 키(sharding key)를 잘못 설계하면 특정 샤드에 데이터가 편중되어(Hot Shard) 분산의 이점을 상실합니다. `rand()` 기반 샤딩은 균등 분배는 되지만 같은 user_id 데이터가 여러 샤드에 흩어져 user 단위 집계 시 네트워크 전송 비용이 증가합니다.

---

## 정리

ClickHouse 쿼리 최적화는 단일 기법 하나로 해결되지 않습니다. 다음 체크리스트를 운영 환경에 적용해보세요.

| 레이어 | 핵심 전략 |
|---|---|
| **스키마 설계** | 적절한 데이터 타입 (`LowCardinality`, `UInt` 계열), `ORDER BY` 컬럼 순서 최적화 |
| **파티션** | 쿼리 패턴에 맞는 파티션 granularity, 파티션 수 모니터링 |
| **인덱스** | Sparse Index 활용, 필요 시 Skip Index 추가 |
| **집계 최적화** | Materialized View + AggregatingMergeTree로 사전 집계 |
| **쿼리 작성** | `SELECT *` 금지, WHERE 절 파티션/정렬키 매칭, 오른쪽 JOIN 테이블 크기 제어 |
| **시스템 설정** | `max_threads`, `max_memory_usage` 워크로드별 튜닝 |

ClickHouse는 올바르게 설계하면 기존 RDBMS 대비 수십~수백 배의 분석 성능을 보여줍니다. 특히 `EXPLAIN`과 `system.query_log` 테이블을 적극 활용해 실제 쿼리 실행 계획과 병목 지점을 데이터로 확인하는 습관을 기르는 것이 장기적으로 가장 중요한 최적화 투자입니다.