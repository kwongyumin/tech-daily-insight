# TimescaleDB 시계열 데이터 저장과 분석

## 개요

IoT 센서 데이터, 금융 거래 내역, 애플리케이션 메트릭, 서버 로그 등 **시간의 흐름에 따라 지속적으로 쌓이는 데이터**를 효율적으로 다루는 것은 현대 백엔드 시스템의 핵심 과제 중 하나다. 일반 RDBMS로 이런 워크로드를 처리하다 보면 시간이 지날수록 조회 성능이 급격히 저하되고, 보관 정책 적용도 복잡해지는 경험을 해봤을 것이다.

**TimescaleDB**는 PostgreSQL 익스텐션으로 구현된 오픈소스 시계열 데이터베이스다. PostgreSQL의 SQL 인터페이스와 생태계를 그대로 활용하면서, 내부적으로 시계열 데이터에 최적화된 스토리지 엔진을 제공한다. "PostgreSQL을 버리지 않고 시계열 성능을 얻는다"는 것이 가장 큰 매력이다.

이 포스팅에서는 TimescaleDB의 핵심 개념부터 실전 운영까지, 실무에서 바로 적용 가능한 수준으로 다룬다.

---

## 핵심 개념

### Hypertable

TimescaleDB의 가장 핵심적인 추상화다. 사용자 입장에서는 일반 PostgreSQL 테이블처럼 보이지만, 내부적으로는 **Chunk**라는 단위로 시간(및 선택적으로 공간) 기준으로 자동 파티셔닝된다.

```
Hypertable (users가 보는 논리적 테이블)
├── Chunk_1 (2024-01-01 ~ 2024-01-07)
├── Chunk_2 (2024-01-07 ~ 2024-01-14)
└── Chunk_N (...)
```

각 청크는 실제 PostgreSQL 테이블이며, 쿼리 플래너가 시간 범위에 따라 필요한 청크만 스캔하도록 **청크 제외(Chunk Exclusion)** 를 자동으로 수행한다. 이것이 시계열 범위 쿼리에서 탁월한 성능을 내는 이유다.

### Continuous Aggregate

시계열 데이터에서 자주 발생하는 패턴은 "최근 1시간 단위 평균", "일별 최대값" 같은 집계 쿼리다. 매번 전체 원본 데이터를 스캔하면 비용이 크다. Continuous Aggregate는 이를 **materialized view** 방식으로 자동으로 증분 갱신해준다.

### Compression

TimescaleDB는 청크 단위로 **컬럼 기반 압축**을 적용할 수 있다. 시계열 데이터는 특성상 같은 컬럼의 연속된 값들이 유사한 경우가 많아 압축률이 매우 높다. 실제로 10~20배 압축도 흔하다.

### Data Retention Policy

오래된 데이터를 자동으로 삭제하는 정책을 선언적으로 설정할 수 있다. `drop_chunks` 기반으로 동작하며, 청크 전체를 파일시스템 수준에서 제거하므로 일반 `DELETE`보다 훨씬 빠르고 블로트(bloat)가 없다.

---

## 실전 예제

### 환경 구성

Docker를 이용한 빠른 시작:

```yaml
# docker-compose.yml
version: '3.8'
services:
  timescaledb:
    image: timescale/timescaledb:latest-pg15
    environment:
      POSTGRES_DB: metrics_db
      POSTGRES_USER: admin
      POSTGRES_PASSWORD: secret
    ports:
      - "5432:5432"
    volumes:
      - tsdb_data:/var/lib/postgresql/data

volumes:
  tsdb_data:
```

### 스키마 설계 및 Hypertable 생성

IoT 센서 메트릭을 저장하는 테이블을 예제로 사용한다.

```sql
-- TimescaleDB 익스텐션 활성화
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- 센서 메타데이터 테이블
CREATE TABLE sensors (
    sensor_id   SERIAL PRIMARY KEY,
    location    TEXT NOT NULL,
    device_type TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 시계열 메트릭 테이블
CREATE TABLE sensor_metrics (
    time        TIMESTAMPTZ     NOT NULL,
    sensor_id   INTEGER         NOT NULL REFERENCES sensors(sensor_id),
    temperature DOUBLE PRECISION,
    humidity    DOUBLE PRECISION,
    pressure    DOUBLE PRECISION
);

-- Hypertable로 변환 (7일 단위 청크)
SELECT create_hypertable(
    'sensor_metrics',
    'time',
    chunk_time_interval => INTERVAL '7 days'
);

-- 복합 인덱스 (sensor_id + time 기반 조회 최적화)
CREATE INDEX ON sensor_metrics (sensor_id, time DESC);
```

### 데이터 삽입

TimescaleDB는 표준 SQL INSERT를 그대로 사용한다. 배치 삽입 시 성능을 위해 `COPY` 또는 멀티 로우 INSERT를 활용하자.

```sql
-- 단건 삽입
INSERT INTO sensor_metrics (time, sensor_id, temperature, humidity, pressure)
VALUES (NOW(), 1, 23.5, 65.2, 1013.25);

-- 배치 삽입 (실무에서 권장)
INSERT INTO sensor_metrics (time, sensor_id, temperature, humidity, pressure)
VALUES
    ('2024-06-01 00:00:00+00', 1, 22.1, 64.0, 1012.5),
    ('2024-06-01 00:01:00+00', 1, 22.3, 64.2, 1012.6),
    ('2024-06-01 00:02:00+00', 2, 19.8, 70.1, 1011.9);
```

### 시계열 분석 쿼리

TimescaleDB의 `time_bucket` 함수는 시계열 집계의 핵심이다.

```sql
-- 1시간 단위 평균 온도 조회
SELECT
    time_bucket('1 hour', time) AS bucket,
    sensor_id,
    AVG(temperature)            AS avg_temp,
    MAX(temperature)            AS max_temp,
    MIN(temperature)            AS min_temp,
    COUNT(*)                    AS sample_count
FROM sensor_metrics
WHERE
    time >= NOW() - INTERVAL '24 hours'
    AND sensor_id = 1
GROUP BY bucket, sensor_id
ORDER BY bucket DESC;

-- 이동 평균 (Window Function 활용)
SELECT
    time,
    sensor_id,
    temperature,
    AVG(temperature) OVER (
        PARTITION BY sensor_id
        ORDER BY time
        ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
    ) AS moving_avg_5
FROM sensor_metrics
WHERE time >= NOW() - INTERVAL '1 hour'
ORDER BY time DESC;
```

### Continuous Aggregate 설정

```sql
-- 1시간 단위 집계 Materialized View 생성
CREATE MATERIALIZED VIEW sensor_metrics_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    sensor_id,
    AVG(temperature)            AS avg_temp,
    MAX(temperature)            AS max_temp,
    MIN(temperature)            AS min_temp,
    AVG(humidity)               AS avg_humidity
FROM sensor_metrics
GROUP BY bucket, sensor_id
WITH NO DATA;

-- 자동 갱신 정책: 1시간마다 갱신, 최근 3시간 범위 처리
SELECT add_continuous_aggregate_policy(
    'sensor_metrics_hourly',
    start_offset => INTERVAL '3 hours',
    end_offset   => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour'
);

-- Continuous Aggregate 조회 (일반 테이블처럼 사용)
SELECT *
FROM sensor_metrics_hourly
WHERE bucket >= NOW() - INTERVAL '7 days'
  AND sensor_id = 1
ORDER BY bucket DESC;
```

### 압축 정책 설정

```sql
-- 압축 설정 (sensor_id 기준 세그먼트화, time 기준 정렬)
ALTER TABLE sensor_metrics SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'sensor_id',
    timescaledb.compress_orderby = 'time DESC'
);

-- 7일 이상 된 청크 자동 압축
SELECT add_compression_policy('sensor_metrics', INTERVAL '7 days');

-- 수동 압축 (특정 청크)
SELECT compress_chunk(c.chunk_schema || '.' || c.chunk_name)
FROM timescaledb_information.chunks c
WHERE c.hypertable_name = 'sensor_metrics'
  AND c.range_end < NOW() - INTERVAL '7 days';

-- 압축 현황 확인
SELECT
    chunk_name,
    before_compression_total_bytes,
    after_compression_total_bytes,
    ROUND(
        (1 - after_compression_total_bytes::NUMERIC / before_compression_total_bytes) * 100, 2
    ) AS compression_ratio_pct
FROM chunk_compression_stats('sensor_metrics')
ORDER BY chunk_name;
```

### 데이터 보관 정책

```sql
-- 90일 이상 데이터 자동 삭제
SELECT add_retention_policy('sensor_metrics', INTERVAL '90 days');

-- 정책 현황 확인
SELECT * FROM timescaledb_information.jobs
WHERE proc_name = 'policy_retention';
```

### Spring Boot 연동 예제

```java
// build.gradle
// implementation 'org.springframework.boot:spring-boot-starter-data-jpa'
// implementation 'org.postgresql:postgresql'

@Entity
@Table(name = "sensor_metrics")
public class SensorMetric {

    @Id
    @Column(name = "time")
    private OffsetDateTime time;

    @Column(name = "sensor_id")
    private Integer sensorId;

    private Double temperature;
    private Double humidity;
    private Double pressure;
}

// Repository
public interface SensorMetricRepository extends JpaRepository<SensorMetric, OffsetDateTime> {

    @Query(value = """
        SELECT time_bucket('1 hour', time) AS bucket,
               sensor_id,
               AVG(temperature) AS avg_temp,
               MAX(temperature) AS max_temp
        FROM sensor_metrics
        WHERE time >= :from AND sensor_id = :sensorId
        GROUP BY bucket, sensor_id
        ORDER BY bucket DESC
        """, nativeQuery = true)
    List<Object[]> findHourlyAggregates(
        @Param("from") OffsetDateTime from,
        @Param("sensorId") Integer sensorId
    );
}

// Service
@Service
@RequiredArgsConstructor
public class MetricAnalysisService {

    private final SensorMetricRepository repository;

    public List<HourlyMetricDto> getHourlyMetrics(int sensorId, int hours) {
        OffsetDateTime from = OffsetDateTime.now().minusHours(hours);
        List<Object[]> rows = repository.findHourlyAggregates(from, sensorId);

        return rows.stream()
            .map(row -> HourlyMetricDto.builder()
                .bucket((OffsetDateTime) row[0])
                .sensorId((Integer) row[1])
                .avgTemp((Double) row[2])
                .maxTemp((Double) row[3])
                .build())
            .toList();
    }
}
```

---

## 주의사항 및 트레이드오프

### 청크 크기 선택

청크 간격은 워크로드에 맞게 조정해야 한다. 기본값(7일)이 항상 최선은 아니다.

- **너무 작은 청크**: 청크 수가 많아져 메타데이터 오버헤드 증가, 플래너 부담 증가
- **너무 큰 청크**: 청크 제외 효과 감소, 압축/삭제 단위가 커져 세밀한 제어 어려움
- **권장**: 최근 활성 청크가 메모리(RAM)의 25% 이하에 맞는 크기 선택

```sql
-- 청크 크기 확인
SELECT chunk_name, range_start, range_end,
       pg_size_pretty(total_bytes) AS size
FROM timescaledb_information.chunks
WHERE hypertable_name = 'sensor_metrics'
ORDER BY range_start DESC
LIMIT 10;
```

### 압축된 청크의 UPDATE/DELETE 제한

압축된 청크에 대한 `UPDATE`, `DELETE`는 내부적으로 청크를 먼저 해제(decompress)한 뒤 처리하므로 성능 비용이 크다. **시계열 데이터는 기본적으로 불변(immutable)으로 설계**하는 것이 좋으며, 수정이 필요한 경우 보정 이벤트(correction event)를 추가 삽입하는 패턴을 권장한다.

### Continuous Aggregate의 실시간성 한계

`end_offset` 설정으로 인해 최근 일정 시간의 데이터는 집계 뷰에 반영되지 않을 수 있다. 실시간 조회가 필요하다면 `WITH (timescaledb.materialized_only = false)` 옵션으로 원본 데이터와 자동 병합하도록 설정할 수 있다.

```sql
-- 실시간 데이터 자동 병합 활성화
ALTER MATERIALIZED VIEW sensor_metrics_hourly
SET (timescaledb.materialized_only = false);
```

### 스케일아웃과 Timescale Cloud

오픈소스 단일 노드 버전은 수평 확장에 한계가 있다. 대규모 클러스터가 필요하다면 **Timescale Cloud** 또는 **Citus 익스텐션** 조합을 검토해야 한다. 하지만 단일 노드 TimescaleDB도 적절한 인덱스와 압축, 하드웨어 스펙이면 초당 수백만 건 이상을 처리하는 사례가 많다.

### PostgreSQL 버전 업그레이드 주의

TimescaleDB는 PostgreSQL 마이너/메이저 버전 업그레이드 시 반드시 호환성을 확인해야 한다. 특히 메이저 버전 업그레이드는 `pg_upgrade` 전에 TimescaleDB 공식 문서의 업그레이드 가이드를 반드시 따를 것을 권장한다.

---

## 정리

TimescaleDB는 "PostgreSQL을 이미 쓰고 있는 팀이 시계열 요구사항을 만났을 때" 가장 현실적인 선택지 중 하나다.

| 기능 | 효과 |
|------|------|
| Hypertable + Chunk | 시간 범위 쿼리 성능 대폭 향상 |
| Continuous Aggregate | 집계 쿼리 부하 분산 |
| Compression | 스토리지 비용 10~20배 절감 |
| Retention Policy | 운영 자동화, 블로트 방지 |
| 표준 SQL 호환 | 기존 ORM, 툴체인 그대로 활용 |

InfluxDB, Prometheus 같은 전용 시계열 DB와 비교해 SQL 표준 준수, 조인 가능성, 기존 PostgreSQL 운영 노하우 재사용이라는 점에서 팀의 학습 비용과 운영 복잡성을 크게 줄일 수 있다.

다만 극단적인 쓰기 처리량(초당 수천만 건 이상)이나 글로벌 분산 클러스터가 필요한 환경이라면, 전용 시계열 솔루션이나 Timescale Cloud로의 전환을 검토하는 것이 맞다. **도구의 강점을 정확히 이해하고 워크로드에 맞게 선택하는 것**이 결국 좋은 시스템 설계의 출발점이다.