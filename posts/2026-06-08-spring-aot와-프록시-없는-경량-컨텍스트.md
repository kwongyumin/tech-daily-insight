# Spring AOT와 프록시 없는 경량 컨텍스트

## 개요

Spring Framework 6과 Spring Boot 3이 등장하면서 **AOT(Ahead-Of-Time) 컴파일**이 일급 기능으로 자리 잡았다. GraalVM Native Image를 통한 네이티브 실행 파일 생성이 가능해졌고, 이에 따라 기존 Spring이 런타임에 수행하던 많은 작업들이 빌드 타임으로 이동하게 되었다.

그 중에서도 가장 눈에 띄는 변화는 **프록시 기반 AOP 메커니즘의 재설계**다. 전통적인 Spring 애플리케이션은 `@Transactional`, `@Cacheable`, `@Async` 같은 어노테이션을 처리하기 위해 런타임에 CGLIB 또는 JDK 동적 프록시를 생성했다. 하지만 AOT 환경에서는 이런 동적 클래스 생성이 불가능하거나 제한적이기 때문에, Spring은 **프록시 없는(proxy-free) 경량 컨텍스트** 전략을 도입했다.

이 글에서는 Spring AOT가 어떻게 동작하는지, 프록시 없는 컨텍스트가 실무에서 어떤 의미를 갖는지, 그리고 이를 적용할 때 주의해야 할 트레이드오프까지 깊이 있게 다뤄보겠다.

---

## 핵심 개념

### AOT란 무엇인가?

AOT(Ahead-Of-Time) 처리는 애플리케이션 컨텍스트를 **빌드 시점에 분석**하고, 런타임에 필요한 메타데이터와 코드를 미리 생성해두는 방식이다. Spring AOT 엔진은 다음과 같은 작업을 빌드 타임에 수행한다.

- **빈 정의 분석**: `@ComponentScan`, `@Configuration` 등을 분석해 빈 등록 정보를 정적으로 추출
- **힌트 생성**: Reflection, 리소스 접근, 직렬화 등에 필요한 GraalVM 힌트 파일 자동 생성
- **소스 코드 생성**: `BeanFactory`를 초기화하는 Java 소스 코드를 직접 생성 (`GeneratedClasses`)

```
빌드 타임                          런타임
┌─────────────────────┐           ┌──────────────────────┐
│  Spring AOT Engine  │  →  생성  │  Generated Sources   │
│  - Bean 분석        │           │  - BeanDefinitions   │
│  - Proxy 후보 탐색  │           │  - AOT Proxies       │
│  - 힌트 파일 생성   │           │  - reflect-config    │
└─────────────────────┘           └──────────────────────┘
```

### 런타임 프록시의 문제점

기존 Spring의 동적 프록시는 강력하지만 몇 가지 근본적인 한계가 있다.

1. **Reflection 의존**: CGLIB는 런타임에 바이트코드를 조작하며, GraalVM 네이티브 환경에서는 동작하지 않는다.
2. **콜드 스타트 지연**: 프록시 클래스 생성과 바이트코드 위빙은 JVM 기동 시점에 오버헤드를 유발한다.
3. **메모리 사용**: 동적으로 생성된 클래스는 메타스페이스를 점유하며, 대규모 시스템에서 문제가 될 수 있다.

### AOT 프록시의 등장

Spring AOT는 이 문제를 **빌드 타임에 프록시 소스 코드를 직접 생성**하는 방식으로 해결한다. `@Transactional`이 붙은 서비스 클래스가 있다면, AOT 엔진이 빌드 시점에 해당 클래스를 상속한 프록시 클래스를 Java 소스로 생성하고, 이를 컴파일에 포함시킨다.

또한 Spring 6.x부터는 **`@Configuration(proxyBeanMethods = false)`**를 적극 권장하며, `@Bean` 메서드 간 직접 호출 대신 파라미터 주입 방식을 사용하도록 유도한다. 이는 CGLIB 프록시 없이도 동작하는 경량 컨텍스트를 구성할 수 있게 해준다.

---

## 실전 예제

### 예제 1: proxyBeanMethods 비활성화

가장 간단하게 적용할 수 있는 AOT 친화적 설정이다.

```java
// 기존 방식 - CGLIB 프록시 생성
@Configuration
public class LegacyConfig {

    @Bean
    public DataSource dataSource() {
        return new EmbeddedDatabaseBuilder()
                .setType(EmbeddedDatabaseType.H2)
                .build();
    }

    @Bean
    public JdbcTemplate jdbcTemplate() {
        // dataSource()를 직접 호출 → CGLIB가 싱글톤 보장
        return new JdbcTemplate(dataSource());
    }
}

// AOT 친화적 방식 - 프록시 없음
@Configuration(proxyBeanMethods = false)
public class AotFriendlyConfig {

    @Bean
    public DataSource dataSource() {
        return new EmbeddedDatabaseBuilder()
                .setType(EmbeddedDatabaseType.H2)
                .build();
    }

    @Bean
    public JdbcTemplate jdbcTemplate(DataSource dataSource) {
        // 파라미터 주입으로 싱글톤 보장 → 프록시 불필요
        return new JdbcTemplate(dataSource);
    }
}
```

### 예제 2: @Transactional과 AOT 처리

`@Transactional`은 AOT 환경에서 어떻게 처리될까? Spring Boot 3 프로젝트를 빌드하면 `build/generated/aotSources` 디렉토리에 생성된 코드를 확인할 수 있다.

```java
// 원본 서비스
@Service
@Transactional
public class OrderService {

    private final OrderRepository orderRepository;

    public OrderService(OrderRepository orderRepository) {
        this.orderRepository = orderRepository;
    }

    public Order createOrder(CreateOrderRequest request) {
        Order order = Order.from(request);
        return orderRepository.save(order);
    }

    @Transactional(readOnly = true)
    public List<Order> findAll() {
        return orderRepository.findAll();
    }
}
```

AOT 빌드 후 생성되는 코드는 대략 다음과 같은 구조를 갖는다.

```java
// AOT가 생성하는 빈 등록 코드 (실제 생성 파일 단순화)
public class OrderService__BeanDefinitions {

    @Bean
    public static BeanDefinition getOrderServiceBeanDefinition() {
        Class<?> beanType = OrderService.class;
        RootBeanDefinition beanDefinition = new RootBeanDefinition(beanType);
        beanDefinition.setTargetType(OrderService.class);
        // 트랜잭션 어드바이스가 정적으로 등록됨
        beanDefinition.setAttribute(
            Conventions.getQualifiedAttributeName(
                AnnotationAwareAspectJAutoProxyCreator.class, "preserveTargetClass"), Boolean.TRUE);
        return beanDefinition;
    }
}
```

### 예제 3: Native Image 빌드 설정

`build.gradle`에 네이티브 이미지 빌드를 위한 설정을 추가한다.

```groovy
plugins {
    id 'org.springframework.boot' version '3.2.5'
    id 'io.spring.dependency-management' version '1.1.4'
    id 'org.graalvm.buildtools.native' version '0.9.28'
    id 'java'
}

dependencies {
    implementation 'org.springframework.boot:spring-boot-starter-web'
    implementation 'org.springframework.boot:spring-boot-starter-data-jpa'
    implementation 'org.springframework.boot:spring-boot-starter-aop'
    testImplementation 'org.springframework.boot:spring-boot-starter-test'
}

graalvmNative {
    binaries {
        main {
            imageName = 'my-app'
            buildArgs.add('--initialize-at-build-time=org.slf4j')
            buildArgs.add('-H:+ReportExceptionStackTraces')
        }
    }
}
```

AOT 소스 생성 확인:

```bash
# AOT 소스 생성
./gradlew processAot

# 생성된 파일 확인
ls build/generated/aotSources/

# 네이티브 이미지 빌드 (GraalVM 설치 필요)
./gradlew nativeCompile

# 실행
./build/native/nativeCompile/my-app
```

### 예제 4: RuntimeHints를 이용한 수동 힌트 등록

AOT가 자동으로 감지하지 못하는 리플렉션 사용 시 `RuntimeHintsRegistrar`를 구현해야 한다.

```java
@Configuration(proxyBeanMethods = false)
@ImportRuntimeHints(MyRuntimeHints.class)
public class MyAppConfig {

    @Bean
    public ObjectMapper objectMapper() {
        return new ObjectMapper()
                .registerModule(new JavaTimeModule())
                .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    }
}

// 커스텀 힌트 등록
public class MyRuntimeHints implements RuntimeHintsRegistrar {

    @Override
    public void registerHints(RuntimeHints hints, ClassLoader classLoader) {
        // 리플렉션으로 접근하는 클래스 등록
        hints.reflection()
                .registerType(CustomEvent.class,
                        MemberCategory.INVOKE_DECLARED_CONSTRUCTORS,
                        MemberCategory.DECLARED_FIELDS)
                .registerType(OrderDto.class,
                        MemberCategory.INVOKE_DECLARED_CONSTRUCTORS,
                        MemberCategory.DECLARED_FIELDS);

        // 리소스 파일 등록
        hints.resources()
                .registerPattern("templates/*.html")
                .registerPattern("i18n/*.properties");

        // 프록시 등록 (JDK 프록시 필요 시)
        hints.proxies()
                .registerJdkProxy(OrderRepository.class);
    }
}
```

### 예제 5: AOT 테스트

AOT 컨텍스트가 올바르게 동작하는지 검증하는 테스트 코드다.

```java
@SpringBootTest
@Import(TestcontainersConfiguration.class)
class OrderServiceAotTest {

    @Autowired
    private OrderService orderService;

    @Test
    void contextLoads() {
        assertThat(orderService).isNotNull();
    }

    @Test
    void createOrderShouldPersist() {
        CreateOrderRequest request = new CreateOrderRequest("item-001", 2, BigDecimal.valueOf(9900));
        Order saved = orderService.createOrder(request);

        assertThat(saved.getId()).isNotNull();
        assertThat(saved.getItemCode()).isEqualTo("item-001");
    }
}

// AOT 컨텍스트 전용 테스트
@TestPropertySource(properties = "spring.aot.enabled=true")
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE)
class AotContextSmokeTest {

    @Autowired
    ApplicationContext context;

    @Test
    void allBeansAreProperlyLoaded() {
        String[] beanNames = context.getBeanDefinitionNames();
        assertThat(beanNames).hasSizeGreaterThan(0);
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. Self-Invocation 문제는 여전히 존재한다

`proxyBeanMethods = false`와 AOT 환경에서도 **같은 클래스 내 메서드 간 `@Transactional` 호출은 여전히 동작하지 않는다.** 이는 프록시 우회 문제가 아니라 AOP의 구조적 한계다.

```java
@Service
public class OrderService {

    public void processOrder(Long id) {
        // ❌ 트랜잭션이 적용되지 않음 - self-invocation
        this.updateStatus(id, OrderStatus.PROCESSING);
    }

    @Transactional
    public void updateStatus(Long id, OrderStatus status) {
        // ...
    }
}
```

해결책은 서비스를 분리하거나, `ApplicationContext`에서 빈을 직접 참조하거나, `@Transactional`을 클래스 레벨로 끌어올리는 것이다.

### 2. 동적 빈 등록의 제약

AOT는 빌드 시점에 빈 그래프를 확정하기 때문에, **런타임에 동적으로 빈을 등록하는 패턴**은 제약을 받는다.

```java
// ❌ AOT 환경에서 문제 발생 가능
@Component
public class DynamicBeanRegistrar implements ApplicationContextInitializer<GenericApplicationContext> {

    @Override
    public void initialize(GenericApplicationContext context) {
        // 런타임 조건에 따른 동적 등록 → AOT가 예측 불가
        if (someRuntimeCondition()) {
            context.registerBean(SpecialService.class);
        }
    }
}
```

대신 `@ConditionalOnProperty`, `@Profile` 같은 정적으로 분석 가능한 조건부 빈을 사용해야 한다.

### 3. 빌드 시간과 복잡도 증가

AOT와 네이티브 이미지 빌드는 일반 JVM 빌드보다 **현저히 긴 빌드 시간**을 요구한다. 팀의 CI/CD 파이프라인에서 빌드 시간이 10~30분까지 늘어날 수 있으며, 이는 개발 생산성에 영향을 준다.

| 항목 | JVM 모드 | Native 모드 |
|------|----------|-------------|
| 빌드 시간 | 30초~2분 | 5~30분 |
| 콜드 스타트 | 2~10초 | 50ms~200ms |
| 메모리 사용 | 높음 | 낮음 |
| 런타임 최적화 | JIT 최적화 | 정적 최적화만 |
| 동적 기능 | 완전 지원 | 제약 있음 |

### 4. 서드파티 라이브러리 호환성

아직 모든 서드파티 라이브러리가 AOT/Native를 완벽하게 지원하지는 않는다. 특히 내부적으로 리플렉션을 적극 사용하는 라이브러리는 추가적인 힌트 등록이 필요하다. [GraalVM Reachability Metadata Repository](https://github.com/oracle/graalvm-reachability-metadata)에서 지원 현황을 확인할 수 있다.

---

## 정리

Spring AOT는 단순히 GraalVM을 지원하기 위한 기능이 아니다. **Spring 애플리케이션의 설계 방식을 보다 명시적이고 정적으로 바꾸는 패러다임 전환**이다.

핵심 요약:

- **`@Configuration(proxyBeanMethods = false)`** 를 기본으로 사용하라. 대부분의 경우 CGLIB 프록시 없이도 동일하게 동작한다.
- **파라미터 주입 방식**을 `@Bean` 메서드에서 일관되게 사용하면 프록시 의존성 자체를 제거할 수 있다.
- **`RuntimeHintsRegistrar`** 를 적극 활용해 AOT가 놓치는 동적 접근을 명시적으로 선언하라.
- 네이티브 이미지가 목표가 아니더라도, AOT 친화적인 코드는 **더 명확하고 빠른 기동 시간**을 제공한다.
- 프로젝트 초기부터 `./gradlew processAot` 를 CI에 포함시켜 AOT 호환성을 지속적으로 검증하라.

Spring AOT의 제약을 단점으로만 볼 필요는 없다. 오히려 동적 마법에 의존하던 코드를 명시적으로 재설계하는 기회로 삼으면, 유지보수성과 성능 모두를 잡을 수 있다.