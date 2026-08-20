# GraalVM Native Image로 Spring Boot 앱 경량화하기

## 테스트 전략과 신뢰성 검증: Native Image 배포를 믿고 떠날 수 있는가?

---

## 개요

GraalVM Native Image로 Spring Boot 애플리케이션을 빌드하면 시작 시간이 수십 밀리초로 줄고, 메모리 사용량이 JVM 대비 40~70% 감소한다는 사실은 이미 잘 알려져 있다. 문제는 그 다음이다.

**"컴파일은 됐는데, 프로덕션에 배포할 수 있다고 확신할 수 있는가?"**

Native Image는 AOT(Ahead-of-Time) 컴파일 특성상 JVM 환경과 런타임 동작이 미묘하게 달라진다. Reflection, Dynamic Proxy, Serialization, 클래스패스 스캔 등 JVM의 동적 특성에 의존하는 코드가 Native Image에서는 조용히 실패하거나, 테스트 환경에서는 통과했지만 실제 빌드 결과물에서는 오작동하는 경우가 빈번하다.

이 글은 Native Image 환경에서의 **테스트 전략 설계**와 **신뢰성 검증 파이프라인** 구축에 초점을 맞춘다. 기본 설정은 건너뛰고, 현장에서 실제로 발생하는 검증 실패 사례와 이를 방어하는 구체적인 방법론을 다룬다.

---

## 핵심 개념: 왜 Native Image 테스트는 별도 전략이 필요한가?

### JVM 테스트와 Native Image 테스트의 간극

일반적인 Spring Boot 테스트(`@SpringBootTest`)는 JVM 위에서 실행된다. 이 테스트가 100% 통과하더라도 Native Image 빌드 결과물은 다음 이유로 다르게 동작할 수 있다.

| 위험 요소 | JVM | Native Image |
|---|---|---|
| Reflection | 런타임 동적 해석 | 빌드 시 힌트 명시 필요 |
| Dynamic Proxy (JDK/CGLIB) | 런타임 생성 | AOT 처리 필요 |
| 리소스 로딩 | 클래스패스 스캔 | `resource-config.json` 등록 필요 |
| `@ConfigurationProperties` 바인딩 | 리플렉션 기반 | AOT 생성 코드로 대체 |
| SerializationFilter | 런타임 적용 | 정적 분석 한계 |

이 간극을 메우는 유일한 방법은 **실제 Native Image 바이너리를 대상으로 테스트를 실행**하거나, **AOT 처리 단계 자체를 검증**하는 것이다.

### Spring Boot 3.x의 AOT 테스트 지원

Spring Boot 3.x부터는 `spring-boot-test` 에서 AOT 모드 테스트를 공식 지원한다. `@SpringBootTest`에 `useMainMethod = UseMainMethod.ALWAYS` 옵션과 함께 AOT 컨텍스트를 로드하는 방식으로, 실제 Native Image를 빌드하지 않고도 AOT 처리 결과를 JVM 위에서 검증할 수 있다.

```bash
# AOT 테스트 실행 (JVM 위에서 AOT 컨텍스트 검증)
./mvnw test -Pnative -DskipNativeBuild=true
```

이것이 **1차 방어선**이다. Native 바이너리 빌드 비용(보통 3~10분)을 치르기 전에 빠른 피드백을 얻는다.

---

## 실전 예제

### 1단계: AOT 컨텍스트 검증 테스트 작성

```java
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@TestPropertySource(properties = "spring.aot.enabled=true")
class NativeContextSmokeTest {

    @Autowired
    private ApplicationContext context;

    @Test
    void contextLoads_withAotProcessing() {
        assertThat(context).isNotNull();
    }

    @Test
    void criticalBeans_areRegistered() {
        // AOT 환경에서 누락되기 쉬운 빈 명시적 검증
        assertThat(context.containsBean("orderService")).isTrue();
        assertThat(context.containsBean("paymentGatewayClient")).isTrue();
    }

    @Test
    void reflectionSensitiveBeans_properlyInitialized() {
        // Reflection 기반 초기화가 필요한 빈 직접 호출
        OrderService orderService = context.getBean(OrderService.class);
        assertThatCode(() -> orderService.validateOrder(Order.empty()))
            .doesNotThrowAnyException();
    }
}
```

### 2단계: Reflection 힌트 누락 감지 테스트

Native Image 빌드 실패의 가장 흔한 원인은 Reflection 힌트 누락이다. 이를 빌드 전에 잡아내는 테스트를 작성한다.

```java
@ExtendWith(SpringExtension.class)
class ReflectionHintVerificationTest {

    @Test
    void domainClasses_haveRequiredReflectionHints() {
        RuntimeHints hints = new RuntimeHints();
        new OrderSerializationHints().registerHints(hints, getClass().getClassLoader());

        // 힌트 등록 여부를 직접 검증
        assertThat(RuntimeHintsPredicates.reflection()
            .onType(Order.class)
            .withMemberCategory(MemberCategory.INVOKE_DECLARED_CONSTRUCTORS))
            .accepts(hints);

        assertThat(RuntimeHintsPredicates.reflection()
            .onType(OrderStatus.class)
            .withMemberCategory(MemberCategory.DECLARED_FIELDS))
            .accepts(hints);
    }

    @Test
    void externalLibraryDtos_registeredForSerialization() {
        RuntimeHints hints = new RuntimeHints();
        new ThirdPartyLibraryHints().registerHints(hints, getClass().getClassLoader());

        // 서드파티 라이브러리 DTO 힌트 검증
        List.of(
            ExternalOrderDto.class,
            ExternalPaymentDto.class,
            ExternalUserDto.class
        ).forEach(clazz ->
            assertThat(RuntimeHintsPredicates.reflection().onType(clazz))
                .accepts(hints)
        );
    }
}
```

### 3단계: Native Image 바이너리 통합 테스트

CI 파이프라인의 마지막 게이트로, 실제 Native 바이너리를 빌드하고 테스트 컨테이너로 실행하여 검증한다.

```java
@Testcontainers
@Tag("native-integration")  // CI에서 선택적 실행
class NativeBinaryIntegrationTest {

    @Container
    static GenericContainer<?> nativeApp = new GenericContainer<>(
        new ImageFromDockerfile()
            .withDockerfileFromBuilder(builder -> builder
                .from("ubuntu:22.04")
                .copy("target/my-app", "/app/my-app")
                .run("chmod +x /app/my-app")
                .cmd("/app/my-app")
                .build()
            )
    )
    .withExposedPorts(8080)
    .withStartupTimeout(Duration.ofSeconds(5))  // Native Image는 수초 내 시작
    .waitingFor(Wait.forHttp("/actuator/health").forStatusCode(200));

    private String baseUrl;

    @BeforeEach
    void setUp() {
        baseUrl = "http://localhost:" + nativeApp.getMappedPort(8080);
    }

    @Test
    void startupTime_isWithinSla() {
        // 시작 시간 SLA 검증 (예: 500ms 이하)
        Long startupMs = fetchStartupTimeFromActuator(baseUrl);
        assertThat(startupMs).isLessThan(500L);
    }

    @Test
    void memoryUsage_isWithinBudget() {
        // 메모리 사용량 예산 검증
        Long heapUsedMb = fetchHeapUsageFromActuator(baseUrl);
        assertThat(heapUsedMb).isLessThan(150L);  // JVM 대비 ~60% 절감 기대
    }

    @Test
    void criticalEndpoints_returnExpectedResponses() {
        RestTemplate restTemplate = new RestTemplate();

        // 실제 바이너리 엔드포인트 E2E 검증
        ResponseEntity<String> response = restTemplate.getForEntity(
            baseUrl + "/api/orders/health", String.class);
        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
    }
}
```

### 4단계: 카오스 및 경계값 테스트

Native Image에서 특히 취약한 시나리오를 집중 테스트한다.

```java
@SpringBootTest
@ActiveProfiles("native-stress")
class NativeEdgeCaseTest {

    @Autowired
    private ObjectMapper objectMapper;

    @Test
    void jackson_deserializesGenericTypes_inNativeMode() throws Exception {
        // Generic 타입 역직렬화 - Native에서 자주 실패하는 케이스
        String json = """
            {"items": [{"id": 1, "name": "test"}], "total": 1}
            """;

        // TypeReference는 Native에서 별도 힌트 필요
        PageResult<OrderDto> result = objectMapper.readValue(
            json, new TypeReference<PageResult<OrderDto>>() {}
        );
        assertThat(result.getItems()).hasSize(1);
    }

    @Test
    void dynamicProxy_worksForTransactionalBeans() {
        // @Transactional CGLIB 프록시 동작 검증
        OrderService proxy = applicationContext.getBean(OrderService.class);
        assertThat(AopUtils.isAopProxy(proxy)).isTrue();
        assertThat(AopUtils.isCglibProxy(proxy)).isTrue();
    }

    @ParameterizedTest
    @ValueSource(strings = {"ko_KR", "en_US", "ja_JP"})
    void localeHandling_worksAcrossNativeImage(String localeStr) {
        // Locale 처리 - Native에서 locale 데이터 누락 흔함
        Locale locale = Locale.forLanguageTag(localeStr);
        MessageFormat format = new MessageFormat("{0, date}", locale);
        assertThatCode(() -> format.format(new Object[]{new Date()}))
            .doesNotThrowAnyException();
    }
}
```

---

## 주의사항 및 트레이드오프

### 테스트 비용 vs 신뢰성 매트릭스

실무에서 모든 테스트를 매 커밋마다 실행하는 것은 비현실적이다. 다음 계층화 전략을 권장한다.

```
┌─────────────────────────────────────────────────────┐
│  Level 3: Native Binary E2E (야간 빌드, PR 머지 전)    │  ← 비용 높음, 신뢰도 최고
│  빌드 시간: 5~15분 / 실행 빈도: 1회/일               │
├─────────────────────────────────────────────────────┤
│  Level 2: AOT Context + Hint 검증 (PR 단위)           │  ← 중간 비용
│  실행 시간: 2~5분 / 실행 빈도: PR마다               │
├─────────────────────────────────────────────────────┤
│  Level 1: JVM 단위/통합 테스트 (커밋마다)             │  ← 비용 낮음
│  실행 시간: 30초~2분 / 실행 빈도: 커밋마다           │
└─────────────────────────────────────────────────────┘
```

### 알려진 함정들

**1. 테스트는 JVM에서, 버그는 Native에서**
`@SpringBootTest`가 JVM 모드로 실행될 때는 AOT 힌트가 없어도 동작한다. 반드시 `spring.aot.enabled=true`를 설정하거나, Maven의 `native` 프로파일을 활용해야 AOT 경로가 활성화된다.

**2. GraalVM 버전과 Spring Boot 버전 고정**
Native Image 동작은 GraalVM 버전에 민감하다. CI에서 `graalvm-jdk:21.0.2` 처럼 마이너 버전까지 고정하지 않으면 테스트가 로컬에서는 통과하고 CI에서는 실패하는 상황이 생긴다.

**3. Tracing Agent 출력을 맹신하지 말 것**
`-agentlib:native-image-agent`로 생성한 힌트 파일은 테스트 실행 경로만 커버한다. 프로덕션에서만 실행되는 코드 경로(특정 조건의 예외 처리, 드문 코드 분기)는 반드시 수동으로 힌트를 추가하고 테스트를 보강해야 한다.

**4. 메모리 절감 수치는 워크로드 의존적**
"JVM 대비 60% 메모리 절감"은 단순 REST API 기준이다. JPA + QueryDSL + 복잡한 도메인 모델을 사용하는 경우 실제 절감은 20~30% 수준에 그치는 경우가 많다. `NativeBinaryIntegrationTest`에서 실측값으로 SLA를 설정해야 한다.

---

## 정리

Native Image 도입의 성패는 빌드 성공 여부가 아니라, **프로덕션 동작에 대한 신뢰를 확보할 수 있는 테스트 체계**를 갖추는 데 달려 있다.

핵심 체크리스트:

- [ ] AOT 컨텍스트 스모크 테스트 작성 (`spring.aot.enabled=true`)
- [ ] `RuntimeHintsPredicates`로 Reflection 힌트 등록 검증
- [ ] Generic 타입 직렬화/역직렬화 케이스 명시적 테스트
- [ ] `@Transactional`, `@Async` 등 프록시 기반 빈 동작 검증
- [ ] Testcontainers로 실제 Native 바이너리 E2E 테스트 구성
- [ ] 시작 시간, 메모리 사용량 SLA 수치화 및 자동 검증
- [ ] CI 파이프라인에서 테스트 레벨 계층화 (커밋/PR/야간)

Native Image는 서버리스, 컨테이너 환경에서 강력한 무기가 될 수 있다. 하지만 JVM이라는 안전망 없이 날아가는 만큼, 테스트가 그 안전망을 대신해야 한다. 위의 전략을 팀의 CI/CD 파이프라인에 단계적으로 통합하는 것부터 시작하길 권한다.