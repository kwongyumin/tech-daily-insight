# Apache Cassandra 분산 NoSQL 데이터 모델링

## 개요

Apache Cassandra는 Facebook이 개발하고 현재 Apache Software Foundation에서 관리하는 분산 NoSQL 데이터베이스입니다. Google Bigtable의 데이터 모델과 Amazon Dynamo의 분산 아키텍처를 결합한 설계로, 수평 확장성과 고가용성을 동시에 달성합니다.

Cassandra를 처음 접하는 개발자들이 흔히 저지르는 실수는 RDBMS 사고방식으로 스키마를 설계하는 것입니다. **Cassandra에서 데이터 모델링의 핵심은 "데이터를 어떻게 저장할 것인가"가 아니라 "데이터를 어떻게 조회할 것인가"에서 출발합니다.** 쿼리 패턴이 먼저 결정되어야 하고, 스키마는 그에 종속됩니다.

이 글에서는 Cassandra의 핵심 개념과 분산 환경에서의 실전 데이터 모델링 전략을 깊이 있게 다룹니다.

---

## 핵심 개념

### 파티션 키와 클러스터링 키

Cassandra의 Primary Key는 두 가지 구성요소로 나뉩니다.

```cql
PRIMARY KEY ((partition_key), clustering_key_1, clustering_key_2)
```

- **파티션 키(Partition Key)**: 데이터가 어느 노드에 저장될지를 결정합니다. 해시 함수(Murmur3)를 통해 토큰 링의 특정 노드로 라우팅됩니다. 파티션 키가 같은 데이터는 동일 노드에 저장됩니다.
- **클러스터링 키(Clustering Key)**: 동일 파티션 내에서 데이터의 정렬 순서를 결정합니다. 범위 조회(range query)를 가능하게 하는 핵심 요소입니다.

파티션 키 설계 시 가장 중요한 원칙은 **데이터의 균등 분산**과 **파티션 크기 제한**입니다. 하나의 파티션이 수백 MB를 초과하면 읽기/쓰기 성능이 급격히 저하됩니다.

### Consistency Level

Cassandra는 CAP 정리에서 AP(Availability + Partition Tolerance)를 선택하지만, Consistency Level을 통해 일관성 강도를 조절할 수 있습니다.

| Consistency Level | 설명 |
|---|---|
| ONE | 하나의 레플리카에서 응답 |
| QUORUM | 과반수 레플리카에서 응답 (RF=3이면 2개) |
| LOCAL_QUORUM | 로컬 데이터센터 내 과반수 |
| ALL | 모든 레플리카에서 응답 |

강한 일관성이 필요하다면 Write + Read Consistency Level의 합이 Replication Factor를 초과해야 합니다 (예: W=QUORUM + R=QUORUM with RF=3).

### 와이드 파티션 패턴 (Wide Partition Pattern)

Cassandra의 진가는 하나의 파티션 안에 수천~수백만 개의 row를 효율적으로 저장하고 정렬된 상태로 조회할 수 있다는 점입니다. 이를 활용한 **시계열 데이터 저장**이 대표적인 패턴입니다.

---

## 실전 예제

### 시나리오: IoT 센서 시계열 데이터 모델링

수천 개의 IoT 센서에서 1초마다 온도 데이터가 수집되는 시스템을 설계합니다.

#### 나쁜 설계 (RDBMS 사고방식)

```cql
-- 안티패턴: 센서 ID만으로 파티션을 나누면 파티션이 무한정 커짐
CREATE TABLE sensor_data_bad (
    sensor_id  UUID,
    timestamp  TIMESTAMP,
    value      DOUBLE,
    PRIMARY KEY (sensor_id, timestamp)
) WITH CLUSTERING ORDER BY (timestamp DESC);
```

위 설계는 시간이 지남에 따라 단일 파티션이 무한히 커지는 **Unbounded Partition** 문제가 발생합니다.

#### 좋은 설계 (버킷 전략 적용)

```cql
-- 날짜(bucket)를 파티션 키에 포함시켜 파티션 크기를 제한
CREATE TABLE sensor_data (
    sensor_id  TEXT,
    date       DATE,           -- 버킷 단위 (일별)
    timestamp  TIMESTAMP,
    value      DOUBLE,
    unit       TEXT,
    PRIMARY KEY ((sensor_id, date), timestamp)
) WITH CLUSTERING ORDER BY (timestamp DESC)
   AND default_time_to_live = 2592000  -- 30일 TTL
   AND compaction = {
       'class': 'TimeWindowCompactionStrategy',
       'compaction_window_unit': 'DAYS',
       'compaction_window_size': 1
   };
```

**핵심 포인트:**
- `(sensor_id, date)` 복합 파티션 키로 파티션 크기를 하루 단위로 제한
- `TimeWindowCompactionStrategy`로 시계열 데이터의 compaction 효율 극대화
- TTL 설정으로 오래된 데이터 자동 삭제

#### Java/Spring에서 활용 (Spring Data Cassandra)

```java
// Entity 정의
@Table("sensor_data")
public class SensorData {

    @PrimaryKeyColumn(name = "sensor_id", ordinal = 0, type = PrimaryKeyType.PARTITIONED)
    private String sensorId;

    @PrimaryKeyColumn(name = "date", ordinal = 1, type = PrimaryKeyType.PARTITIONED)
    private LocalDate date;

    @PrimaryKeyColumn(name = "timestamp", ordinal = 2, type = PrimaryKeyType.CLUSTERED,
                      ordering = Ordering.DESCENDING)
    private Instant timestamp;

    private Double value;
    private String unit;

    // constructors, getters, setters...
}

// Repository
@Repository
public interface SensorDataRepository extends CassandraRepository<SensorData, SensorDataKey> {

    @Query("SELECT * FROM sensor_data WHERE sensor_id = ?0 AND date = ?1 " +
           "AND timestamp >= ?2 AND timestamp <= ?3")
    List<SensorData> findByRange(String sensorId, LocalDate date,
                                  Instant from, Instant to);
}

// Service 레이어에서 날짜 경계 처리
@Service
@RequiredArgsConstructor
public class SensorDataService {

    private final SensorDataRepository repository;

    public List<SensorData> getDataForPeriod(String sensorId,
                                              Instant from, Instant to) {
        // 날짜가 바뀌는 경우 여러 파티션 조회 필요
        List<LocalDate> dates = getDatesInRange(from, to);

        return dates.stream()
                .flatMap(date -> repository.findByRange(sensorId, date, from, to).stream())
                .sorted(Comparator.comparing(SensorData::getTimestamp).reversed())
                .collect(Collectors.toList());
    }

    private List<LocalDate> getDatesInRange(Instant from, Instant to) {
        LocalDate startDate = from.atZone(ZoneOffset.UTC).toLocalDate();
        LocalDate endDate = to.atZone(ZoneOffset.UTC).toLocalDate();

        return startDate.datesUntil(endDate.plusDays(1))
                .collect(Collectors.toList());
    }
}
```

### 시나리오: 사용자 타임라인 (One Table Per Query 패턴)

소셜 미디어의 사용자 피드를 설계합니다. "특정 사용자의 최근 게시물 조회"와 "특정 해시태그의 게시물 조회"를 모두 지원해야 합니다.

```cql
-- 쿼리 1: 사용자별 타임라인
CREATE TABLE posts_by_user (
    user_id    UUID,
    post_id    TIMEUUID,   -- 시간 기반 UUID로 정렬 보장
    content    TEXT,
    hashtags   SET<TEXT>,
    PRIMARY KEY (user_id, post_id)
) WITH CLUSTERING ORDER BY (post_id DESC);

-- 쿼리 2: 해시태그별 게시물
CREATE TABLE posts_by_hashtag (
    hashtag    TEXT,
    year_month TEXT,        -- 버킷 (월 단위)
    post_id    TIMEUUID,
    user_id    UUID,
    content    TEXT,
    PRIMARY KEY ((hashtag, year_month), post_id)
) WITH CLUSTERING ORDER BY (post_id DESC);
```

동일한 데이터를 두 테이블에 **중복 저장**합니다. 이는 Cassandra에서 정상적인 패턴이며, 쓰기 시 두 테이블에 동시 삽입하거나 Batch를 활용합니다.

```cql
-- Logged Batch로 원자적 쓰기 (단, 동일 파티션이 아닌 경우 성능 저하 주의)
BEGIN BATCH
    INSERT INTO posts_by_user (user_id, post_id, content, hashtags)
    VALUES (?, now(), ?, ?);

    INSERT INTO posts_by_hashtag (hashtag, year_month, post_id, user_id, content)
    VALUES (?, ?, now(), ?, ?);
APPLY BATCH;
```

---

## 주의사항 및 트레이드오프

### 1. Secondary Index의 함정

Cassandra의 Secondary Index는 RDBMS와 다르게 **각 노드에 로컬 인덱스**를 생성합니다. 즉, 카디널리티가 높은 컬럼에 Secondary Index를 걸면 전체 노드에 쿼리가 분산되어 **성능이 오히려 저하**됩니다.

```cql
-- 위험한 사용: 고카디널리티 컬럼에 인덱스
CREATE INDEX ON users(email);  -- 거의 모든 값이 유일 → 전체 노드 스캔 발생

-- 대안: Materialized View 또는 별도 테이블 생성
CREATE TABLE users_by_email (
    email    TEXT PRIMARY KEY,
    user_id  UUID,
    username TEXT
);
```

**SAI(Storage-Attached Index)**는 Cassandra 4.0 이후 도입된 개선된 인덱스로, 고카디널리티에서도 상대적으로 안전하게 사용할 수 있습니다.

### 2. ALLOW FILTERING 절대 금지

```cql
-- 절대 금지: 운영 환경에서 ALLOW FILTERING은 전체 테이블 스캔
SELECT * FROM sensor_data WHERE value > 30 ALLOW FILTERING;
```

`ALLOW FILTERING`은 파티션 키 없이 필터링을 강제로 허용하지만, 데이터 증가와 함께 성능이 선형으로 악화됩니다.

### 3. 파티션 핫스팟

시간 기반 파티션 키(예: 현재 날짜)를 사용할 경우, 모든 쓰기가 특정 노드로 집중되는 **핫스팟** 문제가 발생합니다.

```cql
-- 핫스팟 발생 가능
PRIMARY KEY (current_date, event_id)

-- 해결: 버킷 번호를 추가하여 쓰기 분산
-- bucket = hash(event_id) % NUM_BUCKETS
PRIMARY KEY ((date, bucket), event_id)
```

### 4. Tombstone 누적 문제

Cassandra에서 DELETE는 즉시 삭제가 아닌 **Tombstone** 마커를 생성합니다. Tombstone이 과도하게 누적되면 읽기 성능이 심각하게 저하됩니다.

- TTL을 활용해 자연적으로 데이터를 만료시키는 전략 권장
- `gc_grace_seconds` 설정을 통해 Tombstone 정리 주기 관리
- 자주 삭제가 일어나는 패턴이라면 Compaction 전략을 `LeveledCompactionStrategy`로 변경 검토

### 5. 분산 트랜잭션의 한계

Cassandra는 LWT(Lightweight Transactions, Paxos 기반)를 지원하지만, 성능 오버헤드가 4~5배에 달합니다.

```cql
-- LWT: 조건부 삽입 (성능 비용 높음)
INSERT INTO users (user_id, email) VALUES (uuid(), 'test@test.com')
IF NOT EXISTS;
```

**복잡한 비즈니스 트랜잭션이 필요하다면 Cassandra는 적합하지 않습니다.** 이런 경우 RDBMS와 병행 사용하거나, 애플리케이션 레벨에서 멱등성을 보장하는 설계가 필요합니다.

---

## 정리

Cassandra 데이터 모델링의 핵심 원칙을 정리하면 다음과 같습니다.

| 원칙 | 설명 |
|---|---|
| Query-First Design | 쿼리 패턴을 먼저 정의하고 테이블 설계 |
| One Table Per Query | 각 쿼리 패턴에 최적화된 전용 테이블 생성 |
| Denormalization | 데이터 중복을 허용하고 조인 회피 |
| Partition Size 관리 | 단일 파티션은 수백 MB 이하로 유지 |
| 균등 분산 | 파티션 키를 통해 노드 간 데이터 균등 분산 |

Cassandra는 **대용량 쓰기**, **선형 확장**, **고가용성**이 요구되는 시나리오에서 탁월한 선택입니다. 반면 복잡한 관계형 쿼리나 강한 ACID 트랜잭션이 필요한 도메인에는 적합하지 않습니다.

실무에서는 Cassandra를 단독으로 사용하기보다 **RDBMS나 Redis와 조합하여** 각 기술의 장점을 살리는 폴리글랏 퍼시스턴스(Polyglot Persistence) 전략이 효과적입니다. 모델링 초기에 충분한 쿼리 분석과 용량 계획(capacity planning)에 투자하면, 이후 운영 과정에서의 리스크를 크게 줄일 수 있습니다.