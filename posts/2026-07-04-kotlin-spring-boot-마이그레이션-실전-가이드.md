# Kotlin + Spring Boot 마이그레이션 실전 가이드

## 개요

Java 기반의 Spring Boot 프로젝트를 Kotlin으로 마이그레이션하는 것은 이제 단순한 트렌드를 넘어 실무적인 선택이 되고 있습니다. JetBrains의 공식 지원과 Spring 팀의 Kotlin 우선 지원(Kotlin-first support), 그리고 Kotlin 특유의 간결한 문법과 Null 안전성은 대규모 서비스에서도 충분한 설득력을 가집니다.

이 포스팅은 기존 Java/Spring Boot 프로젝트를 Kotlin으로 점진적으로 마이그레이션하는 실전 전략을 다룹니다. 단순한 문법 변환이 아닌, 실무에서 맞닥뜨리는 빌드 설정, JPA 엔티티 처리, 테스트 코드 작성, 그리고 Java와의 혼용 전략까지 구체적으로 살펴보겠습니다.

---

## 핵심 개념

### 왜 Kotlin인가?

Kotlin은 JVM 위에서 동작하며 Java와 100% 상호운용이 가능합니다. Spring Boot 2.x부터 공식적으로 Kotlin을 지원하기 시작했고, Spring Boot 3.x에서는 Kotlin DSL과 확장 함수들이 더욱 풍부해졌습니다.

핵심 이점을 정리하면 다음과 같습니다.

- **Null 안전성**: 컴파일 타임에 NPE를 방지
- **데이터 클래스**: `equals`, `hashCode`, `toString` 자동 생성
- **확장 함수**: 기존 클래스를 수정하지 않고 기능 추가
- **코루틴**: 비동기 처리의 간결한 표현
- **간결한 문법**: 보일러플레이트 코드 대폭 감소

### 마이그레이션 전략: Big Bang vs 점진적 전환

실무에서는 **점진적 전환(Incremental Migration)** 을 강력히 권장합니다. Kotlin과 Java는 동일한 JVM에서 공존할 수 있기 때문에, 모듈 단위 혹은 레이어 단위로 순차적으로 전환하는 방식이 리스크를 최소화합니다.

---

## 실전 예제

### 1. 빌드 설정 (Gradle Kotlin DSL)

기존 `build.gradle`을 `build.gradle.kts`로 전환하면서 Kotlin 컴파일러 플러그인을 추가합니다.

```kotlin
// build.gradle.kts
import org.jetbrains.kotlin.gradle.tasks.KotlinCompile

plugins {
    id("org.springframework.boot") version "3.2.0"
    id("io.spring.dependency-management") version "1.1.4"
    kotlin("jvm") version "1.9.21"
    kotlin("plugin.spring") version "1.9.21"   // @Component, @Transactional 등 open 처리
    kotlin("plugin.jpa") version "1.9.21"      // JPA 엔티티 기본 생성자 자동 생성
    kotlin("plugin.allopen") version "1.9.21"
}

dependencies {
    implementation("org.springframework.boot:spring-boot-starter-web")
    implementation("org.springframework.boot:spring-boot-starter-data-jpa")
    implementation("com.fasterxml.jackson.module:jackson-module-kotlin")
    implementation("org.jetbrains.kotlin:kotlin-reflect")
    runtimeOnly("com.h2database:h2")
    testImplementation("org.springframework.boot:spring-boot-starter-test")
}

tasks.withType<KotlinCompile> {
    kotlinOptions {
        freeCompilerArgs += "-Xjsr305=strict"   // Spring의 @NonNull 등을 엄격하게 처리
        jvmTarget = "17"
    }
}

allOpen {
    annotation("jakarta.persistence.Entity")
    annotation("jakarta.persistence.MappedSuperclass")
    annotation("jakarta.persistence.Embeddable")
}
```

> **주의**: Kotlin 클래스는 기본적으로 `final`입니다. Spring AOP(프록시 기반)가 동작하려면 `kotlin-allopen` 플러그인 또는 `kotlin-spring` 플러그인이 반드시 필요합니다.

---

### 2. 엔티티 및 리포지토리 변환

Java의 장황한 JPA 엔티티를 Kotlin으로 변환하면 코드량이 크게 줄어듭니다.

**Java 기존 코드:**
```java
@Entity
@Table(name = "orders")
public class Order {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false)
    private String customerName;

    @Enumerated(EnumType.STRING)
    private OrderStatus status;

    @CreatedDate
    private LocalDateTime createdAt;

    // Getter, Setter, 기본 생성자, 빌더 패턴...
}
```

**Kotlin 전환 코드:**
```kotlin
@Entity
@Table(name = "orders")
class Order(
    @Column(nullable = false)
    val customerName: String,

    @Enumerated(EnumType.STRING)
    var status: OrderStatus = OrderStatus.PENDING,

    @CreatedDate
    val createdAt: LocalDateTime = LocalDateTime.now(),

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    val id: Long = 0L
)

enum class OrderStatus { PENDING, CONFIRMED, SHIPPED, DELIVERED, CANCELLED }
```

리포지토리는 인터페이스 그대로 사용하되, 반환 타입에 Kotlin의 Nullable 타입을 명시합니다.

```kotlin
@Repository
interface OrderRepository : JpaRepository<Order, Long> {
    fun findByCustomerName(customerName: String): List<Order>
    fun findByIdAndStatus(id: Long, status: OrderStatus): Order?  // Nullable 반환
    
    @Query("SELECT o FROM Order o WHERE o.status = :status AND o.createdAt >= :from")
    fun findRecentByStatus(
        @Param("status") status: OrderStatus,
        @Param("from") from: LocalDateTime
    ): List<Order>
}
```

---

### 3. 서비스 레이어 변환

```kotlin
@Service
@Transactional(readOnly = true)
class OrderService(
    private val orderRepository: OrderRepository,
    private val orderEventPublisher: OrderEventPublisher
) {

    fun getOrder(id: Long): OrderResponse {
        val order = orderRepository.findById(id)
            .orElseThrow { OrderNotFoundException("Order not found: $id") }
        return OrderResponse.from(order)
    }

    fun getOrdersByCustomer(customerName: String): List<OrderResponse> =
        orderRepository.findByCustomerName(customerName)
            .map { OrderResponse.from(it) }

    @Transactional
    fun confirmOrder(id: Long): OrderResponse {
        val order = orderRepository.findByIdAndStatus(id, OrderStatus.PENDING)
            ?: throw IllegalStateException("주문 상태가 PENDING이 아닙니다: $id")

        // 코틀린의 let, apply, run 등 스코프 함수 활용
        return order.apply {
            status = OrderStatus.CONFIRMED
        }.also {
            orderEventPublisher.publish(OrderConfirmedEvent(it.id))
        }.let {
            orderRepository.save(it)
            OrderResponse.from(it)
        }
    }
}
```

---

### 4. DTO와 데이터 클래스

Kotlin의 `data class`는 DTO 작성에 매우 적합합니다.

```kotlin
data class OrderResponse(
    val id: Long,
    val customerName: String,
    val status: String,
    val createdAt: LocalDateTime
) {
    companion object {
        fun from(order: Order) = OrderResponse(
            id = order.id,
            customerName = order.customerName,
            status = order.status.name,
            createdAt = order.createdAt
        )
    }
}

data class CreateOrderRequest(
    @field:NotBlank(message = "고객명은 필수입니다")
    val customerName: String,

    @field:NotNull
    val items: List<OrderItemRequest>
)
```

> `@field:` 접두사는 Kotlin에서 Jackson 및 Bean Validation 어노테이션이 필드에 올바르게 적용되도록 합니다. 이를 누락하면 유효성 검사가 동작하지 않는 이슈가 발생합니다.

---

### 5. 테스트 코드 작성

Kotlin의 테스트 코드는 backtick을 사용한 메서드 이름으로 가독성을 높일 수 있습니다.

```kotlin
@SpringBootTest
@Transactional
class OrderServiceTest(
    @Autowired private val orderService: OrderService,
    @Autowired private val orderRepository: OrderRepository
) {

    @Test
    fun `주문 확정 시 상태가 CONFIRMED로 변경된다`() {
        // given
        val order = orderRepository.save(
            Order(customerName = "홍길동", status = OrderStatus.PENDING)
        )

        // when
        val response = orderService.confirmOrder(order.id)

        // then
        assertThat(response.status).isEqualTo("CONFIRMED")
    }

    @Test
    fun `존재하지 않는 주문 조회 시 예외가 발생한다`() {
        assertThatThrownBy { orderService.getOrder(999L) }
            .isInstanceOf(OrderNotFoundException::class.java)
            .hasMessageContaining("999")
    }
}
```

---

### 6. Java와의 혼용 전략

마이그레이션 도중에는 Java와 Kotlin 파일이 공존합니다. 이때 주의해야 할 상호운용 패턴입니다.

```kotlin
// Kotlin에서 Java 클래스를 호출할 때 Platform Type 처리
@Service
class LegacyIntegrationService(
    private val javaLegacyService: JavaLegacyService  // Java 클래스
) {
    fun processLegacyData(id: Long): String {
        // Java 반환값은 Platform Type (String!)
        // 명시적으로 Nullable처리하거나 !! 연산자를 신중하게 사용
        return javaLegacyService.getData(id) 
            ?: throw IllegalStateException("레거시 서비스에서 null 반환: $id")
    }
}
```

```java
// Java에서 Kotlin 클래스를 사용할 때
// Kotlin의 data class companion object는 Java에서 Companion으로 접근
OrderResponse response = OrderResponse.Companion.from(order);

// @JvmStatic 어노테이션을 사용하면 Java 친화적으로 사용 가능
companion object {
    @JvmStatic
    fun from(order: Order) = OrderResponse(...)
}
```

---

## 주의사항 및 트레이드오프

### 1. JPA와 Kotlin의 긴장 관계

JPA는 리플렉션을 통해 기본 생성자를 필요로 합니다. `kotlin-jpa` 플러그인이 이를 해결하지만, `data class`를 엔티티로 사용하는 것은 권장하지 않습니다. `equals`/`hashCode`가 id 기반으로 동작해야 하는 JPA 특성과 `data class`의 자동 구현이 충돌할 수 있습니다.

### 2. 코루틴 도입 시점

WebFlux 없이 MVC 환경에서 코루틴을 도입할 경우, `@Async`와의 혼용이나 트랜잭션 전파 문제가 발생할 수 있습니다. 코루틴은 WebFlux와 함께 사용하거나 독립적인 비동기 작업에만 제한적으로 적용하는 것이 안전합니다.

### 3. Jackson 설정

`jackson-module-kotlin`을 반드시 등록해야 Kotlin의 data class 역직렬화가 정상 동작합니다.

```kotlin
@Configuration
class JacksonConfig {
    @Bean
    fun objectMapper(): ObjectMapper = jacksonObjectMapper().apply {
        configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false)
        registerModule(JavaTimeModule())
    }
}
```

### 4. 빌드 시간 증가

Kotlin 컴파일러는 Java보다 빌드 시간이 길 수 있습니다. Gradle의 `--build-cache`와 `--parallel` 옵션을 적극 활용하고, 모듈 분리를 통해 증분 빌드 효율을 높이세요.

### 5. 팀의 학습 곡선

스코프 함수(`let`, `run`, `apply`, `also`, `with`)의 과도한 사용은 오히려 가독성을 해칩니다. 팀 내 코딩 컨벤션을 명확히 정의하고, 코드 리뷰에서 Kotlin 숙련도 격차를 고려하세요.

---

## 정리

Kotlin + Spring Boot 마이그레이션은 올바른 전략과 도구 설정만 갖추면 충분히 실현 가능한 작업입니다. 핵심 체크리스트를 정리합니다.

| 항목 | 설명 |
|------|------|
| 빌드 설정 | `kotlin-spring`, `kotlin-jpa`, `kotlin-allopen` 플러그인 필수 |
| 엔티티 | `data class` 지양, `allOpen` + `noArg` 플러그인으로 처리 |
| DTO | `data class` + `@field:` 어노테이션 접두사 적용 |
| Jackson | `jackson-module-kotlin` 등록 필수 |
| 테스트 | backtick 메서드명으로 가독성 향상 |
| 혼용 전략 | Platform Type 처리 명시, `@JvmStatic` 활용 |

점진적 마이그레이션 순서는 **DTO → 서비스 레이어 → 컨트롤러 → 엔티티** 순서를 추천합니다. 사이드 이펙트가 적은 레이어부터 시작하여 팀의 Kotlin 적응도를 높이면서 자연스럽게 전환하는 것이 실무에서 가장 안전한 접근법입니다.

Kotlin은 단순히 "Java를 더 짧게 쓰는 언어"가 아닙니다. Null 안전성, 불변성, 함수형 프로그래밍 패러다임을 제대로 활용할 때 비로소 마이그레이션의 진가가 드러납니다.