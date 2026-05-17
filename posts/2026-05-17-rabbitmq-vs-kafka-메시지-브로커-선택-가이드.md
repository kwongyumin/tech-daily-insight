# RabbitMQ vs Kafka 메시지 브로커 선택 가이드

## 개요

메시지 브로커는 현대 분산 시스템의 핵심 구성 요소입니다. 마이크로서비스 아키텍처가 보편화되면서 서비스 간 비동기 통신을 담당하는 메시지 브로커의 선택이 시스템 전체의 성능과 안정성에 직접적인 영향을 미치게 되었습니다.

현업에서 가장 많이 비교되는 두 메시지 브로커인 **RabbitMQ**와 **Apache Kafka**는 각자의 철학과 설계 목표가 명확히 다릅니다. 단순히 "어느 것이 더 낫다"는 이분법적 비교가 아니라, 각 시스템의 특성을 이해하고 **비즈니스 요구사항에 맞는 선택**을 하는 것이 중요합니다.

이 글에서는 두 브로커의 핵심 개념, 아키텍처 차이, 실전 예제 코드, 그리고 실무에서의 트레이드오프를 중심으로 선택 기준을 제시합니다.

---

## 핵심 개념

### RabbitMQ: 스마트 브로커, 단순 컨슈머

RabbitMQ는 **AMQP(Advanced Message Queuing Protocol)** 기반의 전통적인 메시지 브로커입니다. 메시지 라우팅 로직이 브로커에 집중되어 있으며, 다양한 Exchange 타입(Direct, Topic, Fanout, Headers)을 통해 복잡한 라우팅 규칙을 표현할 수 있습니다.

**RabbitMQ의 핵심 구성 요소:**

| 구성 요소 | 역할 |
|-----------|------|
| Producer | 메시지 발행 |
| Exchange | 라우팅 규칙 적용 |
| Queue | 메시지 저장 및 대기 |
| Consumer | 메시지 소비 후 ACK 처리 |
| Binding | Exchange와 Queue 연결 규칙 |

RabbitMQ는 메시지가 **소비되면 삭제**되는 구조이며, 메시지 순서 보장, 우선순위 큐, Dead Letter Queue 등의 기능을 기본 제공합니다.

### Apache Kafka: 단순 브로커, 스마트 컨슈머

Kafka는 LinkedIn에서 대용량 로그 처리를 위해 만들어진 **분산 이벤트 스트리밍 플랫폼**입니다. 메시지(이벤트)는 **토픽의 파티션에 순서대로 append**되며, 컨슈머는 자신의 오프셋(offset)을 관리하면서 원하는 시점부터 메시지를 읽을 수 있습니다.

**Kafka의 핵심 구성 요소:**

| 구성 요소 | 역할 |
|-----------|------|
| Producer | 토픽에 메시지 발행 |
| Topic/Partition | 메시지 저장 단위 (수평 확장 가능) |
| Offset | 컨슈머가 읽은 위치 추적 |
| Consumer Group | 파티션을 분산하여 소비 |
| Broker | 클러스터 내 개별 노드 |

Kafka는 메시지가 소비되어도 **보존 기간(retention period) 동안 데이터를 유지**합니다. 이는 이벤트 소싱, 재처리, 감사 로그 등에 강력한 이점을 제공합니다.

### 아키텍처 비교 요약

```
RabbitMQ:
Producer → Exchange → Queue → Consumer (메시지 소비 후 삭제)

Kafka:
Producer → Topic/Partition (로그 append) ← Consumer (offset 관리)
```

---

## 실전 예제

### Spring Boot + RabbitMQ: 주문 처리 시스템

주문이 생성되면 결제 서비스와 재고 서비스로 메시지를 라우팅하는 예제입니다.

**의존성 추가 (build.gradle)**

```groovy
implementation 'org.springframework.boot:spring-boot-starter-amqp'
```

**RabbitMQ 설정**

```java
@Configuration
public class RabbitMQConfig {

    public static final String ORDER_EXCHANGE = "order.exchange";
    public static final String PAYMENT_QUEUE = "payment.queue";
    public static final String INVENTORY_QUEUE = "inventory.queue";
    public static final String DEAD_LETTER_QUEUE = "order.dlq";

    @Bean
    public TopicExchange orderExchange() {
        return new TopicExchange(ORDER_EXCHANGE);
    }

    @Bean
    public Queue paymentQueue() {
        return QueueBuilder.durable(PAYMENT_QUEUE)
                .withArgument("x-dead-letter-exchange", "")
                .withArgument("x-dead-letter-routing-key", DEAD_LETTER_QUEUE)
                .withArgument("x-message-ttl", 60000) // 60초 TTL
                .build();
    }

    @Bean
    public Queue inventoryQueue() {
        return QueueBuilder.durable(INVENTORY_QUEUE).build();
    }

    @Bean
    public Queue deadLetterQueue() {
        return QueueBuilder.durable(DEAD_LETTER_QUEUE).build();
    }

    @Bean
    public Binding paymentBinding(Queue paymentQueue, TopicExchange orderExchange) {
        return BindingBuilder.bind(paymentQueue)
                .to(orderExchange)
                .with("order.created.#"); // Topic 라우팅 키 패턴
    }

    @Bean
    public Binding inventoryBinding(Queue inventoryQueue, TopicExchange orderExchange) {
        return BindingBuilder.bind(inventoryQueue)
                .to(orderExchange)
                .with("order.created.#");
    }
}
```

**메시지 발행 (Producer)**

```java
@Service
@RequiredArgsConstructor
public class OrderProducer {

    private final RabbitTemplate rabbitTemplate;

    public void publishOrderCreated(OrderEvent event) {
        rabbitTemplate.convertAndSend(
                RabbitMQConfig.ORDER_EXCHANGE,
                "order.created." + event.getRegion(), // 동적 라우팅 키
                event,
                message -> {
                    message.getMessageProperties().setMessageId(UUID.randomUUID().toString());
                    message.getMessageProperties().setTimestamp(new Date());
                    return message;
                }
        );
        log.info("Order event published: {}", event.getOrderId());
    }
}
```

**메시지 소비 (Consumer)**

```java
@Component
@RequiredArgsConstructor
@Slf4j
public class PaymentConsumer {

    @RabbitListener(queues = RabbitMQConfig.PAYMENT_QUEUE)
    public void processPayment(OrderEvent event, Channel channel,
                                @Header(AmqpHeaders.DELIVERY_TAG) long deliveryTag) {
        try {
            // 결제 처리 로직
            paymentService.process(event);
            channel.basicAck(deliveryTag, false); // 성공 ACK
        } catch (RecoverableException e) {
            // 재처리 가능한 예외: requeue
            channel.basicNack(deliveryTag, false, true);
        } catch (Exception e) {
            // 재처리 불가 예외: DLQ로 이동
            channel.basicNack(deliveryTag, false, false);
            log.error("Payment processing failed, moved to DLQ: {}", event.getOrderId(), e);
        }
    }
}
```

---

### Spring Boot + Kafka: 실시간 이벤트 스트리밍

사용자 행동 로그를 Kafka로 수집하고, 여러 컨슈머 그룹이 독립적으로 처리하는 예제입니다.

**의존성 추가 (build.gradle)**

```groovy
implementation 'org.springframework.kafka:spring-kafka'
```

**Kafka 설정**

```java
@Configuration
@EnableKafka
public class KafkaConfig {

    @Bean
    public ProducerFactory<String, UserEvent> producerFactory() {
        Map<String, Object> config = new HashMap<>();
        config.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, "localhost:9092");
        config.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        config.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, JsonSerializer.class);
        // 정확히 한 번 전송을 위한 설정
        config.put(ProducerConfig.ENABLE_IDEMPOTENCE_CONFIG, true);
        config.put(ProducerConfig.ACKS_CONFIG, "all");
        config.put(ProducerConfig.RETRIES_CONFIG, 3);
        return new DefaultKafkaProducerFactory<>(config);
    }

    @Bean
    public KafkaTemplate<String, UserEvent> kafkaTemplate() {
        return new KafkaTemplate<>(producerFactory());
    }

    @Bean
    public ConsumerFactory<String, UserEvent> consumerFactory() {
        Map<String, Object> config = new HashMap<>();
        config.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, "localhost:9092");
        config.put(ConsumerConfig.GROUP_ID_CONFIG, "analytics-group");
        config.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        config.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, JsonDeserializer.class);
        config.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        // 자동 커밋 비활성화 (수동 오프셋 관리)
        config.put(ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, false);
        return new DefaultKafkaConsumerFactory<>(config);
    }
}
```

**메시지 발행 (Producer)**

```java
@Service
@RequiredArgsConstructor
@Slf4j
public class UserEventProducer {

    private static final String TOPIC = "user-events";
    private final KafkaTemplate<String, UserEvent> kafkaTemplate;

    public void publishUserEvent(UserEvent event) {
        // userId를 파티션 키로 사용 → 동일 유저 이벤트는 같은 파티션으로
        kafkaTemplate.send(TOPIC, event.getUserId(), event)
                .whenComplete((result, ex) -> {
                    if (ex == null) {
                        log.info("Event sent to partition={}, offset={}",
                                result.getRecordMetadata().partition(),
                                result.getRecordMetadata().offset());
                    } else {
                        log.error("Failed to send event for userId={}", event.getUserId(), ex);
                    }
                });
    }
}
```

**메시지 소비 (Consumer) - 수동 오프셋 커밋**

```java
@Component
@Slf4j
public class AnalyticsConsumer {

    @KafkaListener(
            topics = "user-events",
            groupId = "analytics-group",
            containerFactory = "kafkaListenerContainerFactory"
    )
    public void consume(ConsumerRecord<String, UserEvent> record,
                        Acknowledgment acknowledgment) {
        try {
            UserEvent event = record.value();
            log.info("Processing event: userId={}, partition={}, offset={}",
                    event.getUserId(), record.partition(), record.offset());

            analyticsService.process(event);
            acknowledgment.acknowledge(); // 처리 성공 후 오프셋 커밋

        } catch (Exception e) {
            log.error("Failed to process event at offset={}", record.offset(), e);
            // 오프셋 커밋하지 않음 → 재시작 시 재처리
            throw e;
        }
    }
}
```

---

## 주의사항 및 트레이드오프

### RabbitMQ를 선택할 때 고려할 사항

**장점:**
- 복잡한 라우팅 로직(Topic, Headers Exchange)이 필요할 때
- 메시지 처리 후 즉시 삭제가 필요한 Task Queue 패턴
- 메시지 우선순위, TTL, Dead Letter Queue가 필요한 경우
- 운영이 단순하고 학습 곡선이 낮음

**주의사항:**
- 대용량 처리 시 메시지 백로그가 쌓이면 성능이 급격히 저하됩니다
- 한 번 소비된 메시지는 재처리가 어렵습니다 (Shovel, Federation 플러그인 필요)
- 클러스터링은 지원하지만 Kafka에 비해 수평 확장이 제한적입니다
- 큐가 많아질수록 메모리 사용량이 증가합니다

### Kafka를 선택할 때 고려할 사항

**장점:**
- 초당 수십만 건의 대용량 이벤트 스트리밍
- 이벤트 소싱, CQRS 패턴 구현
- 여러 컨슈머 그룹이 동일 토픽을 독립적으로 처리
- 메시지 재처리 및 감사 로그 용이 (offset reset)

**주의사항:**
- Zookeeper(또는 KRaft) 운영 복잡성이 존재합니다 (최신 Kafka는 KRaft 모드로 단순화)
- 파티션 수는 토픽 생성 후 늘리기 쉽지만 줄이기는 어렵습니다 → **초기 설계가 중요**
- 메시지 순서는 **파티션 내에서만** 보장됩니다
- 컨슈머 수는 파티션 수를 초과할 수 없습니다 (초과된 컨슈머는 idle 상태)
- 소량의 메시지를 낮은 레이턴시로 처리하는 데는 RabbitMQ가 유리합니다

### 선택 기준 요약표

| 기준 | RabbitMQ | Kafka |
|------|----------|-------|
| 처리량 | 중간 (초당 수만 건) | 높음 (초당 수십만 건) |
| 메시지 보존 | 소비 후 삭제 | 기간 기반 보존 |
| 라우팅 복잡도 | 높음 (Exchange 타입) | 낮음 (토픽 기반) |
| 메시지 재처리 | 어려움 | 쉬움 (offset reset) |
| 순서 보장 | 큐 단위 | 파티션 단위 |
| 운영 복잡도 | 낮음 | 높음 |
| 주요 사용 사례 | Task Queue, RPC | Event Streaming, Log |

---

## 정리

RabbitMQ와 Kafka는 서로 경쟁 관계가 아니라 **다른 문제를 해결하는 도구**입니다.

> **"메시지를 어디로 보낼지 브로커가 알아야 한다면 RabbitMQ, 이벤트 스트림을 여러 컨슈머가 독립적으로 해석해야 한다면 Kafka"**

실무에서는 다음 판단 기준을 우선 적용해보세요:

1. **재처리가 필요한가?** → Kafka
2. **복잡한 라우팅 규칙이 필요한가?** → RabbitMQ
3. **초당 10만 건 이상의 처리량이 필요한가?** → Kafka
4. **Task Queue, 작업 분배가 주 목적인가?** → RabbitMQ
5. **이벤트 소싱 / CQRS 패턴을 사용하는가?** → Kafka
6. **빠른 구축과 낮은 운영 복잡도가 중요한가?** → RabbitMQ

두 기술 모두 Spring 생태계와 훌륭하게 통합되므로, 도메인의 특성과 팀의 운영 역량을 함께 고려하여 선택하는 것이 가장 현명한 접근입니다. 경우에 따라 **하이브리드 구성**—예를 들어 주문 처리는 RabbitMQ, 사용자 행동 로그는 Kafka—도 충분히 실용적인 선택이 될 수 있습니다.