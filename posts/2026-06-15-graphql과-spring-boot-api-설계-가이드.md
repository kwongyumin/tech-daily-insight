# GraphQL과 Spring Boot API 설계 가이드

## 개요

REST API는 웹 개발의 표준으로 자리 잡았지만, 클라이언트의 요구사항이 다양해지면서 오버페칭(Over-fetching)과 언더페칭(Under-fetching) 문제가 반복적으로 등장한다. GraphQL은 Facebook이 2015년에 오픈소스로 공개한 쿼리 언어로, 클라이언트가 필요한 데이터를 정확하게 요청할 수 있는 유연한 API 설계를 가능하게 한다.

Spring Boot에서는 `spring-boot-starter-graphql`을 통해 GraphQL 서버를 빠르게 구성할 수 있으며, Spring for GraphQL 프로젝트가 공식적으로 지원되어 안정적인 프로덕션 환경 구성이 가능하다. 이 글에서는 GraphQL의 핵심 개념부터 Spring Boot와의 통합, 그리고 실무에서 마주치는 트레이드오프까지 다룬다.

---

## 핵심 개념

### GraphQL 스키마 우선 설계

GraphQL의 핵심은 **스키마**다. 스키마는 API의 타입 시스템을 정의하며, 클라이언트와 서버 간의 계약(contract) 역할을 한다.

```graphql
# schema.graphqls
type Query {
    user(id: ID!): User
    users(page: Int, size: Int): UserPage
}

type Mutation {
    createUser(input: CreateUserInput!): User
    updateUser(id: ID!, input: UpdateUserInput!): User
    deleteUser(id: ID!): Boolean
}

type User {
    id: ID!
    name: String!
    email: String!
    role: UserRole!
    orders: [Order!]!
    createdAt: String!
}

type UserPage {
    content: [User!]!
    totalElements: Int!
    totalPages: Int!
}

enum UserRole {
    ADMIN
    USER
    GUEST
}

input CreateUserInput {
    name: String!
    email: String!
    role: UserRole!
}

input UpdateUserInput {
    name: String
    email: String
    role: UserRole
}
```

### Resolver의 역할

REST에서 Controller가 요청을 처리하듯, GraphQL에서는 **Resolver**가 각 필드의 데이터를 제공하는 책임을 가진다. Spring for GraphQL에서는 `@QueryMapping`, `@MutationMapping`, `@SchemaMapping` 어노테이션을 통해 Resolver를 선언한다.

### N+1 문제와 DataLoader

GraphQL의 가장 흔한 함정은 N+1 쿼리 문제다. `User` 목록을 조회할 때 각 `User`의 `orders`를 개별적으로 조회하면 N+1번의 쿼리가 발생한다. 이를 해결하기 위해 **DataLoader** 패턴을 사용한다.

---

## 실전 예제

### 의존성 설정

```xml
<!-- pom.xml -->
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-graphql</artifactId>
</dependency>
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-web</artifactId>
</dependency>
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-data-jpa</artifactId>
</dependency>
```

```yaml
# application.yml
spring:
  graphql:
    graphiql:
      enabled: true  # 개발 환경에서 GraphiQL UI 활성화
    schema:
      locations: classpath:graphql/**/
    websocket:
      path: /graphql-ws  # Subscription 지원
```

### 도메인 및 레포지토리

```java
@Entity
@Table(name = "users")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class User {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false)
    private String name;

    @Column(nullable = false, unique = true)
    private String email;

    @Enumerated(EnumType.STRING)
    private UserRole role;

    @OneToMany(mappedBy = "user", fetch = FetchType.LAZY)
    private List<Order> orders = new ArrayList<>();

    @CreatedDate
    private LocalDateTime createdAt;

    @Builder
    public User(String name, String email, UserRole role) {
        this.name = name;
        this.email = email;
        this.role = role;
    }
}
```

### Controller (Resolver)

```java
@Controller
@RequiredArgsConstructor
public class UserGraphQLController {

    private final UserService userService;
    private final OrderService orderService;

    @QueryMapping
    public User user(@Argument Long id) {
        return userService.findById(id)
                .orElseThrow(() -> new UserNotFoundException(id));
    }

    @QueryMapping
    public Page<User> users(
            @Argument int page,
            @Argument int size) {
        return userService.findAll(PageRequest.of(page, size));
    }

    @MutationMapping
    public User createUser(@Argument CreateUserInput input) {
        return userService.create(input);
    }

    @MutationMapping
    public User updateUser(@Argument Long id, @Argument UpdateUserInput input) {
        return userService.update(id, input);
    }

    @MutationMapping
    public boolean deleteUser(@Argument Long id) {
        userService.delete(id);
        return true;
    }

    // N+1 해결: DataLoader를 활용한 orders 배치 조회
    @SchemaMapping(typeName = "User", field = "orders")
    public CompletableFuture<List<Order>> orders(
            User user,
            DataLoader<Long, List<Order>> ordersDataLoader) {
        return ordersDataLoader.load(user.getId());
    }
}
```

### DataLoader 설정

```java
@Configuration
public class DataLoaderConfig {

    @Bean
    public BatchLoaderRegistry batchLoaderRegistry(OrderRepository orderRepository) {
        BatchLoaderRegistry registry = new DefaultBatchLoaderRegistry();

        registry.forTypePair(Long.class, List.class)
                .withName("ordersDataLoader")
                .registerBatchLoader((userIds, env) -> {
                    // userIds에 해당하는 모든 주문을 한 번에 조회
                    List<Order> allOrders = orderRepository
                            .findAllByUserIdIn(new ArrayList<>(userIds));

                    Map<Long, List<Order>> ordersByUserId = allOrders.stream()
                            .collect(Collectors.groupingBy(
                                    order -> order.getUser().getId()
                            ));

                    return Flux.fromIterable(userIds)
                            .map(userId -> ordersByUserId
                                    .getOrDefault(userId, Collections.emptyList()));
                });

        return registry;
    }
}
```

### 예외 처리

GraphQL은 HTTP 상태 코드 대신 응답 바디의 `errors` 필드로 에러를 전달한다. Spring for GraphQL에서는 `DataFetcherExceptionResolverAdapter`를 구현해 예외를 처리한다.

```java
@Component
public class GlobalGraphQLExceptionHandler extends DataFetcherExceptionResolverAdapter {

    @Override
    protected GraphQLError resolveToSingleError(
            Throwable ex, DataFetchingEnvironment env) {

        if (ex instanceof UserNotFoundException) {
            return GraphqlErrorBuilder.newError(env)
                    .errorType(ErrorType.NOT_FOUND)
                    .message(ex.getMessage())
                    .extensions(Map.of(
                            "errorCode", "USER_NOT_FOUND",
                            "timestamp", Instant.now().toString()
                    ))
                    .build();
        }

        if (ex instanceof AccessDeniedException) {
            return GraphqlErrorBuilder.newError(env)
                    .errorType(ErrorType.FORBIDDEN)
                    .message("접근 권한이 없습니다.")
                    .build();
        }

        // 예상하지 못한 에러는 내부 정보를 숨기고 일반 메시지 반환
        return GraphqlErrorBuilder.newError(env)
                .errorType(ErrorType.INTERNAL_ERROR)
                .message("서버 내부 오류가 발생했습니다.")
                .build();
    }
}
```

### 보안: 인증/인가 적용

```java
@Component
public class SecurityDirectiveWiring implements SchemaDirectiveWiring {

    @Override
    public GraphQLFieldDefinition onField(
            SchemaDirectiveWiringEnvironment<GraphQLFieldDefinition> env) {

        GraphQLFieldDefinition field = env.getElement();
        DataFetcher<?> originalFetcher = env.getCodeRegistry()
                .getDataFetcher(env.getFieldsContainer(), field);

        DataFetcher<?> authFetcher = dataFetchingEnvironment -> {
            Authentication auth = SecurityContextHolder.getContext().getAuthentication();
            if (auth == null || !auth.isAuthenticated()) {
                throw new AccessDeniedException("인증이 필요합니다.");
            }
            return originalFetcher.get(dataFetchingEnvironment);
        };

        env.getCodeRegistry().dataFetcher(
                env.getFieldsContainer(), field, authFetcher);

        return field;
    }
}
```

### Subscription 구현

실시간 데이터 스트리밍이 필요한 경우 `@SubscriptionMapping`을 활용한다.

```java
@Controller
@RequiredArgsConstructor
public class OrderSubscriptionController {

    private final OrderEventPublisher eventPublisher;

    @SubscriptionMapping
    public Publisher<Order> orderStatusChanged(@Argument Long userId) {
        return eventPublisher.getOrderStream()
                .filter(order -> order.getUser().getId().equals(userId));
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. 쿼리 복잡도 제한

GraphQL의 유연성은 악의적이거나 부주의한 클라이언트가 깊이 중첩된 쿼리로 서버에 과부하를 줄 수 있다는 위험을 내포한다. **쿼리 복잡도(Query Complexity)**와 **쿼리 깊이(Query Depth)** 제한을 반드시 설정하라.

```java
@Configuration
public class GraphQLConfig {

    @Bean
    public GraphQlSourceBuilderCustomizer sourceBuilderCustomizer() {
        return builder -> builder
                .configureGraphQl(graphQlBuilder -> graphQlBuilder
                        .instrumentation(new MaxQueryComplexityInstrumentation(100))
                        .instrumentation(new MaxQueryDepthInstrumentation(10)));
    }
}
```

### 2. 캐싱 전략의 차이

REST는 URL 기반이라 HTTP 캐싱(ETags, Cache-Control)을 자연스럽게 활용할 수 있다. 반면 GraphQL은 대부분 POST 방식의 단일 엔드포인트(`/graphql`)를 사용하므로, **HTTP 레벨 캐싱이 사실상 불가능**하다. 대신 아래 전략을 고려해야 한다.

- **Persisted Queries**: 쿼리를 서버에 사전 등록하고 해시로 요청 → GET 방식으로 캐싱 가능
- **애플리케이션 레벨 캐싱**: Caffeine, Redis 등을 DataLoader와 연계

### 3. REST와의 공존 전략

모든 것을 GraphQL로 전환할 필요는 없다. 파일 업로드, Webhook, 외부 파트너 연동처럼 REST가 더 적합한 케이스가 분명히 존재한다. 실무에서는 **BFF(Backend For Frontend)** 패턴으로 GraphQL을 프론트엔드 전용 게이트웨이로 두고, 내부 마이크로서비스 간 통신은 REST나 gRPC를 유지하는 방식이 효과적이다.

### 4. 모니터링과 관측 가능성

REST는 URL과 HTTP 메서드로 엔드포인트를 구분하지만, GraphQL은 단일 엔드포인트이므로 기존 APM 도구가 쿼리별 성능을 자동으로 구분하지 못할 수 있다. **Apollo Studio**나 **GraphQL Voyager** 같은 도구를 활용하거나, Spring Micrometer와 연계해 `operationName` 기반의 메트릭을 수집해야 한다.

```java
@Component
public class GraphQLMetricsInstrumentation extends SimplePerformantInstrumentation {

    private final MeterRegistry meterRegistry;

    @Override
    public InstrumentationContext<ExecutionResult> beginExecution(
            InstrumentationExecutionParameters parameters,
            InstrumentationState state) {

        String operationName = parameters.getOperation() != null
                ? parameters.getOperation()
                : "anonymous";

        Timer.Sample sample = Timer.start(meterRegistry);

        return new SimpleInstrumentationContext<>() {
            @Override
            public void onCompleted(ExecutionResult result, Throwable t) {
                sample.stop(Timer.builder("graphql.execution")
                        .tag("operation", operationName)
                        .tag("success", String.valueOf(t == null))
                        .register(meterRegistry));
            }
        };
    }
}
```

### 5. 스키마 버전 관리

REST는 `/v1/`, `/v2/` URL로 버전을 관리하지만, GraphQL은 공식적으로 **스키마 진화(Schema Evolution)** 방식을 권장한다. 필드를 삭제하는 대신 `@deprecated` 지시어를 사용하고, 새 필드를 추가하는 방식으로 하위 호환성을 유지해야 한다.

---

## 정리

GraphQL은 단순히 REST를 대체하는 기술이 아니라, **클라이언트 주도 API 설계**라는 패러다임의 전환을 의미한다. Spring for GraphQL은 스프링 생태계와의 자연스러운 통합을 제공하며, 익숙한 어노테이션 기반 개발 경험을 유지하면서 GraphQL의 강점을 활용할 수 있게 해준다.

실무 도입 시 체크리스트를 정리하면 다음과 같다.

| 항목 | 권장 사항 |
|------|-----------|
| N+1 문제 | DataLoader 패턴 의무 적용 |
| 보안 | 쿼리 복잡도/깊이 제한 필수 |
| 예외 처리 | `DataFetcherExceptionResolverAdapter` 구현 |
| 캐싱 | Persisted Queries + 애플리케이션 캐시 조합 |
| 모니터링 | operationName 기반 메트릭 수집 |
| 버전 관리 | 스키마 진화 방식 채택, `@deprecated` 활용 |

새 프로젝트를 시작하거나, 모바일·웹 클라이언트가 다양한 데이터를 유연하게 요청해야 하는 상황이라면 GraphQL은 강력한 선택지다. 다만, 팀의 학습 곡선과 기존 인프라와의 통합 비용을 충분히 고려한 후 도입 여부를 결정하길 권장한다.