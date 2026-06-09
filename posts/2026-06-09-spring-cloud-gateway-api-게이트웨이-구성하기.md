# Spring Cloud Gateway API 게이트웨이 구성하기

## 개요

마이크로서비스 아키텍처가 보편화되면서 여러 서비스 앞단에 위치하는 **API 게이트웨이**의 중요성은 날로 높아지고 있습니다. 인증/인가, 라우팅, 로드밸런싱, 레이트 리미팅, 로깅 등 공통 횡단 관심사(cross-cutting concerns)를 각 서비스에 분산시키지 않고 한 곳에서 처리할 수 있기 때문입니다.

Spring 생태계에서는 과거 Netflix Zuul이 많이 사용되었지만, 현재는 **Spring Cloud Gateway**가 사실상 표준으로 자리잡았습니다. Spring WebFlux 기반의 논블로킹(Non-blocking) 아키텍처로 설계되어 높은 처리량과 낮은 지연시간을 제공하며, Spring 공식 프로젝트로 활발히 유지보수되고 있습니다.

이 글에서는 Spring Cloud Gateway의 핵심 개념을 정리하고, 실무에서 바로 활용할 수 있는 라우팅, 필터, 인증 연동 예제를 단계별로 살펴보겠습니다.

---

## 핵심 개념

Spring Cloud Gateway는 세 가지 핵심 빌딩 블록으로 구성됩니다.

### Route (라우트)
게이트웨이의 기본 단위입니다. 고유 ID, 목적지 URI, Predicate 집합, Filter 집합으로 정의됩니다. 요청이 Predicate 조건을 모두 만족할 때 해당 라우트가 선택됩니다.

### Predicate (프레디케이트)
HTTP 요청의 헤더, 경로, 메서드, 파라미터 등을 검사하여 라우트 매칭 여부를 결정합니다. Java 8의 `Predicate<ServerWebExchange>` 함수형 인터페이스를 기반으로 합니다.

### Filter (필터)
요청/응답을 변형하거나 부가 로직을 실행합니다. **GatewayFilter**와 **GlobalFilter** 두 종류가 있으며, GlobalFilter는 모든 라우트에 적용됩니다. 필터는 `pre`(요청 전)와 `post`(응답 후) 단계로 나뉩니다.

---

## 실전 예제

### 1. 의존성 설정

```xml
<!-- pom.xml -->
<dependency>
    <groupId>org.springframework.cloud</groupId>
    <artifactId>spring-cloud-starter-gateway</artifactId>
</dependency>
<dependency>
    <groupId>org.springframework.cloud</groupId>
    <artifactId>spring-cloud-starter-netflix-eureka-client</artifactId>
</dependency>
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-data-redis-reactive</artifactId>
</dependency>
```

> ⚠️ Spring Cloud Gateway는 WebFlux 기반이므로 `spring-boot-starter-web`과 함께 사용하면 충돌이 발생합니다. 반드시 제외해야 합니다.

### 2. 기본 라우팅 설정 (YAML)

```yaml
# application.yml
spring:
  application:
    name: api-gateway
  cloud:
    gateway:
      routes:
        - id: user-service
          uri: lb://USER-SERVICE          # Eureka 서비스 디스커버리 사용
          predicates:
            - Path=/api/users/**
          filters:
            - StripPrefix=1               # /api prefix 제거
            - name: RequestRateLimiter
              args:
                redis-rate-limiter.replenishRate: 10
                redis-rate-limiter.burstCapacity: 20
                key-resolver: "#{@ipKeyResolver}"

        - id: order-service
          uri: lb://ORDER-SERVICE
          predicates:
            - Path=/api/orders/**
            - Method=GET,POST
            - Header=X-Request-Source, mobile|web
          filters:
            - StripPrefix=1
            - AddRequestHeader=X-Gateway-Time, #{T(java.time.Instant).now().toString()}

      default-filters:
        - DedupeResponseHeader=Access-Control-Allow-Credentials Access-Control-Allow-Origin
```

### 3. Java Config 방식 라우팅

YAML보다 타입 안전성이 높고 조건부 라우팅 구성이 필요할 때 유용합니다.

```java
@Configuration
public class GatewayConfig {

    @Bean
    public RouteLocator customRouteLocator(RouteLocatorBuilder builder) {
        return builder.routes()
            .route("product-service", r -> r
                .path("/api/products/**")
                .and()
                .method(HttpMethod.GET, HttpMethod.POST, HttpMethod.PUT)
                .filters(f -> f
                    .stripPrefix(1)
                    .addRequestHeader("X-Service-Name", "product-service")
                    .retry(config -> config
                        .setRetries(3)
                        .setStatuses(HttpStatus.BAD_GATEWAY, HttpStatus.SERVICE_UNAVAILABLE)
                        .setBackoff(Duration.ofMillis(100), Duration.ofSeconds(1), 2, true)
                    )
                    .circuitBreaker(config -> config
                        .setName("productCircuitBreaker")
                        .setFallbackUri("forward:/fallback/product")
                    )
                )
                .uri("lb://PRODUCT-SERVICE")
            )
            .build();
    }
}
```

### 4. 커스텀 GlobalFilter 구현 (JWT 인증)

실무에서 가장 많이 구현하는 패턴인 JWT 토큰 검증 필터입니다.

```java
@Component
@Slf4j
public class JwtAuthenticationFilter implements GlobalFilter, Ordered {

    private static final String BEARER_PREFIX = "Bearer ";
    private final JwtTokenProvider jwtTokenProvider;

    // 인증 제외 경로
    private static final List<String> WHITE_LIST = List.of(
        "/api/auth/login",
        "/api/auth/refresh",
        "/api/health"
    );

    public JwtAuthenticationFilter(JwtTokenProvider jwtTokenProvider) {
        this.jwtTokenProvider = jwtTokenProvider;
    }

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        String path = exchange.getRequest().getPath().value();

        // 화이트리스트 경로는 통과
        if (WHITE_LIST.stream().anyMatch(path::startsWith)) {
            return chain.filter(exchange);
        }

        String authHeader = exchange.getRequest()
            .getHeaders()
            .getFirst(HttpHeaders.AUTHORIZATION);

        if (authHeader == null || !authHeader.startsWith(BEARER_PREFIX)) {
            return onError(exchange, HttpStatus.UNAUTHORIZED, "Missing or invalid Authorization header");
        }

        String token = authHeader.substring(BEARER_PREFIX.length());

        return jwtTokenProvider.validateToken(token)
            .flatMap(claims -> {
                String userId = claims.getSubject();
                String role = claims.get("role", String.class);

                // 하위 서비스로 사용자 정보 전달
                ServerHttpRequest mutatedRequest = exchange.getRequest()
                    .mutate()
                    .header("X-User-Id", userId)
                    .header("X-User-Role", role)
                    .build();

                log.debug("Authenticated user: {}, path: {}", userId, path);
                return chain.filter(exchange.mutate().request(mutatedRequest).build());
            })
            .onErrorResume(e -> {
                log.warn("JWT validation failed: {}", e.getMessage());
                return onError(exchange, HttpStatus.UNAUTHORIZED, "Invalid token");
            });
    }

    private Mono<Void> onError(ServerWebExchange exchange, HttpStatus status, String message) {
        ServerHttpResponse response = exchange.getResponse();
        response.setStatusCode(status);
        response.getHeaders().setContentType(MediaType.APPLICATION_JSON);

        String body = String.format("{\"error\": \"%s\", \"status\": %d}", message, status.value());
        DataBuffer buffer = response.bufferFactory().wrap(body.getBytes(StandardCharsets.UTF_8));
        return response.writeWith(Mono.just(buffer));
    }

    @Override
    public int getOrder() {
        return -100; // 높은 우선순위
    }
}
```

### 5. 커스텀 GatewayFilter - 요청/응답 로깅

```java
@Component
public class LoggingGatewayFilterFactory
        extends AbstractGatewayFilterFactory<LoggingGatewayFilterFactory.Config> {

    private static final Logger log = LoggerFactory.getLogger(LoggingGatewayFilterFactory.class);

    public LoggingGatewayFilterFactory() {
        super(Config.class);
    }

    @Override
    public GatewayFilter apply(Config config) {
        return (exchange, chain) -> {
            long startTime = System.currentTimeMillis();
            ServerHttpRequest request = exchange.getRequest();

            log.info("[{}] {} {} - requestId: {}",
                config.getLevel(),
                request.getMethod(),
                request.getURI(),
                request.getId()
            );

            return chain.filter(exchange)
                .then(Mono.fromRunnable(() -> {
                    long elapsed = System.currentTimeMillis() - startTime;
                    log.info("[{}] {} {} - status: {}, elapsed: {}ms",
                        config.getLevel(),
                        request.getMethod(),
                        request.getURI(),
                        exchange.getResponse().getStatusCode(),
                        elapsed
                    );
                }));
        };
    }

    @Getter @Setter
    public static class Config {
        private String level = "INFO";
    }
}
```

YAML에서 다음과 같이 사용합니다.

```yaml
filters:
  - name: Logging
    args:
      level: DEBUG
```

### 6. Rate Limiter 키 리졸버 설정

```java
@Configuration
public class RateLimiterConfig {

    // IP 기반 Rate Limiting
    @Bean
    public KeyResolver ipKeyResolver() {
        return exchange -> Mono.just(
            Objects.requireNonNull(
                exchange.getRequest().getRemoteAddress()
            ).getAddress().getHostAddress()
        );
    }

    // 사용자 ID 기반 Rate Limiting (JWT 필터 이후 헤더에서 추출)
    @Bean
    public KeyResolver userKeyResolver() {
        return exchange -> {
            String userId = exchange.getRequest().getHeaders().getFirst("X-User-Id");
            return Mono.just(userId != null ? userId : "anonymous");
        };
    }
}
```

---

## 주의사항 및 트레이드오프

### ⚠️ WebFlux 러닝커브
Spring Cloud Gateway는 Project Reactor(Mono/Flux) 기반입니다. 명령형 코드에 익숙한 개발자라면 비동기 스트림 프로그래밍 패러다임에 적응하는 시간이 필요합니다. 특히 필터 체인 내에서 블로킹 코드(`Thread.sleep`, JDBC 동기 호출 등)를 실행하면 **전체 이벤트 루프가 블록**되므로 절대 피해야 합니다.

### ⚠️ RequestBody 접근의 복잡성
Reactive 스트림은 기본적으로 한 번만 소비(consume)할 수 있습니다. 필터에서 Request Body를 읽고 하위 서비스로 다시 전달하려면 `ServerRequest`를 통해 Body를 캐싱해야 합니다. Spring Cloud Gateway는 이를 위해 `ModifyRequestBodyGatewayFilterFactory`를 제공하지만, 대용량 바디 처리 시 메모리 사용량에 주의해야 합니다.

### ⚠️ 분산 트레이싱 연동
게이트웨이는 요청 흐름의 시작점이므로 **Micrometer Tracing**(구 Sleuth) + Zipkin/Jaeger 연동이 필수적입니다. `spring-cloud-starter-zipkin`을 추가하면 `X-B3-TraceId` 헤더가 자동 전파됩니다.

### ⚠️ Circuit Breaker 폴백 설계
폴백 URI(`forward:/fallback/...`)를 설정할 때 게이트웨이 내부에 폴백 컨트롤러를 두거나, 별도의 폴백 서비스를 운영하는 방식을 선택해야 합니다. 폴백 응답이 원본 서비스 스펙과 호환되도록 응답 형식을 통일하는 것이 중요합니다.

### 트레이드오프: YAML vs Java Config
| 항목 | YAML | Java Config |
|------|------|-------------|
| 가독성 | 높음 | 중간 |
| 타입 안전성 | 낮음 | 높음 |
| 동적 변경 | Spring Cloud Config 필요 | 재배포 필요 |
| 조건부 라우팅 | 제한적 | 유연함 |

운영 환경에서는 두 방식을 혼합하여 정적 설정은 YAML, 복잡한 조건부 라우팅은 Java Config로 관리하는 것을 권장합니다.

---

## 정리

Spring Cloud Gateway는 단순한 리버스 프록시를 넘어, 마이크로서비스 아키텍처의 **보안, 가용성, 관측성**을 한 곳에서 제어하는 핵심 인프라 컴포넌트입니다.

이 글에서 다룬 내용을 요약하면 다음과 같습니다.

- **Route / Predicate / Filter** 세 가지 빌딩 블록 이해
- YAML과 Java Config를 혼합한 유연한 라우팅 구성
- `GlobalFilter`를 활용한 JWT 인증 처리
- Rate Limiter, Circuit Breaker, Retry를 통한 탄력성(resilience) 확보
- 커스텀 필터 팩토리로 공통 로직 모듈화

실무 적용 시에는 **성능 테스트**(Gatling, k6 등)를 통해 게이트웨이가 병목이 되지 않는지 반드시 검증하고, Actuator 엔드포인트(`/actuator/gateway/routes`)를 활용하여 런타임에 라우팅 상태를 모니터링하는 습관을 들이는 것을 권장합니다.

다음 포스팅에서는 Spring Cloud Gateway와 Keycloak을 연동한 **OAuth2 Resource Server 구성**을 다룰 예정입니다.