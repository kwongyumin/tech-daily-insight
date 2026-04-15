# DynamoDB 설계 원칙과 Single-Table Design

## 개요

RDS 같은 관계형 데이터베이스에 익숙한 개발자가 DynamoDB를 처음 접하면 흔히 하는 실수가 있다. 엔티티마다 테이블을 하나씩 만드는 것이다. 이 접근법은 DynamoDB의 설계 철학과 정면으로 충돌하며, 성능 저하와 비용 낭비로 이어진다.

DynamoDB는 **NoSQL Key-Value/Document 데이터베이스**로, 수평 확장과 일관된 밀리초 단위 응답을 목표로 설계되었다. 이 목표를 달성하기 위해 DynamoDB는 **Single-Table Design(단일 테이블 설계)**이라는 패턴을 권장한다. AWS의 수석 DynamoDB 엔지니어 Rick Houlihan이 주창한 이 패턴은 처음에는 낯설지만, 제대로 이해하면 강력한 무기가 된다.

이 글에서는 DynamoDB의 핵심 설계 원칙부터 Single-Table Design의 실전 구현까지 깊이 있게 다룬다.

---

## 핵심 개념

### 1. DynamoDB의 기본 구조

DynamoDB의 모든 것은 **Primary Key**에서 시작한다.

- **Partition Key (PK)**: 데이터를 어느 파티션에 저장할지 결정하는 키. 해시 함수를 통해 물리적 파티션이 결정된다.
- **Sort Key (SK)**: 같은 파티션 내에서 데이터를 정렬하는 키. 선택 사항이지만 Single-Table Design에서는 필수다.

```
PK (Partition Key) + SK (Sort Key) = Composite Primary Key
```

PK만으로는 정확한 단일 아이템을 조회(Get)하거나 전체 파티션을 스캔해야 한다. SK가 있으면 **begins_with, between, >, <** 등의 범위 조회가 가능해지고, 이것이 Single-Table Design의 핵심 무기가 된다.

### 2. GSI (Global Secondary Index)

PK/SK 조합 외의 다른 접근 패턴을 지원하기 위한 인덱스다. GSI는 별도의 파티션에 데이터를 복제하므로 읽기/쓰기 비용이 추가된다. 설계 단계에서 **접근 패턴(Access Pattern)**을 먼저 정의하고, 필요한 GSI만 최소화하는 것이 원칙이다.

### 3. Single-Table Design의 핵심 원칙

**RDBMS 설계와의 가장 큰 차이점**:

| 구분 | RDBMS | DynamoDB Single-Table |
|------|-------|----------------------|
| 설계 시작점 | 엔티티 정규화 | 접근 패턴 정의 |
| 조인 | SQL JOIN | 미리 비정규화하여 저장 |
| 테이블 수 | 엔티티마다 1개 | 애플리케이션당 1개 (원칙) |
| 유연성 | 스키마 변경 쉬움 | 접근 패턴 변경 어려움 |

Single-Table Design에서는 **다른 종류의 엔티티가 같은 테이블에 공존**한다. PK와 SK의 값에 엔티티 타입을 인코딩하는 방식으로 이를 구분한다.

---

## 실전 예제

전자상거래 애플리케이션을 예로 들자. 다음 엔티티가 존재한다:
- **Customer** (고객)
- **Order** (주문)
- **OrderItem** (주문 상품)

### 접근 패턴 정의 (Access Patterns)

설계 전, 반드시 접근 패턴을 먼저 나열해야 한다.

1. 고객 ID로 고객 정보 조회
2. 고객의 모든 주문 목록 조회
3. 특정 주문의 모든 주문 상품 조회
4. 주문 ID로 주문 정보 조회
5. 특정 날짜 범위의 주문 조회

### 테이블 설계

```
Table Name: ecommerce

PK           | SK                    | EntityType | Attributes...
-------------|----------------------|------------|---------------
CUST#C001    | METADATA              | CUSTOMER   | name, email, ...
CUST#C001    | ORDER#O001            | ORDER      | status, total, createdAt, ...
CUST#C001    | ORDER#O002            | ORDER      | status, total, createdAt, ...
ORDER#O001   | ITEM#PROD#P001        | ORDER_ITEM | quantity, price, ...
ORDER#O001   | ITEM#PROD#P002        | ORDER_ITEM | quantity, price, ...
```

PK와 SK에 **접두사(prefix)**를 붙이는 것이 핵심이다. `CUST#`, `ORDER#`, `ITEM#`과 같은 접두사를 통해 엔티티 타입을 식별하고 SK 범위 조회를 활용한다.

### Java 코드 예제 (AWS SDK v2)

먼저 의존성을 추가한다.

```xml
<dependency>
    <groupId>software.amazon.awssdk</groupId>
    <artifactId>dynamodb-enhanced</artifactId>
    <version>2.25.0</version>
</dependency>
```

**엔티티 클래스 정의**:

```java
@DynamoDbBean
public class CustomerRecord {
    private String pk;
    private String sk;
    private String entityType;
    private String customerId;
    private String name;
    private String email;

    @DynamoDbPartitionKey
    @DynamoDbAttribute("PK")
    public String getPk() { return pk; }

    @DynamoDbSortKey
    @DynamoDbAttribute("SK")
    public String getSk() { return sk; }

    // ... getters/setters
}
```

**Repository 계층 구현**:

```java
@Repository
public class CustomerRepository {

    private final DynamoDbTable<CustomerRecord> table;

    public CustomerRepository(DynamoDbEnhancedClient enhancedClient) {
        this.table = enhancedClient.table("ecommerce", TableSchema.fromBean(CustomerRecord.class));
    }

    public void save(Customer customer) {
        CustomerRecord record = new CustomerRecord();
        record.setPk("CUST#" + customer.getId());
        record.setSk("METADATA");
        record.setEntityType("CUSTOMER");
        record.setCustomerId(customer.getId());
        record.setName(customer.getName());
        record.setEmail(customer.getEmail());

        table.putItem(record);
    }

    public Optional<Customer> findById(String customerId) {
        Key key = Key.builder()
                .partitionValue("CUST#" + customerId)
                .sortValue("METADATA")
                .build();

        CustomerRecord record = table.getItem(key);
        return Optional.ofNullable(record).map(this::toDomain);
    }

    // 고객의 모든 주문 조회 (SK begins_with "ORDER#")
    public List<Order> findOrdersByCustomerId(String customerId) {
        QueryConditional queryConditional = QueryConditional
                .sortBeginsWith(Key.builder()
                        .partitionValue("CUST#" + customerId)
                        .sortValue("ORDER#")
                        .build());

        return table.query(queryConditional)
                .items()
                .stream()
                .filter(r -> "ORDER".equals(r.getEntityType()))
                .map(this::toOrder)
                .collect(Collectors.toList());
    }

    private Customer toDomain(CustomerRecord record) {
        return new Customer(record.getCustomerId(), record.getName(), record.getEmail());
    }
}
```

**날짜 범위 조건 조회** (GSI 활용):

특정 날짜 범위의 주문을 조회하려면 GSI가 필요하다. `GSI1PK`와 `GSI1SK`를 추가한다.

```java
// GSI 설계
// GSI1PK: ORDER#STATUS#PENDING
// GSI1SK: 2024-01-15T10:30:00Z (ISO 8601 형식)

public List<Order> findOrdersByDateRange(String status, Instant from, Instant to) {
    QueryConditional rangeQuery = QueryConditional
            .sortBetween(
                Key.builder()
                    .partitionValue("ORDER#STATUS#" + status)
                    .sortValue(from.toString())
                    .build(),
                Key.builder()
                    .partitionValue("ORDER#STATUS#" + status)
                    .sortValue(to.toString())
                    .build()
            );

    DynamoDbIndex<OrderRecord> gsi = orderTable.index("GSI1");
    return gsi.query(rangeQuery)
              .stream()
              .flatMap(page -> page.items().stream())
              .map(this::toOrder)
              .collect(Collectors.toList());
}
```

### Overloaded Index 패턴

Single-Table Design에서는 **GSI를 재사용(Overload)**하는 것이 일반적이다. 하나의 GSI로 여러 엔티티 타입의 다양한 접근 패턴을 처리한다.

```
GSI1PK           | GSI1SK              | EntityType
-----------------|---------------------|------------
EMAIL#user@a.com | CUST#C001           | CUSTOMER  (이메일로 고객 조회)
REGION#SEOUL     | ORDER#2024-01-15    | ORDER     (지역별 주문 조회)
CATEGORY#ELEC    | PROD#P001           | PRODUCT   (카테고리별 상품 조회)
```

하나의 GSI가 세 가지 완전히 다른 접근 패턴을 처리한다. 이것이 GSI Overloading 패턴의 핵심이다.

---

## 주의사항 및 트레이드오프

### 1. Hot Partition 문제

PK 설계가 잘못되면 특정 파티션에 트래픽이 집중되어 **Throttling**이 발생한다.

```
❌ 나쁜 예: PK = "USER" (모든 유저가 같은 파티션)
✅ 좋은 예: PK = "USER#" + userId (유저마다 다른 파티션)

❌ 나쁜 예: PK = 날짜 (특정 날짜에 트래픽 집중)
✅ 좋은 예: PK = 날짜 + "#" + (userId % 10) (샤딩)
```

### 2. Scan은 최대한 피하라

`Scan`은 테이블 전체를 읽으므로 비용과 성능 양쪽에서 최악이다. 접근 패턴에 `Scan`이 필요하다면 설계를 다시 검토해야 한다. 어쩔 수 없이 필요하다면 `FilterExpression`과 함께 병렬 스캔을 고려한다.

### 3. 트랜잭션의 한계

DynamoDB의 `TransactWriteItems`는 최대 **100개 아이템**에만 적용된다. 복잡한 비즈니스 트랜잭션이 필요하다면 애플리케이션 레벨의 **Saga 패턴**이나 **Outbox 패턴**을 고려해야 한다.

### 4. Single-Table Design이 항상 정답은 아니다

다음 상황에서는 Multi-Table 접근이 더 나을 수 있다:

- **팀이 DynamoDB에 익숙하지 않은 경우**: 학습 곡선이 매우 가파르다.
- **접근 패턴이 초기에 불명확한 경우**: 나중에 패턴이 바뀌면 재설계 비용이 크다.
- **마이크로서비스 환경**: 서비스별로 테이블을 분리하는 것이 경계를 명확히 한다.
- **보안 요구사항**: IAM 정책으로 테이블 단위 접근 제어는 쉽지만, 아이템 단위 제어는 복잡하다.

### 5. 데이터 모델링 도구 활용

실무에서는 **NoSQL Workbench for DynamoDB**(AWS 공식 제공)를 적극 활용하자. 시각적으로 테이블 구조를 설계하고 접근 패턴을 검증할 수 있어 설계 오류를 사전에 잡을 수 있다.

---

## 정리

DynamoDB Single-Table Design은 **"접근 패턴이 먼저, 엔티티가 나중"**이라는 철학의 산물이다.

핵심을 요약하면:

1. **접근 패턴을 먼저 정의하라**: 어떻게 읽을지 알아야 어떻게 저장할지 결정할 수 있다.
2. **PK/SK에 의미 있는 접두사를 사용하라**: 엔티티 타입 식별과 범위 조회의 핵심이다.
3. **GSI는 최소화하고 Overloading 패턴을 활용하라**: 불필요한 GSI는 비용 낭비다.
4. **Hot Partition을 항상 경계하라**: 잘못된 PK 설계는 전체 시스템 성능을 망친다.
5. **Scan은 설계 실패의 신호다**: Query로 해결하지 못하면 설계를 재검토하라.

처음에는 RDBMS의 직관과 충돌하는 부분이 많아 어색하게 느껴질 수 있다. 그러나 접근 패턴 중심의 사고방식에 익숙해지면, DynamoDB가 제공하는 무한한 수평 확장성과 일관된 성능의 가치를 온전히 누릴 수 있다. 실무에서는 **NoSQL Workbench**로 충분히 검증한 후 구현에 들어가는 것을 강력히 권장한다.