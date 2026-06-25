# PostgreSQL 쿼리 최적화와 인덱스 전략 완벽 가이드

## 개요

PostgreSQL은 강력한 오픈소스 RDBMS이지만, 잘못된 쿼리 설계나 인덱스 전략으로 인해 성능 병목이 발생하는 경우가 많다. 테이블이 수백만 건을 넘어서는 순간, 쿼리 한 줄의 차이가 응답 시간을 수십 배까지 차이 나게 만든다.

이 글에서는 실무에서 자주 마주치는 PostgreSQL 성능 문제를 중심으로, **실행 계획(EXPLAIN) 분석**, **인덱스 종류와 선택 기준**, **쿼리 리팩토링 패턴**까지 체계적으로 다룬다. 단순한 이론 설명을 넘어, 실제 운영 환경에서 바로 적용할 수 있는 예제를 함께 제시한다.

---

## 핵심 개념

### 1. 실행 계획(EXPLAIN) 읽는 법

PostgreSQL 쿼리 최적화의 시작은 `EXPLAIN ANALYZE`다. 실행 계획을 읽지 못하면 어디서 병목이 발생하는지 알 수 없다.

```sql
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT u.id, u.name, o.total_amount
FROM users u
JOIN orders o ON u.id = o.user_id
WHERE u.status = 'ACTIVE'
  AND o.created_at >= NOW() - INTERVAL '30 days';
```

출력 결과에서 핵심적으로 봐야 할 항목은 다음과 같다.

| 항목 | 설명 |
|---|---|
| `Seq Scan` | 풀 테이블 스캔. 인덱스 미사용 |
| `Index Scan` | 인덱스를 통한 조회 |
| `Bitmap Heap Scan` | 다수의 행을 인덱스로 조회 후 Heap 접근 |
| `Nested Loop / Hash Join` | 조인 전략 |
| `rows=` | 예상 행 수 (실제와 차이가 크면 통계 문제) |
| `actual time=` | 실제 실행 시간 (ms) |
| `Buffers: hit/read` | 캐시 히트율 |

`rows` 예측값과 실제값이 10배 이상 차이난다면, 테이블 통계가 오래된 것이므로 `ANALYZE` 명령으로 통계를 갱신해야 한다.

```sql
-- 특정 테이블 통계 갱신
ANALYZE users;
ANALYZE orders;
```

---

### 2. 인덱스 종류와 선택 기준

PostgreSQL은 다양한 인덱스 타입을 지원한다. 상황에 맞는 인덱스를 선택하는 것이 핵심이다.

#### B-Tree (기본값)
등호(`=`), 범위(`<`, `>`, `BETWEEN`), 정렬(`ORDER BY`)에 최적화된 범용 인덱스다. 대부분의 케이스에서 사용한다.

#### Hash
등호(`=`) 검색에만 사용 가능하며, 범위 검색은 지원하지 않는다. PostgreSQL 10 이전에는 WAL 지원이 없어 잘 쓰이지 않았으나, 현재는 안전하게 사용 가능하다.

#### GIN (Generalized Inverted Index)
배열, JSONB, 전문 검색(Full-text Search)에 적합하다.

#### GiST
지리 정보(PostGIS), 범위 타입, 기하학 데이터에 사용한다.

#### BRIN (Block Range Index)
물리적으로 정렬된 대용량 테이블(시계열 로그 등)에서 극도로 작은 인덱스 크기로 성능을 낸다.

---

## 실전 예제

### 예제 1: 복합 인덱스(Composite Index) 설계

가장 많이 실수하는 부분이 복합 인덱스 컬럼 순서다. **선택도(Cardinality)가 높고, WHERE 절에서 등호로 자주 사용되는 컬럼을 앞에 배치**해야 한다.

```sql
-- 잘못된 예시: status는 카디널리티가 낮아 뒤에 와야 함
CREATE INDEX idx_orders_bad ON orders(status, user_id, created_at);

-- 올바른 예시: user_id(고유값 많음) -> created_at(범위) -> status(낮은 카디널리티)
CREATE INDEX idx_orders_good ON orders(user_id, created_at, status);

-- 실제 활용 쿼리
SELECT * FROM orders
WHERE user_id = 12345
  AND created_at >= '2024-01-01'
  AND status = 'COMPLETED';
```

복합 인덱스는 **왼쪽 접두사 규칙(Leftmost Prefix Rule)**을 따른다. `(user_id, created_at, status)` 인덱스는 `user_id`만으로도, `user_id + created_at`으로도 사용 가능하지만, `created_at`만 단독으로는 사용할 수 없다.

---

### 예제 2: 부분 인덱스(Partial Index)로 인덱스 크기 줄이기

전체 데이터 중 특정 조건의 데이터만 자주 조회한다면, 부분 인덱스가 훨씬 효율적이다.

```sql
-- 전체 주문 중 'PENDING' 상태만 처리하는 배치 작업이 잦은 경우
CREATE INDEX idx_orders_pending ON orders(created_at)
WHERE status = 'PENDING';

-- 소프트 딜리트 패턴에서 삭제되지 않은 레코드만 인덱싱
CREATE INDEX idx_users_active ON users(email)
WHERE deleted_at IS NULL;

-- 인덱스 활용 쿼리 (WHERE 조건이 부분 인덱스 조건과 일치해야 함)
SELECT * FROM users
WHERE email = 'user@example.com'
  AND deleted_at IS NULL;
```

이 방법은 인덱스 크기를 대폭 줄이고, 쓰기 성능에 미치는 영향도 최소화한다.

---

### 예제 3: JSONB 인덱싱

PostgreSQL의 강력한 기능 중 하나인 JSONB 컬럼도 GIN 인덱스로 최적화할 수 있다.

```sql
-- 예시 테이블
CREATE TABLE events (
    id BIGSERIAL PRIMARY KEY,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- GIN 인덱스 생성 (모든 키/값 검색 가능)
CREATE INDEX idx_events_payload ON events USING GIN (payload);

-- jsonb_path_ops 연산자 클래스: @> 연산에 특화, 인덱스 크기 작음
CREATE INDEX idx_events_payload_path ON events USING GIN (payload jsonb_path_ops);

-- 활용 예시: 특정 이벤트 타입 조회
SELECT * FROM events
WHERE payload @> '{"event_type": "PURCHASE", "currency": "KRW"}';

-- 특정 키 존재 여부 검색
SELECT * FROM events
WHERE payload ? 'user_id';
```

특정 JSON 키만 자주 조회한다면, 표현식 인덱스를 사용하는 것이 더 효율적이다.

```sql
-- 특정 JSON 필드만 B-Tree 인덱스로
CREATE INDEX idx_events_user_id ON events ((payload->>'user_id'));

SELECT * FROM events
WHERE payload->>'user_id' = '12345';
```

---

### 예제 4: 느린 쿼리 패턴 개선

#### N+1 문제와 서브쿼리 최적화

```sql
-- 나쁜 패턴: 상관 서브쿼리(Correlated Subquery)
SELECT 
    u.id,
    u.name,
    (SELECT COUNT(*) FROM orders o WHERE o.user_id = u.id) AS order_count
FROM users u
WHERE u.status = 'ACTIVE';

-- 좋은 패턴: LEFT JOIN + GROUP BY 또는 CTE 활용
WITH user_order_counts AS (
    SELECT user_id, COUNT(*) AS order_count
    FROM orders
    GROUP BY user_id
)
SELECT 
    u.id,
    u.name,
    COALESCE(uoc.order_count, 0) AS order_count
FROM users u
LEFT JOIN user_order_counts uoc ON u.id = uoc.user_id
WHERE u.status = 'ACTIVE';
```

#### 페이지네이션 최적화 (Keyset Pagination)

`OFFSET`이 커질수록 성능이 급격히 저하된다. Keyset(커서) 기반 페이지네이션으로 전환하라.

```sql
-- 나쁜 패턴: OFFSET 기반 (OFFSET이 클수록 느려짐)
SELECT * FROM orders
ORDER BY created_at DESC
LIMIT 20 OFFSET 100000;

-- 좋은 패턴: Keyset Pagination (마지막으로 받은 id/created_at 기준)
SELECT * FROM orders
WHERE (created_at, id) < ('2024-06-01 12:00:00', 98765)
ORDER BY created_at DESC, id DESC
LIMIT 20;

-- 이를 위한 복합 인덱스
CREATE INDEX idx_orders_pagination ON orders(created_at DESC, id DESC);
```

---

### 예제 5: 인덱스 사용 여부 모니터링

운영 중인 인덱스가 실제로 사용되고 있는지 확인하는 쿼리다.

```sql
-- 인덱스 사용 통계 조회
SELECT 
    schemaname,
    tablename,
    indexname,
    idx_scan AS scans,
    idx_tup_read AS tuples_read,
    idx_tup_fetch AS tuples_fetched,
    pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes
ORDER BY idx_scan ASC;

-- 사용되지 않는 인덱스 찾기 (idx_scan = 0인 인덱스)
SELECT indexrelid::regclass AS index_name,
       pg_size_pretty(pg_relation_size(indexrelid)) AS wasted_size
FROM pg_stat_user_indexes
WHERE idx_scan = 0
  AND pg_relation_size(indexrelid) > 1024 * 1024; -- 1MB 이상만

-- 중복 인덱스 탐지
SELECT 
    indrelid::regclass AS table_name,
    array_agg(indexrelid::regclass) AS duplicate_indexes
FROM pg_index
GROUP BY indrelid, indkey
HAVING COUNT(*) > 1;
```

---

## 주의사항 및 트레이드오프

### 인덱스는 공짜가 아니다

인덱스를 무분별하게 추가하면 오히려 성능이 나빠진다.

- **쓰기 성능 저하**: INSERT/UPDATE/DELETE 시 모든 인덱스를 갱신해야 한다. 인덱스가 많을수록 쓰기 비용 증가.
- **저장 공간**: 인덱스도 디스크와 메모리를 사용한다. `shared_buffers`에서 데이터 페이지와 경쟁한다.
- **플래너 혼란**: 인덱스가 너무 많으면 쿼리 플래너가 잘못된 인덱스를 선택할 수 있다.

### 인덱스가 무시되는 경우

```sql
-- 함수로 감싸면 인덱스 미사용
SELECT * FROM users WHERE LOWER(email) = 'user@example.com';
-- 해결: 표현식 인덱스 생성
CREATE INDEX idx_users_email_lower ON users(LOWER(email));

-- 묵시적 타입 캐스팅
SELECT * FROM orders WHERE user_id = '12345'; -- user_id가 INTEGER인 경우 주의

-- LIKE의 와일드카드가 앞에 오면 인덱스 미사용
SELECT * FROM users WHERE name LIKE '%홍길동'; -- Seq Scan 발생
-- 앞에서 시작하는 패턴은 사용 가능
SELECT * FROM users WHERE name LIKE '홍길동%';
```

### VACUUM과 통계 관리

인덱스 성능은 테이블 통계와 Bloat(팽창)에 크게 영향받는다.

```sql
-- 테이블/인덱스 bloat 확인
SELECT 
    relname AS table_name,
    n_live_tup,
    n_dead_tup,
    ROUND(n_dead_tup * 100.0 / NULLIF(n_live_tup + n_dead_tup, 0), 2) AS dead_ratio
FROM pg_stat_user_tables
WHERE n_dead_tup > 1000
ORDER BY dead_ratio DESC;

-- 필요시 수동 VACUUM
VACUUM ANALYZE orders;

-- 인덱스 재구성 (잠금 없이)
REINDEX INDEX CONCURRENTLY idx_orders_good;
```

### `pg_hint_plan` 활용 (최후의 수단)

쿼리 플래너의 선택이 명백히 잘못됐을 때, 힌트를 사용할 수 있다. 단, **근본 원인(통계 부정확, 잘못된 인덱스 설계)을 먼저 해결하는 것이 원칙**이다.

```sql
-- pg_hint_plan 확장 설치 후
/*+ IndexScan(o idx_orders_good) HashJoin(u o) */
SELECT u.id, u.name, o.total_amount
FROM users u
JOIN orders o ON u.id = o.user_id
WHERE u.status = 'ACTIVE';
```

---

## 정리

PostgreSQL 쿼리 최적화는 단순히 인덱스를 추가하는 작업이 아니다. **실행 계획을 분석하고, 데이터 분포를 이해하고, 읽기/쓰기 트레이드오프를 고려**하는 종합적인 과정이다.

핵심 원칙을 정리하면 다음과 같다.

1. **먼저 측정하라**: `EXPLAIN ANALYZE`로 병목 지점을 정확히 파악한다.
2. **통계를 최신으로 유지하라**: `ANALYZE`를 정기적으로 실행하고 `autovacuum` 설정을 튜닝한다.
3. **인덱스 컬럼 순서를 신중하게 결정하라**: 등호 조건 → 범위 조건 → 정렬 순으로 배치한다.
4. **부분 인덱스와 표현식 인덱스를 적극 활용하라**: 불필요한 인덱스 크기를 줄이고 효율을 높인다.
5. **사용되지 않는 인덱스는 제거하라**: 쓰기 성능과 유지보수 비용을 줄인다.
6. **OFFSET 페이지네이션을 Keyset으로 전환하라**: 대용량 데이터에서 필수적인 전환이다.

성능 최적화는 한 번으로 끝나지 않는다. 데이터가 증가하고 쿼리 패턴이 변함에 따라 지속적인 모니터링과 튜닝이 필요하다. `pg_stat_statements`, `pg_stat_user_indexes` 등 PostgreSQL 내장 뷰를 습관적으로 확인하는 것이 장기적으로 안정적인 시스템을 유지하는 지름길이다.