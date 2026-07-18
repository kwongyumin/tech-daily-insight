# Redis Cluster 샤딩과 Failover 전략

## 개요

Redis는 단일 인스턴스로도 강력한 성능을 제공하지만, 수백만 건의 트래픽과 수 테라바이트에 달하는 데이터를 다뤄야 하는 프로덕션 환경에서는 단일 노드의 메모리 한계와 SPOF(Single Point of Failure) 문제가 반드시 등장합니다. Redis Cluster는 이 두 가지 문제를 동시에 해결하기 위한 공식 솔루션입니다.

이 글에서는 Redis Cluster의 핵심인 **해시 슬롯 기반 샤딩**과 **자동 Failover 메커니즘**을 깊이 있게 다루고, 실무에서 자주 마주치는 설정 이슈와 Spring Boot 연동 코드까지 함께 살펴봅니다.

---

## 핵심 개념

### 해시 슬롯(Hash Slot) 기반 샤딩

Redis Cluster는 데이터를 **16,384개의 해시 슬롯**으로 분산합니다. 각 키는 아래 공식으로 슬롯이 결정됩니다.

```
HASH_SLOT = CRC16(key) mod 16384
```

예를 들어 3개의 마스터 노드가 있다면 슬롯은 다음처럼 균등하게 분배됩니다.

| 노드 | 슬롯 범위 |
|------|-----------|
| Node A | 0 ~ 5460 |
| Node B | 5461 ~ 10922 |
| Node C | 10923 ~ 16383 |

클라이언트가 특정 키를 조회할 때 해당 슬롯을 담당하는 노드가 아니라면, Redis는 `MOVED` 리다이렉션 응답을 반환하고, 스마트 클라이언트는 이를 캐싱하여 이후 요청을 올바른 노드로 직접 전달합니다.

### 해시 태그(Hash Tag)

멀티 키 연산(`MGET`, `MSET`, 파이프라이닝 등)을 위해 특정 키들을 같은 슬롯에 강제 배치할 수 있습니다.

```
user:{1001}:profile
user:{1001}:session
```

중괄호 `{}` 안의 내용만을 기준으로 슬롯이 결정되므로 위 두 키는 반드시 같은 노드에 저장됩니다.

### 복제 구성과 Failover

Redis Cluster는 각 마스터 노드에 하나 이상의 레플리카(슬레이브)를 두어 고가용성을 보장합니다. 권장 최소 구성은 **3 마스터 + 3 레플리카** 총 6노드입니다.

```
Master A ─── Replica A'
Master B ─── Replica B'
Master C ─── Replica C'
```

마스터 노드에 장애가 발생하면 클러스터는 **Gossip 프로토콜(클러스터 버스, 포트+10000)**을 통해 노드 상태를 감지하고, 일정 타임아웃(`cluster-node-timeout`) 이후 레플리카를 자동으로 마스터로 승격시킵니다.

---

## 실전 예제

### Docker Compose로 로컬 클러스터 구성

```yaml
# docker-compose.yml
version: '3.8'
services:
  redis-1:
    image: redis:7.2
    command: redis-server --port 7001 --cluster-enabled yes --cluster-config-file nodes.conf --cluster-node-timeout 5000 --appendonly yes
    ports:
      - "7001:7001"
    networks:
      - redis-cluster-net

  redis-2:
    image: redis:7.2
    command: redis-server --port 7002 --cluster-enabled yes --cluster-config-file nodes.conf --cluster-node-timeout 5000 --appendonly yes
    ports:
      - "7002:7002"
    networks:
      - redis-cluster-net

  redis-3:
    image: redis:7.2
    command: redis-server --port 7003 --cluster-enabled yes --cluster-config-file nodes.conf --cluster-node-timeout 5000 --appendonly yes
    ports:
      - "7003:7003"
    networks:
      - redis-cluster-net

  redis-4:
    image: redis:7.2
    command: redis-server --port 7004 --cluster-enabled yes --cluster-config-file nodes.conf --cluster-node-timeout 5000 --appendonly yes
    ports:
      - "7004:7004"
    networks:
      - redis-cluster-net

  redis-5:
    image: redis:7.2
    command: redis-server --port 7005 --cluster-enabled yes --cluster-config-file nodes.conf --cluster-node-timeout 5000 --appendonly yes
    ports:
      - "7005:7005"
    networks:
      - redis-cluster-net

  redis-6:
    image: redis:7.2
    command: redis-server --port 7006 --cluster-enabled yes --cluster-config-file nodes.conf --cluster-node-timeout 5000 --appendonly yes
    ports:
      - "7006:7006"
    networks:
      - redis-cluster-net

networks:
  redis-cluster-net:
    driver: bridge
```

컨테이너 기동 후 클러스터를 초기화합니다.

```bash
# 클러스터 생성 (--cluster-replicas 1 = 마스터당 레플리카 1개)
docker exec -it <redis-1-container-id> redis-cli \
  --cluster create \
  127.0.0.1:7001 127.0.0.1:7002 127.0.0.1:7003 \
  127.0.0.1:7004 127.0.0.1:7005 127.0.0.1:7006 \
  --cluster-replicas 1 --yes

# 클러스터 상태 확인
redis-cli -p 7001 cluster info
redis-cli -p 7001 cluster nodes
```

### Spring Boot + Lettuce 연동

Spring Data Redis는 기본적으로 Lettuce 드라이버를 사용하며, 클러스터 모드를 자동으로 지원합니다.

```yaml
# application.yml
spring:
  data:
    redis:
      cluster:
        nodes:
          - 127.0.0.1:7001
          - 127.0.0.1:7002
          - 127.0.0.1:7003
          - 127.0.0.1:7004
          - 127.0.0.1:7005
          - 127.0.0.1:7006
        max-redirects: 3
      timeout: 2000ms
      lettuce:
        cluster:
          refresh:
            adaptive: true          # 토폴로지 변경 시 자동 갱신
            period: 30s
```

```java
@Configuration
public class RedisClusterConfig {

    @Bean
    public LettuceClientConfigurationBuilderCustomizer lettuceCustomizer() {
        return builder -> builder
            .clientOptions(
                ClusterClientOptions.builder()
                    .topologyRefreshOptions(
                        ClusterTopologyRefreshOptions.builder()
                            .enableAdaptiveRefreshTrigger(
                                RefreshTrigger.MOVED_REDIRECT,
                                RefreshTrigger.PERSISTENT_RECONNECTS
                            )
                            .adaptiveRefreshTriggersTimeout(Duration.ofSeconds(30))
                            .enablePeriodicRefresh(Duration.ofMinutes(1))
                            .build()
                    )
                    .nodeFilter(it ->
                        !(it.is(RedisClusterNode.NodeFlag.SLAVE) && it.is(RedisClusterNode.NodeFlag.FAIL))
                    )
                    .build()
            );
    }

    @Bean
    public RedisTemplate<String, Object> redisTemplate(RedisConnectionFactory factory) {
        RedisTemplate<String, Object> template = new RedisTemplate<>();
        template.setConnectionFactory(factory);
        template.setKeySerializer(new StringRedisSerializer());
        template.setValueSerializer(new GenericJackson2JsonRedisSerializer());
        template.setEnableTransactionSupport(false); // 클러스터에서 트랜잭션 주의
        return template;
    }
}
```

### Failover 시뮬레이션 및 검증

```java
@Service
@Slf4j
@RequiredArgsConstructor
public class RedisClusterHealthService {

    private final RedisTemplate<String, Object> redisTemplate;
    private final StringRedisTemplate stringRedisTemplate;

    // 해시 태그를 활용한 멀티 키 연산
    public void saveUserSession(Long userId, Map<String, String> sessionData) {
        String sessionKey = "session:{" + userId + "}:data";
        String lockKey   = "session:{" + userId + "}:lock";

        // 같은 슬롯에 저장됨을 보장
        redisTemplate.opsForHash().putAll(sessionKey, sessionData);
        redisTemplate.expire(sessionKey, Duration.ofMinutes(30));
        stringRedisTemplate.opsForValue().set(lockKey, "1", Duration.ofSeconds(10));
    }

    // Failover 발생 후 자동 재연결 확인
    public boolean checkClusterHealth() {
        try {
            redisTemplate.getConnectionFactory()
                .getConnection()
                .ping();
            return true;
        } catch (RedisConnectionException e) {
            log.error("Cluster health check failed: {}", e.getMessage());
            return false;
        }
    }

    // Circuit Breaker 패턴과 결합 예시
    @CircuitBreaker(name = "redis", fallbackMethod = "fallbackGet")
    public Object getWithCircuitBreaker(String key) {
        return redisTemplate.opsForValue().get(key);
    }

    public Object fallbackGet(String key, Exception e) {
        log.warn("Redis fallback triggered for key: {}, error: {}", key, e.getMessage());
        return null; // DB나 로컬 캐시로 대체
    }
}
```

### 노드 추가 및 리샤딩

운영 중 스케일 아웃이 필요할 때는 아래 절차를 따릅니다.

```bash
# 1. 새 노드를 마스터로 추가
redis-cli --cluster add-node \
  127.0.0.1:7007 127.0.0.1:7001

# 2. 레플리카로 추가
redis-cli --cluster add-node \
  127.0.0.1:7008 127.0.0.1:7001 \
  --cluster-slave --cluster-master-id <master-node-id>

# 3. 슬롯 리샤딩 (1000개 슬롯을 새 마스터로 이동)
redis-cli --cluster reshard 127.0.0.1:7001 \
  --cluster-from all \
  --cluster-to <new-master-node-id> \
  --cluster-slots 1000 \
  --cluster-yes
```

---

## 주의사항 및 트레이드오프

### 1. 멀티 키 명령어 제한

클러스터 환경에서 `MGET`, `MSET`, `SUNION` 등 다중 키를 처리하는 명령어는 **모든 키가 동일한 슬롯에 있어야** 동작합니다. 그렇지 않으면 `CROSSSLOT` 에러가 발생합니다. 해시 태그로 해결할 수 있지만, 과도하게 사용하면 특정 슬롯에 데이터가 집중되어 핫스팟이 생길 수 있습니다.

### 2. Lua 스크립트 제한

Lua 스크립트 내에서 사용하는 모든 키는 동일한 슬롯에 있어야 합니다. 스크립트 작성 시 반드시 해시 태그를 통해 키 배치를 제어해야 합니다.

### 3. cluster-node-timeout 튜닝

이 값이 너무 짧으면 네트워크 지연으로 인한 **허위 Failover**가 발생하고, 너무 길면 실제 장애 감지가 늦어집니다. 일반적으로 **5,000ms~15,000ms** 사이에서 네트워크 환경에 맞게 튜닝합니다.

```bash
# 현재 설정 확인
redis-cli -p 7001 config get cluster-node-timeout

# 런타임 변경 (재시작 불필요)
redis-cli -p 7001 config set cluster-node-timeout 10000
```

### 4. 레플리카 읽기(Read From Replica)

Lettuce에서 레플리카 읽기를 활성화하면 읽기 처리량을 높일 수 있지만, **복제 지연(Replication Lag)**으로 인해 stale data가 반환될 수 있습니다. 강한 일관성이 필요한 서비스에서는 주의해야 합니다.

```java
// 레플리카 읽기 활성화 (주의: 일관성 약화)
@Bean
public LettuceClientConfigurationBuilderCustomizer readReplicaCustomizer() {
    return builder -> builder.readFrom(ReadFrom.REPLICA_PREFERRED);
}
```

### 5. Persistence와 Failover의 관계

AOF나 RDB가 비활성화된 상태에서 마스터가 재시작되면 빈 데이터셋으로 복구됩니다. Failover 이후 레플리카가 비어있는 마스터와 동기화되어 **데이터 전체 손실**이 일어날 수 있습니다. 프로덕션에서는 반드시 `appendonly yes`와 함께 `cluster-require-full-coverage no` 설정을 검토하세요.

### 6. 네트워크 파티션과 Split-Brain

과반수 마스터(quorum)가 살아있어야 클러스터가 정상 동작합니다. 만약 네트워크 파티션으로 마스터가 2개 남은 파티션과 1개 남은 파티션으로 나뉘면, 1개 파티션은 쓰기를 거부하여 Split-Brain을 방지합니다. 이 특성 때문에 마스터는 반드시 **홀수 개(최소 3개)**로 구성해야 합니다.

---

## 정리

Redis Cluster는 수평 확장과 고가용성을 동시에 달성할 수 있는 강력한 솔루션이지만, 단일 인스턴스나 Sentinel 구성과는 다른 **운영 복잡도**를 수반합니다. 핵심 체크리스트를 정리하면 다음과 같습니다.

| 항목 | 권장 설정 |
|------|-----------|
| 최소 노드 수 | 3 마스터 + 3 레플리카 (6노드) |
| cluster-node-timeout | 5,000ms ~ 15,000ms |
| 토폴로지 자동 갱신 | Lettuce Adaptive Refresh 활성화 |
| 데이터 영속성 | AOF 활성화 필수 |
| 멀티 키 연산 | 해시 태그로 슬롯 강제 배치 |
| 읽기 확장 | ReadFrom.REPLICA_PREFERRED (정합성 트레이드오프 인지 필요) |

Redis Cluster를 처음 도입할 때 가장 흔한 실수는 Sentinel이나 단순 복제 구성과 동일하게 취급하는 것입니다. **슬롯 기반의 데이터 모델링**을 사전에 설계하고, 토폴로지 변경 시 클라이언트의 재연결 동작을 충분히 검증한 후 프로덕션에 적용하길 강력히 권장합니다.