# Spring AOT와 프록시 없는 경량 컨텍스트

## 개요

Spring Framework 6와 Spring Boot 3의 등장과 함께 **AOT(Ahead-Of-Time) 컴파일**이 본격적으로 스프링 생태계의 중심으로 들어왔다. GraalVM Native Image를 활용한 네이티브 빌드가 화두가 되면서, 전통적인 스프링의 런타임 리플렉션과 동적 프록시 중심의 아키텍처는 근본적인 도전에 직면했다.

이 글에서는 Spring AOT가 무엇인지, 그리고 AOT 환경에서 프록시 없이도 경량 컨텍스트를 구성할 수 있는 방법과 실전 적용 전략을 살펴본다. 단순히 "빌드가 빠르다"는 수준을 넘어, 왜 AOT가 필요하고 어떤 트레이드오프를 감수해야 하는지를 실무 관점에서 짚어본다.

---

## 핵심 개념

### AOT란 무엇인가?

전통적인 스프링 애플리케이션은 **JIT(Just-In-Time)** 방식으로 동작한다. 애플리케이션이 시작될 때 `ApplicationContext`가 초기화되면서 빈 정의를 읽고, 리플렉션으로 빈을 생성하고, CGLIB 또는 JDK 다이나믹 프록시로 AOP를 적용한다. 이 모든 과정이 **런타임**에 일어난다.

AOT는 이 과정을 **빌드 타임**으로 앞당긴다. 스프링 AOT 엔진은 빌드 시점에 애플리케이션 컨텍스트를 분석하여 다음을 생성한다:

- 빈 등록 및 의존성 주입 코드 (리플렉션 없이 직접 호출)
- 리플렉션 힌트, 리소스 힌트, 프록시 힌트
- `BeanFactory` 초기화를 위한 소스 코드

결과적으로 GraalVM Native Image 빌드 시 이 사전 생성된 코드를 활용해 리플렉션과 동적 프록시 의존성을 최소화한다.

### 동적 프록시의 문제

스프링의 `@Transactional`, `@Cacheable`, `@Async` 등은 내부적으로 CGLIB이나 JDK 동적 프록시를 사용한다. 네이티브 빌드 환경에서는 이 방식이 문제가 된다:

1. **CGLIB 프록시**: 런타임에 바이트코드를 생성하므로 Native Image에서 사전 등록 없이는 동작 불가
2. **JDK 동적 프록시**: 인터페이스 기반이지만 역시 런타임 리플렉션 의존
3. **클래스 경로 스캔**: `@ComponentScan` 자체가 런타임 스캔이므로 AOT 환경에선 사전 처리 필요

Spring 6부터는 이를 해결하기 위해 **프록시리스(Proxyless) 트랜잭션**, **AOT 힌트 API**, 그리고 **`@ImportRuntimeHints`** 같은 메커니즘을 제공한다.

### Spring AOT 처리 파이프라인

```
소스 코드
   ↓
[빌드 타임] Spring AOT 엔진 실행
   ├── BeanDefinition 분석
   ├── BeanRegistrationAotProcessor 실행
   ├── BeanFactoryInitializationAotProcessor 실행
   └── 힌트 파일 + 소스 코드 생성
         ↓
[GraalVM] Native Image 컴파일
   └── 힌트 기반 리플렉션/프록시 사전 등록
         ↓
실행 파일 (빠른 시작, 낮은 메모리)
```

---

## 실전 예제

### 1. AOT 친화적인 빈 구성

일반적인 `@Configuration` 클래스는 AOT에서 문제없이 처리되지만, 람다나 익명 클래스를 빈으로 등록하는 경우 AOT 엔진이 추론하지 못할 수 있다.

```java
// ❌ AOT 엔진이 타입 추론 어려운 패턴
@Bean
public Predicate<String> emailValidator() {
    return email -> email.contains("@");
}

// ✅ AOT 친화적인 명시적 타입 구성
@Bean
public EmailValidator emailValidator() {
    return new EmailValidator();
}

public static class EmailValidator implements Predicate<String> {
    @Override
    public boolean test(String email) {
        return email != null && email.contains("@");
    }
}
```

### 2. 프록시 없는 트랜잭션 처리 (Programmatic Transaction)

AOT 환경에서 `@Transactional`을 완전히 배제하고, `TransactionTemplate`으로 명시적 트랜잭션을 처리하면 CGLIB 프록시 없이도 동일한 효과를 얻을 수 있다.

```java
@Service
public class OrderService {

    private final TransactionTemplate transactionTemplate;
    private final OrderRepository orderRepository;
    private final InventoryRepository inventoryRepository;

    public OrderService(PlatformTransactionManager txManager,
                        OrderRepository orderRepository,
                        InventoryRepository inventoryRepository) {
        this.transactionTemplate = new TransactionTemplate(txManager);
        this.orderRepository = orderRepository;
        this.inventoryRepository = inventoryRepository;
    }

    public Order createOrder(OrderRequest request) {
        return transactionTemplate.execute(status -> {
            try {
                Order order = Order.from(request);
                orderRepository.save(order);
                inventoryRepository.decrease(request.getProductId(), request.getQuantity());
                return order;
            } catch (InsufficientStockException e) {
                status.setRollbackOnly();
                throw e;
            }
        });
    }
}
```

### 3. RuntimeHints 등록으로 리플렉션 명시

AOT 환경에서 리플렉션이 필요한 클래스는 `RuntimeHintsRegistrar`로 명시적으로 등록해야 한다.

```java
@Configuration
@ImportRuntimeHints(AppRuntimeHints.class)
public class AppConfig {
    // ...
}

public class AppRuntimeHints implements RuntimeHintsRegistrar {

    @Override
    public void registerHints(RuntimeHints hints, ClassLoader classLoader) {
        // 리플렉션이 필요한 클래스 등록
        hints.reflection()
            .registerType(OrderDto.class,
                MemberCategory.INVOKE_DECLARED_CONSTRUCTORS,
                MemberCategory.DECLARED_FIELDS)
            .registerType(ProductDto.class,
                MemberCategory.INVOKE_DECLARED_CONSTRUCTORS,
                MemberCategory.DECLARED_FIELDS);

        // 리소스 파일 등록
        hints.resources()
            .registerPattern("templates/*.html")
            .registerPattern("i18n/*.properties");

        // JDK 동적 프록시가 필요한 경우
        hints.proxies()
            .registerJdkProxy(OrderRepository.class);
    }
}
```

### 4. AOT 전용 BeanRegistrationAotProcessor 구현

특정 빈에 대해 AOT 코드 생성 방식을 커스터마이징하고 싶다면 `BeanRegistrationAotProcessor`를 구현한다.

```java
@Component
public class CustomCacheAotProcessor implements BeanRegistrationAotProcessor {

    @Override
    public BeanRegistrationAotContribution processAheadOfTime(RegisteredBean registeredBean) {
        Class<?> beanClass = registeredBean.getBeanClass();
        
        if (!beanClass.isAnnotationPresent(CustomCacheable.class)) {
            return null; // 해당 없으면 기본 처리
        }

        return (generationContext, beanRegistrationCode) -> {
            // AOT 코드 생성 시 힌트 추가
            RuntimeHints hints = generationContext.getRuntimeHints();
            hints.reflection().registerType(beanClass,
                MemberCategory.INVOKE_DECLARED_METHODS);
        };
    }
}
```

### 5. 경량 컨텍스트 구성 - 슬라이스 테스트 전략

AOT 환경에서의 테스트도 경량화가 중요하다. 전체 컨텍스트 대신 슬라이스 테스트를 적극 활용하자.

```java
// 서비스 계층 단위 테스트 - Spring Context 없이
class OrderServiceTest {

    private OrderService orderService;
    private TransactionTemplate transactionTemplate;

    @BeforeEach
    void setUp() {
        PlatformTransactionManager txManager = mock(PlatformTransactionManager.class);
        TransactionStatus txStatus = mock(TransactionStatus.class);
        
        when(txManager.getTransaction(any())).thenReturn(txStatus);
        
        transactionTemplate = new TransactionTemplate(txManager);
        orderService = new OrderService(
            txManager,
            mock(OrderRepository.class),
            mock(InventoryRepository.class)
        );
    }

    @Test
    void createOrder_성공시_주문_반환() {
        // given
        OrderRequest request = new OrderRequest("product-1", 2);
        
        // when & then
        assertDoesNotThrow(() -> orderService.createOrder(request));
    }
}

// 데이터 계층 슬라이스 테스트
@DataJpaTest
@Import(TestcontainersConfig.class)
class OrderRepositoryTest {

    @Autowired
    private OrderRepository orderRepository;

    @Test
    void save_저장후_조회_성공() {
        Order order = Order.builder()
            .productId("product-1")
            .quantity(2)
            .build();

        Order saved = orderRepository.save(order);

        assertThat(saved.getId()).isNotNull();
    }
}
```

### 6. build.gradle AOT 설정

```groovy
plugins {
    id 'org.springframework.boot' version '3.2.0'
    id 'io.spring.dependency-management' version '1.1.4'
    id 'org.graalvm.buildtools.native' version '0.9.28'
}

graalvmNative {
    binaries {
        main {
            imageName = 'my-app'
            buildArgs.add('--initialize-at-build-time=org.slf4j')
            buildArgs.add('-H:+ReportExceptionStackTraces')
        }
    }
    toolchainDetection = false
}

// AOT 테스트 활성화
tasks.named('test') {
    jvmArgs '-Dspring.aot.enabled=true'
}
```

---

## 주의사항 및 트레이드오프

### AOT 도입 시 반드시 확인해야 할 것들

**1. 조건부 빈(Conditional Bean)의 한계**

`@ConditionalOnProperty`, `@ConditionalOnMissingBean` 등의 조건부 빈은 AOT 빌드 시 조건이 고정된다. 즉, 빌드 시점의 환경 변수에 따라 조건이 결정되므로, 런타임 환경에 따라 다른 빈을 등록하는 패턴은 AOT에서 동작하지 않을 수 있다.

```java
// ⚠️ 이 빈은 AOT 빌드 시점의 조건으로 고정됨
@Bean
@ConditionalOnProperty(name = "feature.new-payment", havingValue = "true")
public PaymentService newPaymentService() {
    return new NewPaymentService();
}
```

**2. 동적 빈 로딩 불가**

플러그인 아키텍처나 런타임에 클래스를 동적으로 로드하는 패턴은 네이티브 이미지에서 동작하지 않는다. 이 경우 SPI(Service Provider Interface)를 사전에 등록해두는 방식으로 전환이 필요하다.

**3. 빌드 시간 증가**

AOT + Native Image 빌드는 일반 JAR 빌드 대비 수 분에서 수십 분까지 걸릴 수 있다. CI/CD 파이프라인 설계 시 이를 반드시 고려해야 한다.

| 구분 | 일반 JVM 빌드 | Native Image 빌드 |
|------|---------------|-------------------|
| 빌드 시간 | ~30초 | ~5~15분 |
| 시작 시간 | ~2~5초 | ~0.05~0.1초 |
| 메모리 사용 | ~256MB+ | ~50~100MB |
| 피크 처리량 | JIT 최적화 후 높음 | 다소 낮을 수 있음 |

**4. 서드파티 라이브러리 호환성**

모든 라이브러리가 AOT/GraalVM을 지원하는 것은 아니다. `reachability-metadata` 저장소를 통해 GraalVM 커뮤니티가 관리하는 힌트를 참고하고, 사용 중인 라이브러리의 네이티브 지원 여부를 반드시 사전에 검토해야 한다.

**5. 프로파일 전략 재검토**

`@Profile("dev")`, `@Profile("prod")`처럼 프로파일로 빈을 분리하는 방식은 AOT 빌드 시 **모든 프로파일의 빈이 포함**될 수 있다. 네이티브 빌드 전용 프로파일을 별도로 관리하거나, 환경별로 다른 이미지를 빌드하는 전략을 고려해야 한다.

---

## 정리

Spring AOT는 단순한 성능 최적화 도구가 아니다. 스프링의 철학 자체를 **"런타임 유연성"에서 "빌드 타임 명시성"으로** 일부 전환하는 패러다임 변화다.

실무 도입을 고려한다면 다음 순서를 권장한다:

1. **AOT 테스트 모드 먼저 활성화**: `spring.aot.enabled=true` 옵션으로 JVM 위에서 AOT 동작을 먼저 검증
2. **리플렉션 및 프록시 의존 코드 식별**: `@Transactional`, 동적 프록시 사용처 파악 후 `TransactionTemplate`으로 단계적 전환
3. **RuntimeHints 등록**: 누락된 힌트로 인한 런타임 오류를 사전에 차단
4. **슬라이스 테스트 강화**: 경량 컨텍스트 전략으로 테스트 속도와 격리성 동시 확보
5. **모니터링 및 피크 처리량 검증**: Native Image의 JIT 최적화 부재가 실제 서비스에 미치는 영향 측정

AOT와 Native Image는 **쇼트-리빙 서비스**(람다, 사이드카, CLI 도구)나 **빠른 스케일아웃이 필요한 마이크로서비스**에 특히 유리하다. 반면 오랫동안 실행되며 JIT 최적화의 혜택을 충분히 누리는 모놀리식 서비스에는 전통적인 JVM 방식이 여전히 유리할 수 있다.

중요한 것은 기술 자체보다 **어떤 컨텍스트에서 이 기술이 가치를 발휘하는가**를 이해하는 것이다. Spring AOT는 그 선택지를 넓혀주는 강력한 도구다.