# API Gateway 패턴과 서비스 라우팅 전략

## 개요

마이크로서비스 아키텍처가 보편화되면서 수십, 수백 개의 서비스를 운영하는 환경이 일반화되었다. 클라이언트가 각 서비스의 엔드포인트를 직접 알고 호출하는 방식은 유지보수 측면에서 명백한 한계를 갖는다. 인증/인가, 로깅, 트래픽 제어, SSL 종단 처리 같은 공통 관심사를 서비스마다 중복 구현해야 하고, 클라이언트는 내부 토폴로지 변경에 직접 영향을 받는다.

**API Gateway**는 이 문제를 해결하는 핵심 패턴이다. 클라이언트와 백엔드 서비스 사이의 단일 진입점 역할을 하며, 라우팅, 인증, 속도 제한, 로드밸런싱 등을 중앙에서 처리한다. 이 글에서는 API Gateway의 핵심 개념과 함께, 실무에서 자주 쓰이는 서비스 라우팅 전략을 Spring Cloud Gateway를 중심으로 깊이 있게 살펴본다.

---

## 핵심 개념

### API Gateway의 주요 책임

API Gateway는 단순한 리버스 프록시가 아니다. 실무에서 Gateway가 담당하는 핵심 역할은 다음과 같다.

| 역할 | 설명 |
|---|---|
| **라우팅** | 요청 경로, 헤더, 메서드 기반으로 적절한 백엔드 서비스로 전달 |
| **인증/인가** | JWT 검증, OAuth2 토큰 처리 등 보안 게이트 역할 |
| **로드밸런싱** | 서비스 인스턴스 간 트래픽 분산 |
| **서킷 브레이커** | 장애 전파 방지 및 폴백 처리 |
| **속도 제한(Rate Limiting)** | API 남용 방지 |
| **요청/응답 변환** | 헤더 추가/제거, 경로 재작성 등 |
| **로깅 & 모니터링** | 중앙 집중식 트래픽 추적 |

### Backend for Frontend (BFF) 패턴

BFF는 API Gateway의 확장 패턴으로, 클라이언트 유형(Web, Mobile, Third-party)에 따라 별도의 Gateway를 두는 전략이다. 모바일에서는 데이터 최적화가 필요하고, 웹은 더 풍부한 데이터를 요구하는 경우처럼 클라이언트별 요구사항이 다를 때 효과적이다.

```
[Mobile App]  →  [Mobile BFF]  →  [User Service]
[Web App]     →  [Web BFF]     →  [Order Service]
[Partner API] →  [Partner GW]  →  [Product Service]
```

### 라우팅 전략의 종류

라우팅 전략은 크게 네 가지 기준으로 분류된다.

1. **Path-based Routing**: URL 경로 기반 라우팅 (`/api/users/**` → User Service)
2. **Header-based Routing**: 요청 헤더 기반 (`X-Version: v2` → 새 버전 서비스)
3. **Weight-based Routing**: 가중치 기반 트래픽 분산 (카나리 배포)
4. **Predicate Routing**: 복합 조건 조합 라우팅

---

## 실전 예제

### 환경 구성

Spring Cloud Gateway를 사용한다. `build.gradle`에 의존성을 추가한다.

```groovy
dependencies {
    implementation 'org.springframework.cloud:spring-cloud-starter-gateway'
    implementation 'org.springframework.cloud:spring-cloud-starter-netflix-eureka-client'
    implementation 'org.springframework.cloud:spring-cloud-starter-circuitbreaker-reactor-resilience4j'
    implementation 'org.springframework.boot:spring-boot-starter-actuator'
    implementation 'io.micrometer:micrometer-tracing-bridge-brave'
}
```

### 1. Path-based Routing 설정

`application.yml`로 선언적으로 라우팅을 구성한다.

```yaml
spring:
  cloud:
    gateway:
      routes:
        - id: user-service
          uri: lb://USER-SERVICE        # Eureka 서비스 디스커버리 연동
          predicates:
            - Path=/api/users/**
          filters:
            - StripPrefix=1             # /api 제거 후 전달
            - AddRequestHeader=X-Gateway-Source, api-gateway
            - name: RequestRateLimiter
              args:
                redis-rate-limiter.replenishRate: 100
                redis-rate-limiter.burstCapacity: 200

        - id: order-service
          uri: lb://ORDER-SERVICE
          predicates:
            - Path=/api/orders/**
            - Method=GET,POST
          filters:
            - StripPrefix=1
            - name: CircuitBreaker
              args:
                name: orderServiceCB
                fallbackUri: forward:/fallback/orders
```

### 2. Java Config 기반 라우팅

복잡한 조건이나 동적 라우팅이 필요한 경우, Java Config 방식이 더 유연하다.

```java
@Configuration
public class GatewayRouteConfig {

    @Bean
    public RouteLocator customRouteLocator(RouteLocatorBuilder builder) {
        return builder.routes()
            // Header 기반 버전 라우팅
            .route("user-service-v2", r -> r
                .path("/api/users/**")
                .and()
                .header("X-API-Version", "v2")
                .filters(f -> f
                    .stripPrefix(1)
                    .rewritePath("/api/users/(?<segment>.*)", "/v2/users/${segment}")
                    .addRequestHeader("X-Forwarded-By", "api-gateway")
                    .retry(config -> config
                        .setRetries(3)
                        .setStatuses(HttpStatus.SERVICE_UNAVAILABLE)
                        .setMethods(HttpMethod.GET)
                    )
                )
                .uri("lb://USER-SERVICE-V2")
            )
            // Weight 기반 카나리 배포
            .route("product-service-stable", r -> r
                .path("/api/products/**")
                .and()
                .weight("product-group", 90)   // 90% 트래픽
                .uri("lb://PRODUCT-SERVICE")
            )
            .route("product-service-canary", r -> r
                .path("/api/products/**")
                .and()
                .weight("product-group", 10)   // 10% 트래픽 (카나리)
                .uri("lb://PRODUCT-SERVICE-CANARY")
            )
            .build();
    }
}
```

### 3. 커스텀 Global Filter로 인증 처리

JWT 검증을 Gateway 레이어에서 중앙 처리하는 예제다.

```java
@Component
@Slf4j
public class AuthenticationGlobalFilter implements GlobalFilter, Ordered {

    private static final String AUTH_HEADER = "Authorization";
    private static final String BEARER_PREFIX = "Bearer ";

    // 인증 불필요 경로 목록
    private static final List<String> WHITE_LIST = List.of(
        "/api/auth/login",
        "/api/auth/refresh",
        "/actuator/health"
    );

    private final JwtTokenProvider jwtTokenProvider;

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        ServerHttpRequest request = exchange.getRequest();
        String path = request.getPath().value();

        // 화이트리스트 경로는 인증 건너뜀
        if (isWhitelisted(path)) {
            return chain.filter(exchange);
        }

        String authHeader = request.getHeaders().getFirst(AUTH_HEADER);

        if (authHeader == null || !authHeader.startsWith(BEARER_PREFIX)) {
            return unauthorizedResponse(exchange, "Missing or invalid Authorization header");
        }

        String token = authHeader.substring(BEARER_PREFIX.length());

        return jwtTokenProvider.validateToken(token)
            .flatMap(claims -> {
                // 검증된 사용자 정보를 다운스트림 서비스에 헤더로 전달
                ServerHttpRequest mutatedRequest = request.mutate()
                    .header("X-User-Id", claims.getSubject())
                    .header("X-User-Role", claims.get("role", String.class))
                    .build();

                log.debug("Authenticated request: userId={}, path={}", 
                    claims.getSubject(), path);

                return chain.filter(exchange.mutate().request(mutatedRequest).build());
            })
            .onErrorResume(e -> {
                log.warn("JWT validation failed: {}", e.getMessage());
                return unauthorizedResponse(exchange, "Invalid token");
            });
    }

    private boolean isWhitelisted(String path) {
        return WHITE_LIST.stream().anyMatch(path::startsWith);
    }

    private Mono<Void> unauthorizedResponse(ServerWebExchange exchange, String message) {
        ServerHttpResponse response = exchange.getResponse();
        response.setStatusCode(HttpStatus.UNAUTHORIZED);
        response.getHeaders().setContentType(MediaType.APPLICATION_JSON);

        byte[] body = ("{\"error\": \"" + message + "\"}").getBytes(StandardCharsets.UTF_8);
        DataBuffer buffer = response.bufferFactory().wrap(body);
        return response.writeWith(Mono.just(buffer));
    }

    @Override
    public int getOrder() {
        return -100; // 높은 우선순위로 실행
    }
}
```

### 4. 서킷 브레이커 및 폴백 처리

장애 전파를 막는 폴백 컨트롤러를 구성한다.

```java
@RestController
@RequestMapping("/fallback")
@Slf4j
public class FallbackController {

    @GetMapping("/orders")
    public ResponseEntity<Map<String, Object>> ordersFallback(ServerWebExchange exchange) {
        log.warn("Order service circuit breaker activated");

        Map<String, Object> response = new LinkedHashMap<>();
        response.put("status", "degraded");
        response.put("message", "주문 서비스가 일시적으로 응답하지 않습니다. 잠시 후 다시 시도해주세요.");
        response.put("timestamp", Instant.now().toString());

        return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).body(response);
    }
}
```

서킷 브레이커 세부 설정은 `application.yml`에서 관리한다.

```yaml
resilience4j:
  circuitbreaker:
    instances:
      orderServiceCB:
        slidingWindowSize: 10
        minimumNumberOfCalls: 5
        failureRateThreshold: 50
        waitDurationInOpenState: 10s
        permittedNumberOfCallsInHalfOpenState: 3
```

---

## 주의사항 및 트레이드오프

### 단일 장애 지점(SPOF) 문제

API Gateway는 모든 트래픽이 통과하는 중앙 집중 구조이므로, Gateway 자체가 단일 장애 지점이 될 수 있다. **반드시 고가용성 구성**이 필요하다.

- 최소 2개 이상의 Gateway 인스턴스를 운영한다.
- 앞단에 L4/L7 로드밸런서(NLB, ALB)를 배치한다.
- 헬스체크 엔드포인트(`/actuator/health`)를 주기적으로 모니터링한다.

### 성능 오버헤드

Gateway를 경유하면 필연적으로 추가 네트워크 홉이 발생한다. 다음 사항을 고려해야 한다.

- Spring Cloud Gateway는 **Netty 기반 논블로킹** 구조로, 처리량 측면에서 Zuul 1.x보다 유리하다.
- Gateway에서 수행하는 로직(JWT 파싱, 암호화 등)이 무거울수록 레이턴시가 증가한다. 최대한 경량화하라.
- 인증 결과를 **캐싱(Redis)**하면 매 요청마다 검증 비용을 줄일 수 있다.

### 라우팅 복잡도 폭증

서비스가 많아질수록 라우팅 규칙이 기하급수적으로 복잡해진다. **동적 라우팅**과 **서비스 디스커버리** 조합을 활용하면 관리 부담을 줄일 수 있다.

```yaml
spring:
  cloud:
    gateway:
      discovery:
        locator:
          enabled: true          # Eureka 기반 자동 라우팅 활성화
          lower-case-service-id: true
```

단, 자동 라우팅은 모든 서비스를 외부에 노출할 위험이 있으므로, 명시적 라우팅과 병행하여 필요한 서비스만 노출하도록 관리해야 한다.

### 책임 과다(God Gateway) 안티패턴

Gateway에 비즈니스 로직을 넣는 경우를 종종 본다. 데이터 집계, 복잡한 오케스트레이션 로직을 Gateway에 구현하면 유지보수 악몽이 시작된다. **Gateway는 횡단 관심사(Cross-cutting Concerns)만 처리**해야 한다. 데이터 집계가 필요하다면 BFF 또는 별도 Aggregation 서비스를 두는 것이 바람직하다.

### 버전 관리 전략

API 버전 관리는 Gateway 레이어에서 처리하는 것이 일반적이다. URL 기반(`/v1/`, `/v2/`)과 헤더 기반(`X-API-Version: 2`) 중 어느 방식을 선택하든 일관성을 유지해야 한다. 버전별 라우팅 규칙이 늘어나면 반드시 **주기적인 구버전 폐기(Deprecation) 정책**을 함께 운영해야 한다.

---

## 정리

API Gateway는 마이크로서비스 환경에서 없어서는 안 될 핵심 인프라 컴포넌트다. 올바르게 설계된 Gateway는 서비스 간 결합도를 낮추고, 공통 관심사를 중앙 집중화하며, 클라이언트 경험을 일관되게 유지한다.

이 글에서 다룬 핵심 포인트를 정리하면 다음과 같다.

- **Path, Header, Weight 기반 라우팅**을 조합하면 카나리 배포, API 버전 관리, A/B 테스트를 Gateway 수준에서 우아하게 처리할 수 있다.
- **Global Filter**를 통해 인증/인가, 로깅, 요청 변환 등의 공통 처리를 중앙화한다.
- **서킷 브레이커와 폴백**은 필수다. 장애 격리 없는 Gateway는 장애 전파 가속기가 될 수 있다.
- Gateway는 **단일 장애 지점**이 되므로, 고가용성 구성과 모니터링은 선택이 아닌 필수다.
- **God Gateway 안티패턴**을 경계하라. 비즈니스 로직은 Gateway가 아닌 서비스에 있어야 한다.

실무에서 Kong, AWS API Gateway, Nginx 기반 솔루션도 많이 사용되지만, Spring 생태계에 이미 익숙한 팀이라면 Spring Cloud Gateway의 유연성과 커스터마이징 용이성은 분명한 강점이다. 서비스 규모와 팀 역량에 맞는 도구를 선택하되, Gateway가 담당해야 할 책임의 경계를 처음부터 명확히 정의하는 것이 장기적으로 건강한 아키텍처를 유지하는 핵심이다.