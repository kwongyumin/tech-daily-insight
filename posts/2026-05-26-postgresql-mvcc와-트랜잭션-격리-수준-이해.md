# PostgreSQL MVCC와 트랜잭션 격리 수준 이해

## 개요

데이터베이스를 운영하다 보면 동시성 문제와 필연적으로 마주하게 됩니다. "왜 방금 커밋한 데이터가 다른 트랜잭션에서 보이지 않지?", "Dirty Read가 발생하는데 어떻게 막아야 하지?"와 같은 질문들은 실무에서 자주 접하는 이슈입니다.

PostgreSQL은 이러한 동시성 문제를 해결하기 위해 **MVCC(Multi-Version Concurrency Control, 다중 버전 동시성 제어)** 를 핵심 메커니즘으로 사용합니다. MVCC를 올바르게 이해하면 트랜잭션 격리 수준 선택, 성능 튜닝, 데드락 방지 등 다양한 실무 문제를 더 자신감 있게 다룰 수 있습니다.

이 글에서는 PostgreSQL의 MVCC 동작 원리부터 각 격리 수준의 특성, 그리고 실무에서의 선택 기준까지 깊이 있게 살펴보겠습니다.

---

## 핵심 개념

### MVCC란 무엇인가?

전통적인 Lock 기반 동시성 제어는 읽기 작업도 잠금을 획득해야 해서 성능 병목이 발생합니다. MVCC는 이 문제를 **데이터의 여러 버전을 동시에 유지**함으로써 해결합니다.

PostgreSQL에서 각 행(row)은 **숨겨진 시스템 컬럼**을 통해 버전 정보를 관리합니다.

| 컬럼 | 설명 |
|------|------|
| `xmin` | 해당 행을 삽입한 트랜잭션 ID |
| `xmax` | 해당 행을 삭제(또는 업데이트)한 트랜잭션 ID |
| `ctid` | 행의 물리적 위치 |

```sql
-- xmin, xmax 직접 조회하기
SELECT xmin, xmax, id, name FROM users WHERE id = 1;

/*
 xmin  | xmax | id |  name
-------+------+----+--------
 12345 |    0 |  1 | Alice
*/
```

UPDATE 연산 시 PostgreSQL은 기존 행을 수정하지 않고, **새로운 행을 INSERT하고 기존 행의 xmax를 현재 트랜잭션 ID로 설정**합니다. 이것이 MVCC의 핵심입니다.

### 트랜잭션 ID와 Snapshot

PostgreSQL은 트랜잭션이 시작될 때 **스냅샷(Snapshot)** 을 생성합니다. 스냅샷은 다음 정보를 포함합니다.

- `xmin`: 스냅샷 생성 시점의 가장 오래된 활성 트랜잭션 ID
- `xmax`: 스냅샷 생성 시점 이후에 할당될 다음 트랜잭션 ID
- `xip_list`: 스냅샷 생성 시점의 활성(진행 중인) 트랜잭션 ID 목록

이 스냅샷을 기반으로 **어떤 버전의 행이 현재 트랜잭션에게 보여야 하는지**를 결정합니다.

### Dead Tuple과 VACUUM

MVCC의 부작용으로 **Dead Tuple**이 쌓입니다. 더 이상 어떤 트랜잭션에도 보이지 않는 과거 버전의 행들입니다. PostgreSQL의 `VACUUM` 프로세스가 이를 정리합니다.

```sql
-- 테이블의 Dead Tuple 현황 확인
SELECT
    schemaname,
    relname,
    n_live_tup,
    n_dead_tup,
    last_vacuum,
    last_autovacuum
FROM pg_stat_user_tables
WHERE relname = 'orders';
```

---

## 트랜잭션 격리 수준

SQL 표준은 4가지 격리 수준을 정의하며, 각 수준은 발생 가능한 이상 현상(anomaly)을 다르게 허용합니다.

| 격리 수준 | Dirty Read | Non-Repeatable Read | Phantom Read |
|-----------|------------|---------------------|--------------|
| READ UNCOMMITTED | 가능 | 가능 | 가능 |
| READ COMMITTED | 불가 | 가능 | 가능 |
| REPEATABLE READ | 불가 | 불가 | 가능 |
| SERIALIZABLE | 불가 | 불가 | 불가 |

> **PostgreSQL의 특이점**: PostgreSQL은 `READ UNCOMMITTED`를 설정해도 내부적으로 `READ COMMITTED`처럼 동작합니다. MVCC 특성상 커밋되지 않은 데이터를 읽을 이유가 없기 때문입니다.

---

## 실전 예제

### READ COMMITTED (기본값)

PostgreSQL의 기본 격리 수준입니다. 각 쿼리가 실행될 때마다 새로운 스냅샷을 생성합니다.

```sql
-- 세션 1
BEGIN;
SELECT balance FROM accounts WHERE id = 1; -- 결과: 1000

-- (세션 2에서 UPDATE 후 COMMIT)

SELECT balance FROM accounts WHERE id = 1; -- 결과: 900 (Non-Repeatable Read 발생!)
COMMIT;
```

같은 트랜잭션 내에서 같은 쿼리를 실행해도 다른 결과가 나올 수 있습니다. **잔액 차감 후 포인트 적립**처럼 두 작업이 일관된 뷰를 필요로 한다면 문제가 됩니다.

### REPEATABLE READ

트랜잭션 시작 시점의 스냅샷을 트랜잭션 전체에 걸쳐 유지합니다.

```sql
-- 세션 1
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ;
SELECT balance FROM accounts WHERE id = 1; -- 결과: 1000

-- (세션 2에서 UPDATE 후 COMMIT)

SELECT balance FROM accounts WHERE id = 1; -- 결과: 1000 (동일한 결과 보장!)
COMMIT;
```

단, **업데이트 충돌**이 발생하면 PostgreSQL은 에러를 반환합니다.

```sql
-- 세션 1
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ;
UPDATE accounts SET balance = balance - 100 WHERE id = 1;

-- 세션 2가 동일한 행을 먼저 업데이트하고 COMMIT한 경우
-- 세션 1은 아래 에러 발생:
-- ERROR: could not serialize access due to concurrent update
```

이를 Spring에서 처리하는 예시입니다.

```java
@Service
@Transactional(isolation = Isolation.REPEATABLE_READ)
public class AccountService {

    private final AccountRepository accountRepository;
    private final JdbcTemplate jdbcTemplate;

    public void transferWithRetry(Long fromId, Long toId, BigDecimal amount) {
        int maxRetries = 3;
        int attempt = 0;

        while (attempt < maxRetries) {
            try {
                performTransfer(fromId, toId, amount);
                return;
            } catch (CannotSerializeTransactionException ex) {
                attempt++;
                log.warn("Serialization failure, retrying... attempt {}/{}", attempt, maxRetries);
                if (attempt >= maxRetries) {
                    throw new RuntimeException("Transfer failed after " + maxRetries + " attempts", ex);
                }
            }
        }
    }

    @Transactional(isolation = Isolation.REPEATABLE_READ,
                   rollbackFor = Exception.class)
    private void performTransfer(Long fromId, Long toId, BigDecimal amount) {
        Account from = accountRepository.findById(fromId)
            .orElseThrow(() -> new EntityNotFoundException("Account not found: " + fromId));

        if (from.getBalance().compareTo(amount) < 0) {
            throw new InsufficientBalanceException("잔액 부족");
        }

        from.setBalance(from.getBalance().subtract(amount));

        Account to = accountRepository.findById(toId)
            .orElseThrow(() -> new EntityNotFoundException("Account not found: " + toId));
        to.setBalance(to.getBalance().add(amount));

        accountRepository.saveAll(List.of(from, to));
    }
}
```

### SERIALIZABLE

가장 강력한 격리 수준으로, **SSI(Serializable Snapshot Isolation)** 알고리즘을 사용합니다. 모든 트랜잭션이 직렬(순차적)로 실행된 것과 동일한 결과를 보장합니다.

```sql
-- 재고 관리 시나리오: 재고가 0이 되는 경우를 방지

-- 세션 1
BEGIN TRANSACTION ISOLATION LEVEL SERIALIZABLE;
SELECT COUNT(*) FROM inventory WHERE product_id = 10 AND status = 'available'; -- 결과: 1

-- 세션 2
BEGIN TRANSACTION ISOLATION LEVEL SERIALIZABLE;
SELECT COUNT(*) FROM inventory WHERE product_id = 10 AND status = 'available'; -- 결과: 1

-- 세션 1
UPDATE inventory SET status = 'sold', buyer_id = 101
WHERE product_id = 10 AND status = 'available';
COMMIT; -- 성공

-- 세션 2
UPDATE inventory SET status = 'sold', buyer_id = 102
WHERE product_id = 10 AND status = 'available';
COMMIT;
-- ERROR: could not serialize access due to read/write dependencies among transactions
-- 세션 2는 자동으로 롤백됨
```

SERIALIZABLE은 Phantom Read도 방지합니다. 두 트랜잭션이 **상호 의존하는 읽기/쓰기 패턴**을 감지하면 하나를 롤백시킵니다.

### 격리 수준 설정 방법

```sql
-- 세션 레벨 설정
SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL REPEATABLE READ;

-- 트랜잭션별 설정
BEGIN ISOLATION LEVEL SERIALIZABLE;

-- 현재 격리 수준 확인
SHOW transaction_isolation;
```

```yaml
# Spring Boot application.yml에서 기본 설정
spring:
  datasource:
    hikari:
      transaction-isolation: TRANSACTION_REPEATABLE_READ
```

---

## 주의사항 및 트레이드오프

### 1. 격리 수준이 높을수록 성능 비용이 증가한다

`SERIALIZABLE`은 트랜잭션 간 의존성을 추적하기 위해 추가적인 메모리와 CPU를 사용합니다. 고트래픽 환경에서 불필요하게 높은 격리 수준을 사용하면 처리량이 급격히 감소할 수 있습니다.

```sql
-- pg_stat_activity로 현재 트랜잭션 격리 수준 모니터링
SELECT
    pid,
    usename,
    application_name,
    state,
    query_start,
    query
FROM pg_stat_activity
WHERE state != 'idle';
```

### 2. Long-running Transaction은 MVCC의 적이다

트랜잭션이 오래 열려 있으면 해당 트랜잭션 시작 이전의 Dead Tuple을 VACUUM이 정리하지 못합니다. 이는 **테이블 bloat**으로 이어져 성능을 저하시킵니다.

```sql
-- 오래 실행 중인 트랜잭션 탐지
SELECT
    pid,
    now() - pg_stat_activity.xact_start AS duration,
    query,
    state
FROM pg_stat_activity
WHERE (now() - pg_stat_activity.xact_start) > interval '5 minutes'
AND state != 'idle'
ORDER BY duration DESC;
```

### 3. Serialization Failure에 대한 재시도 로직은 필수다

`REPEATABLE READ`와 `SERIALIZABLE`에서는 직렬화 실패로 인한 롤백이 발생할 수 있습니다. 애플리케이션 레이어에서 이를 **반드시 처리**해야 합니다. Spring의 `@Retryable`을 활용하면 깔끔하게 구현할 수 있습니다.

```java
@Retryable(
    value = {CannotSerializeTransactionException.class},
    maxAttempts = 3,
    backoff = @Backoff(delay = 100, multiplier = 2)
)
@Transactional(isolation = Isolation.SERIALIZABLE)
public OrderResult placeOrder(OrderRequest request) {
    // 주문 처리 로직
}

@Recover
public OrderResult recoverPlaceOrder(CannotSerializeTransactionException ex, OrderRequest request) {
    log.error("Order placement failed after retries for request: {}", request.getId());
    throw new OrderProcessingException("주문 처리에 실패했습니다. 다시 시도해주세요.");
}
```

### 4. SELECT FOR UPDATE로 명시적 잠금 활용

낙관적 락 대신 **비관적 락**이 필요한 경우 `SELECT FOR UPDATE`를 사용합니다. 단, 데드락 가능성을 항상 염두에 두어야 합니다.

```sql
-- 항상 일관된 순서로 잠금 획득 (데드락 방지)
BEGIN;
SELECT * FROM accounts WHERE id IN (1, 2) ORDER BY id FOR UPDATE;
-- id 순서로 정렬하여 잠금을 일관되게 획득
UPDATE accounts SET balance = balance - 100 WHERE id = 1;
UPDATE accounts SET balance = balance + 100 WHERE id = 2;
COMMIT;
```

### 5. 격리 수준 선택 가이드

| 시나리오 | 권장 격리 수준 |
|----------|---------------|
| 단순 조회, 대시보드 | READ COMMITTED |
| 보고서, 통계 집계 | REPEATABLE READ |
| 금융 거래, 재고 차감 | REPEATABLE READ + 재시도 또는 SERIALIZABLE |
| 복잡한 비즈니스 불변식 보장 | SERIALIZABLE |

---

## 정리

PostgreSQL의 MVCC는 **읽기와 쓰기가 서로를 블로킹하지 않는다**는 강력한 특성을 제공합니다. 이를 올바르게 활용하려면 다음 사항을 기억하세요.

1. **MVCC는 읽기 성능을 위해 Dead Tuple을 희생하며**, VACUUM이 이를 관리합니다. Long-running Transaction은 이 과정을 방해합니다.

2. **격리 수준은 비즈니스 요구사항에 맞게 선택**하세요. 무조건 높은 격리 수준이 안전한 것이 아니라, 불필요한 성능 저하를 초래할 수 있습니다.

3. **REPEATABLE READ와 SERIALIZABLE에서는 직렬화 실패가 발생할 수 있으며**, 애플리케이션에서 적절한 재시도 로직이 반드시 필요합니다.

4. **PostgreSQL의 SERIALIZABLE은 SSI 알고리즘** 덕분에 전통적인 Lock 기반 구현보다 훨씬 효율적입니다. 필요한 경우 적극적으로 활용하세요.

동시성 문제는 단순히 격리 수준을 올리는 것으로 해결되지 않습니다. MVCC의 동작 원리를 이해하고, 적절한 인덱스 설계, 트랜잭션 범위 최소화, 잠금 순서 일관성 유지 등을 함께 고려해야 진정으로 안정적인 시스템을 구축할 수 있습니다.