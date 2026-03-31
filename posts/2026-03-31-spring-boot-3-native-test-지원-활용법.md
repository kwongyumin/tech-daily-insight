# Spring Boot 3 Native Test 지원 활용법

## 개요

GraalVM Native Image는 JVM 기반 애플리케이션을 AOT(Ahead-of-Time) 컴파일하여 시작 시간을 획기적으로 줄이고 메모리 사용량을 낮출 수 있는 강력한 기술입니다. 하지만 Native Image 환경에서는 리플렉션, 동적 프록시, 클래스 로딩 등 JVM의 동적 기능들이 제한되기 때문에, 런타임에서 잘 동작하던 코드가 Native 빌드 후 예기치 않게 실패하는 경우가 빈번했습니다.

Spring Boot 3.x는 이 문제를 해결하기 위해 **Native Test** 지원을 공식화했습니다. `@SpringBootTest`와 함께 Native 컨텍스트에서 테스트를 실행하는 파이프라인을 제공하여, 실제 Native 바이너리를 빌드하기 전에 AOT 처리 결과의 정합성을 검증할 수 있습니다. 이 포스팅에서는 Spring Boot 3의 Native Test 지원 구조를 이해하고, 실무에서 바로 활용할 수 있는 설정 및 예제를 살펴보겠습니다.

---

## 핵심 개념

### AOT(Ahead-of-Time) 처리와 테스트의 관계

Spring Boot 3는 빌드 시점에 AOT 엔진이 애플리케이션 컨텍스트를 분석하여 아래 결과물을 생성합니다.

- **BeanFactory 초기화 코드**: 런타임 리플렉션 없이 빈을 등록하는 정적 코드
- **Reflection hints**: GraalVM에게 어떤 클래스/메서드/필드가 리플렉션 대상인지 알려주는 메타데이터
- **Resource hints**: classpath 리소스 접근 정보
- **Proxy hints**: 동적 프록시 생성 정보

Native Test는 이 AOT 결과물을 사용하여 테스트 컨텍스트를 구성합니다. 즉, JVM 위에서 실행되지만 AOT로 생성된 코드 경로를 밟기 때문에 실제 Native 빌드와 유사한 환경을 사전에 검증할 수 있습니다.

### 두 가지 테스트 모드

| 모드 | 설명 | 실행 방법 |
|---|---|---|
| **JVM 모드 (기본)** | 일반 JVM에서 동적 스프링 컨텍스트로 테스트 | `./mvnw test` |
| **Native Test 모드** | AOT 처리된 컨텍스트로 테스트 (JVM에서 실행) | `./mvnw -PnativeTest test` |
| **Native 바이너리 테스트** | 실제 Native Image 빌드 후 테스트 | `./mvnw -Pnative test` |

실무에서는 CI/CD 파이프라인에서 **Native Test 모드**를 먼저 실행하여 빠르게 AOT 호환성을 검증하고, 필요한 경우에만 전체 Native 빌드를 수행하는 전략이 효율적입니다.

---

## 실전 예제

### 프로젝트 설정

`pom.xml`에 Native 관련 플러그인을 추가합니다.

```xml
<parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.2.5</version>
</parent>

<dependencies>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-data-jpa</artifactId>
    </dependency>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-test</artifactId>
        <scope>test</scope>
    </dependency>
</dependencies>

<build>
    <plugins>
        <plugin>
            <groupId>org.graalvm.buildtools</groupId>
            <artifactId>native-maven-plugin</artifactId>
        </plugin>
        <plugin>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-maven-plugin</artifactId>
        </plugin>
    </plugins>
</build>

<profiles>
    <profile>
        <id>nativeTest</id>
        <build>
            <plugins>
                <plugin>
                    <groupId>org.graalvm.buildtools</groupId>
                    <artifactId>native-maven-plugin</artifactId>
                    <executions>
                        <execution>
                            <id>native-test</id>
                            <goals>
                                <goal>test</goal>
                            </goals>
                        </execution>
                    </executions>
                </plugin>
            </plugins>
        </build>
    </profile>
</profiles>
```

### 도메인 및 서비스 예제

```java
// Order.java
@Entity
@Table(name = "orders")
public class Order {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false)
    private String productName;

    @Enumerated(EnumType.STRING)
    private OrderStatus status;

    // 생성자, getter, setter 생략
}

// OrderStatus.java
public enum OrderStatus {
    PENDING, CONFIRMED, SHIPPED, DELIVERED
}

// OrderRepository.java
@Repository
public interface OrderRepository extends JpaRepository<Order, Long> {
    List<Order> findByStatus(OrderStatus status);
}

// OrderService.java
@Service
@Transactional(readOnly = true)
public class OrderService {

    private final OrderRepository orderRepository;

    public OrderService(OrderRepository orderRepository) {
        this.orderRepository = orderRepository;
    }

    @Transactional
    public Order createOrder(String productName) {
        Order order = new Order(productName, OrderStatus.PENDING);
        return orderRepository.save(order);
    }

    public List<Order> getPendingOrders() {
        return orderRepository.findByStatus(OrderStatus.PENDING);
    }
}
```

### Native Test 작성

```java
// OrderServiceNativeTest.java
@SpringBootTest
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.ANY)
class OrderServiceNativeTest {

    @Autowired
    private OrderService orderService;

    @Autowired
    private OrderRepository orderRepository;

    @BeforeEach
    void setUp() {
        orderRepository.deleteAll();
    }

    @Test
    void 주문_생성_및_조회_AOT_컨텍스트_검증() {
        // given
        String productName = "MacBook Pro";

        // when
        Order created = orderService.createOrder(productName);

        // then
        assertThat(created.getId()).isNotNull();
        assertThat(created.getStatus()).isEqualTo(OrderStatus.PENDING);

        List<Order> pendingOrders = orderService.getPendingOrders();
        assertThat(pendingOrders).hasSize(1);
        assertThat(pendingOrders.get(0).getProductName()).isEqualTo(productName);
    }
}
```

### RuntimeHints를 활용한 커스텀 힌트 등록

리플렉션을 직접 사용하는 컴포넌트가 있다면 `RuntimeHintsRegistrar`를 구현하여 힌트를 등록해야 합니다.

```java
// CustomJsonSerializer.java — 리플렉션을 사용하는 가상의 컴포넌트
@Component
public class CustomJsonSerializer {

    public String serialize(Object obj) throws Exception {
        StringBuilder sb = new StringBuilder("{");
        for (Field field : obj.getClass().getDeclaredFields()) {
            field.setAccessible(true);
            sb.append("\"").append(field.getName()).append("\":")
              .append("\"").append(field.get(obj)).append("\",");
        }
        // ... 생략
        return sb.toString();
    }
}

// OrderRuntimeHints.java
public class OrderRuntimeHints implements RuntimeHintsRegistrar {

    @Override
    public void registerHints(RuntimeHints hints, ClassLoader classLoader) {
        // Order 클래스의 모든 필드에 대한 리플렉션 허용
        hints.reflection()
            .registerType(Order.class,
                MemberCategory.DECLARED_FIELDS,
                MemberCategory.INVOKE_DECLARED_CONSTRUCTORS,
                MemberCategory.INVOKE_DECLARED_METHODS);

        // Enum 타입 리플렉션 허용
        hints.reflection()
            .registerType(OrderStatus.class,
                MemberCategory.DECLARED_FIELDS,
                MemberCategory.INVOKE_DECLARED_METHODS);

        // 특정 classpath 리소스 등록
        hints.resources().registerPattern("order-templates/*.json");
    }
}

// 힌트 등록을 애플리케이션에 연결
@SpringBootApplication
@ImportRuntimeHints(OrderRuntimeHints.class)
public class OrderApplication {
    public static void main(String[] args) {
        SpringApplication.run(OrderApplication.class, args);
    }
}
```

### RuntimeHints 테스트 — `RuntimeHintsPredicates` 활용

커스텀 힌트가 올바르게 등록되었는지 단위 테스트로 검증할 수 있습니다.

```java
// OrderRuntimeHintsTest.java
@ExtendWith(MockitoExtension.class)
class OrderRuntimeHintsTest {

    private final RuntimeHints hints = new RuntimeHints();
    private final OrderRuntimeHints registrar = new OrderRuntimeHints();

    @BeforeEach
    void setUp() {
        registrar.registerHints(hints, getClass().getClassLoader());
    }

    @Test
    void Order_클래스_리플렉션_힌트_등록_검증() {
        assertThat(RuntimeHintsPredicates.reflection()
            .onType(Order.class)
            .withMemberCategories(
                MemberCategory.DECLARED_FIELDS,
                MemberCategory.INVOKE_DECLARED_METHODS))
            .accepts(hints);
    }

    @Test
    void OrderStatus_Enum_리플렉션_힌트_등록_검증() {
        assertThat(RuntimeHintsPredicates.reflection()
            .onType(OrderStatus.class))
            .accepts(hints);
    }

    @Test
    void 리소스_패턴_힌트_등록_검증() {
        assertThat(RuntimeHintsPredicates.resource()
            .forResource("order-templates/default.json"))
            .accepts(hints);
    }
}
```

### @TestConfiguration과 Native Test 조합

Native Test 환경에서도 `@TestConfiguration`을 활용한 Bean 교체가 가능합니다.

```java
@SpringBootTest
class OrderServiceWithMockRepoTest {

    @TestConfiguration(proxyBeanMethods = false) // Native 환경에서는 proxyBeanMethods = false 권장
    static class TestConfig {
        @Bean
        @Primary
        OrderRepository mockOrderRepository() {
            return Mockito.mock(OrderRepository.class);
        }
    }

    @Autowired
    private OrderService orderService;

    @Autowired
    private OrderRepository orderRepository;

    @Test
    void 서비스_레이어_격리_테스트() {
        Order mockOrder = new Order("TestProduct", OrderStatus.PENDING);
        given(orderRepository.save(any(Order.class))).willReturn(mockOrder);

        Order result = orderService.createOrder("TestProduct");

        assertThat(result.getProductName()).isEqualTo("TestProduct");
        verify(orderRepository, times(1)).save(any(Order.class));
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. `proxyBeanMethods = false` 적용 필요성

Native Image는 CGLIB 기반의 빈 프록시 생성을 지원하지 않습니다. `@Configuration` 클래스의 기본값인 `proxyBeanMethods = true`는 CGLIB 프록시를 사용하므로, Native 환경에서는 반드시 `false`로 설정해야 합니다.

```java
// Native 환경 권장
@Configuration(proxyBeanMethods = false)
public class AppConfig { ... }
```

### 2. 동적 기능 사용 시 명시적 힌트 필수

아래와 같은 패턴들은 Native 환경에서 별도 힌트 없이는 실패합니다.

- `Class.forName()`, `Method.invoke()` 등 직접 리플렉션
- `ObjectMapper`의 런타임 타입 바인딩 (특히 Generic 타입)
- Lombok `@Builder`, `@Data` 사용 시 생성자 힌트 누락 가능성
- Spring의 `@EventListener`와 `@Async` 조합

### 3. 빌드 시간과 CI 전략

Native 바이너리 빌드는 통상 5~15분이 소요됩니다. 모든 커밋에 Full Native 빌드를 실행하는 것은 비효율적이므로, 아래와 같은 단계별 전략을 권장합니다.

```
PR 단계     : ./mvnw test (JVM 테스트)
Merge 단계  : ./mvnw -PnativeTest test (AOT 컨텍스트 테스트)
배포 단계   : ./mvnw -Pnative spring-boot:build-image (전체 Native 빌드)
```

### 4. 테스트 슬라이싱 제한

`@WebMvcTest`, `@DataJpaTest` 같은 슬라이스 테스트는 Native Test 모드에서 완전히 지원되지 않는 경우가 있습니다. 현재 Spring Boot 3.2 기준으로 `@SpringBootTest`를 사용한 통합 테스트 형태가 가장 안정적입니다.

### 5. GraalVM 버전 호환성

Spring Boot 3.2.x는 GraalVM 21 이상을 요구합니다. GraalVM CE와 Oracle GraalVM 간의 최적화 차이가 존재하며, 프로덕션 환경 목표에 맞는 버전을 선택해야 합니다.

---

## 정리

Spring Boot 3의 Native Test 지원은 단순히 테스트 도구 추가가 아니라, **AOT 컴파일 친화적 코드를 작성하도록 유도하는 개발 패러다임의 변화**입니다. 핵심 포인트를 정리하면 다음과 같습니다.

- **Native Test 모드**는 JVM에서 실행되지만 AOT 생성 코드를 검증하므로, 전체 Native 빌드 없이도 호환성 문제를 조기에 발견할 수 있다.
- **`RuntimeHintsRegistrar`와 `RuntimeHintsPredicates`**를 조합하면 커스텀 힌트 등록의 정확성을 단위 테스트 수준에서 검증할 수 있다.
- **`proxyBeanMethods = false`**, 리플렉션 최소화, 명시적 힌트 등록은 Native 호환 코드의 3대 원칙이다.
- CI 파이프라인을 **JVM 테스트 → Native Test → Full Native 빌드** 3단계로 나누면 비용 효율적인 품질 관리가 가능하다.

Native Image 생태계는 빠르게 성숙하고 있으며, Spring Boot 3.x의 AOT 엔진은 개발자가 직접 힌트를 작성해야 하는 부담을 점점 줄여가고 있습니다. 지금 Native Test를 도입하여 미래 클라우드 네이티브 환경에 대비하는 코드베이스를 구축해보시기 바랍니다.