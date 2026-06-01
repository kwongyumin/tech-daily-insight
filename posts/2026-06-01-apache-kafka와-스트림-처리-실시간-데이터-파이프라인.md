# Apache Kafka와 스트림 처리 실시간 데이터 파이프라인

## 개요

현대의 데이터 중심 아키텍처에서 실시간 데이터 처리는 선택이 아닌 필수가 되었다. 사용자 행동 분석, 금융 거래 모니터링, IoT 센서 데이터 집계 등 수많은 도메인에서 밀리초 단위의 반응성을 요구한다. 이 요구를 충족시키는 핵심 기술 스택의 중심에 **Apache Kafka**가 있다.

Kafka는 단순한 메시지 큐를 넘어, 고처리량 분산 이벤트 스트리밍 플랫폼으로 자리잡았다. LinkedIn에서 시작된 이 프로젝트는 현재 Fortune 100 기업의 80% 이상이 채택할 만큼 사실상의 산업 표준이 되었다. 이번 포스팅에서는 Kafka의 핵심 아키텍처를 짚어보고, **Kafka Streams**와 **Spring Kafka**를 활용하여 실무에서 바로 적용 가능한 실시간 데이터 파이프라인을 구성하는 방법을 살펴본다.

---

## 핵심 개념

### Kafka 아키텍처의 근간

Kafka를 제대로 쓰려면 몇 가지 핵심 개념을 정확히 이해해야 한다.

**Topic과 Partition**
Topic은 메시지의 논리적 채널이고, 각 Topic은 여러 Partition으로 분할된다. Partition은 Kafka의 병렬성과 확장성의 핵심 단위다. 메시지는 Partition 내에서만 순서가 보장되며, Partition Key를 통해 동일 키의 메시지는 항상 같은 Partition으로 라우팅된다.

**Consumer Group**
Consumer Group은 하나의 Topic을 여러 Consumer가 협력하여 처리하는 단위다. 각 Partition은 동일 그룹 내 하나의 Consumer에만 할당되므로, Partition 수가 Consumer 수의 상한선이 된다. 이를 고려하지 않으면 일부 Consumer가 유휴 상태가 되는 낭비가 발생한다.

**Offset 관리**
Consumer는 자신이 처리한 메시지의 위치(Offset)를 Kafka 내부 토픽(`__consumer_offsets`)에 커밋한다. At-least-once, At-most-once, Exactly-once의 전달 보장 수준은 모두 이 Offset 관리 전략에서 결정된다.

### Kafka Streams vs. Apache Flink vs. Spark Streaming

| 항목 | Kafka Streams | Apache Flink | Spark Streaming |
|---|---|---|---|
| 배포 방식 | 라이브러리 (JVM 내) | 별도 클러스터 | 별도 클러스터 |
| 지연 시간 | 밀리초 | 밀리초 | 수 초 (마이크로배치) |
| 학습 곡선 | 낮음 | 높음 | 중간 |
| Exactly-once | 지원 | 지원 | 지원 (제한적) |
| 상태 관리 | RocksDB 기반 | 자체 State Backend | 외부 의존 |

Kafka Streams는 별도 클러스터 없이 애플리케이션 내에 임베딩되어 운영 부담이 낮다는 장점이 있다. 마이크로서비스 환경에서 각 서비스가 독립적인 스트림 처리 로직을 가져야 할 때 특히 유리하다.

---

## 실전 예제

이커머스 플랫폼의 **실시간 주문 처리 파이프라인**을 구축하는 시나리오를 예로 들겠다. 주문 이벤트가 발생하면 재고 차감, 결제 검증, 알림 발송이 실시간으로 연쇄 처리되어야 한다.

### 의존성 설정 (build.gradle)

```groovy
dependencies {
    implementation 'org.springframework.boot:spring-boot-starter'
    implementation 'org.springframework.kafka:spring-kafka'
    implementation 'org.apache.kafka:kafka-streams'
    implementation 'io.confluent:kafka-streams-avro-serde:7.5.0'
    implementation 'org.apache.avro:avro:1.11.3'
    testImplementation 'org.springframework.kafka:spring-kafka-test'
}
```

### 도메인 모델 및 Kafka 설정

```java
// OrderEvent.java
@Data
@Builder
public class OrderEvent {
    private String orderId;
    private String userId;
    private String productId;
    private int quantity;
    private BigDecimal price;
    private OrderStatus status;
    private Instant createdAt;
}

// KafkaConfig.java
@Configuration
@EnableKafkaStreams
public class KafkaConfig {

    @Value("${spring.kafka.bootstrap-servers}")
    private String bootstrapServers;

    @Bean(name = KafkaStreamsDefaultConfiguration.DEFAULT_STREAMS_CONFIG_BEAN_NAME)
    public KafkaStreamsConfiguration kafkaStreamsConfig() {
        Map<String, Object> props = new HashMap<>();
        props.put(StreamsConfig.APPLICATION_ID_CONFIG, "order-processing-streams");
        props.put(StreamsConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrapServers);
        props.put(StreamsConfig.DEFAULT_KEY_SERDE_CLASS_CONFIG, Serdes.String().getClass());
        props.put(StreamsConfig.DEFAULT_VALUE_SERDE_CLASS_CONFIG, JsonSerde.class);
        
        // Exactly-once 처리 보장
        props.put(StreamsConfig.PROCESSING_GUARANTEE_CONFIG, 
                  StreamsConfig.EXACTLY_ONCE_V2);
        
        // 상태 저장소 위치
        props.put(StreamsConfig.STATE_DIR_CONFIG, "/var/kafka-streams/state");
        
        // 커밋 간격 (ms)
        props.put(StreamsConfig.COMMIT_INTERVAL_MS_CONFIG, 1000);
        
        return new KafkaStreamsConfiguration(props);
    }

    @Bean
    public NewTopic orderTopic() {
        return TopicBuilder.name("orders")
                .partitions(12)
                .replicas(3)
                .config(TopicConfig.RETENTION_MS_CONFIG, "604800000") // 7일
                .build();
    }

    @Bean
    public NewTopic processedOrderTopic() {
        return TopicBuilder.name("processed-orders")
                .partitions(12)
                .replicas(3)
                .build();
    }
}
```

### Producer: 주문 이벤트 발행

```java
@Service
@Slf4j
@RequiredArgsConstructor
public class OrderEventProducer {

    private final KafkaTemplate<String, OrderEvent> kafkaTemplate;

    public void publishOrderEvent(OrderEvent event) {
        // 동일 사용자의 주문은 같은 파티션으로 라우팅
        CompletableFuture<SendResult<String, OrderEvent>> future =
            kafkaTemplate.send("orders", event.getUserId(), event);

        future.whenComplete((result, ex) -> {
            if (ex != null) {
                log.error("주문 이벤트 발행 실패 [orderId={}]: {}", 
                          event.getOrderId(), ex.getMessage());
                // Dead Letter Queue로 전송하는 fallback 처리
                publishToDlq(event, ex);
            } else {
                log.info("주문 이벤트 발행 성공 [orderId={}, partition={}, offset={}]",
                         event.getOrderId(),
                         result.getRecordMetadata().partition(),
                         result.getRecordMetadata().offset());
            }
        });
    }

    private void publishToDlq(OrderEvent event, Throwable ex) {
        kafkaTemplate.send("orders-dlq", event.getUserId(), event);
    }
}
```

### Kafka Streams: 실시간 집계 및 변환 파이프라인

```java
@Configuration
@Slf4j
@RequiredArgsConstructor
public class OrderStreamProcessor {

    private static final String ORDERS_TOPIC = "orders";
    private static final String HIGH_VALUE_ORDERS_TOPIC = "high-value-orders";
    private static final String ORDER_STATS_TOPIC = "order-stats";

    @Bean
    public KStream<String, OrderEvent> orderProcessingPipeline(StreamsBuilder builder) {
        JsonSerde<OrderEvent> orderSerde = new JsonSerde<>(OrderEvent.class);
        JsonSerde<OrderStats> statsSerde = new JsonSerde<>(OrderStats.class);

        KStream<String, OrderEvent> orderStream = builder.stream(
            ORDERS_TOPIC,
            Consumed.with(Serdes.String(), orderSerde)
        );

        // 1. 유효하지 않은 주문 필터링
        KStream<String, OrderEvent> validOrders = orderStream
            .filter((key, order) -> order != null 
                    && order.getQuantity() > 0 
                    && order.getPrice().compareTo(BigDecimal.ZERO) > 0)
            .peek((key, order) -> log.debug("유효 주문 처리 중: {}", order.getOrderId()));

        // 2. 고가 주문 분기 처리 (branch)
        Map<String, KStream<String, OrderEvent>> branches = validOrders.split()
            .branch((key, order) -> 
                order.getPrice().multiply(BigDecimal.valueOf(order.getQuantity()))
                     .compareTo(new BigDecimal("100000")) >= 0,
                Branched.withConsumer(stream -> 
                    stream.to(HIGH_VALUE_ORDERS_TOPIC, 
                              Produced.with(Serdes.String(), orderSerde)))
            )
            .defaultBranch(Branched.as("standard"));

        // 3. 1분 윈도우 기반 실시간 주문 통계 집계
        validOrders
            .groupByKey()
            .windowedBy(TimeWindows.ofSizeWithNoGrace(Duration.ofMinutes(1)))
            .aggregate(
                OrderStats::new,
                (key, order, stats) -> stats.accumulate(order),
                Materialized.<String, OrderStats, WindowStore<Bytes, byte[]>>as(
                    "order-stats-store")
                    .withValueSerde(statsSerde)
            )
            .toStream()
            .map((windowedKey, stats) -> KeyValue.pair(
                windowedKey.key(), stats
            ))
            .to(ORDER_STATS_TOPIC, Produced.with(Serdes.String(), statsSerde));

        return validOrders;
    }
}

// 집계용 통계 모델
@Data
@NoArgsConstructor
public class OrderStats {
    private long orderCount = 0;
    private BigDecimal totalRevenue = BigDecimal.ZERO;
    private BigDecimal maxOrderValue = BigDecimal.ZERO;

    public OrderStats accumulate(OrderEvent order) {
        BigDecimal orderValue = order.getPrice()
            .multiply(BigDecimal.valueOf(order.getQuantity()));
        this.orderCount++;
        this.totalRevenue = this.totalRevenue.add(orderValue);
        this.maxOrderValue = this.maxOrderValue.max(orderValue);
        return this;
    }
}
```

### Consumer: 처리된 이벤트 소비

```java
@Service
@Slf4j
@RequiredArgsConstructor
public class HighValueOrderConsumer {

    private final NotificationService notificationService;
    private final FraudDetectionService fraudDetectionService;

    @KafkaListener(
        topics = "high-value-orders",
        groupId = "high-value-order-processors",
        concurrency = "3",  // 파티션 수에 맞게 조정
        containerFactory = "kafkaListenerContainerFactory"
    )
    public void processHighValueOrder(
            @Payload OrderEvent order,
            @Header(KafkaHeaders.RECEIVED_PARTITION) int partition,
            @Header(KafkaHeaders.OFFSET) long offset,
            Acknowledgment acknowledgment) {
        
        try {
            log.info("고가 주문 처리 시작 [orderId={}, partition={}, offset={}]",
                     order.getOrderId(), partition, offset);

            // 사기 탐지 후 알림 발송
            if (!fraudDetectionService.isSuspicious(order)) {
                notificationService.sendVipAlert(order);
            } else {
                notificationService.sendFraudAlert(order);
            }

            // 처리 완료 후 수동 Ack (At-least-once 보장)
            acknowledgment.acknowledge();

        } catch (Exception e) {
            log.error("고가 주문 처리 실패 [orderId={}]", order.getOrderId(), e);
            // 재처리를 위해 Ack 하지 않음
            throw e;
        }
    }
}
```

### application.yml 핵심 설정

```yaml
spring:
  kafka:
    bootstrap-servers: kafka-1:9092,kafka-2:9092,kafka-3:9092
    producer:
      acks: all              # 모든 복제본 확인 후 응답
      retries: 3
      batch-size: 16384
      linger-ms: 5           # 배치 대기 시간 (처리량 vs 지연 트레이드오프)
      compression-type: snappy
      enable-idempotence: true   # 중복 발행 방지
    consumer:
      auto-offset-reset: earliest
      enable-auto-commit: false  # 수동 커밋으로 정확한 제어
      max-poll-records: 500
      isolation-level: read_committed  # Exactly-once 지원 시 필수
    streams:
      replication-factor: 3
```

---

## 주의사항 및 트레이드오프

### 1. Partition 수는 신중하게 결정하라

Partition 수는 생성 이후 늘리는 것은 가능하지만 **줄이는 것은 불가능**하다. 처음부터 충분한 수를 설정해야 하지만, 너무 많으면 Broker와 ZooKeeper(또는 KRaft)에 부하가 증가한다. 일반적으로 Consumer 인스턴스 수의 2~3배를 권장한다.

### 2. Lag 모니터링은 필수다

Consumer Lag은 시스템 건강의 핵심 지표다. Lag이 지속적으로 증가한다면 Consumer 처리 속도가 Producer 발행 속도를 따라가지 못하는 것이다. **Prometheus + Grafana** 조합으로 `kafka_consumer_group_lag` 메트릭을 반드시 모니터링해야 한다.

### 3. Exactly-once의 비용을 이해하라

`EXACTLY_ONCE_V2`는 Transactional Producer와 함께 동작하며, 처리량이 약 20~30% 감소할 수 있다. 결제나 재고와 같은 **금전적 정확성이 중요한 파이프라인**에만 적용하고, 로그 수집이나 메트릭 집계처럼 약간의 중복이 허용되는 곳에는 At-least-once를 사용하는 것이 현명하다.

### 4. Schema 관리를 소홀히 하지 마라

JSON 직렬화는 빠르게 시작할 수 있지만, 스키마 변경에 취약하다. 팀 규모가 커지거나 다수의 Consumer가 존재한다면 **Confluent Schema Registry**와 Avro 또는 Protobuf를 채택하여 스키마 진화(Schema Evolution)를 관리하는 것을 강력히 권장한다.

### 5. 상태 저장소의 복구 시간을 고려하라

Kafka Streams의 RocksDB 기반 상태 저장소는 장애 후 Changelog Topic에서 상태를 복구한다. 대용량 상태를 보유한 경우 복구 시간이 수 분에서 수십 분까지 걸릴 수 있다. **Standby Replica** (`num.standby.replicas`) 설정으로 복구 시간을 단축할 수 있다.

---

## 정리

Apache Kafka를 중심으로 한 실시간 데이터 파이프라인은 단순한 기술 선택을 넘어 **아키텍처 철학**의 선택이다. 이벤트 소싱, CQRS, 마이크로서비스 간 비동기 통신의 기반 인프라로서 Kafka는 탁월한 선택지다.

이번 포스팅에서 다룬 핵심을 정리하면 다음과 같다.

- **Partition 설계**가 처리량과 순서 보장의 핵심이다
- **Kafka Streams**는 별도 클러스터 없이 강력한 스트림 처리를 가능하게 한다
- **전달 보장 수준(Delivery Semantics)**은 비즈니스 요구사항에 맞게 의식적으로 선택해야 한다
- **Consumer Lag 모니터링**은 파이프라인 안정성의 바로미터다
- **Schema Registry** 도입은 팀과 시스템이 성장할수록 필수불가결해진다

다음 단계로는 Kafka Connect를 활용한 DB-to-Kafka 변경 데이터 캡처(CDC) 구성, 그리고 ksqlDB를 이용한 SQL 기반 스트림 처리를 탐구해볼 것을 추천한다. 실시간 데이터 아키텍처의 여정은 Kafka를 제대로 이해하는 순간부터 새로운 가능성이 열린다.