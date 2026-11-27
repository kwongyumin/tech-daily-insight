# MySQL InnoDB 버퍼 풀 튜닝 전략

## 개요

MySQL을 사용하는 서비스에서 성능 병목의 상당 부분은 디스크 I/O에서 발생한다. InnoDB 스토리지 엔진은 이 문제를 완화하기 위해 **버퍼 풀(Buffer Pool)** 이라는 메모리 캐시 구조를 사용한다. 버퍼 풀은 데이터 페이지와 인덱스 페이지를 메모리에 올려두어 디스크 접근을 최소화하는 핵심 메커니즘이다.

버퍼 풀 튜닝은 단순히 크기를 키우는 것에 그치지 않는다. 풀의 크기, 인스턴스 구성, 페이지 교체 정책, 플러시 전략까지 종합적으로 이해하고 조정해야 실질적인 성능 개선을 얻을 수 있다. 이 글에서는 InnoDB 버퍼 풀의 내부 동작 원리부터 실무에서 적용 가능한 튜닝 전략까지 단계적으로 다룬다.

---

## 핵심 개념

### 버퍼 풀의 구조

InnoDB 버퍼 풀은 **16KB 단위의 페이지(Page)** 로 구성된다. 각 페이지는 테이블 데이터 또는 인덱스 데이터를 담고 있으며, 버퍼 풀은 이 페이지들을 메모리에서 관리한다.

내부적으로 버퍼 풀은 두 가지 LRU 리스트로 나뉜다.

- **Young 영역 (New Sublist)**: 최근에 자주 접근된 "핫(hot)" 페이지
- **Old 영역 (Old Sublist)**: 최근에 로드되었지만 아직 충분히 접근되지 않은 페이지

새로운 페이지가 버퍼 풀에 적재되면 Old 영역의 헤드에 먼저 삽입된다. 이후 `innodb_old_blocks_time` 으로 설정된 시간(기본값 1000ms) 이후에 다시 접근되면 Young 영역으로 승격된다. 이 구조 덕분에 풀 테이블 스캔처럼 일시적으로 대량의 페이지를 적재하는 작업이 기존 핫 데이터를 밀어내는 현상(Cache Pollution)을 방지할 수 있다.

### 주요 상태 지표

튜닝에 앞서 현재 버퍼 풀 상태를 측정하는 것이 먼저다.

```sql
-- 버퍼 풀 전체 현황 확인
SHOW ENGINE INNODB STATUS\G

-- 주요 메트릭 개별 조회
SELECT 
    variable_name,
    variable_value
FROM performance_schema.global_status
WHERE variable_name IN (
    'Innodb_buffer_pool_reads',         -- 디스크에서 읽은 횟수
    'Innodb_buffer_pool_read_requests', -- 총 읽기 요청 수
    'Innodb_buffer_pool_pages_total',   -- 전체 페이지 수
    'Innodb_buffer_pool_pages_free',    -- 여유 페이지 수
    'Innodb_buffer_pool_pages_dirty',   -- 더티 페이지 수
    'Innodb_buffer_pool_wait_free'      -- 여유 페이지 대기 횟수
);
```

**버퍼 풀 히트율(Hit Rate)** 은 가장 중요한 지표다.

```sql
-- 버퍼 풀 히트율 계산
SELECT 
    (1 - (
        SUM(CASE WHEN variable_name = 'Innodb_buffer_pool_reads' THEN variable_value ELSE 0 END) /
        SUM(CASE WHEN variable_name = 'Innodb_buffer_pool_read_requests' THEN variable_value ELSE 0 END)
    )) * 100 AS buffer_pool_hit_rate
FROM performance_schema.global_status
WHERE variable_name IN (
    'Innodb_buffer_pool_reads',
    'Innodb_buffer_pool_read_requests'
);
```

일반적으로 히트율이 **99% 미만**이라면 버퍼 풀 크기나 구성을 재검토할 필요가 있다.

---

## 실전 튜닝 전략

### 1. 버퍼 풀 크기 설정

가장 기본적인 튜닝 포인트다. 일반적인 권장 사항은 **전체 물리 메모리의 50~75%** 를 버퍼 풀에 할당하는 것이다. 단, OS와 다른 프로세스(복제, 모니터링 에이전트 등)를 위한 여유분은 반드시 남겨야 한다.

```ini
# my.cnf 또는 my.ini
[mysqld]
innodb_buffer_pool_size = 12G  # 16GB 서버 기준 예시
```

MySQL 5.7.5 이후부터는 **온라인으로 버퍼 풀 크기를 변경**할 수 있다.

```sql
-- 서비스 중단 없이 버퍼 풀 크기 조정
SET GLOBAL innodb_buffer_pool_size = 12884901888; -- 12GB (바이트 단위)

-- 변경 진행 상태 확인
SELECT event_name, work_completed, work_estimated
FROM performance_schema.events_stages_current
WHERE event_name LIKE '%buffer pool%';
```

> **주의**: 버퍼 풀 크기는 `innodb_buffer_pool_chunk_size`의 배수 × `innodb_buffer_pool_instances`의 배수로 자동 반올림된다. 계획한 크기가 그대로 적용되지 않을 수 있으므로 설정 후 반드시 확인하라.

### 2. 버퍼 풀 인스턴스 분할

버퍼 풀이 클수록 내부 뮤텍스 경합이 심해진다. `innodb_buffer_pool_instances`를 통해 버퍼 풀을 여러 개의 독립 인스턴스로 분리하면 동시성을 개선할 수 있다.

```ini
[mysqld]
innodb_buffer_pool_size     = 12G
innodb_buffer_pool_instances = 8  # 인스턴스당 약 1.5GB
```

**권장 기준:**
- 버퍼 풀 크기가 1GB 미만이면 인스턴스를 1개로 유지
- 1GB 이상이면 보통 인스턴스당 1~2GB가 되도록 분할
- 최대 64개까지 설정 가능하지만, CPU 코어 수와 워크로드를 고려해야 함

### 3. 플러시(Flush) 전략 최적화

더티 페이지를 디스크에 쓰는 플러시 작업은 I/O 부하를 직접 유발한다. 아래 파라미터들로 플러시 빈도와 강도를 조절할 수 있다.

```ini
[mysqld]
# 더티 페이지 비율 목표 (기본값 90, 낮출수록 더 자주 플러시)
innodb_max_dirty_pages_pct        = 75
innodb_max_dirty_pages_pct_lwm    = 10   # 플러시 시작 하한선

# 적응형 플러시 활성화 (I/O 부하에 따라 자동 조절)
innodb_adaptive_flushing          = ON
innodb_adaptive_flushing_lwm      = 10

# I/O 용량 설정 (SSD라면 높게, HDD라면 낮게)
innodb_io_capacity                = 2000
innodb_io_capacity_max            = 4000

# 플러시 이웃 페이지 (SSD는 0으로, HDD는 1 또는 2)
innodb_flush_neighbors            = 0
```

### 4. Old 영역 비율과 접근 시간 조정

풀 테이블 스캔 등의 작업으로 인한 캐시 오염을 막기 위한 파라미터다.

```ini
[mysqld]
# Old 영역이 전체 버퍼 풀에서 차지하는 비율 (기본 37%)
innodb_old_blocks_pct  = 37

# Old 영역에서 Young으로 승격되기까지 최소 경과 시간 (ms)
# 배치 작업이 많은 환경에서는 늘리는 것을 고려
innodb_old_blocks_time = 1000
```

배치 작업이 많거나 OLAP성 쿼리가 섞인 환경이라면 `innodb_old_blocks_time`을 2000~5000ms로 늘려 핫 데이터 보호를 강화할 수 있다.

### 5. 버퍼 풀 워밍업 (Warm-up)

서버 재시작 후 버퍼 풀이 채워지기까지 성능이 급격히 저하되는 "콜드 스타트" 문제가 있다. InnoDB는 버퍼 풀 덤프/로드 기능으로 이를 완화할 수 있다.

```ini
[mysqld]
# 셧다운 시 버퍼 풀 상태를 자동으로 저장
innodb_buffer_pool_dump_at_shutdown = ON

# 시작 시 자동으로 버퍼 풀 복원
innodb_buffer_pool_load_at_startup  = ON

# 각 인스턴스에서 덤프할 페이지 비율 (기본 25%)
innodb_buffer_pool_dump_pct         = 50
```

수동으로도 덤프/로드가 가능하다.

```sql
-- 현재 버퍼 풀 상태를 파일로 저장
SET GLOBAL innodb_buffer_pool_dump_now = ON;

-- 파일에서 버퍼 풀 복원
SET GLOBAL innodb_buffer_pool_load_now = ON;

-- 로드 진행 상태 확인
SHOW STATUS LIKE 'Innodb_buffer_pool_load_status';
```

---

## 모니터링 쿼리 모음

튜닝 효과를 지속적으로 모니터링하는 쿼리를 준비해두는 것이 좋다.

```sql
-- 인스턴스별 버퍼 풀 상세 현황
SELECT 
    pool_id,
    pool_size,
    free_buffers,
    database_pages,
    old_database_pages,
    modified_database_pages AS dirty_pages,
    pages_made_young,
    pages_not_made_young,
    pages_read,
    pages_created,
    pages_written
FROM information_schema.INNODB_BUFFER_POOL_STATS;

-- 버퍼 풀에 올라온 테이블별 페이지 점유 현황
SELECT 
    table_name,
    COUNT(*) AS page_count,
    COUNT(*) * 16 / 1024 AS size_mb,
    SUM(IF(is_old = 'YES', 1, 0)) AS old_page_count
FROM information_schema.INNODB_BUFFER_PAGE
WHERE table_name IS NOT NULL
GROUP BY table_name
ORDER BY page_count DESC
LIMIT 20;
```

---

## 주의사항 및 트레이드오프

### 버퍼 풀을 무조건 크게 잡으면 안 된다

물리 메모리보다 큰 버퍼 풀을 설정하면 OS가 스왑(swap)을 사용하게 되어 오히려 성능이 급락한다. 또한 메모리를 너무 많이 점유하면 연결 스레드, 소트 버퍼, 조인 버퍼 등 다른 메모리 영역이 부족해질 수 있다.

```
전체 메모리 사용량 추산:
버퍼 풀 + (max_connections × 스레드당 메모리) + OS 여유분 ≤ 물리 메모리
```

### innodb_io_capacity는 스토리지 특성에 맞게 설정해야 한다

이 값이 너무 낮으면 더티 페이지가 쌓여 체크포인트가 밀리고, 너무 높으면 I/O를 과도하게 사용해 쿼리 응답 시간에 악영향을 준다. SSD의 경우 실제 디스크 벤치마크(`fio` 등)를 통해 IOPS를 측정한 후 약 50~75% 수준으로 설정하는 것을 권장한다.

### 인스턴스 수는 항상 많을수록 좋은 것이 아니다

인스턴스를 너무 많이 나누면 오히려 각 인스턴스의 용량이 작아져 LRU 관리 효율이 떨어진다. 인스턴스당 최소 1GB 이상이 되도록 유지하는 것이 일반적인 권장사항이다.

### 버퍼 풀 덤프/로드는 대규모 환경에서 시간이 오래 걸린다

덤프 파일에는 페이지의 실제 데이터가 아니라 (space_id, page_no) 쌍만 저장된다. 로드 시 이 목록을 기반으로 디스크에서 다시 읽어오므로, 버퍼 풀이 클수록, 디스크 I/O가 느릴수록 복원 시간이 길어진다. 로드 완료 전에는 성능이 완전히 회복되지 않는다는 점을 운영 계획에 반영해야 한다.

---

## 정리

InnoDB 버퍼 풀 튜닝은 단순한 파라미터 조정이 아니라 **시스템의 메모리 구조, 스토리지 특성, 워크로드 패턴을 종합적으로 이해하는 작업**이다. 핵심을 정리하면 다음과 같다.

| 항목 | 권장 접근 방식 |
|---|---|
| 버퍼 풀 크기 | 물리 메모리의 50~75%, 나머지는 OS와 연결 메모리용으로 확보 |
| 인스턴스 수 | 인스턴스당 1~2GB 기준으로 분할 |
| I/O 용량 | 실측 IOPS의 50~75% 수준으로 설정 |
| flush_neighbors | SSD는 0, HDD는 1~2 |
| 워밍업 | dump_at_shutdown / load_at_startup 활성화 |
| 모니터링 | 히트율 99% 이상 유지, 더티 페이지 비율 주시 |

튜닝은 항상 **측정 → 변경 → 검증**의 순서로 진행해야 한다. 여러 파라미터를 한꺼번에 바꾸면 어느 설정이 효과를 냈는지 알 수 없다. 한 번에 하나씩 변경하고, 충분한 시간 동안 모니터링한 뒤 다음 단계로 넘어가는 습관이 중요하다.
