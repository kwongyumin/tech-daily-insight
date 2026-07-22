# Spring Bean 생명주기와 초기화 순서 트러블슈팅

## 개요

Spring 애플리케이션을 운영하다 보면 "왜 이 Bean은 초기화 시점에 NPE가 발생하지?", "분명히 Bean을 등록했는데 왜 의존성이 주입되지 않은 상태로 메서드가 실행되지?" 같은 경험을 한 번쯤 해봤을 것이다.

이런 문제들은 대부분 **Spring Bean 생명주기에 대한 불완전한 이해**에서 비롯된다. 특히 `@PostConstruct`, `InitializingBean`, `@EventListener(ApplicationReadyEvent)` 중 무엇을 써야 하는지, `@DependsOn`은 언제 필요한지, SmartLifecycle은 무엇인지 헷갈리는 경우가 많다.

이 포스팅에서는 실무에서 마주치는 초기화 순서 관련 트러블슈팅 케이스를 중심으로, Spring Bean 생명주기의 핵심 메커니즘을 깊이 있게 다뤄보겠다.

---

## 핵심 개념: Spring Bean 생명주기 단계

Spring Bean의 생명주기는 크게 다음 단계로 구성된다.

```
1. BeanDefinition 로딩 (ComponentScan, @Configuration 처리)
2. BeanFactory 준비
3. BeanPostProcessor 등록
4. Bean 인스턴스 생성 (생성자 호출)
5. 의존성 주입 (DI)
6. BeanPostProcessor#postProcessBeforeInitialization()
7. 초기화 콜백 (@PostConstruct / afterPropertiesSet() / init-method)
8. BeanPostProcessor#postProcessAfterInitialization()
9. 애플리케이션 컨텍스트 준비 완료 (ApplicationReadyEvent 발행)
10. Bean 사용
11. 소멸 콜백 (@PreDestroy / destroy() / destroy-method)
```

특히 **7번 초기화 콜백 단계**와 **9번 ApplicationReadyEvent** 사이의 차이를 명확히 이해하는 것이 트러블슈팅의 핵심이다.

### 초기화 메커니즘 우선순위

동일 Bean 내에서 여러 초기화 방법을 혼용하면 실행 순서는 다음과 같다.

| 우선순위 | 방법 | 설명 |
|---|---|---|
| 1 | `@PostConstruct` | JSR-250 표준, 권장 방식 |
| 2 | `InitializingBean#afterPropertiesSet()` | Spring 인터페이스 직접 구현 |
| 3 | `@Bean(initMethod = "...")` | XML 방식과 동일한 레거시 방식 |

---

## 실전 예제

### 케이스 1: @PostConstruct에서 다른 Bean 메서드 호출 시 NPE

가장 흔한 실수 패턴이다. `@PostConstruct`가 실행되는 시점에 의존 Bean이 **완전히 초기화되지 않은 경우**가 있다.

```java
@Component
@RequiredArgsConstructor
public class CacheWarmupService {

    private final ProductRepository productRepository;
    private final CacheManager cacheManager; // 이 Bean이 문제

    @PostConstruct
    public void warmup() {
        // CacheManager가 아직 초기화 중이라면 NPE 또는 예외 발생
        List<Product> products = productRepository.findAll();
        products.forEach(p -> cacheManager.getCache("products").put(p.getId(), p));
    }
}
```

**원인 분석:** `CacheManager`가 외부 캐시(Redis, Ehcache 등)에 대한 커넥션 풀을 `@PostConstruct`로 초기화하고 있다면, 두 Bean의 초기화 순서에 따라 커넥션이 준비되기 전에 `warmup()`이 호출될 수 있다.

**해결책 1: `@DependsOn` 명시**

```java
@Component
@DependsOn("cacheManager") // cacheManager Bean이 먼저 초기화되도록 강제
@RequiredArgsConstructor
public class CacheWarmupService {

    private final ProductRepository productRepository;
    private final CacheManager cacheManager;

    @PostConstruct
    public void warmup() {
        List<Product> products = productRepository.findAll();
        products.forEach(p -> cacheManager.getCache("products").put(p.getId(), p));
    }
}
```

**해결책 2: ApplicationReadyEvent 사용 (더 안전한 방법)**

```java
@Component
@RequiredArgsConstructor
@Slf4j
public class CacheWarmupService {

    private final ProductRepository productRepository;
    private final CacheManager cacheManager;

    @EventListener(ApplicationReadyEvent.class)
    public void warmup() {
        // 모든 Bean이 완전히 초기화된 이후 실행됨
        log.info("Cache warmup 시작");
        List<Product> products = productRepository.findAll();
        products.forEach(p -> cacheManager.getCache("products").put(p.getId(), p));
        log.info("Cache warmup 완료: {}건", products.size());
    }
}
```

`ApplicationReadyEvent`는 ApplicationContext refresh가 완전히 끝난 뒤 발행되므로, 모든 Bean이 초기화 완료된 시점에 실행을 보장한다.

---

### 케이스 2: 순환 의존성과 초기화 순서

```java
@Component
public class ServiceA {
    
    @Autowired
    private ServiceB serviceB;

    @PostConstruct
    public void init() {
        serviceB.doSomething(); // ServiceB의 init이 아직 안 끝났다면?
    }
}

@Component
public class ServiceB {

    @Autowired
    private ServiceA serviceA;

    @PostConstruct
    public void init() {
        log.info("ServiceB initialized");
    }

    public void doSomething() { ... }
}
```

이 경우 Spring이 순환 참조를 감지하거나, Spring Boot 2.6+ 이후부터는 기본적으로 순환 의존성을 금지하므로 시작 시점에 예외가 발생한다.

**올바른 해결 방향: 의존성 재설계**

```java
// 공통 로직을 별도 컴포넌트로 분리
@Component
public class SharedService {
    public void doSomething() { ... }
}

@Component
@RequiredArgsConstructor
public class ServiceA {
    private final SharedService sharedService;

    @PostConstruct
    public void init() {
        sharedService.doSomething(); // 순환 제거
    }
}
```

---

### 케이스 3: SmartLifecycle을 활용한 정교한 초기화 제어

단순 `@PostConstruct`로는 부족한 경우, 특히 **서버가 완전히 뜬 후에 특정 작업을 시작**하거나 **종료 시 graceful shutdown**이 필요할 때는 `SmartLifecycle`이 적합하다.

```java
@Component
@Slf4j
public class MessageConsumerLifecycle implements SmartLifecycle {

    private volatile boolean running = false;
    private final KafkaConsumer kafkaConsumer;

    public MessageConsumerLifecycle(KafkaConsumer kafkaConsumer) {
        this.kafkaConsumer = kafkaConsumer;
    }

    @Override
    public void start() {
        log.info("Kafka Consumer 시작");
        kafkaConsumer.startPolling();
        running = true;
    }

    @Override
    public void stop() {
        log.info("Kafka Consumer 정상 종료 중...");
        kafkaConsumer.stopPolling();
        running = false;
    }

    @Override
    public boolean isRunning() {
        return running;
    }

    @Override
    public int getPhase() {
        // 숫자가 클수록 늦게 시작, 빠르게 종료 (기본값: 0)
        // 인프라 관련 Bean들이 먼저 시작된 후 실행되도록 설정
        return Integer.MAX_VALUE;
    }

    @Override
    public boolean isAutoStartup() {
        return true;
    }
}
```

`getPhase()` 값을 통해 여러 `SmartLifecycle` Bean 간의 시작/종료 순서를 제어할 수 있다.

---

### 케이스 4: @Lazy를 활용한 초기화 지연

초기화 비용이 크거나 의존성 충돌이 있는 Bean은 `@Lazy`로 지연 초기화할 수 있다.

```java
@Configuration
public class ExternalApiConfig {

    @Bean
    @Lazy // 최초 사용 시점에 초기화
    public HeavyExternalApiClient heavyApiClient() {
        return new HeavyExternalApiClient(); // 커넥션 풀 초기화에 5초 소요
    }
}

@Service
@RequiredArgsConstructor
public class OrderService {

    @Lazy // 주입 지점에도 @Lazy 선언 필요
    private final HeavyExternalApiClient apiClient;

    public void processOrder(Order order) {
        // 이 메서드가 처음 호출될 때 heavyApiClient가 초기화됨
        apiClient.sendOrder(order);
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. @PostConstruct에서 트랜잭션이 동작하지 않는 문제

```java
@Component
@RequiredArgsConstructor
public class DataInitializer {

    private final UserRepository userRepository;

    @PostConstruct
    @Transactional // 이 어노테이션은 @PostConstruct에서 동작하지 않는다!
    public void init() {
        userRepository.save(new User("admin")); // 트랜잭션 없이 실행될 수 있음
    }
}
```

**이유:** `@Transactional`은 AOP 프록시를 통해 동작하는데, `@PostConstruct`는 프록시가 아닌 실제 객체에서 직접 호출된다.

**해결책:**

```java
@Component
@RequiredArgsConstructor
public class DataInitializer {

    private final DataInitializerHelper helper;

    @EventListener(ApplicationReadyEvent.class)
    public void init() {
        helper.initializeData(); // 트랜잭션이 필요한 로직은 별도 Bean으로 위임
    }
}

@Component
@RequiredArgsConstructor
public class DataInitializerHelper {

    private final UserRepository userRepository;

    @Transactional
    public void initializeData() {
        userRepository.save(new User("admin")); // 정상적으로 트랜잭션 적용
    }
}
```

### 2. @DependsOn의 함정

`@DependsOn`은 Bean 생성 순서만 보장하지, **초기화 완료를 보장하지 않는다**. 비동기 초기화를 수행하는 Bean이 있다면 `@DependsOn`만으로는 부족하다.

```java
@Component
public class AsyncInitBean {

    @PostConstruct
    public void init() {
        CompletableFuture.runAsync(() -> {
            // 비동기로 초기화 작업 수행
            heavyInitialization();
        });
        // @PostConstruct는 즉시 반환 -> 초기화가 완료되지 않은 상태
    }
}
```

이런 경우는 초기화 완료를 알리는 별도의 플래그나 `CountDownLatch`를 사용하거나, `SmartLifecycle`로 마이그레이션하는 것이 좋다.

### 3. 프로파일별 초기화 순서 차이

```java
@Component
@Profile("production")
public class ProductionCacheConfig { ... }

@Component
@Profile("!production")
public class LocalCacheConfig { ... }
```

프로파일에 따라 활성화되는 Bean이 달라지면 초기화 순서도 달라질 수 있다. 로컬에서는 잘 동작하지만 운영에서 초기화 오류가 나는 케이스의 상당수가 이런 이유다.

### 4. BeanPostProcessor 등록 시점 주의

`BeanPostProcessor`를 구현한 Bean은 다른 일반 Bean보다 먼저 인스턴스화된다. 이 시점에는 `@Autowired`, `@Value` 같은 의존성 주입이 완전히 처리되지 않을 수 있다.

```java
// 잘못된 예: BeanPostProcessor에서 @Autowired 사용
@Component
public class CustomBeanPostProcessor implements BeanPostProcessor {

    @Autowired
    private SomeService someService; // 주의: 주입이 안 될 수 있음

    @Override
    public Object postProcessBeforeInitialization(Object bean, String beanName) {
        someService.doSomething(); // NPE 위험
        return bean;
    }
}
```

`BeanPostProcessor` 구현체에서는 생성자 주입을 사용하고, 의존 Bean도 `BeanPostProcessor` 혹은 인프라 Bean으로 설계해야 한다.

---

## 트러블슈팅 체크리스트

실무에서 초기화 관련 문제가 발생했을 때 확인할 체크리스트다.

```
✅ NPE 발생 위치가 @PostConstruct인가?
   → 의존 Bean의 초기화 순서 확인, ApplicationReadyEvent 전환 검토

✅ 트랜잭션이 적용되지 않는가?
   → @PostConstruct + @Transactional 조합 확인, 별도 Bean 위임으로 해결

✅ 로컬은 정상이지만 운영에서 실패하는가?
   → 프로파일별 Bean 차이, 환경변수/외부 설정 로딩 시점 확인

✅ 특정 Bean이 너무 일찍/늦게 초기화되는가?
   → @DependsOn, SmartLifecycle getPhase() 값 조정 검토

✅ 서버 종료 시 데이터 유실이 발생하는가?
   → @PreDestroy 또는 SmartLifecycle#stop() 구현 확인
```

---

## 정리

Spring Bean 생명주기와 초기화 순서 문제는 코드 자체의 버그가 아니라 **프레임워크의 동작 방식에 대한 이해 부족**에서 비롯되는 경우가 대부분이다.

핵심을 정리하면 다음과 같다.

- **단순 초기화 로직**: `@PostConstruct` 사용, 단 트랜잭션 불가
- **모든 Bean 초기화 완료 후 실행**: `@EventListener(ApplicationReadyEvent.class)` 사용
- **Bean 간 초기화 순서 강제**: `@DependsOn` 사용, 비동기 초기화에는 주의
- **정교한 시작/종료 제어**: `SmartLifecycle` 구현
- **초기화 비용이 큰 Bean**: `@Lazy`로 지연 초기화 검토

생명주기를 제대로 이해하면 트러블슈팅 시간이 줄어들 뿐 아니라, 애플리케이션의 시작 시간 최적화와 graceful shutdown 구현에도 자연스럽게 활용할 수 있다. Spring이 제공하는 다양한 초기화 훅을 상황에 맞게 선택하는 것이 견고한 애플리케이션을 만드는 출발점이다.