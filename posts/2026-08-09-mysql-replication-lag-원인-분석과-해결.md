# MySQL Replication Lag 원인 분석과 해결

## 개요

MySQL Replication은 많은 서비스에서 읽기 확장성 확보와 고가용성 구성을 위해 필수적으로 사용되는 기술이다. 그러나 운영 환경에서 빠질 수 없이 마주치게 되는 문제가 바로 **Replication Lag(복제 지연)**이다.

Replication Lag이 심해지면 Read Replica에서 오래된 데이터를 읽는 현상(Stale Read)이 발생하고, 이는 사용자 경험 저하나 데이터 정합성 문제로 이어진다. 심각한 경우 Replica가 Master를 따라잡지 못한 상태에서 장애 조치(Failover)가 발생하면 데이터 유실 위험도 생긴다.

이 글에서는 Replication Lag이 발생하는 근본적인 원인들을 분석하고, 각 원인에 맞는 실전적인 해결 방법을 다룬다.

---

## 핵심 개념

### MySQL Replication 동작 방식

MySQL 비동기 복제는 다음 세 가지 스레드로 동작한다.

- **Binary Log Dump Thread (Master)**: Master에서 변경 사항을 Binary Log로 기록하고 Replica에 전송
- **I/O Thread (Replica)**: Master의 Binary Log를 읽어 Relay Log에 기록
- **SQL Thread (Replica)**: Relay Log를 읽어 실제 데이터에 적용

Replication Lag은 Master에서 변경이 발생한 시점과 Replica에 해당 변경이 완전히 적용된 시점 사이의 시간 차다.

```sql
-- Replica에서 현재 Lag 확인
SHOW SLAVE STATUS\G
-- 또는 MySQL 8.0+
SHOW REPLICA STATUS\G
```

출력 중 핵심 필드:
- `Seconds_Behind_Master`: 복제 지연 초(단순 지표로 참고)
- `Relay_Log_Space`: Relay Log 크기
- `Exec_Master_Log_Pos` vs `Read_Master_Log_Pos`: SQL Thread 처리 위치와 I/O Thread 수신 위치 비교

---

## Replication Lag의 주요 원인과 해결

### 원인 1: 단일 스레드 SQL Thread (Single-threaded Replication)

MySQL 5.6 이전의 기본 복제 방식은 SQL Thread가 단일 스레드로 동작한다. Master가 멀티 스레드로 수많은 트랜잭션을 처리하더라도, Replica는 직렬로 하나씩 적용하므로 처리 속도가 뒤처진다.

**해결: Multi-threaded Replication (MTS) 활성화**

```sql
-- my.cnf 설정
[mysqld]
# 병렬 복제 워커 스레드 수 (CPU 코어 수에 맞게 조정)
slave_parallel_workers = 8          # MySQL 5.7
replica_parallel_workers = 8        # MySQL 8.0+

# 병렬화 정책
# DATABASE: 다른 DB 간 병렬 처리 (5.6+)
# LOGICAL_CLOCK: 바이너리 로그 그룹 커밋 기반 병렬화 (5.7+, 권장)
slave_parallel_type = LOGICAL_CLOCK

# 순서 보장 (데이터 정합성 우선 시 활성화)
slave_preserve_commit_order = ON
```

`LOGICAL_CLOCK` 방식은 Master에서 동시에 커밋된 트랜잭션들을 병렬로 적용한다. Master의 `binlog_group_commit_sync_delay`와 `binlog_group_commit_sync_no_delay_count` 설정으로 그룹 커밋 효율을 높이면 병렬화 효과가 극대화된다.

```sql
-- Master에서 그룹 커밋 튜닝
SET GLOBAL binlog_group_commit_sync_delay = 1000;       -- 마이크로초
SET GLOBAL binlog_group_commit_sync_no_delay_count = 100;
```

---

### 원인 2: 대용량 트랜잭션 (Long Transaction)

배치 작업이나 대량 UPDATE/DELETE를 단일 트랜잭션으로 처리하면 Replica에서도 동일하게 긴 시간이 소요된다. Master에서 1분 걸린 작업이 Replica에서도 1분 이상 걸리면서 Lag이 누적된다.

**해결: 트랜잭션 청킹(Chunking)**

```sql
-- 나쁜 예: 전체를 한 번에 처리
DELETE FROM order_logs WHERE created_at < '2023-01-01';

-- 좋은 예: 배치로 나누어 처리
DELIMITER $$
CREATE PROCEDURE delete_old_logs()
BEGIN
    DECLARE rows_deleted INT DEFAULT 1;
    
    WHILE rows_deleted > 0 DO
        DELETE FROM order_logs 
        WHERE created_at < '2023-01-01' 
        LIMIT 1000;
        
        SET rows_deleted = ROW_COUNT();
        
        -- Replica가 따라잡을 시간을 줌
        DO SLEEP(0.1);
    END WHILE;
END$$
DELIMITER ;
```

Java/Spring 배치 환경에서 청킹 처리:

```java
@Service
@RequiredArgsConstructor
public class LogCleanupService {

    private final JdbcTemplate jdbcTemplate;
    private static final int CHUNK_SIZE = 1000;
    private static final long SLEEP_MS = 100L;

    @Transactional
    public void deleteOldLogsInChunks(LocalDateTime cutoffDate) throws InterruptedException {
        int deletedRows;
        long totalDeleted = 0;

        do {
            deletedRows = jdbcTemplate.update(
                "DELETE FROM order_logs WHERE created_at < ? LIMIT ?",
                Timestamp.valueOf(cutoffDate), CHUNK_SIZE
            );
            totalDeleted += deletedRows;

            if (deletedRows > 0) {
                // Replica에 처리 여유 시간 부여
                Thread.sleep(SLEEP_MS);
                log.info("Deleted {} rows (total: {})", deletedRows, totalDeleted);
            }
        } while (deletedRows == CHUNK_SIZE);

        log.info("Cleanup complete. Total deleted: {}", totalDeleted);
    }
}
```

---

### 원인 3: 인덱스 미비로 인한 Row-based Replication 성능 저하

Row-based Binary Log(RBR) 사용 시, Replica의 SQL Thread는 변경된 각 행을 찾기 위해 테이블 풀 스캔을 할 수 있다. 특히 `binlog_row_image = FULL` 설정에서 PK가 없는 테이블은 Replica에서 심각한 성능 저하를 유발한다.

```sql
-- Replica에서 풀스캔이 발생하는지 확인
-- performance_schema 활용
SELECT * FROM performance_schema.replication_applier_status_by_worker;

-- 모든 테이블에 PK가 있는지 확인
SELECT t.table_schema, t.table_name
FROM information_schema.tables t
LEFT JOIN information_schema.table_constraints tc
    ON t.table_schema = tc.table_schema
    AND t.table_name = tc.table_name
    AND tc.constraint_type = 'PRIMARY KEY'
WHERE t.table_type = 'BASE TABLE'
    AND tc.constraint_name IS NULL
    AND t.table_schema NOT IN ('mysql', 'information_schema', 'performance_schema');
```

PK 없는 테이블에는 즉시 PK를 추가하고, `binlog_row_image`를 `MINIMAL`로 설정하는 것도 고려할 수 있다.

```sql
-- Master 설정
SET GLOBAL binlog_row_image = 'MINIMAL';
```

---

### 원인 4: Replica 서버 리소스 부족

Replica의 CPU, I/O, 메모리가 부족한 경우에도 Lag이 발생한다. 특히 클라우드 환경에서 비용 절감을 위해 Replica를 낮은 사양으로 운영하다가 문제가 생기는 경우가 많다.

**모니터링 쿼리:**

```sql
-- I/O 관련 Replica 상태 확인
SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_reads';
SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_read_requests';

-- Buffer pool hit ratio 계산
SELECT 
    (1 - (
        variable_value / (
            SELECT variable_value 
            FROM performance_schema.global_status 
            WHERE variable_name = 'Innodb_buffer_pool_read_requests'
        )
    )) * 100 AS buffer_pool_hit_ratio
FROM performance_schema.global_status
WHERE variable_name = 'Innodb_buffer_pool_reads';
```

Buffer pool hit ratio가 99% 미만이면 메모리 증설 또는 `innodb_buffer_pool_size` 증가를 고려한다.

---

### 원인 5: 네트워크 지연 및 Binary Log 전송 병목

Master와 Replica 간 네트워크 지연이 크거나, Binary Log 전송 자체가 병목인 경우다.

```sql
-- I/O Thread 상태 확인
SHOW REPLICA STATUS\G
-- Master_Log_File과 Relay_Master_Log_File이 다르면 I/O Thread가 따라가지 못하는 것

-- Semi-synchronous 복제 상태 확인 (사용 중인 경우)
SHOW GLOBAL STATUS LIKE 'Rpl_semi_sync%';
```

Semi-synchronous 복제를 사용 중이라면 `rpl_semi_sync_master_timeout` 값이 너무 짧아 비동기로 폴백되는 빈도가 높지 않은지 확인한다.

---

## Replication Lag 모니터링 자동화

Prometheus + MySQL Exporter를 활용한 모니터링 외에도, 애플리케이션 레벨에서 Lag을 감지하고 대응하는 방어 코드를 작성할 수 있다.

```java
@Component
@RequiredArgsConstructor
public class ReplicationLagChecker {

    private final DataSource replicaDataSource;
    private static final int LAG_THRESHOLD_SECONDS = 5;

    /**
     * Replica Lag이 임계값 초과 시 true 반환
     */
    public boolean isLagExceeded() {
        try (Connection conn = replicaDataSource.getConnection();
             Statement stmt = conn.createStatement();
             ResultSet rs = stmt.executeQuery("SHOW REPLICA STATUS")) {

            if (rs.next()) {
                int lag = rs.getInt("Seconds_Behind_Master");
                // NULL인 경우(복제 중단) rs.wasNull() 체크
                if (rs.wasNull()) {
                    log.error("Replication is not running!");
                    return true;
                }
                return lag > LAG_THRESHOLD_SECONDS;
            }
        } catch (SQLException e) {
            log.error("Failed to check replication lag", e);
        }
        return false;
    }
}

// 중요한 읽기 요청에서 Lag 체크 후 Master로 폴백
@Service
@RequiredArgsConstructor
public class OrderService {

    private final OrderRepository orderRepository;
    private final ReplicationLagChecker lagChecker;
    private final EntityManager masterEntityManager; // Master DB EntityManager

    public Order getOrderForPayment(Long orderId) {
        // 결제와 같이 정합성이 중요한 읽기는 Lag 시 Master에서 조회
        if (lagChecker.isLagExceeded()) {
            log.warn("Replica lag exceeded threshold. Reading from master.");
            return masterEntityManager.find(Order.class, orderId);
        }
        return orderRepository.findById(orderId).orElseThrow();
    }
}
```

---

## 주의사항 및 트레이드오프

### `slave_preserve_commit_order` 활성화 비용

MTS(Multi-threaded Slave) + `slave_preserve_commit_order = ON` 설정은 커밋 순서를 보장하지만, 내부적으로 큐 대기가 발생해 일부 병렬화 효과가 감소한다. 순서 보장이 반드시 필요한 경우에만 활성화하자.

### `LOGICAL_CLOCK` 방식의 전제 조건

`LOGICAL_CLOCK`은 Master에서 그룹 커밋이 활발하게 발생해야 효과적이다. 쓰기 트랜잭션이 매우 적은 환경에서는 병렬화 효과가 미미하다.

### Semi-sync와 Lag

Semi-synchronous 복제는 Lag 자체를 줄이지 않는다. 오히려 Master 쓰기 성능에 영향을 줄 수 있다. Semi-sync는 **데이터 내구성** 목적으로 사용하고, Lag 해소는 별도로 접근해야 한다.

### Stale Read 허용 범위 정의

모든 읽기를 Master로 보내는 것은 Replica의 존재 의미를 없앤다. 비즈니스 요구사항에 따라 "몇 초간의 지연이 허용 가능한가"를 명확히 정의하고, 정합성이 필수인 경우에만 Master로 라우팅하는 정책을 수립하는 것이 중요하다.

---

## 정리

MySQL Replication Lag은 단일 원인보다는 복합적인 요인이 맞물려 발생하는 경우가 많다. 문제 해결 순서를 정리하면 다음과 같다.

| 확인 순서 | 원인 | 주요 해결책 |
|---|---|---|
| 1 | SQL Thread 단일 처리 | MTS + LOGICAL_CLOCK 활성화 |
| 2 | 대용량 트랜잭션 | 배치 청킹 처리 |
| 3 | PK 없는 테이블 | PK 추가, binlog_row_image 최적화 |
| 4 | Replica 리소스 부족 | 사양 증설, buffer pool 튜닝 |
| 5 | 네트워크/전송 병목 | 네트워크 점검, Semi-sync 설정 검토 |

Replication Lag을 0으로 만드는 것은 현실적으로 불가능하다. 목표는 **Lag을 수용 가능한 수준으로 유지**하고, Lag이 임계값을 초과하는 상황에서 **애플리케이션이 안전하게 대응**하도록 설계하는 것이다. 모니터링과 알림 체계를 갖추고, 장애 발생 전에 선제적으로 대응하는 습관이 안정적인 복제 환경을 만드는 핵심이다.