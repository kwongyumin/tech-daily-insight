# MongoDB Spring Data 반정형 데이터 처리 패턴

## 개요

관계형 데이터베이스 중심의 개발 환경에서 MongoDB를 도입하는 팀들이 가장 먼저 맞닥뜨리는 문제는 "스키마를 어떻게 설계할 것인가"가 아니라 **"유연한 구조를 코드에서 어떻게 다룰 것인가"**다. MongoDB의 가장 큰 강점인 스키마 유연성은 동시에 애플리케이션 레이어에서의 복잡성을 높이는 원인이 된다.

Spring Data MongoDB는 이 간극을 메우기 위한 다양한 추상화를 제공하지만, 이를 제대로 활용하려면 단순히 `@Document` 어노테이션을 붙이는 것 이상의 이해가 필요하다. 본 포스팅에서는 실무에서 자주 마주치는 반정형 데이터 시나리오를 중심으로, Spring Data MongoDB에서 사용할 수 있는 패턴들을 깊게 살펴본다.

---

## 핵심 개념

### 반정형 데이터란?

반정형 데이터(Semi-structured data)는 완전한 스키마를 갖추지 않았지만, 태그나 키-값 구조 등 일부 구조를 가진 데이터를 말한다. MongoDB에서는 이것이 다음과 같은 형태로 나타난다.

- 동일 컬렉션 내에 필드 구성이 다른 도큐먼트
- 런타임에 결정되는 동적 속성
- 버전에 따라 구조가 다른 이벤트 페이로드
- 외부 API 응답을 그대로 저장하는 raw 데이터

### Spring Data MongoDB의 주요 처리 도구

| 도구 | 용도 |
|------|------|
| `Document` (BSON) | 원시 BSON 도큐먼트 직접 조작 |
| `Map<String, Object>` | 동적 필드를 자바 맵으로 처리 |
| `@Field` + `Object` 타입 | 특정 필드의 타입을 런타임에 결정 |
| Polymorphism + `@TypeAlias` | 상속 구조로 다형성 처리 |
| `MongoConverter` 커스터마이징 | 변환 로직 직접 제어 |

---

## 실전 예제

### 시나리오 1: 동적 속성을 가진 상품 카탈로그

전자상거래에서 상품 종류별로 속성이 다른 경우가 대표적이다. 의류는 `size`, `color`를 가지고, 전자제품은 `voltage`, `warranty`를 가진다.

```java
@Document(collection = "products")
@Data
public class Product {

    @Id
    private String id;

    private String name;
    private String category;
    private BigDecimal price;

    // 카테고리별 동적 속성을 Map으로 처리
    @Field("attributes")
    private Map<String, Object> attributes = new HashMap<>();

    // 중첩 도큐먼트도 Map으로 처리 가능
    @Field("metadata")
    private Map<String, Object> metadata = new HashMap<>();
}
```

```java
@Repository
public interface ProductRepository extends MongoRepository<Product, String> {

    // 동적 필드에 대한 쿼리 - SpEL과 @Query 활용
    @Query("{ 'category': ?0, 'attributes.?1': { $exists: true } }")
    List<Product> findByCategoryWithAttribute(String category, String attributeKey);

    // 특정 속성 값으로 검색
    @Query("{ 'attributes.color': ?0, 'attributes.size': ?1 }")
    List<Product> findByColorAndSize(String color, String size);
}
```

```java
@Service
@RequiredArgsConstructor
public class ProductService {

    private final ProductRepository productRepository;
    private final MongoTemplate mongoTemplate;

    public Product createClothingProduct(String name, String color, String size) {
        Product product = new Product();
        product.setName(name);
        product.setCategory("CLOTHING");

        Map<String, Object> attributes = new HashMap<>();
        attributes.put("color", color);
        attributes.put("size", size);
        attributes.put("material", "cotton"); // 런타임 결정 속성
        product.setAttributes(attributes);

        return productRepository.save(product);
    }

    // MongoTemplate을 사용한 동적 쿼리
    public List<Product> findProductsByDynamicFilter(Map<String, Object> filters) {
        Query query = new Query();

        filters.forEach((key, value) ->
            query.addCriteria(Criteria.where("attributes." + key).is(value))
        );

        return mongoTemplate.find(query, Product.class);
    }
}
```

### 시나리오 2: 다형성 이벤트 소싱 처리

이벤트 소싱 아키텍처에서 다양한 이벤트 타입을 하나의 컬렉션에 저장하는 패턴이다.

```java
// 기반 이벤트 클래스
@Document(collection = "domain_events")
@JsonTypeInfo(use = JsonTypeInfo.Id.NAME, property = "_eventType")
@JsonSubTypes({
    @JsonSubTypes.Type(value = OrderCreatedEvent.class, name = "ORDER_CREATED"),
    @JsonSubTypes.Type(value = OrderShippedEvent.class, name = "ORDER_SHIPPED"),
    @JsonSubTypes.Type(value = PaymentProcessedEvent.class, name = "PAYMENT_PROCESSED")
})
@Data
public abstract class DomainEvent {

    @Id
    private String id;

    @Field("_eventType")
    private String eventType;

    private String aggregateId;
    private LocalDateTime occurredAt;
    private Integer version;
}
```

```java
@TypeAlias("ORDER_CREATED")
@Data
@EqualsAndHashCode(callSuper = true)
public class OrderCreatedEvent extends DomainEvent {
    private String customerId;
    private List<OrderItem> items;
    private BigDecimal totalAmount;
}

@TypeAlias("ORDER_SHIPPED")
@Data
@EqualsAndHashCode(callSuper = true)
public class OrderShippedEvent extends DomainEvent {
    private String trackingNumber;
    private String carrier;
    private LocalDate estimatedDelivery;
}
```

```java
@Repository
public interface DomainEventRepository extends MongoRepository<DomainEvent, String> {

    List<DomainEvent> findByAggregateIdOrderByVersionAsc(String aggregateId);

    // 특정 타입의 이벤트만 조회
    @Query("{ 'aggregateId': ?0, '_eventType': ?1 }")
    List<DomainEvent> findByAggregateIdAndEventType(String aggregateId, String eventType);
}
```

```java
// Spring Data MongoDB가 _class 필드를 통해 자동으로 타입을 복원함
@Service
@RequiredArgsConstructor
public class EventStoreService {

    private final DomainEventRepository eventRepository;

    public void appendEvent(DomainEvent event) {
        // version 자동 관리
        List<DomainEvent> existing = eventRepository
            .findByAggregateIdOrderByVersionAsc(event.getAggregateId());

        event.setVersion(existing.size() + 1);
        event.setOccurredAt(LocalDateTime.now());
        eventRepository.save(event);
    }

    public <T extends DomainEvent> List<T> getEventsOfType(
            String aggregateId, Class<T> eventType) {

        return eventRepository
            .findByAggregateIdOrderByVersionAsc(aggregateId)
            .stream()
            .filter(eventType::isInstance)
            .map(eventType::cast)
            .collect(Collectors.toList());
    }
}
```

### 시나리오 3: 외부 API 원본 데이터 보존 + 정규화 필드 혼합

외부 API 응답을 그대로 저장하면서 자주 쿼리되는 필드는 정규화하는 패턴이다.

```java
@Document(collection = "external_integrations")
@Data
public class ExternalIntegration {

    @Id
    private String id;

    // 정규화된 필드 - 인덱싱 및 빠른 조회용
    private String externalId;
    private String source; // "GITHUB", "JIRA", "SLACK" 등
    private String status;
    private LocalDateTime lastSyncedAt;

    // 원본 데이터 보존 - org.bson.Document 사용
    @Field("rawPayload")
    private org.bson.Document rawPayload;

    // 추출된 핵심 데이터만 별도 구조화
    @Field("normalized")
    private Map<String, Object> normalized;
}
```

```java
@Service
@RequiredArgsConstructor
public class IntegrationSyncService {

    private final MongoTemplate mongoTemplate;

    public void syncFromExternalApi(String source, Map<String, Object> apiResponse) {
        // 원본 데이터를 BSON Document로 변환
        org.bson.Document rawDoc = new org.bson.Document(apiResponse);

        ExternalIntegration integration = new ExternalIntegration();
        integration.setSource(source);
        integration.setRawPayload(rawDoc);
        integration.setLastSyncedAt(LocalDateTime.now());

        // 소스별 정규화 로직 적용
        Map<String, Object> normalized = normalizeBySource(source, apiResponse);
        integration.setNormalized(normalized);
        integration.setExternalId((String) normalized.get("id"));
        integration.setStatus((String) normalized.getOrDefault("status", "UNKNOWN"));

        // Upsert로 중복 방지
        Query query = Query.query(
            Criteria.where("externalId").is(integration.getExternalId())
                    .and("source").is(source)
        );

        mongoTemplate.upsert(
            query,
            new Update()
                .set("rawPayload", rawDoc)
                .set("normalized", normalized)
                .set("status", integration.getStatus())
                .set("lastSyncedAt", integration.getLastSyncedAt()),
            ExternalIntegration.class
        );
    }

    private Map<String, Object> normalizeBySource(String source, Map<String, Object> raw) {
        return switch (source) {
            case "GITHUB" -> Map.of(
                "id", raw.get("id").toString(),
                "status", raw.getOrDefault("state", "unknown"),
                "title", raw.getOrDefault("title", ""),
                "author", ((Map<?, ?>) raw.getOrDefault("user", Map.of()))
                            .getOrDefault("login", "")
            );
            case "JIRA" -> Map.of(
                "id", raw.get("key"),
                "status", ((Map<?, ?>) ((Map<?, ?>) raw.get("fields"))
                            .get("status")).get("name"),
                "title", ((Map<?, ?>) raw.get("fields")).get("summary")
            );
            default -> raw;
        };
    }
}
```

### 커스텀 컨버터 등록

타입 변환이 복잡해질 때는 `MongoCustomConversions`를 활용한다.

```java
@Configuration
public class MongoConfig {

    @Bean
    public MongoCustomConversions mongoCustomConversions() {
        return new MongoCustomConversions(Arrays.asList(
            new MapToProductAttributesConverter(),
            new ProductAttributesToMapConverter()
        ));
    }
}

@WritingConverter
public class ProductAttributesToMapConverter
        implements Converter<ProductAttributes, Document> {

    @Override
    public Document convert(ProductAttributes source) {
        Document doc = new Document();
        doc.put("type", source.getType());
        // 타입별 직렬화 로직
        source.getAttributes().forEach(doc::put);
        return doc;
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. `_class` 필드 관리

Spring Data MongoDB는 다형성 처리를 위해 도큐먼트에 `_class` 필드를 자동으로 추가한다. 패키지 리팩터링 시 기존 데이터와의 호환성이 깨질 수 있으므로, 반드시 `@TypeAlias`를 명시적으로 선언해야 한다.

```java
// 나쁜 예: 패키지 변경 시 데이터 조회 불가
// _class: "com.example.old.package.OrderCreatedEvent"

// 좋은 예: 별칭으로 패키지 의존성 제거
@TypeAlias("ORDER_CREATED")
public class OrderCreatedEvent extends DomainEvent { ... }
```

### 2. `Map<String, Object>` 타입의 함정

중첩된 BSON 타입이 예상과 다르게 역직렬화될 수 있다. 특히 숫자 타입 처리에 주의해야 한다.

```java
// BSON의 NumberLong은 Long으로, NumberInt는 Integer로 복원됨
// 코드에서 항상 Number 타입으로 캐스팅 후 변환 권장
Object value = attributes.get("price");
BigDecimal price = new BigDecimal(value.toString()); // 안전한 방법
```

### 3. 인덱스 전략

동적 필드에 대한 인덱스는 신중하게 설계해야 한다. 모든 동적 키에 인덱스를 생성하면 메모리 사용량이 폭발적으로 증가한다.

```java
// 조회 빈도가 높은 필드만 선택적으로 인덱싱
@Document(collection = "products")
@CompoundIndex(name = "category_color_idx",
               def = "{'category': 1, 'attributes.color': 1}")
public class Product { ... }
```

### 4. 스키마 유효성 검사

유연성을 허용하되, MongoDB의 JSON Schema Validation을 통해 최소한의 제약을 코드 외부에서도 보장한다.

```java
@PostConstruct
public void ensureCollectionValidation() {
    mongoTemplate.executeCommand("""
        {
          collMod: "products",
          validator: {
            $jsonSchema: {
              required: ["name", "category", "price"],
              properties: {
                price: { bsonType: "decimal" }
              }
            }
          }
        }
    """);
}
```

### 5. 성능 트레이드오프 요약

| 패턴 | 유연성 | 타입 안전성 | 쿼리 성능 | 유지보수성 |
|------|--------|-------------|-----------|-----------|
| `Map<String, Object>` | ★★★ | ★ | ★★ | ★ |
| 다형성 상속 | ★★ | ★★★ | ★★★ | ★★★ |
| BSON Document | ★★★ | ★ | ★★ | ★★ |
| 혼합 구조 (정규화 + raw) | ★★★ | ★★ | ★★★ | ★★ |

---

## 정리

MongoDB Spring Data에서 반정형 데이터를 다루는 데 있어 "하나의 정답"은 없다. 각 시나리오의 특성에 맞는 패턴을 선택하는 것이 핵심이다.

- **카테고리별 속성이 다른 엔티티**: `Map<String, Object>` + 선택적 인덱스
- **다형성 이벤트/메시지**: 상속 계층 + `@TypeAlias` 필수 적용
- **외부 API 원본 보존**: `org.bson.Document` raw 저장 + 정규화 필드 분리
- **복잡한 변환 로직**: `MongoCustomConversions` 커스터마이징

가장 중요한 원칙은 **유연성과 타입 안전성 사이의 경계를 의식적으로 설정하는 것**이다. 비즈니스 로직이 닿는 레이어에서는 강타입을 유지하고, 저장/복원 과정에서만 유연성을 허용하는 설계가 장기적으로 유지보수하기 훨씬 용이하다.

반정형 데이터의 진짜 어려움은 기술적 구현보다 팀 내에서 데이터 구조에 대한 암묵적 지식이 퍼지는 것을 막는 일이다. 코드 레벨의 패턴과 함께 컬렉션 단위의 문서화, 스키마 검증 정책을 병행하는 것을 강력히 권장한다.