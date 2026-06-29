# Redis Pub/Sub과 Stream으로 실시간 메시징 구현

## 개요

실시간 메시징은 현대 애플리케이션에서 빠질 수 없는 요소가 됐다. 채팅 시스템, 알림 서비스, 실시간 대시보드, 마이크로서비스 간 이벤트 전파 등 다양한 도메인에서 메시지 브로커의 역할이 중요해졌다. Kafka나 RabbitMQ 같은 전용 메시지 브로커도 훌륭한 선택이지만, 이미 캐시 레이어로 Redis를 운영 중이라면 Redis가 제공하는 **Pub/Sub**과 **Stream** 기능만으로도 충분히 강력한 실시간 메시징을 구현할 수 있다.

이 글에서는 Redis Pub/Sub과 Stream의 차이를 명확히 짚고, Spring Boot + Lettuce 환경에서 실무에 바로 적용할 수 있는 예제 코드를 함께 살펴본다.

---

## 핵심 개념

### Redis Pub/Sub

Pub/Sub은 **발행-구독(Publish-Subscribe)** 패턴의 가장 단순한 구현이다. 발행자(Publisher)가 특정 채널(Channel)에 메시지를 보내면, 해당 채널을 구독(Subscribe) 중인 모든 클라이언트가 즉시 메시지를 수신한다.

핵심 특징:
- **Fire-and-Forget**: 메시지를 Redis에 저장하지 않는다. 구독자가 없거나 연결이 끊긴 상태면 메시지는 영구적으로 소실된다.
- **브로드캐스트**: 하나의 메시지가 모든 구독자에게 전달된다.
- **패턴 구독**: `PSUBSCRIBE notification.*` 처럼 글로브 패턴으로 여러 채널을 동시에 구독할 수 있다.

```
Publisher → [Channel: "orders"] → Subscriber A
                                 → Subscriber B
                                 → Subscriber C
```

### Redis Stream

Stream은 Redis 5.0에서 도입된 **영속적(persistent) 로그 기반** 자료구조다. Kafka의 컨셉을 Redis에 녹여낸 것으로, 메시지가 Stream에 기록되고 컨슈머가 직접 오프셋을 관리하며 읽어간다.

핵심 특징:
- **메시지 영속성**: 메시지가 Redis에 저장되며, 컨슈머가 나중에도 읽을 수 있다.
- **Consumer Group**: 동일한 메시지를 여러 컨슈머 중 하나만 처리하는 경쟁 소비(competing consumer) 패턴 지원.
- **ACK 메커니즘**: 메시지 처리 완료를 명시적으로 확인해야 하며, 미확인 메시지는 PEL(Pending Entry List)에 남는다.
- **재처리 가능**: 장애 발생 시 미처리 메시지를 재소비할 수 있다.

---

## 실전 예제

### 환경 설정

```gradle
// build.gradle
dependencies {
    implementation 'org.springframework.boot:spring-boot-starter-data-redis'
    implementation 'io.lettuce:lettuce-core:6.3.2.RELEASE'
}
```

```yaml
# application.yml
spring:
  data:
    redis:
      host: localhost
      port: 6379
      lettuce:
        pool:
          max-active: 10
          max-idle: 5
```

### Pub/Sub 구현

**RedisConfig 설정**

```java
@Configuration
public class RedisPubSubConfig {

    @Bean
    public RedisConnectionFactory redisConnectionFactory() {
        return new LettuceConnectionFactory("localhost", 6379);
    }

    @Bean
    public RedisMessageListenerContainer redisMessageListenerContainer(
            RedisConnectionFactory connectionFactory,
            OrderEventSubscriber orderEventSubscriber) {

        RedisMessageListenerContainer container = new RedisMessageListenerContainer();
        container.setConnectionFactory(connectionFactory);
        container.addMessageListener(
            orderEventSubscriber,
            new ChannelTopic("orders")
        );
        // 패턴 구독 예시
        container.addMessageListener(
            orderEventSubscriber,
            new PatternTopic("notification.*")
        );
        return container;
    }

    @Bean
    public RedisTemplate<String, Object> redisTemplate(
            RedisConnectionFactory connectionFactory) {

        RedisTemplate<String, Object> template = new RedisTemplate<>();
        template.setConnectionFactory(connectionFactory);
        template.setKeySerializer(new StringRedisSerializer());
        template.setValueSerializer(new GenericJackson2JsonRedisSerializer());
        return template;
    }
}
```

**Publisher 구현**

```java
@Service
@RequiredArgsConstructor
public class OrderEventPublisher {

    private final RedisTemplate<String, Object> redisTemplate;
    private static final String ORDERS_CHANNEL = "orders";

    public void publishOrderCreated(OrderEvent event) {
        redisTemplate.convertAndSend(ORDERS_CHANNEL, event);
        log.info("Published order event: {}", event.getOrderId());
    }
}

@Getter
@AllArgsConstructor
public class OrderEvent {
    private String orderId;
    private String status;
    private LocalDateTime occurredAt;
}
```

**Subscriber 구현**

```java
@Component
@Slf4j
public class OrderEventSubscriber implements MessageListener {

    private final ObjectMapper objectMapper;

    @Override
    public void onMessage(Message message, byte[] pattern) {
        try {
            String body = new String(message.getBody(), StandardCharsets.UTF_8);
            OrderEvent event = objectMapper.readValue(body, OrderEvent.class);
            log.info("Received order event: orderId={}, status={}",
                     event.getOrderId(), event.getStatus());
            // 비즈니스 로직 처리
            processOrderEvent(event);
        } catch (JsonProcessingException e) {
            log.error("Failed to deserialize message", e);
        }
    }

    private void processOrderEvent(OrderEvent event) {
        // 알림 발송, 상태 업데이트 등
    }
}
```

---

### Redis Stream 구현

Stream은 Pub/Sub보다 설정이 복잡하지만 훨씬 견고하다.

**Stream Producer**

```java
@Service
@RequiredArgsConstructor
@Slf4j
public class OrderStreamProducer {

    private final RedisTemplate<String, Object> redisTemplate;
    private static final String STREAM_KEY = "stream:orders";

    public String publishOrder(OrderEvent event) {
        Map<String, Object> fields = new HashMap<>();
        fields.put("orderId", event.getOrderId());
        fields.put("status", event.getStatus());
        fields.put("occurredAt", event.getOccurredAt().toString());

        // RecordId는 Redis가 자동 생성 (타임스탬프-시퀀스 형식)
        RecordId recordId = redisTemplate.opsForStream()
            .add(StreamRecords.newRecord()
                .in(STREAM_KEY)
                .ofMap(fields));

        log.info("Stream record added: {}", recordId);
        return recordId.getValue();
    }
}
```

**Consumer Group 생성 및 Stream Consumer**

```java
@Component
@RequiredArgsConstructor
@Slf4j
public class OrderStreamConsumer {

    private final RedisTemplate<String, Object> redisTemplate;
    private static final String STREAM_KEY = "stream:orders";
    private static final String GROUP_NAME = "order-processors";
    private static final String CONSUMER_NAME = "consumer-" + UUID.randomUUID();

    @PostConstruct
    public void initConsumerGroup() {
        try {
            redisTemplate.opsForStream()
                .createGroup(STREAM_KEY, ReadOffset.from("0"), GROUP_NAME);
            log.info("Consumer group '{}' created", GROUP_NAME);
        } catch (RedisSystemException e) {
            // 이미 그룹이 존재하면 무시
            log.info("Consumer group already exists, skipping creation");
        }
    }

    @Scheduled(fixedDelay = 1000)
    public void consume() {
        List<MapRecord<String, Object, Object>> records =
            redisTemplate.opsForStream().read(
                Consumer.from(GROUP_NAME, CONSUMER_NAME),
                StreamReadOptions.empty().count(10).block(Duration.ofMillis(2000)),
                StreamOffset.create(STREAM_KEY, ReadOffset.lastConsumed())
            );

        if (records == null || records.isEmpty()) return;

        for (MapRecord<String, Object, Object> record : records) {
            try {
                processRecord(record);
                // 처리 완료 후 ACK
                redisTemplate.opsForStream()
                    .acknowledge(STREAM_KEY, GROUP_NAME, record.getId());
                log.info("Acknowledged record: {}", record.getId());
            } catch (Exception e) {
                log.error("Failed to process record: {}", record.getId(), e);
                // ACK하지 않으면 PEL에 남아 재처리 가능
            }
        }
    }

    private void processRecord(MapRecord<String, Object, Object> record) {
        Map<Object, Object> fields = record.getValue();
        String orderId = (String) fields.get("orderId");
        String status = (String) fields.get("status");
        log.info("Processing stream record: orderId={}, status={}", orderId, status);
    }
}
```

**미처리(Pending) 메시지 재처리**

```java
@Scheduled(fixedDelay = 60000) // 1분마다 실행
public void retryPendingMessages() {
    PendingMessagesSummary pendingSummary = redisTemplate.opsForStream()
        .pending(STREAM_KEY, GROUP_NAME);

    if (pendingSummary.getTotalPendingMessages() == 0) return;

    // 5분 이상 미처리된 메시지 재할당
    PendingMessages pendingMessages = redisTemplate.opsForStream()
        .pending(STREAM_KEY, Consumer.from(GROUP_NAME, CONSUMER_NAME),
                 Range.unbounded(), 100L);

    for (PendingMessage msg : pendingMessages) {
        if (msg.getElapsedTimeSinceLastDelivery().toMinutes() >= 5) {
            redisTemplate.opsForStream()
                .claim(STREAM_KEY, GROUP_NAME, CONSUMER_NAME,
                       Duration.ofMinutes(5), msg.getIdAsString());
        }
    }
}
```

---

## 주의사항 및 트레이드오프

### Pub/Sub 주의사항

**1. 메시지 유실 위험**
Pub/Sub은 구독자가 없거나 네트워크가 단절된 순간의 메시지를 복구할 방법이 없다. 알림 유실이 치명적인 도메인이라면 Stream이나 Kafka를 고려해야 한다.

**2. 스케일 아웃 시 중복 처리**
Pod를 수평 확장하면 모든 인스턴스가 동일한 메시지를 수신한다. 이는 의도된 브로드캐스트라면 괜찮지만, 처리 작업이 한 번만 실행되어야 한다면 분산 락이나 Consumer Group 방식을 사용해야 한다.

**3. 백프레셔 없음**
발행 속도가 소비 속도를 앞서도 Redis는 조절하지 않는다. 소비자 측에서 처리 지연이 발생하면 이벤트 누락으로 이어질 수 있다.

### Redis Stream 주의사항

**1. 메모리 관리**
Stream은 데이터를 메모리에 보관한다. `MAXLEN` 옵션으로 스트림 크기를 제한하거나, TTL 정책을 설정하는 것이 필수다.

```java
// 최대 10,000개 레코드 유지 (근사값 트리밍으로 성능 최적화)
redisTemplate.opsForStream()
    .add(StreamRecords.newRecord()
        .in(STREAM_KEY)
        .ofMap(fields),
    // MAXLEN ~ 10000
    );
```

**2. 단일 Redis 장애 시 데이터 유실**
Redis Sentinel이나 Cluster를 구성하더라도 AOF/RDB 설정이 적절하지 않으면 장애 시 일부 메시지가 유실될 수 있다. 금융 트랜잭션 같은 고신뢰성 도메인에서는 Kafka를 우선 검토할 것을 권장한다.

**3. Consumer Group 관리 복잡성**
컨슈머가 죽으면 PEL에 메시지가 쌓인다. 재처리 로직과 Dead Letter Queue 개념을 반드시 설계에 포함해야 한다.

### Pub/Sub vs Stream 선택 기준

| 기준 | Pub/Sub | Stream |
|---|---|---|
| 메시지 영속성 | ❌ | ✅ |
| 재처리 가능 | ❌ | ✅ |
| 스케일 아웃 | 브로드캐스트 | 파티셔닝 |
| 복잡도 | 낮음 | 높음 |
| 적합한 케이스 | 실시간 알림, 캐시 무효화 | 이벤트 소싱, 작업 큐 |

---

## 정리

Redis Pub/Sub과 Stream은 서로 다른 문제를 해결하기 위한 도구다.

- **Pub/Sub**은 구현이 단순하고 지연이 낮다. 메시지 유실을 허용할 수 있는 실시간 알림, WebSocket 브로드캐스트, 캐시 무효화 신호 같은 케이스에 이상적이다.
- **Stream**은 복잡하지만 견고하다. 메시지 유실을 용납하지 않는 이벤트 기반 처리, 마이크로서비스 간 비동기 통신, 작업 큐에 적합하다.

중요한 것은 이미 인프라에 Redis가 존재한다면, Kafka나 RabbitMQ를 추가하기 전에 Redis Stream으로 요구사항을 충족할 수 있는지 먼저 검토해보는 것이다. 인프라 복잡성을 늘리지 않고도 충분한 수준의 신뢰성과 성능을 확보할 수 있는 경우가 생각보다 많다.

단, Redis는 결국 인메모리 데이터베이스임을 항상 기억하자. 처리량이 수백만 TPS를 넘거나, 강력한 순서 보장과 정확히 한 번(exactly-once) 처리가 필요한 시나리오라면 전용 메시지 브로커로의 전환을 진지하게 고려해야 한다.