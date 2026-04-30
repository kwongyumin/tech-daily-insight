# Redis Pub/Sub과 Stream으로 실시간 메시징 구현

## 개요

실시간 메시징은 현대 서비스에서 빠질 수 없는 요소가 되었다. 채팅 시스템, 알림 푸시, 실시간 대시보드, 이벤트 드리븐 아키텍처까지 다양한 곳에서 메시지 브로커가 필요하다. Kafka나 RabbitMQ 같은 전통적인 메시지 브로커도 훌륭한 선택지이지만, 이미 캐싱 레이어로 Redis를 사용하고 있다면 별도의 인프라 없이 Redis의 Pub/Sub과 Stream으로 실시간 메시징을 구현할 수 있다.

이 글에서는 Redis의 두 가지 메시징 메커니즘인 **Pub/Sub**과 **Stream**의 차이를 명확히 하고, Spring Boot 환경에서 실제로 동작하는 코드를 통해 각각의 유즈케이스를 다뤄본다.

---

## 핵심 개념

### Redis Pub/Sub

Pub/Sub은 발행-구독 패턴의 가장 순수한 형태다. Publisher가 특정 채널에 메시지를 발행하면, 해당 채널을 구독하는 모든 Subscriber가 메시지를 수신한다.

**특징:**
- **Fire-and-Forget**: 메시지를 발행하는 순간, 구독자가 없으면 메시지는 사라진다
- **영속성 없음**: 메시지가 저장되지 않으므로 나중에 재처리가 불가능하다
- **브로드캐스팅**: 하나의 메시지가 모든 구독자에게 전달된다
- **패턴 구독 지원**: `PSUBSCRIBE`를 통해 와일드카드 패턴으로 채널 구독 가능

### Redis Stream

Redis 5.0에서 도입된 Stream은 Kafka의 영향을 받아 설계된 로그 기반의 자료구조다. 단순한 Pub/Sub의 한계를 극복한다.

**특징:**
- **영속성**: 메시지가 Redis 메모리에 저장되어 이후에도 조회 가능
- **Consumer Group**: 여러 소비자가 작업을 분산 처리 가능
- **메시지 확인(ACK)**: 소비자가 명시적으로 처리 완료를 알릴 수 있다
- **메시지 ID 기반 순서 보장**: 시간 기반의 고유 ID(`millisecondsTime-sequenceNumber`)로 순서를 보장

| 항목 | Pub/Sub | Stream |
|------|---------|--------|
| 메시지 영속성 | ❌ | ✅ |
| 소비자 그룹 | ❌ | ✅ |
| 재처리 가능 | ❌ | ✅ |
| 순서 보장 | ❌ | ✅ |
| 실시간 브로드캐스트 | ✅ | 제한적 |
| 구현 복잡도 | 낮음 | 중간 |

---

## 실전 예제

### 환경 설정

```xml
<!-- pom.xml -->
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-data-redis</artifactId>
</dependency>
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

---

### 1. Redis Pub/Sub 구현 — 실시간 알림 시스템

실시간 알림처럼 수신 실패해도 크게 문제없는 유즈케이스에 적합하다.

**RedisConfig.java**

```java
@Configuration
public class RedisConfig {

    @Bean
    public RedisTemplate<String, Object> redisTemplate(RedisConnectionFactory factory) {
        RedisTemplate<String, Object> template = new RedisTemplate<>();
        template.setConnectionFactory(factory);
        template.setKeySerializer(new StringRedisSerializer());
        template.setValueSerializer(new GenericJackson2JsonRedisSerializer());
        return template;
    }

    @Bean
    public RedisMessageListenerContainer messageListenerContainer(
            RedisConnectionFactory factory,
            NotificationSubscriber subscriber) {

        RedisMessageListenerContainer container = new RedisMessageListenerContainer();
        container.setConnectionFactory(factory);
        // 특정 채널 구독
        container.addMessageListener(subscriber, new PatternTopic("notification:*"));
        return container;
    }
}
```

**NotificationSubscriber.java**

```java
@Slf4j
@Component
public class NotificationSubscriber implements MessageListener {

    private final ObjectMapper objectMapper;

    public NotificationSubscriber(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    @Override
    public void onMessage(Message message, byte[] pattern) {
        try {
            String channel = new String(message.getChannel());
            String body = new String(message.getBody());

            NotificationEvent event = objectMapper.readValue(body, NotificationEvent.class);
            log.info("채널: {}, 수신된 알림: {}", channel, event);

            // 실제 처리 로직 (WebSocket 전송, FCM 푸시 등)
            processNotification(channel, event);

        } catch (JsonProcessingException e) {
            log.error("메시지 역직렬화 실패", e);
        }
    }

    private void processNotification(String channel, NotificationEvent event) {
        // WebSocket, SSE 등을 통해 클라이언트에 전달하는 로직
    }
}
```

**NotificationPublisher.java**

```java
@Service
@RequiredArgsConstructor
public class NotificationPublisher {

    private final RedisTemplate<String, Object> redisTemplate;
    private final ObjectMapper objectMapper;

    public void publish(String userId, NotificationEvent event) {
        try {
            String channel = "notification:" + userId;
            String payload = objectMapper.writeValueAsString(event);
            redisTemplate.convertAndSend(channel, payload);
            log.info("알림 발행 완료 - 채널: {}", channel);
        } catch (JsonProcessingException e) {
            log.error("메시지 직렬화 실패", e);
            throw new RuntimeException("알림 발행 실패", e);
        }
    }
}

@Data
@AllArgsConstructor
@NoArgsConstructor
public class NotificationEvent {
    private String type;       // COMMENT, LIKE, FOLLOW 등
    private String senderId;
    private String message;
    private LocalDateTime timestamp;
}
```

---

### 2. Redis Stream 구현 — 주문 이벤트 처리

메시지 유실이 허용되지 않는 주문 처리 같은 유즈케이스에 Stream이 적합하다.

**OrderStreamProducer.java**

```java
@Service
@RequiredArgsConstructor
public class OrderStreamProducer {

    private static final String STREAM_KEY = "orders:stream";
    private final RedisTemplate<String, Object> redisTemplate;

    public String publishOrder(OrderEvent event) {
        Map<String, Object> fields = new HashMap<>();
        fields.put("orderId", event.getOrderId());
        fields.put("userId", event.getUserId());
        fields.put("amount", String.valueOf(event.getAmount()));
        fields.put("status", event.getStatus());
        fields.put("timestamp", event.getTimestamp().toString());

        // RecordId는 Redis가 자동 생성 (현재시간-시퀀스 형식)
        RecordId recordId = redisTemplate.opsForStream()
                .add(StreamRecords.mapBacked(fields).withStreamKey(STREAM_KEY));

        log.info("주문 이벤트 발행 완료 - RecordId: {}", recordId);
        return recordId.getValue();
    }
}
```

**OrderStreamConsumer.java**

```java
@Slf4j
@Service
@RequiredArgsConstructor
public class OrderStreamConsumer {

    private static final String STREAM_KEY = "orders:stream";
    private static final String GROUP_NAME = "order-processors";
    private static final String CONSUMER_NAME = "consumer-" + UUID.randomUUID();

    private final RedisTemplate<String, Object> redisTemplate;
    private final OrderService orderService;

    @PostConstruct
    public void initConsumerGroup() {
        try {
            // Consumer Group 생성 (이미 존재하면 무시)
            redisTemplate.opsForStream()
                    .createGroup(STREAM_KEY, ReadOffset.from("0"), GROUP_NAME);
            log.info("Consumer Group 생성: {}", GROUP_NAME);
        } catch (RedisSystemException e) {
            // BUSYGROUP: Consumer Group already exists
            log.info("Consumer Group 이미 존재함: {}", GROUP_NAME);
        }
    }

    @Scheduled(fixedDelay = 100) // 100ms마다 폴링
    public void consumeOrders() {
        List<MapRecord<String, Object, Object>> records = redisTemplate.opsForStream()
                .read(Consumer.from(GROUP_NAME, CONSUMER_NAME),
                        StreamReadOptions.empty().count(10).block(Duration.ofMillis(50)),
                        StreamOffset.create(STREAM_KEY, ReadOffset.lastConsumed()));

        if (records == null || records.isEmpty()) return;

        for (MapRecord<String, Object, Object> record : records) {
            try {
                processRecord(record);
                // 처리 성공 시 ACK
                redisTemplate.opsForStream()
                        .acknowledge(STREAM_KEY, GROUP_NAME, record.getId());

            } catch (Exception e) {
                log.error("주문 처리 실패 - RecordId: {}", record.getId(), e);
                // ACK 하지 않으면 PEL(Pending Entry List)에 남아 재처리 대상이 됨
            }
        }
    }

    private void processRecord(MapRecord<String, Object, Object> record) {
        Map<Object, Object> data = record.getValue();
        OrderEvent event = OrderEvent.builder()
                .orderId((String) data.get("orderId"))
                .userId((String) data.get("userId"))
                .amount(new BigDecimal((String) data.get("amount")))
                .status((String) data.get("status"))
                .build();

        log.info("주문 처리 중 - OrderId: {}", event.getOrderId());
        orderService.process(event);
    }
}
```

**PEL(Pending Entry List) 재처리 스케줄러**

```java
@Scheduled(fixedDelay = 30_000) // 30초마다 미처리 메시지 재처리
public void retryPendingMessages() {
    // 60초 이상 ACK 되지 않은 메시지 조회
    PendingMessages pendingMessages = redisTemplate.opsForStream()
            .pending(STREAM_KEY, Consumer.from(GROUP_NAME, CONSUMER_NAME),
                    Range.unbounded(), 100L);

    for (PendingMessage pending : pendingMessages) {
        if (pending.getTotalDeliveryCount() >= 3) {
            // 3회 이상 실패 시 DLQ(Dead Letter Queue) 처리
            moveToDeadLetterQueue(pending.getId().getValue());
            redisTemplate.opsForStream()
                    .acknowledge(STREAM_KEY, GROUP_NAME, pending.getId());
        } else {
            // XCLAIM으로 메시지 재할당 후 재처리
            redisTemplate.opsForStream()
                    .claim(STREAM_KEY, Consumer.from(GROUP_NAME, CONSUMER_NAME),
                            Duration.ofSeconds(60), pending.getId());
        }
    }
}
```

---

## 주의사항 및 트레이드오프

### Pub/Sub의 함정

1. **메시지 유실**: 구독자가 다운되거나 네트워크가 끊기면 그 사이 발행된 메시지는 복구 불가능하다. 결제, 주문처럼 중요한 이벤트에는 절대 사용하지 말 것.

2. **수평 확장 시 브로드캐스트**: 서버 인스턴스가 여러 개일 때 모든 인스턴스가 동일한 메시지를 받는다. WebSocket 세션 관리와 함께 사용할 때 중복 처리 로직이 필요하다.

3. **연결 점유**: Subscriber는 전용 Redis 연결을 유지하므로, Lettuce의 Connection Pool 설정을 별도로 관리해야 한다.

### Stream의 트레이드오프

1. **메모리 관리 필수**: Stream은 메시지를 메모리에 쌓으므로 `MAXLEN` 옵션으로 크기를 제한해야 한다.
   ```java
   redisTemplate.opsForStream()
       .add(StreamRecords.mapBacked(fields)
           .withStreamKey(STREAM_KEY));
   // Redis CLI에서: XADD orders:stream MAXLEN ~ 10000 * ...
   ```

2. **폴링 기반**: 기본적으로 폴링 방식이므로 실시간성이 Pub/Sub보다 낮다. `BLOCK` 옵션을 활용하면 완화할 수 있다.

3. **Redis 단일 장애점**: Redis가 다운되면 메시지 처리 전체가 멈춘다. Redis Sentinel이나 Cluster 구성이 필수다.

4. **Kafka 대비 한계**: 대용량 처리(초당 수십만 건)나 장기 보관(수일~수개월)이 필요하다면 Kafka를 선택해야 한다. Redis Stream은 주로 **단기 버퍼** 역할로 활용하는 것이 현실적이다.

### 적절한 선택 기준

```
메시지 유실 허용 + 브로드캐스트 필요    → Pub/Sub
메시지 보장 + 분산 처리 필요             → Stream
대용량 + 장기 보관 + 강한 내구성        → Kafka
```

---

## 정리

Redis Pub/Sub과 Stream은 서로 경쟁하는 기술이 아니라 **보완적인 관계**다. 실시간 알림이나 라이브 채팅처럼 일부 유실을 감수할 수 있고 빠른 브로드캐스트가 필요한 경우엔 Pub/Sub이, 주문·결제처럼 메시지 신뢰성과 재처리가 필요한 곳엔 Stream이 맞다.

핵심은 두 메커니즘의 특성을 명확히 이해하고 유즈케이스에 맞게 선택하는 것이다. 별도의 Kafka 클러스터를 운영할 여건이 되지 않는 스타트업이나 중소 규모 서비스에서 이미 Redis를 사용 중이라면, Redis Stream은 충분히 실용적인 대안이 될 수 있다.

다만 트래픽이 성장하면 결국 전문 메시지 브로커로의 마이그레이션이 필요해지는 시점이 온다. 처음부터 메시지 발행·구독 인터페이스를 추상화해두면 그 전환 비용을 크게 줄일 수 있다.