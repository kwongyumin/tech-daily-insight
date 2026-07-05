# Apache Kafka와 스트림 처리 실시간 데이터 파이프라인

## 개요

현대 서비스에서 실시간 데이터 처리는 선택이 아닌 필수가 되었다. 사용자 행동 분석, 결제 이상 탐지, IoT 센서 데이터 처리 등 수많은 유스케이스가 수 밀리초 단위의 처리 속도를 요구한다. 이러한 환경에서 **Apache Kafka**는 고가용성, 고처리량, 내구성을 갖춘 분산 이벤트 스트리밍 플랫폼으로 사실상의 표준 위치를 차지하고 있다.

이 글에서는 Kafka의 핵심 아키텍처부터 **Kafka Streams**와 **Spring Kafka**를 활용한 실전 파이프라인 구축까지 다룬다. 단순한 Producer/Consumer 구현을 넘어, 실무에서 마주치는 트레이드오프와 운영 이슈까지 함께 살펴본다.

---

## 핵심 개념

### Kafka 아키텍처 재정립

Kafka를 단순한 "메시지 큐"로 이해하면 실무에서 반드시 문제가 생긴다. Kafka는 **분산 커밋 로그(Distributed Commit Log)** 기반의 이벤트 스트리밍 플랫폼이다.

```
┌─────────────────────────────────────────────────────┐
│                   Kafka Cluster                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐           │
│  │ Broker 1 │  │ Broker 2 │  │ Broker 3 │           │
│  │          │  │          │  │          │           │
│  │ Topic A  │  │ Topic A  │  │ Topic A  │           │
│  │ Part 0   │  │ Part 1   │  │ Part 2   │           │
│  │ (Leader) │  │ (Leader) │  │ (Leader) │           │
│  └──────────┘  └──────────┘  └──────────┘           │
└─────────────────────────────────────────────────────┘
        ↑ Produce                  ↓ Consume
  [Producer App]            [Consumer Group A]
                            [Consumer Group B]
```

핵심 개념을 명확히 짚고 가자.

- **Topic & Partition**: 토픽은 논리적 채널이며, 파티션은 물리적 분산 단위다. 파티션 수가 병렬 처리의 상한선을 결정한다.
- **Offset**: 각 파티션 내 메시지의 순차 번호. Consumer는 offset을 통해 재처리(replay)가 가능하다.
- **Consumer Group**: 동일 그룹 내 Consumer들은 파티션을 분배받아 처리한다. 파티션 수보다 Consumer가 많으면 유휴 Consumer가 발생한다.
- **Replication Factor**: 브로커 장애 시 데이터 손실 방지를 위한 복제 계수. 일반적으로 3을 권장한다.

### 스트림 처리 패러다임

| 처리 방식 | 특징 | 적합한 유스케이스 |
|-----------|------|-----------------|
| **Kafka Streams** | 라이브러리 기반, 별도 클러스터 불필요 | 마이크로서비스 내 스트림 처리 |
| **Apache Flink** | 강력한 상태 관리, 정확히 한 번(Exactly-once) 보장 | 복잡한 CEP, 대규모 집계 |
| **Apache Spark Structured Streaming** | 마이크로 배치, 풍부한 ML 생태계 | 배치와 스트림 혼합 처리 |

실무에서는 별도 클러스터 운영 부담 없이 Spring Boot 애플리케이션에 자연스럽게 통합할 수 있는 **Kafka Streams**를 우선 검토하길 권장한다.

---

## 실전 예제

### 환경 설정

`docker-compose.yml`로 로컬 Kafka 환경을 빠르게 구성한다.

```yaml
version: '3.8'
services:
  zookeeper:
    image: confluentinc/cp-zookeeper:7.5.0
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181

  kafka:
    image: confluentinc/cp-kafka:7.5.0
    depends_on:
      - zookeeper
    ports:
      - "9092:9092"
    environment:
      KAFKA_BROKER_ID: 1
      KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://localhost:9092
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_AUTO_CREATE_TOPICS_ENABLE: 'false'

  schema-registry:
    image: confluentinc/cp-schema-registry:7.5.0
    depends_on:
      - kafka
    ports:
      - "8081:8081"
    environment:
      SCHEMA_REGISTRY_KAFKASTORE_BOOTSTRAP_SERVERS: kafka:9092
      SCHEMA_REGISTRY_HOST_NAME: schema-registry
```

### Spring Kafka 기반 Producer/Consumer 구현

```groovy
// build.gradle
dependencies {
    implementation 'org.springframework.kafka:spring-kafka'
    implementation 'org.apache.kafka:kafka-streams'
    implementation 'io.confluent:kafka-streams-avro-serde:7.5.0'
}
```

```java
// KafkaProducerConfig.java
@Configuration
public class KafkaProducerConfig {

    @Value("${spring.kafka.bootstrap-servers}")
    private String bootstrapServers;

    @Bean
    public ProducerFactory<String, OrderEvent> producerFactory() {
        Map<String, Object> config = new HashMap<>();
        config.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrapServers);
        config.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        config.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, JsonSerializer.class);
        // 멱등성 보장: 네트워크 재시도 시 중복 메시지 방지
        config.put(ProducerConfig.ENABLE_IDEMPOTENCE_CONFIG, true);
        config.put(ProducerConfig.ACKS_CONFIG, "all");
        config.put(ProducerConfig.RETRIES_CONFIG, Integer.MAX_VALUE);
        config.put(ProducerConfig.MAX_IN_FLIGHT_REQUESTS_PER_CONNECTION, 5);
        return new DefaultKafkaProducerFactory<>(config);
    }

    @Bean
    public KafkaTemplate<String, OrderEvent> kafkaTemplate() {
        return new KafkaTemplate<>(producerFactory());
    }
}
```

```java
// OrderEventProducer.java
@Service
@Slf4j
@RequiredArgsConstructor
public class OrderEventProducer {

    private final KafkaTemplate<String, OrderEvent> kafkaTemplate;
    private static final String TOPIC = "order-events";

    public CompletableFuture<SendResult<String, OrderEvent>> sendOrderEvent(OrderEvent event) {
        return kafkaTemplate.send(TOPIC, event.getOrderId(), event)
            .thenApply(result -> {
                log.info("Order event sent | topic={}, partition={}, offset={}",
                    result.getRecordMetadata().topic(),
                    result.getRecordMetadata().partition(),
                    result.getRecordMetadata().offset());
                return result;
            })
            .exceptionally(ex -> {
                log.error("Failed to send order event | orderId={}", event.getOrderId(), ex);
                // Dead Letter Queue 처리 또는 재시도 로직
                throw new KafkaPublishException("Order event publish failed", ex);
            });
    }
}
```

### Kafka Streams로 실시간 집계 파이프라인 구축

주문 이벤트를 실시간으로 집계하여 상점별 매출을 계산하는 파이프라인이다.

```java
// OrderStreamProcessor.java
@Configuration
@Slf4j
public class OrderStreamProcessor {

    @Bean
    public KStream<String, OrderEvent> orderStream(StreamsBuilder streamsBuilder) {
        // 입력 스트림: order-events 토픽
        KStream<String, OrderEvent> orderStream = streamsBuilder
            .stream("order-events", Consumed.with(Serdes.String(), orderEventSerde()));

        // 1. 완료된 주문만 필터링
        KStream<String, OrderEvent> completedOrders = orderStream
            .filter((key, order) -> OrderStatus.COMPLETED.equals(order.getStatus()))
            .peek((key, order) -> log.debug("Processing completed order: {}", order.getOrderId()));

        // 2. 상점 ID로 리키잉(rekeying)
        KStream<String, OrderEvent> rekeyedByStore = completedOrders
            .selectKey((orderId, order) -> order.getStoreId());

        // 3. 5분 텀블링 윈도우로 상점별 매출 집계
        TimeWindows timeWindows = TimeWindows
            .ofSizeWithNoGrace(Duration.ofMinutes(5));

        KTable<Windowed<String>, StoreSalesAggregate> salesAggregation = rekeyedByStore
            .groupByKey(Grouped.with(Serdes.String(), orderEventSerde()))
            .windowedBy(timeWindows)
            .aggregate(
                StoreSalesAggregate::new,
                (storeId, order, aggregate) -> aggregate.add(order),
                Materialized.<String, StoreSalesAggregate, WindowStore<Bytes, byte[]>>as("store-sales-store")
                    .withKeySerde(Serdes.String())
                    .withValueSerde(storeSalesAggregateSerde())
            );

        // 4. 집계 결과를 출력 토픽으로 발행
        salesAggregation.toStream()
            .map((windowedKey, aggregate) -> KeyValue.pair(
                windowedKey.key(),
                buildSalesReport(windowedKey, aggregate)
            ))
            .to("store-sales-report", Produced.with(Serdes.String(), salesReportSerde()));

        // 5. 이상 거래 탐지: 단일 주문 금액이 임계값 초과 시 알림
        completedOrders
            .filter((key, order) -> order.getAmount().compareTo(FRAUD_THRESHOLD) > 0)
            .to("fraud-detection-alerts");

        return orderStream;
    }

    private SalesReport buildSalesReport(Windowed<String> windowedKey,
                                          StoreSalesAggregate aggregate) {
        return SalesReport.builder()
            .storeId(windowedKey.key())
            .windowStart(windowedKey.window().startTime())
            .windowEnd(windowedKey.window().endTime())
            .totalAmount(aggregate.getTotalAmount())
            .orderCount(aggregate.getOrderCount())
            .build();
    }
}
```

### Consumer 내결함성 처리

```java
// OrderEventConsumer.java
@Component
@Slf4j
@RequiredArgsConstructor
public class OrderEventConsumer {

    private final SalesReportService salesReportService;

    @KafkaListener(
        topics = "store-sales-report",
        groupId = "sales-dashboard-group",
        containerFactory = "kafkaListenerContainerFactory"
    )
    public void consumeSalesReport(
            @Payload SalesReport report,
            @Header(KafkaHeaders.RECEIVED_PARTITION) int partition,
            @Header(KafkaHeaders.OFFSET) long offset,
            Acknowledgment acknowledgment) {
        try {
            salesReportService.updateDashboard(report);
            // 수동 커밋: 비즈니스 로직 성공 후에만 offset 커밋
            acknowledgment.acknowledge();
            log.info("Sales report processed | storeId={}, partition={}, offset={}",
                report.getStoreId(), partition, offset);
        } catch (TransientException e) {
            // 재시도 가능한 오류: 커밋하지 않고 재처리 유도
            log.warn("Transient error, will retry | offset={}", offset, e);
            throw e;
        } catch (Exception e) {
            // 치명적 오류: DLQ로 전송 후 커밋
            log.error("Fatal error, sending to DLQ | offset={}", offset, e);
            deadLetterPublisher.publish(report, e);
            acknowledgment.acknowledge();
        }
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. 파티션 수 설계는 신중하게

파티션 수는 **한번 늘리면 줄일 수 없다**. 처음부터 충분히 크게 설정하되, 과도하게 늘리면 브로커 메모리 압박과 리밸런싱 시간 증가로 이어진다. 처리량(TPS), Consumer 수, 메시지 크기를 종합적으로 계산해야 한다.

```
권장 파티션 수 = max(Consumer 수, 목표 TPS / 단일 파티션 처리량)
```

### 2. Exactly-Once vs At-Least-Once

| 보장 수준 | 성능 오버헤드 | 설정 |
|-----------|-------------|------|
| At-Most-Once | 없음 | acks=0 |
| At-Least-Once | 낮음 | acks=all, 재시도 활성화 |
| Exactly-Once | 높음(~20%) | 트랜잭션 API + enable.idempotence=true |

금융 거래처럼 중복이 치명적인 경우에만 Exactly-Once를 선택하고, 나머지는 **At-Least-Once + 멱등성 처리**로 충분하다.

### 3. Consumer Lag 모니터링

Consumer Lag(지연)이 늘어나면 실시간성이 무너진다. 반드시 Prometheus + Grafana로 모니터링 체계를 갖춰야 한다.

```yaml
# prometheus.yml
- job_name: 'kafka-consumer-lag'
  static_configs:
    - targets: ['kafka-exporter:9308']
  metrics_path: /metrics
```

핵심 알람 지표:
- `kafka_consumer_group_lag > 10000` → Consumer 스케일 아웃 검토
- `kafka_broker_network_processor_avg_idle_percent < 30%` → 브로커 증설 검토

### 4. Schema Evolution과 하위 호환성

시간이 지나면 이벤트 스키마는 반드시 변경된다. **Confluent Schema Registry + Avro**를 사용하면 하위 호환성을 보장하면서 스키마를 진화시킬 수 있다. JSON 스키마를 직접 사용하면 나중에 반드시 후회한다.

### 5. Kafka Streams 상태 저장소 복구 시간

Kafka Streams의 상태는 RocksDB에 로컬 저장된다. 파드 재시작 시 상태 복구(state restoration)에 상당한 시간이 소요될 수 있다. **Standby Replica** 설정으로 복구 시간을 단축하라.

```java
streamsConfig.put(StreamsConfig.NUM_STANDBY_REPLICAS_CONFIG, 1);
```

---

## 정리

Apache Kafka와 Kafka Streams는 실시간 데이터 파이프라인의 강력한 조합이다. 핵심을 정리하면 다음과 같다.

- **아키텍처 설계**: 파티션 수와 Replication Factor는 초기에 신중하게 결정하라. 변경 비용이 크다.
- **신뢰성**: 멱등 Producer + 수동 Offset 커밋 + DLQ 패턴으로 메시지 유실과 중복을 관리하라.
- **스트림 처리**: 마이크로서비스 환경에서는 Kafka Streams로 충분하다. 복잡한 CEP가 필요할 때 Flink를 고려하라.
- **운영**: Consumer Lag과 브로커 리소스 모니터링은 선택이 아닌 필수다.
- **스키마 관리**: Schema Registry 도입을 처음부터 고려하라. 나중에 마이그레이션은 매우 고통스럽다.

Kafka는 강력하지만 운영 복잡도도 높다. 도입 전 팀의 운영 역량과 실제 요구사항을 냉정하게 평가하고, 단순한 유스케이스라면 Redis Streams나 AWS Kinesis 같은 관리형 서비스도 충분히 좋은 대안이 될 수 있다.