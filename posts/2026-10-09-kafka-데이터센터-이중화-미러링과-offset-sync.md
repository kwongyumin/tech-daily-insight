# Kafka 데이터센터 이중화: 미러링과 Offset Sync

## 개요

대규모 서비스에서 Kafka는 단순한 메시지 큐를 넘어 시스템 간 이벤트 버스, 데이터 파이프라인의 핵심 인프라로 자리잡았다. 그만큼 Kafka 클러스터의 가용성은 곧 서비스 전체의 가용성과 직결된다. 단일 데이터센터(DC)에서 운영 중인 Kafka 클러스터가 장애를 맞으면 메시지 유실, 컨슈머 중단, 심각한 경우 서비스 전체 다운으로 이어질 수 있다.

이를 방어하기 위해 **데이터센터 이중화(Multi-DC Replication)** 전략이 필요하다. 하지만 Kafka의 이중화는 단순히 데이터를 복제하는 것 이상의 문제를 포함한다. 가장 골치 아픈 부분이 바로 **Offset 동기화**다. 프라이머리 DC에서 소비하던 컨슈머가 세컨더리 DC로 전환됐을 때, 어느 메시지부터 다시 읽어야 하는가? 중복 처리는 어떻게 최소화하는가?

이 글에서는 Kafka 미러링의 핵심 도구들과 Offset Sync 메커니즘, 그리고 실제 구성 시 마주치는 트레이드오프를 다룬다.

---

## 핵심 개념

### 1. Kafka 미러링이란?

Kafka 미러링은 하나의 Kafka 클러스터(Source)에서 다른 클러스터(Target)로 토픽의 메시지를 복제하는 작업이다. 이때 중요한 점은 **두 클러스터의 파티션 Offset이 1:1로 일치하지 않는다**는 것이다. Source 클러스터에서 Offset 100인 메시지가 Target 클러스터에서는 Offset 97이 될 수 있다. 미러링 과정에서 재시작, 중복 제거 등의 이유로 Offset이 달라질 수 있기 때문이다.

이 Offset 불일치 문제는 Failover 시 가장 중요하게 다뤄야 할 부분이다.

### 2. 주요 미러링 도구

#### MirrorMaker 1 (MM1)
초기 Kafka 공식 미러링 도구다. 내부적으로 Consumer + Producer 구조로 동작하며, 설정이 간단하지만 Offset 동기화 기능이 없고 운영 중 토픽 자동 감지가 어렵다. 레거시 환경에서만 사용하는 것을 권장한다.

#### MirrorMaker 2 (MM2)
Kafka 2.4부터 공식 지원되는 미러링 솔루션으로, Kafka Connect 기반으로 동작한다. **Offset 동기화(MirrorCheckpointConnector)**, 토픽 자동 감지, 멀티 클러스터 토폴로지 지원이 핵심 개선점이다.

#### Confluent Replicator
Confluent Platform에서 제공하는 엔터프라이즈 수준의 미러링 도구다. MM2와 유사하지만 Schema Registry 동기화, 정확한 Offset 변환 등 추가 기능을 제공한다.

### 3. MM2의 Offset 동기화 메커니즘

MM2는 세 가지 커넥터로 구성된다.

| 커넥터 | 역할 |
|---|---|
| `MirrorSourceConnector` | Source → Target으로 메시지 복제 |
| `MirrorCheckpointConnector` | Consumer Group Offset을 Target에 동기화 |
| `MirrorHeartbeatConnector` | 클러스터 간 연결 상태 모니터링 |

`MirrorCheckpointConnector`는 Source 클러스터의 Consumer Group Offset을 Target 클러스터의 `__consumer_offsets` 토픽에 변환하여 저장한다. 이를 통해 Failover 시 컨슈머가 Target 클러스터에서 올바른 위치부터 재개할 수 있다.

---

## 실전 예제

### MM2 기본 구성

아래는 `primary` DC에서 `secondary` DC로 미러링하는 MM2 설정 예제다.

```properties
# mm2.properties

# 클러스터 별칭 정의
clusters = primary, secondary

# Primary 클러스터 연결
primary.bootstrap.servers = primary-kafka-1:9092,primary-kafka-2:9092

# Secondary 클러스터 연결
secondary.bootstrap.servers = secondary-kafka-1:9092,secondary-kafka-2:9092

# 미러링 방향: primary -> secondary
primary->secondary.enabled = true
primary->secondary.topics = order-events, payment-events, user-events

# Offset 동기화 활성화
primary->secondary.sync.group.offsets.enabled = true
primary->secondary.sync.group.offsets.interval.seconds = 60

# Heartbeat 활성화
primary->secondary.emit.heartbeats.enabled = true
primary->secondary.emit.checkpoints.enabled = true

# 토픽 리플리케이션 팩터
replication.factor = 3

# 복제된 토픽 이름에 prefix 추가 여부 (false: 동일 이름 유지)
replication.policy.class = org.apache.kafka.connect.mirror.IdentityReplicationPolicy
```

> **주의**: `IdentityReplicationPolicy`를 사용하면 토픽 이름이 그대로 유지된다. 기본 정책은 `primary.order-events`처럼 클러스터 이름이 prefix로 붙는다. Active-Passive 구조에서는 `IdentityReplicationPolicy`가 편리하지만, Active-Active에서는 루프 방지를 위해 기본 정책을 권장한다.

### MM2 실행

```bash
# Kafka Connect distributed mode로 실행
./bin/connect-mirror-maker.sh mm2.properties

# 또는 Kafka Connect 클러스터에 REST API로 등록
curl -X POST http://connect-host:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "primary-to-secondary-source",
    "config": {
      "connector.class": "org.apache.kafka.connect.mirror.MirrorSourceConnector",
      "source.cluster.alias": "primary",
      "target.cluster.alias": "secondary",
      "source.cluster.bootstrap.servers": "primary-kafka-1:9092",
      "target.cluster.bootstrap.servers": "secondary-kafka-1:9092",
      "topics": "order-events,payment-events",
      "replication.factor": "3",
      "sync.topic.acls.enabled": "false"
    }
  }'
```

### Failover 시 Offset 변환 (Java 예제)

Failover 후 컨슈머가 Target 클러스터에서 정확한 위치부터 재개하려면, MM2가 저장해둔 Offset 매핑을 활용해야 한다. `RemoteClusterUtils`를 사용하면 Source Offset을 Target Offset으로 변환할 수 있다.

```java
import org.apache.kafka.connect.mirror.RemoteClusterUtils;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.common.TopicPartition;

import java.time.Duration;
import java.util.Map;
import java.util.Properties;
import java.util.Set;

public class FailoverConsumerExample {

    public static void main(String[] args) throws Exception {
        String sourceClusterAlias = "primary";
        String consumerGroup = "order-processing-group";

        Properties adminProps = new Properties();
        adminProps.put("bootstrap.servers", "secondary-kafka-1:9092");

        // Source Consumer Group의 Offset을 Target Offset으로 변환
        Map<TopicPartition, Long> translatedOffsets =
            RemoteClusterUtils.translateOffsets(
                adminProps,
                sourceClusterAlias,
                consumerGroup,
                Duration.ofSeconds(30)
            );

        System.out.println("Translated offsets: " + translatedOffsets);

        // 변환된 Offset으로 Consumer 초기화
        Properties consumerProps = new Properties();
        consumerProps.put("bootstrap.servers", "secondary-kafka-1:9092");
        consumerProps.put("group.id", consumerGroup);
        consumerProps.put("key.deserializer",
            "org.apache.kafka.common.serialization.StringDeserializer");
        consumerProps.put("value.deserializer",
            "org.apache.kafka.common.serialization.StringDeserializer");
        consumerProps.put("auto.offset.reset", "earliest");

        try (KafkaConsumer<String, String> consumer = new KafkaConsumer<>(consumerProps)) {
            Set<TopicPartition> partitions = translatedOffsets.keySet();
            consumer.assign(partitions);

            // 변환된 Offset으로 Seek
            translatedOffsets.forEach((tp, offset) -> {
                // 약간의 안전 마진을 위해 offset을 뒤로 당길 수도 있음
                long safeOffset = Math.max(0, offset - 10);
                consumer.seek(tp, safeOffset);
                System.out.printf("Seeking %s to offset %d%n", tp, safeOffset);
            });

            while (true) {
                var records = consumer.poll(Duration.ofMillis(1000));
                records.forEach(record -> {
                    System.out.printf("Consumed: partition=%d, offset=%d, value=%s%n",
                        record.partition(), record.offset(), record.value());
                    // 멱등성(Idempotency) 처리 로직 필요
                });
            }
        }
    }
}
```

### Offset 동기화 상태 모니터링

```bash
# mm2-offsets 토픽에서 Checkpoint 확인
kafka-console-consumer.sh \
  --bootstrap-server secondary-kafka-1:9092 \
  --topic mm2-offsets.primary.internal \
  --from-beginning \
  --property print.key=true

# Consumer Group Offset 비교
kafka-consumer-groups.sh \
  --bootstrap-server primary-kafka-1:9092 \
  --group order-processing-group \
  --describe

kafka-consumer-groups.sh \
  --bootstrap-server secondary-kafka-1:9092 \
  --group order-processing-group \
  --describe
```

---

## 주의사항 및 트레이드오프

### 1. Offset 불일치와 중복 처리는 피할 수 없다

`sync.group.offsets.interval.seconds`를 60초로 설정했다면, 최대 60초 치의 메시지가 Failover 후 재처리될 수 있다. **정확히 한 번(Exactly-once) 처리는 미러링 환경에서 보장하기 매우 어렵다.** 따라서 컨슈머 로직은 반드시 **멱등성(Idempotency)**을 갖도록 설계해야 한다.

중복 방지를 위한 일반적인 패턴:
- **DB Upsert**: Primary Key 기반의 Upsert로 중복 삽입 방지
- **Redis 중복 필터**: 처리 완료된 메시지 ID를 TTL과 함께 캐싱
- **트랜잭션 아웃박스 패턴**: 이벤트와 상태 변경을 동일 트랜잭션으로 묶어 처리

### 2. Active-Active vs Active-Passive

| 구분 | Active-Active | Active-Passive |
|---|---|---|
| 가용성 | 높음 | 중간 |
| 복잡도 | 매우 높음 | 비교적 낮음 |
| 메시지 루프 위험 | 있음 | 없음 |
| Offset 관리 | 복잡 | 비교적 단순 |
| Failover | 자동/즉시 | 수동 또는 반자동 |

Active-Active 구조에서는 양방향 미러링으로 인한 **메시지 루프**가 발생할 수 있다. MM2는 기본 ReplicationPolicy에서 토픽 이름에 클러스터 alias를 붙여 이를 방지하지만, `IdentityReplicationPolicy` 사용 시에는 별도의 루프 방지 로직이 필요하다.

### 3. 네트워크 대역폭과 레이턴시

데이터센터 간 링크는 일반적으로 내부 네트워크 대비 레이턴시가 크고 대역폭이 제한된다. 다음을 고려해야 한다.

- **압축 활성화**: `compression.type = lz4` 또는 `zstd` 설정
- **배치 크기 조정**: `batch.size`, `linger.ms` 튜닝으로 처리량 향상
- **미러링 대상 선별**: 모든 토픽이 아닌, 재해 복구에 필요한 핵심 토픽만 미러링

### 4. Schema Registry 동기화

Avro, Protobuf 등 스키마 기반 직렬화를 사용한다면 Schema Registry도 함께 이중화해야 한다. 그렇지 않으면 Target 클러스터에서 메시지 역직렬화가 실패한다. Confluent Schema Registry는 기본적으로 Primary-Secondary 모드의 복제를 지원한다.

### 5. Failover 자동화의 위험성

자동 Failover는 매력적이지만 **Split-brain** 문제를 유발할 수 있다. 네트워크 파티션으로 DC 간 연결이 끊겼지만 두 DC 모두 정상인 상황에서 자동으로 세컨더리가 승격되면, 양쪽에서 프로듀서가 메시지를 보내는 상황이 발생한다. 이는 데이터 일관성에 심각한 문제를 초래할 수 있으므로, **자동 Failover 도입 전 충분한 테스트와 롤백 계획**이 필수다.

---

## 정리

Kafka 데이터센터 이중화는 단순한 데이터 복제가 아니라, **Offset 변환, 중복 처리 허용, Failover 전략, 네트워크 비용**을 모두 고려한 종합적인 설계 문제다.

핵심 체크리스트:

- [ ] MirrorMaker 2를 사용하고 `MirrorCheckpointConnector`를 활성화했는가
- [ ] `sync.group.offsets.interval.seconds` 값이 비즈니스 허용 손실 범위 내인가
- [ ] 컨슈머 로직이 멱등성을 보장하는가
- [ ] Active-Active/Passive 구조 선택이 서비스 특성과 일치하는가
- [ ] Schema Registry가 함께 이중화되어 있는가
- [ ] Failover 시나리오를 정기적으로 드릴(Drill) 테스트하는가

재해 복구 계획은 실제 장애가 발생하기 전에 반복 검증해야만 의미가 있다. Offset 변환 코드와 Failover 절차를 Runbook으로 문서화하고, 정기적인 카오스 테스트를 통해 시스템의 신뢰성을 지속적으로 검증하는 것을 권장한다.
