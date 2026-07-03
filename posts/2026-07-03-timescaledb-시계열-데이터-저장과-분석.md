# TimescaleDB 시계열 데이터 저장과 분석

## 개요

IoT 센서 데이터, 금융 거래 로그, 애플리케이션 메트릭, 사용자 이벤트 스트림… 현대 백엔드 시스템이 다뤄야 하는 데이터의 상당 부분은 **시계열(Time-Series)** 특성을 가집니다. 시간 순서대로 끊임없이 유입되고, 최근 데이터에 대한 조회가 빈번하며, 오래된 데이터는 집계하거나 삭제하는 패턴이 반복됩니다.

이런 워크로드에 일반적인 PostgreSQL이나 MySQL을 그대로 사용하면 테이블이 수억 건을 넘어서는 순간부터 인덱스 비대화, 파티셔닝 관리 복잡성, 집계 쿼리 성능 저하가 체감됩니다. **TimescaleDB**는 이 문제를 PostgreSQL 확장(Extension) 형태로 해결합니다. 표준 SQL을 그대로 쓰면서도 시계열에 최적화된 청크(Chunk) 기반 파티셔닝, 자동 압축, 연속 집계(Continuous Aggregate)를 제공합니다.

이 글에서는 TimescaleDB의 핵심 개념을 정리하고, 실무에서 바로 활용할 수 있는 스키마 설계, 쿼리 패턴, Spring Boot 연동 코드까지 다룹니다.

---

## 핵심 개념

### Hypertable과 Chunk

TimescaleDB의 가장 근본적인 추상화는 **Hypertable**입니다. 사용자 입장에서는 일반 테이블처럼 보이지만, 내부적으로는 시간(혹은 시간 + 공간) 기준으로 분할된 **Chunk**들의 집합입니다.

```
Hypertable: sensor_data
├── _timescaledb_internal._hyper_1_1_chunk  (2024-01-01 ~ 2024-01-07)
├── _timescaledb_internal._hyper_1_2_chunk  (2024-01-07 ~ 2024-01-14)
└── _timescaledb_internal._hyper_1_3_chunk  (2024-01-14 ~ 2024-01-21)
```

각 Chunk는 독립적인 PostgreSQL 테이블이므로, 시간 범위 필터가 있는 쿼리는 해당 Chunk만 스캔합니다. 인덱스도 Chunk 단위로 관리되어 B-Tree 인덱스 크기가 메모리에 들어올 수 있는 수준으로 유지됩니다.

### 연속 집계 (Continuous Aggregate)

시계열 데이터의 전형적인 쿼리는 "지난 1시간 동안의 평균 온도"처럼 집계를 수반합니다. 매번 원본 데이터를 풀스캔하는 것은 비효율적이므로, TimescaleDB는 **Materialized View** 기반의 연속 집계를 제공합니다.

```sql
CREATE MATERIALIZED VIEW sensor_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', recorded_at) AS bucket,
    sensor_id,
    AVG(temperature)  AS avg_temp,
    MAX(temperature)  AS max_temp,
    MIN(temperature)  AS min_temp
FROM sensor_data
GROUP BY bucket, sensor_id;
```

백그라운드 워커가 새로 추가된 데이터만 증분 갱신하므로, 집계 쿼리의 응답 시간이 극적으로 줄어듭니다.

### 데이터 보존 정책과 압축

오래된 데이터를 자동으로 삭제하거나 압축하는 정책을 데이터베이스 레벨에서 설정할 수 있습니다.

```sql
-- 90일 이상 된 데이터 자동 삭제
SELECT add_retention_policy('sensor_data', INTERVAL '90 days');

-- 30일 이상 된 청크를 컬럼 지향 방식으로 압축 (평균 90% 이상 압축률)
ALTER TABLE sensor_data SET (
    timescaledb.compress,
    timescaledb.compress_orderby = 'recorded_at DESC',
    timescaledb.compress_segmentby = 'sensor_id'
);
SELECT add_compression_policy('sensor_data', INTERVAL '30 days');
```

---

## 실전 예제

### 스키마 설계

IoT 센서 데이터를 저장하는 시나리오를 가정합니다.

```sql
-- TimescaleDB 확장 활성화
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- 센서 메타데이터 (일반 테이블)
CREATE TABLE sensors (
    sensor_id   BIGSERIAL PRIMARY KEY,
    name        TEXT        NOT NULL,
    location    TEXT        NOT NULL,
    unit        TEXT        NOT NULL DEFAULT '°C',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 측정값 (Hypertable로 변환 예정)
CREATE TABLE sensor_data (
    recorded_at  TIMESTAMPTZ NOT NULL,
    sensor_id    BIGINT      NOT NULL REFERENCES sensors(sensor_id),
    temperature  DOUBLE PRECISION,
    humidity     DOUBLE PRECISION,
    pressure     DOUBLE PRECISION
);

-- Hypertable 변환 (7일 단위 청크)
SELECT create_hypertable(
    'sensor_data',
    'recorded_at',
    chunk_time_interval => INTERVAL '7 days'
);

-- 복합 인덱스: 특정 센서의 최근 데이터 조회 최적화
CREATE INDEX ON sensor_data (sensor_id, recorded_at DESC);
```

> **설계 팁**: `chunk_time_interval`은 초당 삽입량에 따라 조정하세요. 청크 하나가 메모리(work_mem의 25% 수준)에 들어올 수 있는 크기가 이상적입니다. 초당 수천 건 이상이면 1일, 수백 건 수준이면 7일~1개월이 적합합니다.

### 데이터 삽입 및 조회 쿼리

```sql
-- 벌크 삽입 (COPY 또는 배치 INSERT 권장)
INSERT INTO sensor_data (recorded_at, sensor_id, temperature, humidity, pressure)
VALUES
    (NOW(),                    1, 23.5, 60.2, 1013.2),
    (NOW() - INTERVAL '1 min', 1, 23.3, 60.5, 1013.1),
    (NOW() - INTERVAL '2 min', 1, 23.1, 60.8, 1013.0);

-- time_bucket: 15분 단위 집계
SELECT
    time_bucket('15 minutes', recorded_at) AS bucket,
    sensor_id,
    ROUND(AVG(temperature)::NUMERIC, 2) AS avg_temp,
    ROUND(MAX(temperature)::NUMERIC, 2) AS max_temp
FROM sensor_data
WHERE
    sensor_id = 1
    AND recorded_at >= NOW() - INTERVAL '24 hours'
GROUP BY bucket, sensor_id
ORDER BY bucket DESC;

-- first/last: 각 15분 구간의 첫 번째/마지막 값
SELECT
    time_bucket('15 minutes', recorded_at) AS bucket,
    sensor_id,
    first(temperature, recorded_at) AS open_temp,
    last(temperature, recorded_at)  AS close_temp
FROM sensor_data
WHERE recorded_at >= NOW() - INTERVAL '6 hours'
GROUP BY bucket, sensor_id
ORDER BY bucket DESC;
```

### Spring Boot + JPA/JDBC 연동

```java
// build.gradle
dependencies {
    implementation 'org.springframework.boot:spring-boot-starter-data-jpa'
    implementation 'org.springframework.boot:spring-boot-starter-jdbc'
    runtimeOnly 'org.postgresql:postgresql'
}
```

```java
// SensorData.java - JPA 엔티티
@Entity
@Table(name = "sensor_data")
@IdClass(SensorDataId.class)
public class SensorData {

    @Id
    @Column(name = "recorded_at")
    private OffsetDateTime recordedAt;

    @Id
    @Column(name = "sensor_id")
    private Long sensorId;

    private Double temperature;
    private Double humidity;
    private Double pressure;
}
```

```java
// SensorDataRepository.java - 네이티브 쿼리 활용
@Repository
public interface SensorDataRepository extends JpaRepository<SensorData, SensorDataId> {

    @Query(value = """
        SELECT
            time_bucket('15 minutes', recorded_at) AS bucket,
            sensor_id,
            AVG(temperature) AS avg_temp,
            MAX(temperature) AS max_temp
        FROM sensor_data
        WHERE sensor_id = :sensorId
          AND recorded_at >= NOW() - CAST(:interval AS INTERVAL)
        GROUP BY bucket, sensor_id
        ORDER BY bucket DESC
        """, nativeQuery = true)
    List<Object[]> findBucketedData(
        @Param("sensorId") Long sensorId,
        @Param("interval") String interval
    );
}
```

```java
// SensorDataService.java - 벌크 삽입 (JdbcTemplate 권장)
@Service
@RequiredArgsConstructor
public class SensorDataService {

    private final JdbcTemplate jdbcTemplate;

    public void bulkInsert(List<SensorDataDto> dataList) {
        String sql = """
            INSERT INTO sensor_data (recorded_at, sensor_id, temperature, humidity, pressure)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """;

        jdbcTemplate.batchUpdate(sql, dataList, 500, (ps, dto) -> {
            ps.setObject(1, dto.getRecordedAt());
            ps.setLong(2, dto.getSensorId());
            ps.setDouble(3, dto.getTemperature());
            ps.setDouble(4, dto.getHumidity());
            ps.setDouble(5, dto.getPressure());
        });
    }
}
```

### 연속 집계와 실시간 조회 결합

```sql
-- 연속 집계 뷰 생성
CREATE MATERIALIZED VIEW sensor_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', recorded_at) AS bucket,
    sensor_id,
    AVG(temperature) AS avg_temp,
    MAX(temperature) AS max_temp,
    MIN(temperature) AS min_temp,
    COUNT(*)         AS sample_count
FROM sensor_data
GROUP BY bucket, sensor_id
WITH NO DATA;

-- 자동 갱신 정책 (1시간마다, 최근 2시간 데이터 반영)
SELECT add_continuous_aggregate_policy('sensor_hourly',
    start_offset => INTERVAL '2 hours',
    end_offset   => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour'
);

-- 실시간 집계 활성화 (집계된 데이터 + 미집계 최신 데이터를 함께 조회)
ALTER MATERIALIZED VIEW sensor_hourly
    SET (timescaledb.materialized_only = false);
```

---

## 주의사항 및 트레이드오프

### 1. 압축된 청크는 수정 불가

압축된 청크에 `UPDATE`나 `DELETE`를 실행하면 에러가 발생합니다. 배치로 과거 데이터를 수정해야 하는 요건이 있다면 압축 정책 적용 전에 반드시 확인하세요. 필요하다면 해당 청크를 임시로 압축 해제(`decompress_chunk`) 후 작업해야 합니다.

```sql
-- 특정 청크 압축 해제
SELECT decompress_chunk('_timescaledb_internal._hyper_1_1_chunk');
```

### 2. JPA와의 궁합

TimescaleDB 전용 함수(`time_bucket`, `first`, `last`)는 JPQL에서 지원되지 않습니다. 복잡한 집계 쿼리는 반드시 **네이티브 쿼리** 또는 **JdbcTemplate**을 사용해야 합니다. Querydsl을 사용한다면 `Expressions.stringTemplate`을 활용하거나, 처음부터 MyBatis나 jOOQ를 고려하는 것도 좋습니다.

### 3. 청크 크기와 인덱스 메모리

청크 크기를 너무 크게 설정하면 인덱스가 메모리에 올라오지 않아 성능이 저하됩니다. 반대로 너무 작으면 청크 수가 폭발적으로 늘어나 쿼리 플래너 오버헤드가 생깁니다. 초기에는 기본값(7일)으로 시작하고, `timescaledb_information.chunks` 뷰로 모니터링하며 조정하세요.

```sql
-- 청크 정보 조회
SELECT
    chunk_name,
    range_start,
    range_end,
    pg_size_pretty(total_bytes) AS total_size,
    is_compressed
FROM timescaledb_information.chunks
WHERE hypertable_name = 'sensor_data'
ORDER BY range_start DESC;
```

### 4. 연속 집계의 지연 허용 구간

`end_offset`을 설정하면 해당 시간 내의 최신 데이터는 집계 뷰에 반영되지 않습니다. 실시간성이 중요한 대시보드라면 `materialized_only = false` 옵션으로 미집계 데이터를 투명하게 포함시키되, 성능 비용이 있음을 인지해야 합니다.

### 5. TimescaleDB vs. InfluxDB vs. ClickHouse

| 항목 | TimescaleDB | InfluxDB | ClickHouse |
|------|-------------|----------|------------|
| 쿼리 언어 | SQL (PostgreSQL 호환) | Flux / InfluxQL | SQL (방언) |
| 조인 지원 | ✅ 완전 지원 | ❌ 제한적 | ✅ 지원 |
| 운영 복잡도 | 낮음 (PG 확장) | 중간 | 높음 |
| 압축률 | 높음 | 높음 | 매우 높음 |
| 적합 용도 | 혼합 워크로드 | 순수 시계열 | 대용량 분석 |

기존 PostgreSQL 인프라가 있거나 관계형 데이터와 시계열 데이터를 함께 관리해야 한다면 TimescaleDB가 가장 낮은 전환 비용으로 강력한 시계열 기능을 제공합니다.

---

## 정리

TimescaleDB는 "PostgreSQL을 그대로 쓰면서 시계열 워크로드를 잘 처리하고 싶다"는 니즈를 가장 현실적으로 충족시키는 솔루션입니다. 핵심 포인트를 정리하면 다음과 같습니다.

- **Hypertable + Chunk**: 자동 시간 파티셔닝으로 인덱스와 쿼리 범위를 제어
- **time_bucket + first/last**: 시계열 집계를 위한 전용 함수 활용
- **연속 집계**: 대시보드와 분석 쿼리의 응답 시간을 획기적으로 단축
- **압축 + 보존 정책**: 스토리지 비용을 자동으로 관리
- **네이티브 쿼리 우선**: JPA의 한계를 인식하고 복잡한 쿼리는 JDBC 레벨에서 처리

시계열 전용 DB 도입을 고민하고 있다면, 먼저 TimescaleDB로 기존 PostgreSQL을 업그레이드해보세요. 운영 환경의 복잡도를 최소화하면서 충분한 성능 향상을 경험할 수 있을 것입니다.