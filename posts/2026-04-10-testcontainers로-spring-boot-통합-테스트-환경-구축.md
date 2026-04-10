# Testcontainers로 Spring Boot 통합 테스트 환경 구축

## 개요

통합 테스트는 단위 테스트가 커버하지 못하는 레이어 간 상호작용, 실제 데이터베이스 쿼리 동작, 외부 메시지 브로커 연동 등을 검증하는 데 필수적입니다. 그러나 오랫동안 실무 현장에서 통합 테스트는 "로컬 환경에 미리 DB가 설치되어 있어야 한다"거나 "CI 서버에 별도 인프라를 구성해야 한다"는 이유로 관리가 소홀해지기 쉬웠습니다.

**Testcontainers**는 이 문제를 Docker 컨테이너를 통해 우아하게 해결합니다. 테스트 실행 시 필요한 인프라(PostgreSQL, Redis, Kafka 등)를 컨테이너로 자동 기동하고, 테스트가 끝나면 깔끔하게 정리합니다. 결과적으로 "내 로컬에서는 됩니다"라는 변명이 사라지고, 어느 환경에서든 동일한 조건으로 테스트를 실행할 수 있습니다.

이 글에서는 Spring Boot 3.x 환경에서 Testcontainers를 실전 수준으로 활용하는 방법을 다룹니다. PostgreSQL, Redis, Kafka를 예제로 삼아 실무에서 바로 적용할 수 있는 패턴을 공유합니다.

---

## 핵심 개념

### Testcontainers의 동작 원리

Testcontainers는 Java 라이브러리로, Docker 데몬과 통신하여 테스트 코드 내에서 컨테이너 생명주기를 제어합니다. 핵심 흐름은 다음과 같습니다.

1. 테스트 클래스 로딩 시 `@Container` 또는 수동으로 컨테이너 인스턴스 생성
2. 컨테이너 시작 → 랜덤 포트로 바인딩
3. Spring ApplicationContext에 동적으로 생성된 포트/URL 주입
4. 테스트 실행
5. 컨테이너 종료 및 제거

### Spring Boot 3.x의 Testcontainers 지원

Spring Boot 3.1부터 `@ServiceConnection` 어노테이션이 도입되어, 이전보다 훨씬 간결하게 컨테이너와 Spring 설정을 연결할 수 있습니다. `DynamicPropertySource`를 통해 수동으로 프로퍼티를 주입하는 보일러플레이트 코드가 크게 줄었습니다.

---

## 실전 예제

### 의존성 설정

`build.gradle`에 아래와 같이 의존성을 추가합니다.

```groovy
dependencies {
    // Spring Boot Testcontainers BOM 자동 관리
    testImplementation 'org.springframework.boot:spring-boot-testcontainers'
    testImplementation 'org.testcontainers:junit-jupiter'
    testImplementation 'org.testcontainers:postgresql'
    testImplementation 'org.testcontainers:kafka'
    testImplementation 'com.redis:testcontainers-redis:2.2.2'

    testImplementation 'org.springframework.boot:spring-boot-starter-test'
    testImplementation 'org.springframework.kafka:spring-kafka-test'
}
```

> Spring Boot 3.1+ 환경에서는 `spring-boot-testcontainers` 스타터가 버전 관리를 담당하므로, 개별 Testcontainers 모듈 버전을 따로 명시할 필요가 없습니다.

---

### 패턴 1: 공통 컨테이너 설정 추상화

매 테스트 클래스마다 컨테이너를 선언하면 기동 시간이 누적됩니다. 추상 클래스로 공통 컨테이너를 정의하고 재사용하는 패턴이 실무에서 효과적입니다.

```java
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@Testcontainers
public abstract class IntegrationTestBase {

    @Container
    @ServiceConnection
    static final PostgreSQLContainer<?> POSTGRES =
            new PostgreSQLContainer<>("postgres:16-alpine")
                    .withDatabaseName("testdb")
                    .withUsername("test")
                    .withPassword("test")
                    .withReuse(true); // 컨테이너 재사용 활성화

    @Container
    @ServiceConnection
    static final KafkaContainer KAFKA =
            new KafkaContainer(DockerImageName.parse("confluentinc/cp-kafka:7.6.0"))
                    .withReuse(true);

    static {
        // 컨테이너 재사용을 위한 Ryuk 비활성화 (선택)
        POSTGRES.start();
        KAFKA.start();
    }
}
```

`withReuse(true)`를 사용하면 동일한 컨테이너 설정으로 여러 테스트를 실행할 때 컨테이너를 재기동하지 않고 재사용합니다. 단, `~/.testcontainers.properties`에 `testcontainers.reuse.enable=true` 설정이 필요합니다.

---

### 패턴 2: PostgreSQL + JPA 통합 테스트

```java
@Transactional
class UserRepositoryTest extends IntegrationTestBase {

    @Autowired
    private UserRepository userRepository;

    @Test
    @DisplayName("사용자 저장 및 이메일로 조회")
    void saveAndFindByEmail() {
        // given
        User user = User.builder()
                .email("dev@example.com")
                .name("홍길동")
                .build();

        // when
        userRepository.save(user);
        Optional<User> found = userRepository.findByEmail("dev@example.com");

        // then
        assertThat(found).isPresent();
        assertThat(found.get().getName()).isEqualTo("홍길동");
    }

    @Test
    @DisplayName("존재하지 않는 이메일 조회 시 empty 반환")
    void findByEmail_notFound() {
        Optional<User> found = userRepository.findByEmail("notexist@example.com");
        assertThat(found).isEmpty();
    }
}
```

실제 PostgreSQL에서 실행되므로 H2 호환성 문제가 없습니다. `JSONB`, `ARRAY`, `ENUM` 타입 같은 PostgreSQL 전용 기능도 그대로 테스트할 수 있습니다.

---

### 패턴 3: Kafka 프로듀서/컨슈머 통합 테스트

```java
@SpringBootTest
@Testcontainers
class OrderEventConsumerTest extends IntegrationTestBase {

    @Autowired
    private KafkaTemplate<String, OrderEvent> kafkaTemplate;

    @Autowired
    private OrderEventConsumer orderEventConsumer;

    @Test
    @DisplayName("주문 이벤트 발행 시 컨슈머가 처리한다")
    void consumeOrderEvent() throws InterruptedException {
        // given
        OrderEvent event = new OrderEvent("ORDER-001", "COMPLETED", LocalDateTime.now());
        CountDownLatch latch = new CountDownLatch(1);
        orderEventConsumer.setLatch(latch);

        // when
        kafkaTemplate.send("order.events", event.orderId(), event);

        // then
        boolean consumed = latch.await(10, TimeUnit.SECONDS);
        assertThat(consumed).isTrue();
        assertThat(orderEventConsumer.getLastReceivedEvent().orderId())
                .isEqualTo("ORDER-001");
    }
}
```

```java
@Component
public class OrderEventConsumer {

    private CountDownLatch latch = new CountDownLatch(1);
    private OrderEvent lastReceivedEvent;

    @KafkaListener(topics = "order.events", groupId = "test-group")
    public void consume(OrderEvent event) {
        this.lastReceivedEvent = event;
        latch.countDown();
    }

    public void setLatch(CountDownLatch latch) {
        this.latch = latch;
    }

    public OrderEvent getLastReceivedEvent() {
        return lastReceivedEvent;
    }
}
```

---

### 패턴 4: DynamicPropertySource를 활용한 커스텀 설정

`@ServiceConnection`이 지원하지 않는 컨테이너나 커스텀 설정이 필요할 때는 `DynamicPropertySource`를 사용합니다.

```java
@SpringBootTest
@Testcontainers
class RedisSessionTest {

    @Container
    static final GenericContainer<?> REDIS =
            new GenericContainer<>(DockerImageName.parse("redis:7.2-alpine"))
                    .withExposedPorts(6379)
                    .withCommand("redis-server", "--requirepass", "testpassword");

    @DynamicPropertySource
    static void redisProperties(DynamicPropertyRegistry registry) {
        registry.add("spring.data.redis.host", REDIS::getHost);
        registry.add("spring.data.redis.port", REDIS::getFirstMappedPort);
        registry.add("spring.data.redis.password", () -> "testpassword");
    }

    @Autowired
    private StringRedisTemplate redisTemplate;

    @Test
    @DisplayName("Redis에 값을 저장하고 TTL과 함께 조회한다")
    void setAndGetWithTtl() {
        // given
        String key = "session:user:1";
        String value = "session-token-xyz";

        // when
        redisTemplate.opsForValue().set(key, value, Duration.ofMinutes(30));

        // then
        assertThat(redisTemplate.opsForValue().get(key)).isEqualTo(value);
        assertThat(redisTemplate.getExpire(key, TimeUnit.SECONDS))
                .isGreaterThan(0L);
    }
}
```

---

### 패턴 5: TestConfiguration으로 컨테이너 Bean 등록

Spring Boot 3.1+에서는 `@TestConfiguration`과 조합하여 개발 서버 실행 시에도 Testcontainers를 활용할 수 있습니다.

```java
@TestConfiguration(proxyBeanMethods = false)
public class TestContainersConfig {

    @Bean
    @ServiceConnection
    PostgreSQLContainer<?> postgresContainer() {
        return new PostgreSQLContainer<>("postgres:16-alpine")
                .withDatabaseName("testdb");
    }

    @Bean
    @ServiceConnection
    KafkaContainer kafkaContainer() {
        return new KafkaContainer(
                DockerImageName.parse("confluentinc/cp-kafka:7.6.0"));
    }
}
```

```java
// 개발 환경에서 로컬 실행용 메인 클래스
@SpringBootApplication
public class TestApplication {

    public static void main(String[] args) {
        SpringApplication.from(Application::main)
                .with(TestContainersConfig.class)
                .run(args);
    }
}
```

이 패턴은 로컬 개발 시 Docker Compose 없이도 의존 인프라를 자동으로 구성해주므로 매우 유용합니다.

---

## 주의사항 및 트레이드오프

### 테스트 실행 속도

컨테이너 기동 시간이 추가되므로 단위 테스트 대비 속도가 느립니다. 이를 최소화하기 위한 전략은 다음과 같습니다.

- **`withReuse(true)` 활성화**: 동일 설정 컨테이너를 프로세스 간에도 재사용
- **공통 추상 클래스 사용**: JVM 내에서 static 컨테이너를 공유하여 재기동 방지
- **`@DirtiesContext` 최소화**: ApplicationContext를 재생성하면 컨테이너도 재시작될 수 있음

### CI 환경 요구사항

Testcontainers는 Docker 데몬이 필요합니다. GitHub Actions, GitLab CI 등 대부분의 CI 환경은 기본적으로 Docker를 지원하지만, 일부 제한된 환경에서는 `--privileged` 옵션이 필요하거나 Testcontainers Cloud 같은 원격 컨테이너 솔루션을 검토해야 합니다.

### 데이터 격리

여러 테스트가 같은 컨테이너를 공유할 때 데이터 격리에 주의해야 합니다.

- JPA 테스트에는 `@Transactional`로 롤백 처리
- Kafka 토픽은 테스트별로 고유한 이름 사용 또는 `@EmbeddedKafka`와 비교 검토
- Redis는 테스트 후 명시적으로 키를 삭제하거나 별도 DB index 사용

### 이미지 버전 관리

`latest` 태그 사용은 지양하고, 운영 환경과 동일한 버전을 명시합니다. 이미지 버전이 달라지면 동작 차이가 발생할 수 있으며, 이는 Testcontainers를 사용하는 주된 이유(환경 일치)를 역행합니다.

```java
// 권장
new PostgreSQLContainer<>("postgres:16.2-alpine");

// 지양
new PostgreSQLContainer<>("postgres:latest");
```

### 메모리 사용량

여러 컨테이너를 동시에 기동하면 메모리 사용량이 급증합니다. 필요한 컨테이너만 슬라이스 테스트(`@DataJpaTest`, `@WebMvcTest`)로 분리하여 실행하고, 전체 통합 테스트는 별도 태그(`@Tag("integration")`)로 구분하여 선택적으로 실행하는 전략을 권장합니다.

---

## 정리

Testcontainers는 통합 테스트의 고질적인 문제인 **환경 의존성**을 근본적으로 해결합니다. 핵심 포인트를 정리하면 다음과 같습니다.

| 관심사 | 권장 전략 |
|---|---|
| 컨테이너 공유 | 추상 클래스 + static 컨테이너 |
| Spring 설정 주입 | `@ServiceConnection` (3.1+) 또는 `DynamicPropertySource` |
| 컨테이너 재사용 | `withReuse(true)` + `testcontainers.reuse.enable=true` |
| 데이터 격리 | `@Transactional` 롤백, 고유 리소스 명명 |
| CI 통합 | Docker 지원 환경 확인, 이미지 버전 고정 |

H2 같은 인메모리 DB로 통합 테스트를 작성하면 방언 차이, 함수 미지원 등으로 실제 버그를 잡지 못하는 경우가 생깁니다. Testcontainers로 실제 운영 환경과 동일한 인프라 위에서 테스트를 실행하면 이러한 위험을 크게 줄일 수 있습니다.

처음 도입 시에는 가장 핵심적인 레포지토리 레이어부터 시작하고, 점진적으로 Kafka, Redis 등으로 범위를 확장하는 접근을 권장합니다. 한번 구성해두면 팀 전체가 "로컬 환경 세팅" 없이 `./gradlew test` 한 줄로 완전한 통합 테스트를 실행할 수 있는 쾌적한 개발 환경을 갖게 됩니다.