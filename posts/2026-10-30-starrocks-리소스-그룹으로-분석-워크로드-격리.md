# StarRocks 리소스 그룹으로 분석 워크로드 격리

## 개요

분석 플랫폼을 운영하다 보면 필연적으로 마주치는 문제가 있다. 대시보드를 위한 짧고 빠른 쿼리와, 데이터 과학자가 돌리는 수십 분짜리 집계 쿼리, 그리고 ETL 파이프라인의 배치 쿼리가 같은 클러스터 자원을 두고 경쟁하는 상황이다. 결과는 예측 가능하다. 무거운 쿼리 하나가 클러스터 전체를 잠식하면 실시간 대시보드가 타임아웃 나고, 온콜 알람이 울리기 시작한다.

StarRocks는 이 문제를 해결하기 위해 **리소스 그룹(Resource Group)** 기능을 제공한다. CPU, 메모리, 동시 쿼리 수를 논리적으로 분리하여 워크로드 유형별로 격리할 수 있다. 이 글에서는 리소스 그룹의 핵심 개념부터 실전 설정 예제, 그리고 운영 시 주의해야 할 트레이드오프까지 다룬다.

---

## 핵심 개념

### 리소스 그룹이란?

StarRocks의 리소스 그룹은 쿼리 실행 시 사용할 수 있는 자원의 상한선을 정의하는 논리적 단위다. BE(Backend) 노드 수준에서 동작하며, 각 BE 노드는 리소스 그룹 설정에 따라 CPU 코어와 메모리를 분할해 제공한다.

핵심 파라미터는 다음과 같다:

| 파라미터 | 설명 |
|---|---|
| `cpu_core_limit` | 해당 그룹이 사용할 수 있는 CPU 코어 수 (소프트 리밋) |
| `mem_limit` | BE 총 메모리 대비 비율 (0.0~1.0) |
| `concurrency_limit` | 동시에 실행할 수 있는 쿼리 수 |
| `big_query_cpu_second_limit` | 단일 쿼리의 CPU 사용 시간 상한 |
| `big_query_scan_rows_limit` | 단일 쿼리의 스캔 행 수 상한 |
| `big_query_mem_limit` | 단일 쿼리의 메모리 사용 상한 |

`cpu_core_limit`은 소프트 리밋으로 동작한다는 점이 중요하다. 다른 그룹에 유휴 자원이 있으면 초과해서 사용할 수 있다. 완전한 격리가 필요하다면 `type=short_query`나 별도 클러스터 분리를 고려해야 한다.

### 분류자(Classifier)

리소스 그룹만 만들어서는 동작하지 않는다. 어떤 쿼리를 어느 그룹에 배정할지 결정하는 **분류자(Classifier)**를 함께 정의해야 한다.

분류자는 다음 조건으로 쿼리를 매칭한다:

- `user`: 특정 데이터베이스 사용자
- `role`: 사용자의 역할
- `query_type`: `SELECT`, `INSERT` 등
- `source_ip`: 클라이언트 IP 또는 CIDR
- `databases`: 접근하는 데이터베이스 이름

여러 분류자가 매칭될 경우, StarRocks는 **명확도(specificity)** 기준으로 가장 구체적인 분류자를 선택한다. 예를 들어 `user + role`을 모두 지정한 분류자가 `user`만 지정한 분류자보다 우선순위가 높다.

---

## 실전 예제

### 시나리오 설계

다음과 같은 워크로드 유형을 격리한다고 가정하자:

1. **대시보드 쿼리**: 응답 시간 < 3초, 동시 요청 많음
2. **애드혹 분석 쿼리**: 데이터 분석가가 실행, 수 분 소요 가능
3. **ETL 배치 쿼리**: 야간 배치, 응답 시간보다 처리량 중요

### 리소스 그룹 생성

```sql
-- 1. 대시보드용 리소스 그룹 (빠른 응답 우선)
CREATE RESOURCE GROUP dashboard_rg
PROPERTIES (
    "cpu_core_limit" = "16",
    "mem_limit" = "0.3",
    "concurrency_limit" = "50",
    "big_query_cpu_second_limit" = "60",
    "big_query_scan_rows_limit" = "100000000",
    "big_query_mem_limit" = "2147483648"  -- 2GB
);

-- 2. 애드혹 분석용 리소스 그룹
CREATE RESOURCE GROUP adhoc_rg
PROPERTIES (
    "cpu_core_limit" = "8",
    "mem_limit" = "0.4",
    "concurrency_limit" = "10",
    "big_query_cpu_second_limit" = "600",
    "big_query_scan_rows_limit" = "5000000000",
    "big_query_mem_limit" = "8589934592"  -- 8GB
);

-- 3. ETL 배치용 리소스 그룹
CREATE RESOURCE GROUP etl_rg
PROPERTIES (
    "cpu_core_limit" = "8",
    "mem_limit" = "0.25",
    "concurrency_limit" = "5",
    "big_query_cpu_second_limit" = "3600",
    "big_query_scan_rows_limit" = "10000000000",
    "big_query_mem_limit" = "17179869184"  -- 16GB
);
```

> **참고**: `mem_limit` 합산이 1.0을 초과해도 StarRocks가 즉시 오류를 내지는 않지만, 실제 메모리 경합이 발생할 수 있으므로 전체 합을 0.95 이하로 유지하는 것을 권장한다.

### 분류자 등록

```sql
-- 대시보드 서비스 계정에 분류자 적용
CREATE RESOURCE GROUP dashboard_rg
TO (
    user = 'dashboard_svc',
    query_type in ('SELECT')
);

-- 분석팀 역할에 분류자 적용
CREATE RESOURCE GROUP adhoc_rg
TO (
    role = 'analyst',
    query_type in ('SELECT')
);

-- ETL 전용 사용자 + IP 대역으로 분류자 적용
CREATE RESOURCE GROUP etl_rg
TO (
    user = 'etl_user',
    source_ip = '10.0.2.0/24'
);
```

분류자는 리소스 그룹 생성 시 `TO` 절로 함께 정의하거나, 이미 생성된 그룹에 나중에 추가할 수도 있다.

```sql
-- 기존 그룹에 분류자 추가
ALTER RESOURCE GROUP adhoc_rg
ADD (
    user = 'data_scientist_01',
    query_type in ('SELECT')
);
```

### 세션 레벨 수동 지정

자동 분류가 어렵거나 테스트가 필요할 때는 세션 변수로 직접 지정할 수 있다.

```sql
-- 세션 단위로 리소스 그룹 지정
SET resource_group = 'adhoc_rg';

-- 이후 실행되는 쿼리는 adhoc_rg에서 처리됨
SELECT
    user_id,
    COUNT(*) AS event_count,
    SUM(revenue) AS total_revenue
FROM events
WHERE event_date BETWEEN '2024-01-01' AND '2024-03-31'
GROUP BY user_id
ORDER BY total_revenue DESC
LIMIT 1000;

-- 원복
SET resource_group = '';
```

### 리소스 그룹 상태 조회

```sql
-- 등록된 리소스 그룹 목록 확인
SHOW RESOURCE GROUPS ALL;

-- 특정 그룹 상세 확인
SHOW RESOURCE GROUP dashboard_rg;
```

출력 예시:
```
+----------------+------+--------------+-----------+-------------------+-----------------------------+------------------------+------------------+
| name           | id   | cpu_core_limit | mem_limit | concurrency_limit | big_query_cpu_second_limit  | big_query_scan_rows_limit | classifiers    |
+----------------+------+--------------+-----------+-------------------+-----------------------------+------------------------+------------------+
| dashboard_rg   | 1001 | 16           | 0.3       | 50                | 60                          | 100000000              | ...            |
+----------------+------+--------------+-----------+-------------------+-----------------------------+------------------------+------------------+
```

### 모니터링 쿼리

운영 중에 어떤 그룹에서 쿼리가 실행되는지 확인하려면 `information_schema`를 활용한다.

```sql
-- 현재 실행 중인 쿼리와 리소스 그룹 확인
SELECT
    query_id,
    user,
    resource_group,
    query_cpu_time,
    query_mem_bytes,
    state,
    LEFT(query, 100) AS query_snippet
FROM information_schema.process_list
WHERE resource_group IS NOT NULL
ORDER BY query_cpu_time DESC;
```

Prometheus + Grafana를 사용한다면 StarRocks의 내장 메트릭 엔드포인트(`/metrics`)에서 `starrocks_be_resource_group_*` 계열 메트릭을 수집해 그룹별 CPU/메모리 사용량을 대시보드화할 수 있다.

---

## 주의사항 및 트레이드오프

### 1. cpu_core_limit은 소프트 리밋이다

앞서 언급했지만 이 점을 운영팀 전체가 명확히 인지해야 한다. 클러스터가 한산할 때 ETL 쿼리가 `cpu_core_limit`을 초과해 자원을 사용하는 것은 의도된 동작이다. 그러나 대시보드 트래픽이 몰리는 피크 타임에 ETL이 자원을 반납하지 않으면 대시보드 지연이 발생할 수 있다.

**완전한 격리가 필요한 경우**: `type=short_query` 속성을 갖는 특별 그룹을 생성하면 단기 쿼리 전용 CPU 슬롯을 예약할 수 있다. 단, 이 기능은 StarRocks 버전에 따라 지원 범위가 다를 수 있으므로 릴리즈 노트를 확인하자.

### 2. mem_limit 설정 시 OOM 리스크

`mem_limit`의 합산이 1.0을 넘더라도 StarRocks는 실제 쿼리 실행 시점에야 메모리를 할당한다. 따라서 모든 그룹이 동시에 풀로드 상태가 되면 OOM이 발생하고 BE 프로세스가 재시작될 수 있다. 여유 버퍼를 두고 합산 0.9~0.95 수준으로 설계하는 것이 안전하다.

### 3. 분류자 우선순위 혼란

분류자 매칭 로직이 직관적이지 않을 수 있다. 같은 사용자가 다수의 분류자에 매칭될 때 어느 그룹으로 들어가는지 반드시 테스트해야 한다.

```sql
-- 특정 쿼리가 어떤 리소스 그룹으로 분류되는지 확인
-- (실행 전 EXPLAIN 또는 실행 후 audit log 확인)
SELECT resource_group
FROM information_schema.process_list
WHERE query_id = 'your-query-id';
```

### 4. big_query 제한의 양날의 검

`big_query_cpu_second_limit`와 `big_query_scan_rows_limit`은 클러스터를 보호하는 훌륭한 수단이지만, 임계값을 너무 낮게 설정하면 정상적인 분석 쿼리가 중간에 강제 종료되는 사고가 발생한다. 초기에는 느슨하게 설정하고, 실제 쿼리 통계를 수집한 후 점진적으로 조여가는 방식을 권장한다.

### 5. 리소스 그룹은 FE 메타데이터에 저장된다

리소스 그룹 설정은 FE(Frontend)의 메타데이터에 저장된다. FE 재시작 후에도 설정이 유지되지만, DR(재해 복구) 시나리오나 클러스터 마이그레이션 시 설정을 별도로 백업/재적용해야 한다. 설정을 IaC(Infrastructure as Code)로 관리하는 것을 강력히 권장한다.

```bash
# 리소스 그룹 DDL을 파일로 관리하는 예시 구조
resource-groups/
├── dashboard_rg.sql
├── adhoc_rg.sql
├── etl_rg.sql
└── apply.sh
```

---

## 정리

StarRocks 리소스 그룹은 단일 클러스터에서 다양한 워크로드를 격리하는 실용적인 수단이다. 핵심을 요약하면 다음과 같다.

- **리소스 그룹**으로 CPU, 메모리, 동시 쿼리 수 상한을 워크로드 유형별로 정의한다.
- **분류자**로 사용자, 역할, IP, 쿼리 타입에 따라 자동으로 그룹을 배정한다.
- `cpu_core_limit`은 소프트 리밋임을 항상 기억하고, 완전 격리가 필요하면 클러스터 분리를 고려한다.
- `big_query_*` 제한은 클러스터 보호에 효과적이지만 임계값 튜닝이 필요하다.
- 설정을 SQL 파일로 버전 관리하고, Prometheus/Grafana로 그룹별 자원 사용량을 모니터링하자.

분석 플랫폼의 사용자 유형과 SLA가 다양해질수록 리소스 그룹의 중요성은 커진다. 초기 설계 단계부터 워크로드 분류 기준을 명확히 하고, 운영 데이터를 기반으로 지속적으로 임계값을 개선해 나가는 것이 장기적으로 안정적인 분석 플랫폼을 유지하는 핵심이다.
