# Change Data Capture와 Debezium 실전 적용

## 개요

마이크로서비스 아키텍처가 보편화되면서 서비스 간 데이터 동기화 문제는 점점 복잡해졌다. 주문 서비스의 DB 변경이 재고 서비스에 반영되어야 하고, 그 변경이 다시 검색 인덱스, 분석 파이프라인, 알림 시스템으로 전파되어야 하는 시나리오는 이제 흔한 요구사항이 되었다.

전통적인 접근 방식인 **이중 쓰기(Dual Write)** 나 **폴링 기반 동기화** 는 각각 원자성 보장 실패와 성능 저하라는 문제를 안고 있다. 이 문제를 근본적으로 해결하는 패턴이 바로 **CDC(Change Data Capture)** 이며, 그 대표 구현체가 **Debezium** 이다.

이 글에서는 CDC의 핵심 개념을 짚고, Debezium을 MySQL과 Kafka 환경에 실전 적용하는 방법을 코드 중심으로 살펴본다.

---

## 핵심 개념

### CDC란 무엇인가

CDC는 데이터베이스의 변경 사항(INSERT, UPDATE, DELETE)을 캡처하여 다른 시스템으로 스트리밍하는 기법이다. 애플리케이션 코드 레벨이 아닌 **DB 레벨**에서 변경을 감지하기 때문에 애플리케이션이 모르는 변경(배치 작업, DDL 변경, 직접 쿼리 등)도 포착할 수 있다.

CDC 구현 방식은 크게 세 가지다:

| 방식 | 설명 | 한계 |
|------|------|------|
| **로그 기반 (Log-based)** | DB의 트랜잭션 로그(binlog, WAL 등)를 읽음 | DB마다 설정 필요 |
| **트리거 기반** | DB 트리거로 변경 내역을 별도 테이블에 기록 | 성능 오버헤드 |
| **타임스탬프 기반** | `updated_at` 컬럼으로 폴링 | DELETE 감지 불가 |

Debezium은 **로그 기반 CDC** 를 사용한다. MySQL의 binlog, PostgreSQL의 WAL, MongoDB의 Oplog 등 각 DB의 네이티브 복제 로그를 직접 읽어 변경 이벤트를 생성한다. 이 방식은 애플리케이션에 부하를 주지 않으면서도 정확한 변경 순서와 DELETE 이벤트를 보장한다.

### Debezium 아키텍처

```
MySQL (binlog) → Debezium Connector (Kafka Connect) → Kafka Topic → Consumer
```

Debezium은 **Kafka Connect** 위에서 Source Connector로 동작한다. 각 커넥터는 DB의 로그를 읽어 Kafka 토픽에 이벤트를 발행하고, 다운스트림 서비스들은 해당 토픽을 구독하여 변경 사항을 처리한다.

핵심 설계 원칙:
- **At-Least-Once 보장**: 커넥터 재시작 시 마지막 오프셋부터 재처리
- **Snapshot 모드**: 커넥터 최초 실행 시 현재 테이블 전체를 스냅샷으로 캡처
- **Schema Evolution 지원**: Schema Registry와 연동하여 스키마 변경 추적

---

## 실전 예제

### 환경 구성

`docker-compose.yml`로 로컬 환경을 빠르게 세팅한다.

```yaml
version: '3.8'
services:
  mysql:
    image: mysql:8.0
    environment:
      MYSQL_ROOT_PASSWORD: rootpassword
      MYSQL_DATABASE: shop
      MYSQL_USER: debezium
      MYSQL_PASSWORD: dbzpassword
    command:
      - --server-id=1
      - --log-bin=mysql-bin
      - --binlog-format=ROW
      - --binlog-row-image=FULL
    ports:
      - "3306:3306"

  zookeeper:
    image: confluentinc/cp-zookeeper:7.4.0
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181

  kafka:
    image: confluentinc/cp-kafka:7.4.0
    depends_on: [zookeeper]
    environment:
      KAFKA_BROKER_ID: 1
      KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:9092
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
    ports:
      - "9092:9092"

  kafka-connect:
    image: debezium/connect:2.4
    depends_on: [kafka, mysql]
    environment:
      BOOTSTRAP_SERVERS: kafka:9092
      GROUP_ID: debezium-connect-group
      CONFIG_STORAGE_TOPIC: debezium_configs
      OFFSET_STORAGE_TOPIC: debezium_offsets
      STATUS_STORAGE_TOPIC: debezium_status
    ports:
      - "8083:8083"
```

### MySQL 사전 설정

Debezium 전용 DB 유저에게 binlog 읽기 권한을 부여한다.

```sql
-- Debezium 전용 계정 권한 설정
GRANT SELECT, RELOAD, SHOW DATABASES, REPLICATION SLAVE, REPLICATION CLIENT
  ON *.* TO 'debezium'@'%';

-- 모니터링할 테이블 생성
CREATE TABLE orders (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  product_id BIGINT NOT NULL,
  quantity INT NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
```

### Debezium Connector 등록

Kafka Connect REST API를 통해 커넥터를 등록한다.

```bash
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "mysql-orders-connector",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "tasks.max": "1",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbzpassword",
      "database.server.id": "184054",
      "topic.prefix": "shop",
      "database.include.list": "shop",
      "table.include.list": "shop.orders",
      "snapshot.mode": "initial",
      "include.schema.changes": "true",
      "decimal.handling.mode": "double",
      "time.precision.mode": "connect",
      "transforms": "unwrap",
      "transforms.unwrap.type": "io.debezium.transforms.ExtractNewRecordState",
      "transforms.unwrap.drop.tombstones": "false",
      "transforms.unwrap.delete.handling.mode": "rewrite"
    }
  }'
```

> **`ExtractNewRecordState` SMT**: Debezium의 기본 이벤트 페이로드는 `before`/`after` 필드를 포함한 복잡한 구조다. 이 SMT(Single Message Transform)를 적용하면 `after` 상태만 추출하여 컨슈머 처리를 단순화할 수 있다.

### Spring Boot 컨슈머 구현

Kafka 토픽에서 변경 이벤트를 소비하는 Spring Boot 애플리케이션 예제다.

```java
// OrderChangeEvent.java
@JsonIgnoreProperties(ignoreUnknown = true)
public record OrderChangeEvent(
    Long id,
    Long userId,
    Long productId,
    Integer quantity,
    String status,
    String op  // 'c'=create, 'u'=update, 'd'=delete, 'r'=read(snapshot)
) {}
```

```java
// OrderCdcConsumer.java
@Slf4j
@Component
@RequiredArgsConstructor
public class OrderCdcConsumer {

    private final InventoryService inventoryService;
    private final SearchIndexService searchIndexService;
    private final ObjectMapper objectMapper;

    @KafkaListener(
        topics = "shop.shop.orders",
        groupId = "inventory-sync-group",
        containerFactory = "kafkaListenerContainerFactory"
    )
    public void consume(ConsumerRecord<String, String> record) {
        if (record.value() == null) {
            // Tombstone 이벤트 처리 (DELETE 후 로그 압축용)
            log.info("Tombstone event received for key: {}", record.key());
            return;
        }

        try {
            OrderChangeEvent event = objectMapper.readValue(
                record.value(), OrderChangeEvent.class
            );
            processEvent(event);
        } catch (JsonProcessingException e) {
            log.error("Failed to deserialize CDC event: {}", record.value(), e);
            // Dead Letter Queue로 보내는 로직 추가 권장
        }
    }

    private void processEvent(OrderChangeEvent event) {
        switch (event.op()) {
            case "c" -> handleOrderCreated(event);
            case "u" -> handleOrderUpdated(event);
            case "d" -> handleOrderDeleted(event);
            case "r" -> handleSnapshot(event);
            default -> log.warn("Unknown operation: {}", event.op());
        }
    }

    private void handleOrderCreated(OrderChangeEvent event) {
        log.info("New order created: orderId={}, userId={}", event.id(), event.userId());
        inventoryService.reserveStock(event.productId(), event.quantity());
        searchIndexService.indexOrder(event);
    }

    private void handleOrderUpdated(OrderChangeEvent event) {
        if ("CANCELLED".equals(event.status())) {
            inventoryService.releaseStock(event.productId(), event.quantity());
        }
        searchIndexService.updateOrder(event);
    }

    private void handleOrderDeleted(OrderChangeEvent event) {
        searchIndexService.deleteOrder(event.id());
    }

    private void handleSnapshot(OrderChangeEvent event) {
        log.debug("Snapshot event: orderId={}", event.id());
        searchIndexService.indexOrder(event); // 초기 동기화
    }
}
```

```yaml
# application.yml
spring:
  kafka:
    bootstrap-servers: localhost:9092
    consumer:
      group-id: inventory-sync-group
      auto-offset-reset: earliest
      key-deserializer: org.apache.kafka.common.serialization.StringDeserializer
      value-deserializer: org.apache.kafka.common.serialization.StringDeserializer
      enable-auto-commit: false
    listener:
      ack-mode: MANUAL_IMMEDIATE  # 처리 완료 후 명시적 커밋
```

---

## 주의사항 및 트레이드오프

### 1. 멱등성(Idempotency) 설계는 필수

CDC는 At-Least-Once 보장이기 때문에 같은 이벤트가 중복 전달될 수 있다. 컨슈머는 반드시 멱등하게 설계해야 한다. 이벤트 ID나 `updated_at` 타임스탬프를 기준으로 중복 처리를 방어하는 로직을 추가하라.

```java
// 처리 여부를 Redis나 DB에 기록하여 중복 방지
if (processedEventStore.isAlreadyProcessed(event.id(), event.updatedAt())) {
    log.debug("Skipping duplicate event: {}", event.id());
    return;
}
```

### 2. 스키마 변경 관리

테이블에 컬럼이 추가되거나 삭제되면 Debezium 이벤트 구조도 바뀐다. Schema Registry(Confluent Schema Registry 또는 AWS Glue Schema Registry)를 도입하여 스키마 호환성을 관리하는 것이 운영 환경에서는 필수적이다. `@JsonIgnoreProperties(ignoreUnknown = true)` 같은 방어 코드만으로는 한계가 있다.

### 3. Binlog 보존 기간 설정

MySQL binlog 보존 기간(`binlog_expire_logs_seconds`)이 짧으면 커넥터가 오프라인 상태에서 복구 시 필요한 binlog가 삭제되어 전체 재스냅샷이 필요해진다. 운영 환경에서는 최소 7일 이상의 보존 기간을 권장한다.

### 4. 초기 스냅샷 성능 고려

`snapshot.mode: initial` 설정 시 커넥터는 대상 테이블 전체를 READ 락을 걸고 스냅샷을 찍는다. 대용량 테이블에서는 서비스에 영향을 줄 수 있으므로 `snapshot.locking.mode: minimal` 또는 `snapshot.mode: schema_only`(이후 binlog만 추적)를 상황에 맞게 선택해야 한다.

### 5. 이벤트 순서 보장 범위

Debezium은 **같은 파티션 내에서만** 이벤트 순서를 보장한다. 기본적으로 테이블의 PK가 Kafka 메시지 키로 사용되므로, 같은 row의 변경은 같은 파티션으로 가서 순서가 보장된다. 하지만 여러 테이블 간 조인 로직이 필요한 경우 별도의 순서 보장 전략이 필요하다.

### 트레이드오프 요약

| 항목 | 장점 | 단점 |
|------|------|------|
| **신뢰성** | DB 레벨 변경 감지, 누락 없음 | Kafka 인프라 의존성 증가 |
| **성능** | 애플리케이션 부하 없음 | 초기 스냅샷 시 DB 부하 |
| **결합도** | 발행자/구독자 완전 분리 | 이벤트 스키마 관리 복잡도 |
| **운영** | 다양한 DB 지원 | 커넥터 모니터링 필요 |

---

## 정리

CDC와 Debezium은 마이크로서비스 환경의 데이터 동기화 문제를 **애플리케이션 코드 변경 없이** 해결할 수 있는 강력한 도구다. 특히 레거시 시스템을 점진적으로 마이크로서비스로 분리할 때, 혹은 CQRS 패턴에서 쿼리 모델을 비동기로 갱신할 때 그 진가가 드러난다.

실전 도입 시 체크리스트를 정리하면 다음과 같다:

- [ ] MySQL binlog 형식을 `ROW`로 설정하고 보존 기간 확인
- [ ] Debezium 전용 DB 계정과 최소 권한 부여
- [ ] 컨슈머 멱등성 로직 구현
- [ ] Schema Registry 연동으로 스키마 변경 대응
- [ ] Dead Letter Queue(DLQ) 설정으로 처리 실패 이벤트 보관
- [ ] 커넥터 상태 모니터링 (Prometheus + Grafana 연동 권장)

CDC는 단순한 데이터 동기화 도구를 넘어, 이벤트 소싱과 CQRS 아키텍처의 핵심 인프라가 될 수 있다. 처음 도입할 때는 단일 테이블, 단일 컨슈머로 시작하여 운영 패턴을 익힌 뒤 점진적으로 확장하는 전략을 권장한다.
