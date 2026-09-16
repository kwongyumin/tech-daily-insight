# MySQL HA·DR 토폴로지 설계와 페일오버

## 개요

서비스 규모가 커질수록 데이터베이스의 가용성(High Availability, HA)과 재해 복구(Disaster Recovery, DR)는 선택이 아닌 필수가 된다. MySQL은 오픈소스 생태계에서 가장 널리 쓰이는 RDBMS 중 하나이지만, 기본 설치 상태로는 단일 장애점(Single Point of Failure)을 피할 수 없다.

이 글에서는 MySQL HA·DR 토폴로지의 핵심 개념을 정리하고, 실제 운영 환경에서 사용하는 페일오버 전략과 구성 예제를 다룬다. InnoDB Cluster, 비동기 복제, 그리고 외부 HA 솔루션(Orchestrator, ProxySQL)을 조합해 어떻게 견고한 아키텍처를 만들 수 있는지를 단계적으로 살펴본다.

---

## 핵심 개념

### 1. RPO와 RTO — 설계의 출발점

HA·DR 아키텍처를 설계하기 전에 반드시 비즈니스 요구사항을 숫자로 정의해야 한다.

- **RPO (Recovery Point Objective)**: 장애 발생 시 얼마나 오래된 데이터까지 손실을 허용하는가. RPO=0이면 데이터 손실 불허.
- **RTO (Recovery Time Objective)**: 장애 발생 후 서비스가 얼마 만에 복구되어야 하는가.

RPO와 RTO가 작을수록 비용과 복잡도는 올라간다. 설계 전에 이 값을 명확히 합의하지 않으면 과잉 설계나 미흡한 설계로 이어진다.

### 2. MySQL 복제 방식 비교

| 방식 | 데이터 보장 | 지연 | 복잡도 |
|------|------------|------|--------|
| 비동기 복제 (Async) | 낮음 (데이터 유실 가능) | 최소 | 낮음 |
| 반동기 복제 (Semi-sync) | 중간 (1개 이상 replica 확인) | 낮음 | 중간 |
| 그룹 복제 (Group Replication) | 높음 (쿼럼 기반) | 중간 | 높음 |

**GTID(Global Transaction Identifier)**는 복제 토폴로지에서 페일오버 시 바이너리 로그 위치를 자동으로 추적해주기 때문에, 현대적인 MySQL 구성에서는 사실상 필수다.

### 3. 주요 토폴로지 패턴

#### Single Primary + Replica (고전적 패턴)

```
[Primary] ──── binlog ────► [Replica-1]
                         └──► [Replica-2]
```

읽기 분산에는 효과적이지만, Primary 장애 시 수동 또는 외부 도구의 개입이 필요하다.

#### InnoDB Cluster (그룹 복제 기반)

```
[Primary]  ◄──── Group Replication ────► [Secondary-1]
                                      └──► [Secondary-2]
         [MySQL Router] ← 애플리케이션
```

MySQL Shell과 MySQL Router를 함께 사용하며, 쿼럼(quorum) 기반으로 자동 페일오버를 지원한다. 최소 3개 노드가 필요하다.

#### 다중 리전 DR 구성

```
[Region A - Primary]
        │ 반동기/비동기 복제
        ▼
[Region B - DR Replica]
```

리전 간 지연(latency) 때문에 완전 동기 복제는 현실적으로 어렵다. RPO를 최소화하려면 반동기 복제를 쓰되, 지연 허용 범위를 rpl_semi_sync_master_timeout으로 제어한다.

---

## 실전 예제

### InnoDB Cluster 구성 (MySQL Shell)

먼저 각 노드에서 공통 설정을 확인하고 적용한다.

```bash
# MySQL Shell로 클러스터 구성 준비
mysqlsh root@node1:3306

# 각 노드 설정 검증 및 자동 수정
JS> dba.checkInstanceConfiguration('root@node1:3306')
JS> dba.configureInstance('root@node1:3306')

# node2, node3도 동일하게 수행
JS> dba.configureInstance('root@node2:3306')
JS> dba.configureInstance('root@node3:3306')
```

```javascript
// 클러스터 생성 및 노드 추가
var cluster = dba.createCluster('prodCluster', {
  gtidSetIsComplete: true,
  replicationAllowedHost: '10.0.%'
});

cluster.addInstance('root@node2:3306', {
  recoveryMethod: 'clone'
});

cluster.addInstance('root@node3:3306', {
  recoveryMethod: 'clone'
});

// 클러스터 상태 확인
cluster.status();
```

### MySQL Router 설정

MySQL Router는 애플리케이션과 클러스터 사이에서 읽기/쓰기 라우팅과 페일오버를 처리한다.

```ini
# mysqlrouter.conf 핵심 설정
[routing:primary]
bind_address = 0.0.0.0
bind_port = 6446
destinations = metadata-cache://prodCluster/?role=PRIMARY
routing_strategy = first-available
protocol = classic

[routing:secondaries]
bind_address = 0.0.0.0
bind_port = 6447
destinations = metadata-cache://prodCluster/?role=SECONDARY
routing_strategy = round-robin-with-fallback
protocol = classic
```

```bash
# Router를 클러스터에 부트스트랩
mysqlrouter --bootstrap root@node1:3306 \
            --directory /etc/mysqlrouter \
            --conf-use-sockets \
            --account router_user \
            --force
```

### ProxySQL + Orchestrator 조합 (비동기 복제 환경)

InnoDB Cluster를 사용하지 않는 환경에서는 ProxySQL과 Orchestrator 조합이 널리 쓰인다.

```sql
-- ProxySQL 어드민 콘솔에서 호스트 그룹 설정
-- 10 = Primary, 20 = Replica
INSERT INTO mysql_servers (hostgroup_id, hostname, port, weight) VALUES
  (10, 'db-primary', 3306, 1000),
  (20, 'db-replica-1', 3306, 1000),
  (20, 'db-replica-2', 3306, 1000);

-- 쿼리 라우팅 규칙 설정
INSERT INTO mysql_query_rules (rule_id, active, match_digest, destination_hostgroup, apply) VALUES
  (1, 1, '^SELECT.*FOR UPDATE', 10, 1),
  (2, 1, '^SELECT', 20, 1),
  (3, 1, '.*', 10, 1);

LOAD MYSQL SERVERS TO RUNTIME;
LOAD MYSQL QUERY RULES TO RUNTIME;
SAVE MYSQL SERVERS TO DISK;
SAVE MYSQL QUERY RULES TO DISK;
```

```yaml
# orchestrator.conf.json (핵심 항목)
{
  "MySQLTopologyUser": "orchestrator",
  "MySQLTopologyPassword": "secret",
  "RecoverMasterClusterFilters": ["*"],
  "RecoverIntermediateMasterClusterFilters": ["*"],
  "FailMasterPromotionIfSQLThreadNotUpToDate": true,
  "DelayMasterPromotionIfSQLThreadNotUpToDate": true,
  "PostMasterFailoverProcesses": [
    "echo 'Failover completed. New master: {failureClusterAlias}' | mail -s 'DB Failover' ops@example.com",
    "/usr/local/bin/update_proxysql_primary.sh {successorHost} {successorPort}"
  ]
}
```

### Spring Boot 애플리케이션에서 멀티 DataSource 설정

```yaml
# application.yml
spring:
  datasource:
    primary:
      jdbc-url: jdbc:mysql://proxysql:6446/mydb?useSSL=true&serverTimezone=UTC
      username: appuser
      password: secret
      hikari:
        maximum-pool-size: 20
        connection-timeout: 3000
        validation-timeout: 1000
    replica:
      jdbc-url: jdbc:mysql://proxysql:6447/mydb?useSSL=true&serverTimezone=UTC
      username: appuser
      password: secret
      hikari:
        maximum-pool-size: 30
        connection-timeout: 3000
```

```java
@Configuration
public class DataSourceConfig {

    @Bean
    @ConfigurationProperties("spring.datasource.primary.hikari")
    public HikariDataSource primaryDataSource() {
        return DataSourceBuilder.create()
                .type(HikariDataSource.class)
                .build();
    }

    @Bean
    @ConfigurationProperties("spring.datasource.replica.hikari")
    public HikariDataSource replicaDataSource() {
        return DataSourceBuilder.create()
                .type(HikariDataSource.class)
                .build();
    }

    @Bean
    public DataSource routingDataSource(
            @Qualifier("primaryDataSource") DataSource primary,
            @Qualifier("replicaDataSource") DataSource replica) {

        AbstractRoutingDataSource routing = new AbstractRoutingDataSource() {
            @Override
            protected Object determineCurrentLookupKey() {
                return TransactionSynchronizationManager.isCurrentTransactionReadOnly()
                        ? "replica" : "primary";
            }
        };

        Map<Object, Object> sources = new HashMap<>();
        sources.put("primary", primary);
        sources.put("replica", replica);

        routing.setTargetDataSources(sources);
        routing.setDefaultTargetDataSource(primary);
        routing.afterPropertiesSet();
        return routing;
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. 페일오버 시 Split-Brain 문제

그룹 복제나 외부 HA 도구 없이 수동 페일오버를 할 때 가장 위험한 상황이 Split-Brain이다. 구 Primary가 완전히 죽지 않은 상태에서 새 Primary가 쓰기를 받으면 데이터가 충돌한다.

**대응책**:
- Fencing(STONITH): 구 Primary를 네트워크 레벨에서 격리
- `super_read_only = ON`을 페일오버 직후 구 Primary에 강제 적용
- InnoDB Cluster는 쿼럼을 잃은 노드가 자동으로 read-only로 전환됨

### 2. 비동기 복제에서의 데이터 유실

Orchestrator가 페일오버를 수행할 때, Replica가 Primary의 마지막 트랜잭션을 다 받지 못한 상태일 수 있다.

- `errant transaction` 발생 시 해당 Replica는 새 Primary의 복제 체인에 참여하지 못한다.
- `FailMasterPromotionIfSQLThreadNotUpToDate: true` 설정으로 SQL 스레드가 따라잡을 때까지 프로모션을 지연시키는 것이 안전하다.
- 완전한 RPO=0이 필요하다면 반동기 복제(semi-sync)로 전환해야 한다.

### 3. MySQL Router / ProxySQL의 헬스체크 튜닝

프록시의 헬스체크 주기가 너무 길면 장애 감지가 늦어지고, 너무 짧으면 정상 노드에 부하를 주거나 false positive가 발생한다.

```ini
# ProxySQL 헬스체크 튜닝
mysql_variables:
  monitor_ping_interval: 2000       # 2초마다 Ping
  monitor_connect_timeout: 200      # 연결 타임아웃 200ms
  monitor_ping_timeout: 100         # Ping 타임아웃 100ms
  monitor_read_only_interval: 1500  # read_only 확인 주기
```

### 4. 리전 간 DR — 지연과 일관성의 타협

리전 간 동기 복제는 왕복 지연(RTT) 때문에 쓰기 레이턴시를 크게 높인다. 실무에서는 아래 전략을 주로 사용한다.

- **비동기 복제 + 정기 백업**: RPO는 수 분 수준. 복잡도 낮음.
- **반동기 복제 + 짧은 타임아웃**: RPO는 수 초 이내. 타임아웃 초과 시 자동으로 비동기로 강등되는 점에 주의.
- **binlog 기반 CDC 파이프라인(Debezium 등)**: DR 리전에 거의 실시간으로 변경분을 전달하되, 실제 MySQL 복제 토폴로지 밖에서 관리.

### 5. 클러스터 확장 시 노드 수 홀수 유지

그룹 복제는 쿼럼(과반수) 기반이므로 노드 수가 짝수이면 네트워크 파티션 발생 시 어느 쪽도 쿼럼을 확보하지 못할 수 있다. **항상 홀수(3, 5, 7) 노드**로 구성한다.

---

## 정리

MySQL HA·DR 설계는 단일 솔루션으로 해결되지 않는다. RPO·RTO 목표에 따라 복제 방식(비동기, 반동기, 그룹 복제)을 결정하고, 토폴로지에 맞는 페일오버 도구(InnoDB Cluster, Orchestrator, ProxySQL)를 조합해야 한다.

| 목표 | 권장 구성 |
|------|-----------|
| 낮은 복잡도, 읽기 분산 | 비동기 복제 + ProxySQL |
| 자동 페일오버, RPO < 수 초 | InnoDB Cluster + MySQL Router |
| 멀티 리전 DR | 반동기 복제 또는 CDC 파이프라인 |
| RPO = 0 (무손실) | 그룹 복제 + 반동기 강제 모드 |

핵심은 **장애 시나리오를 미리 정의하고, 정기적으로 페일오버를 테스트하는 것**이다. 문서에만 존재하는 DR 계획은 실제 장애 앞에서 무력하다. 스테이징 환경에서 Chaos Engineering 방식으로 노드를 강제 종료해보고, 페일오버 시간과 데이터 정합성을 실측하는 습관이 안정적인 서비스의 기반이 된다.
