# PostgreSQL Vacuum과 Bloat 관리 전략

## 개요

PostgreSQL을 운영하다 보면 어느 순간 테이블 크기가 예상보다 훨씬 커졌거나, 단순한 쿼리가 이상하게 느려지는 경험을 한다. 원인을 추적하다 보면 대부분 **Table Bloat**과 **Vacuum**의 동작 방식을 제대로 이해하지 못한 데서 비롯된 경우가 많다.

PostgreSQL은 MVCC(Multi-Version Concurrency Control) 기반으로 동작한다. 레코드를 UPDATE하거나 DELETE할 때 기존 행을 즉시 제거하지 않고 "Dead Tuple"로 남겨두는 방식이다. 이 Dead Tuple이 쌓이면 디스크 낭비와 쿼리 성능 저하로 이어진다. Vacuum은 이 문제를 해결하기 위한 PostgreSQL의 핵심 메커니즘이다.

이 글에서는 Vacuum의 동작 원리부터 Bloat 측정, 그리고 실무에서 바로 적용할 수 있는 튜닝 전략까지 다룬다.

---

## 핵심 개념

### MVCC와 Dead Tuple

PostgreSQL의 모든 행에는 `xmin`과 `xmax`라는 시스템 컬럼이 있다.

- **xmin**: 해당 행을 삽입한 트랜잭션 ID
- **xmax**: 해당 행을 삭제하거나 업데이트한 트랜잭션 ID (없으면 0)

UPDATE 시 기존 행의 `xmax`를 설정하고, 새로운 행을 INSERT한다. DELETE 시에는 `xmax`만 설정한다. 즉, 물리적인 데이터는 남아 있고, 트랜잭션 가시성에 따라 읽기/쓰기 여부가 결정된다.

이 때문에 대규모 UPDATE나 DELETE 이후 테이블 크기가 줄어들지 않는 현상이 발생한다. 이를 **Table Bloat**이라고 한다.

### Vacuum의 역할

Vacuum은 크게 두 가지 작업을 수행한다.

1. **Dead Tuple 회수**: 더 이상 어떤 트랜잭션에서도 참조하지 않는 Dead Tuple을 마킹하여 재사용 가능한 공간으로 만든다.
2. **Transaction ID Wraparound 방지**: PostgreSQL의 트랜잭션 ID는 32비트 정수이므로 약 21억 건 후 순환된다. Vacuum은 오래된 트랜잭션 ID를 freeze 처리하여 이를 방지한다.

> `VACUUM`은 공간을 OS에 반환하지 않는다. 공간을 OS에 돌려주려면 `VACUUM FULL`이 필요하지만, 이는 테이블 전체를 잠그므로 운영 환경에서는 신중하게 사용해야 한다.

### Autovacuum

PostgreSQL은 백그라운드에서 자동으로 Vacuum을 실행하는 **Autovacuum** 데몬을 제공한다. 기본 설정은 다음과 같다.

```sql
-- 현재 autovacuum 관련 설정 확인
SHOW autovacuum;
SHOW autovacuum_vacuum_threshold;
SHOW autovacuum_vacuum_scale_factor;
SHOW autovacuum_analyze_threshold;
SHOW autovacuum_analyze_scale_factor;
```

Autovacuum이 트리거되는 조건:
```
vacuum_threshold = autovacuum_vacuum_threshold + autovacuum_vacuum_scale_factor * table_size
```
기본값으로는 `50 + 0.2 * 테이블 행 수`가 넘는 Dead Tuple이 존재하면 Autovacuum이 실행된다.

---

## 실전 예제

### 1. Bloat 현황 파악

운영 중인 DB에서 Bloat이 얼마나 심한지 측정하는 것이 첫 번째 단계다. `pgstattuple` 익스텐션을 활용하면 정밀한 측정이 가능하다.

```sql
-- pgstattuple 익스텐션 설치
CREATE EXTENSION IF NOT EXISTS pgstattuple;

-- 특정 테이블의 Bloat 상세 확인
SELECT
    table_len,
    tuple_count,
    tuple_len,
    dead_tuple_count,
    dead_tuple_len,
    free_space,
    ROUND((dead_tuple_len::NUMERIC / table_len) * 100, 2) AS dead_tuple_ratio
FROM pgstattuple('public.orders');
```

익스텐션 없이도 시스템 카탈로그로 근사치를 구할 수 있다.

```sql
-- 테이블별 Bloat 근사치 조회
WITH bloat_info AS (
    SELECT
        schemaname,
        tablename,
        pg_size_pretty(pg_total_relation_size(schemaname || '.' || tablename)) AS total_size,
        n_live_tup,
        n_dead_tup,
        CASE
            WHEN n_live_tup + n_dead_tup > 0
            THEN ROUND(100.0 * n_dead_tup / (n_live_tup + n_dead_tup), 2)
            ELSE 0
        END AS dead_ratio,
        last_vacuum,
        last_autovacuum,
        last_analyze,
        last_autoanalyze
    FROM pg_stat_user_tables
)
SELECT *
FROM bloat_info
WHERE dead_ratio > 10  -- Dead Tuple 비율 10% 초과 테이블만 필터
ORDER BY dead_ratio DESC;
```

### 2. 인덱스 Bloat 확인

테이블뿐만 아니라 인덱스에도 Bloat이 발생한다.

```sql
-- 인덱스 크기 및 사용률 확인
SELECT
    schemaname,
    tablename,
    indexname,
    pg_size_pretty(pg_relation_size(indexrelid)) AS index_size,
    idx_scan,
    idx_tup_read,
    idx_tup_fetch
FROM pg_stat_user_indexes
JOIN pg_index USING (indexrelid)
WHERE schemaname = 'public'
ORDER BY pg_relation_size(indexrelid) DESC;
```

### 3. Autovacuum 튜닝 - 테이블 단위 설정

트래픽이 많은 테이블은 전역 설정보다 더 공격적인 Vacuum 정책이 필요하다.

```sql
-- 고트래픽 테이블에 대한 Autovacuum 커스텀 설정
ALTER TABLE orders SET (
    autovacuum_vacuum_scale_factor = 0.01,     -- 기본 0.2 → 1%만 Dead Tuple 쌓여도 실행
    autovacuum_vacuum_threshold = 100,          -- 최소 100건 이상일 때
    autovacuum_analyze_scale_factor = 0.005,   -- Analyze 기준도 강화
    autovacuum_vacuum_cost_delay = 2,           -- ms 단위, I/O 부하 조절
    autovacuum_vacuum_cost_limit = 400          -- 기본 200 → 더 빠르게 처리
);

-- 설정 확인
SELECT reloptions FROM pg_class WHERE relname = 'orders';
```

### 4. Vacuum 수동 실행 및 모니터링

```sql
-- 일반 Vacuum (테이블 잠금 없음)
VACUUM ANALYZE orders;

-- 특정 컬럼 통계 업데이트 포함
VACUUM (ANALYZE, VERBOSE) orders;

-- 실행 중인 Vacuum 프로세스 모니터링
SELECT
    pid,
    query,
    state,
    wait_event_type,
    wait_event,
    NOW() - query_start AS duration
FROM pg_stat_activity
WHERE query LIKE '%vacuum%'
   OR query LIKE '%VACUUM%';
```

### 5. pg_repack을 활용한 무중단 Bloat 제거

`VACUUM FULL`은 테이블 전체를 잠그기 때문에 운영 환경에서 사용하기 어렵다. 이때 `pg_repack`을 사용하면 테이블 잠금 없이 Bloat을 제거할 수 있다.

```bash
# pg_repack 설치 (Ubuntu/Debian 기준)
sudo apt-get install postgresql-14-repack

# 특정 테이블 repack
pg_repack -h localhost -p 5432 -U postgres -d mydb -t orders

# 전체 DB repack (주의: 시간이 오래 걸림)
pg_repack -h localhost -p 5432 -U postgres -d mydb

# 인덱스만 재구성
pg_repack -h localhost -p 5432 -U postgres -d mydb -t orders --only-indexes
```

### 6. Vacuum 진행 상황 실시간 모니터링

```sql
-- PostgreSQL 9.6+ Vacuum 진행률 확인
SELECT
    p.pid,
    p.phase,
    p.heap_blks_total,
    p.heap_blks_scanned,
    p.heap_blks_vacuumed,
    ROUND(100.0 * p.heap_blks_vacuumed / NULLIF(p.heap_blks_total, 0), 1) AS progress_pct,
    p.index_vacuum_count,
    p.num_dead_tuples
FROM pg_stat_progress_vacuum p
JOIN pg_class c ON c.oid = p.relid
WHERE c.relname = 'orders';
```

---

## 주의사항 및 트레이드오프

### Long-Running Transaction 문제

Autovacuum이 정상 동작해도 Bloat이 해결되지 않는 가장 흔한 원인은 **장시간 실행되는 트랜잭션** 때문이다. Vacuum은 활성 트랜잭션이 참조할 수 있는 행은 Dead Tuple로 처리할 수 없다.

```sql
-- 오래된 트랜잭션 확인 (Vacuum 방해 주범)
SELECT
    pid,
    usename,
    application_name,
    state,
    NOW() - xact_start AS transaction_duration,
    query
FROM pg_stat_activity
WHERE xact_start IS NOT NULL
  AND NOW() - xact_start > INTERVAL '10 minutes'
ORDER BY xact_start ASC;
```

이런 경우 애플리케이션 레벨에서 트랜잭션 타임아웃을 설정하거나, 필요하다면 강제 종료를 검토해야 한다.

```sql
-- 트랜잭션 타임아웃 설정 (postgresql.conf 또는 세션 단위)
SET statement_timeout = '30min';
SET idle_in_transaction_session_timeout = '5min';
```

### Autovacuum vs. I/O 부하 트레이드오프

Autovacuum을 너무 공격적으로 설정하면 디스크 I/O 부하가 급증하여 실제 쿼리 성능에 영향을 줄 수 있다. `autovacuum_vacuum_cost_delay`와 `autovacuum_vacuum_cost_limit`으로 I/O 부하를 제어한다.

- **cost_delay 낮게, cost_limit 높게**: Vacuum이 빠르게 처리되지만 I/O 부하 증가
- **cost_delay 높게, cost_limit 낮게**: I/O 부하는 낮지만 Vacuum 속도 감소

운영 환경에서는 피크 타임을 피해 `pg_cron`으로 수동 Vacuum을 예약하는 방법도 효과적이다.

```sql
-- pg_cron 활용 예약 Vacuum (새벽 3시 실행)
SELECT cron.schedule(
    'vacuum-orders-table',
    '0 3 * * *',
    'VACUUM ANALYZE public.orders'
);
```

### VACUUM FULL의 위험성

`VACUUM FULL`은 테이블을 완전히 다시 작성하며 OS에 공간을 반환하지만, 실행 중 **ACCESS EXCLUSIVE LOCK**을 획득한다. 즉, 해당 테이블에 대한 모든 읽기/쓰기가 블로킹된다. 운영 환경에서는 `pg_repack`으로 대체하는 것을 강력히 권장한다.

| 방법 | 잠금 수준 | OS 공간 반환 | 소요 시간 | 운영 중 사용 |
|------|----------|------------|---------|------------|
| VACUUM | 없음(거의) | X | 빠름 | 가능 |
| VACUUM FULL | ACCESS EXCLUSIVE | O | 느림 | 불가 |
| pg_repack | 최소화 | O | 중간 | 가능 |

---

## 정리

PostgreSQL Bloat 관리는 단순히 Vacuum을 실행하는 것 이상의 전략이 필요하다.

1. **pg_stat_user_tables와 pgstattuple로 정기적인 모니터링**을 수행하고, Bloat이 심한 테이블을 식별한다.
2. **고트래픽 테이블에는 테이블 단위의 Autovacuum 설정**을 적용하여 기본 전역 설정보다 더 민감하게 반응하도록 한다.
3. **Long-Running Transaction을 통제**한다. 이것이 해결되지 않으면 어떤 Vacuum 설정도 효과를 발휘하기 어렵다.
4. **공간 반환이 필요하다면 pg_repack**을 활용하여 서비스 중단 없이 처리한다.
5. **I/O 부하와 Vacuum 속도의 트레이드오프**를 인식하고, 피크 타임을 피한 스케줄링을 고려한다.

Bloat은 한 번 제거한다고 끝나는 문제가 아니다. 지속적인 모니터링 체계와 예방적인 설정이 함께 갖춰져야 안정적인 PostgreSQL 운영이 가능하다.