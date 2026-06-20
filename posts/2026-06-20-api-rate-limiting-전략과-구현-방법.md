# API Rate Limiting 전략과 구현 방법

## 개요

트래픽이 급증하거나 악의적인 클라이언트가 API를 남용할 때, 서비스 전체가 다운되는 경험을 해본 적 있는가? API Rate Limiting은 이러한 상황을 방지하기 위한 필수적인 방어 메커니즘이다. 단순히 "초당 100건만 허용"하는 것처럼 보이지만, 실제 프로덕션 환경에서는 알고리즘 선택, 분산 환경 동기화, 사용자 경험까지 고려해야 하는 복잡한 주제다.

이 포스팅에서는 주요 Rate Limiting 알고리즘의 특성과 트레이드오프를 분석하고, Spring Boot + Redis 환경에서 실제로 동작하는 구현 예제를 통해 실무에 바로 적용할 수 있는 인사이트를 제공한다.

---

## 핵심 개념: Rate Limiting 알고리즘

### 1. Fixed Window Counter

가장 단순한 방식으로, 특정 시간 윈도우(예: 1분) 동안 요청 수를 카운트한다.

```
|--- 1분 윈도우 ---|--- 1분 윈도우 ---|
0:00           1:00           2:00
  [요청 100개]    [요청 100개]
```

**장점**: 구현이 단순하고 메모리 효율이 높다.  
**단점**: 윈도우 경계에서 버스트가 발생할 수 있다. 0:59에 100건, 1:01에 100건이 들어오면 2초 동안 200건을 처리하게 된다.

### 2. Sliding Window Log

각 요청의 타임스탬프를 로그로 저장하고, 현재 시점 기준으로 윈도우 내 요청 수를 정확하게 계산한다.

**장점**: 가장 정확한 Rate Limiting이 가능하다.  
**단점**: 모든 요청의 타임스탬프를 저장해야 하므로 메모리 사용량이 많다.

### 3. Sliding Window Counter

Fixed Window와 Sliding Window Log의 절충안이다. 현재 윈도우와 이전 윈도우의 카운터를 가중 평균으로 계산한다.

```
요청수 = 이전_윈도우_카운트 × (1 - 현재_윈도우_경과_비율) + 현재_윈도우_카운트
```

### 4. Token Bucket

버킷에 토큰이 일정 속도로 채워지고, 요청이 들어올 때마다 토큰을 소비하는 방식이다. AWS API Gateway, Stripe 등 대형 서비스에서 채택하는 알고리즘이다.

**장점**: 순간적인 버스트 트래픽을 허용하면서도 평균 처리율을 제어할 수 있다.

### 5. Leaky Bucket

요청을 큐에 쌓아두고 일정한 속도로만 처리한다. 출력 속도가 일정하여 다운스트림 시스템을 보호하는 데 유리하다.

---

## 실전 예제: Spring Boot + Redis 구현

### 의존성 설정

```xml
<!-- pom.xml -->
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-data-redis</artifactId>
</dependency>
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-aop</artifactId>
</dependency>
```

### Sliding Window Counter 구현

Redis의 원자적 연산을 활용하여 분산 환경에서도 안전하게 동작하는 Sliding Window Counter를 구현한다.

```java
@Service
@RequiredArgsConstructor
public class RateLimiterService {

    private final StringRedisTemplate redisTemplate;

    /**
     * Sliding Window Counter 방식의 Rate Limiting
     * @param key     식별자 (IP, userId 등)
     * @param limit   허용 요청 수
     * @param windowSeconds 윈도우 크기 (초)
     */
    public boolean isAllowed(String key, int limit, long windowSeconds) {
        long currentTime = System.currentTimeMillis() / 1000; // 초 단위
        long currentWindow = currentTime / windowSeconds;
        long previousWindow = currentWindow - 1;

        String currentKey = String.format("rl:%s:%d", key, currentWindow);
        String previousKey = String.format("rl:%s:%d", key, previousWindow);

        // Lua 스크립트로 원자적 실행 보장
        String luaScript = """
            local current_key = KEYS[1]
            local previous_key = KEYS[2]
            local limit = tonumber(ARGV[1])
            local window_seconds = tonumber(ARGV[2])
            local elapsed_ratio = tonumber(ARGV[3])
            
            local current_count = tonumber(redis.call('GET', current_key) or '0')
            local previous_count = tonumber(redis.call('GET', previous_key) or '0')
            
            -- Sliding Window 계산
            local weighted_count = previous_count * (1 - elapsed_ratio) + current_count
            
            if weighted_count >= limit then
                return 0
            end
            
            -- 현재 윈도우 카운트 증가
            redis.call('INCR', current_key)
            redis.call('EXPIRE', current_key, window_seconds * 2)
            
            return 1
            """;

        double elapsedRatio = (currentTime % windowSeconds) / (double) windowSeconds;

        RedisScript<Long> script = RedisScript.of(luaScript, Long.class);
        Long result = redisTemplate.execute(
            script,
            List.of(currentKey, previousKey),
            String.valueOf(limit),
            String.valueOf(windowSeconds),
            String.format("%.4f", elapsedRatio)
        );

        return result != null && result == 1L;
    }
}
```

### 커스텀 어노테이션 기반 AOP 적용

선언적으로 Rate Limiting을 적용할 수 있도록 AOP를 활용한다.

```java
// 어노테이션 정의
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
public @interface RateLimit {
    int limit() default 100;
    long windowSeconds() default 60;
    String keyPrefix() default "";
    RateLimitKeyType keyType() default RateLimitKeyType.IP;
}

public enum RateLimitKeyType {
    IP, USER_ID, API_KEY
}
```

```java
// AOP Aspect 구현
@Aspect
@Component
@RequiredArgsConstructor
@Slf4j
public class RateLimitAspect {

    private final RateLimiterService rateLimiterService;
    private final HttpServletRequest request;

    @Around("@annotation(rateLimit)")
    public Object around(ProceedingJoinPoint joinPoint, RateLimit rateLimit) throws Throwable {
        String key = resolveKey(rateLimit);

        if (!rateLimiterService.isAllowed(key, rateLimit.limit(), rateLimit.windowSeconds())) {
            log.warn("Rate limit exceeded for key: {}", key);
            throw new RateLimitExceededException(
                String.format("요청 한도를 초과했습니다. %d초 후 다시 시도해주세요.", rateLimit.windowSeconds())
            );
        }

        return joinPoint.proceed();
    }

    private String resolveKey(RateLimit rateLimit) {
        String prefix = rateLimit.keyPrefix().isEmpty()
            ? "default"
            : rateLimit.keyPrefix();

        return switch (rateLimit.keyType()) {
            case IP -> prefix + ":" + getClientIp();
            case USER_ID -> prefix + ":" + getCurrentUserId();
            case API_KEY -> prefix + ":" + getApiKey();
        };
    }

    private String getClientIp() {
        String xForwardedFor = request.getHeader("X-Forwarded-For");
        if (xForwardedFor != null && !xForwardedFor.isEmpty()) {
            return xForwardedFor.split(",")[0].trim();
        }
        return request.getRemoteAddr();
    }
    
    // getCurrentUserId(), getApiKey() 구현 생략
}
```

```java
// 컨트롤러 적용 예시
@RestController
@RequestMapping("/api/v1")
public class UserController {

    @GetMapping("/users")
    @RateLimit(limit = 60, windowSeconds = 60, keyType = RateLimitKeyType.IP)
    public ResponseEntity<List<User>> getUsers() {
        // ...
    }

    @PostMapping("/messages")
    @RateLimit(limit = 10, windowSeconds = 60, keyType = RateLimitKeyType.USER_ID, keyPrefix = "msg")
    public ResponseEntity<Message> sendMessage(@RequestBody MessageRequest request) {
        // ...
    }
}
```

### 예외 처리 및 응답 헤더

RFC 6585 표준을 따라 클라이언트에 적절한 정보를 제공한다.

```java
@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(RateLimitExceededException.class)
    public ResponseEntity<ErrorResponse> handleRateLimitExceeded(
            RateLimitExceededException ex,
            HttpServletRequest request) {

        HttpHeaders headers = new HttpHeaders();
        headers.add("X-RateLimit-Limit", "100");
        headers.add("X-RateLimit-Remaining", "0");
        headers.add("X-RateLimit-Reset", String.valueOf(System.currentTimeMillis() / 1000 + 60));
        headers.add("Retry-After", "60");

        return ResponseEntity
            .status(HttpStatus.TOO_MANY_REQUESTS) // 429
            .headers(headers)
            .body(new ErrorResponse("RATE_LIMIT_EXCEEDED", ex.getMessage()));
    }
}
```

### Spring Cloud Gateway 활용 (API Gateway 레벨)

마이크로서비스 환경에서는 각 서비스보다 Gateway 레벨에서 처리하는 것이 효율적이다.

```yaml
# application.yml
spring:
  cloud:
    gateway:
      routes:
        - id: user-service
          uri: lb://user-service
          predicates:
            - Path=/api/v1/users/**
          filters:
            - name: RequestRateLimiter
              args:
                redis-rate-limiter.replenishRate: 10    # 초당 토큰 보충 수
                redis-rate-limiter.burstCapacity: 20    # 버킷 최대 용량
                redis-rate-limiter.requestedTokens: 1   # 요청당 소비 토큰
                key-resolver: "#{@ipKeyResolver}"
```

```java
@Bean
public KeyResolver ipKeyResolver() {
    return exchange -> Mono.just(
        Objects.requireNonNull(exchange.getRequest().getRemoteAddress())
               .getAddress()
               .getHostAddress()
    );
}
```

---

## 주의사항 및 트레이드오프

### 1. 분산 환경에서의 Race Condition

Redis 단일 인스턴스를 사용하더라도 `GET → 조건 확인 → INCR` 순서의 비원자적 연산은 Race Condition을 유발한다. 반드시 **Lua 스크립트** 또는 **Redis Transaction(MULTI/EXEC)**을 활용해 원자성을 보장해야 한다.

Redis Cluster 환경에서는 동일한 키가 같은 슬롯에 위치하도록 **해시 태그**를 적극 활용하자.

```java
// 해시 태그 적용 예시
String key = String.format("rl:{%s}:%d", userId, currentWindow);
//                              ^^^^ 해시 태그로 동일 슬롯 보장
```

### 2. Redis 장애 시 Fallback 전략

Rate Limiter의 Redis가 다운되면 어떻게 할 것인가? 크게 두 가지 선택지가 있다.

- **Fail Open**: Redis 연결 실패 시 모든 요청을 허용 → 서비스 가용성 우선
- **Fail Closed**: Redis 연결 실패 시 모든 요청을 차단 → 보안 우선

실무에서는 대부분 **Fail Open** 정책을 채택하되, 회로 차단기(Circuit Breaker)와 로컬 메모리 기반의 임시 Rate Limiter를 조합하는 방식을 권장한다.

```java
public boolean isAllowed(String key, int limit, long windowSeconds) {
    try {
        return checkRedis(key, limit, windowSeconds);
    } catch (RedisException e) {
        log.error("Redis unavailable, falling back to local limiter", e);
        return localRateLimiter.isAllowed(key, limit); // Caffeine Cache 활용
    }
}
```

### 3. 키 설계 전략

Rate Limiting의 효과는 **키를 어떻게 설계하느냐**에 크게 달라진다.

| 키 타입 | 적합한 상황 | 주의사항 |
|---|---|---|
| IP 기반 | 미인증 API, 공개 엔드포인트 | NAT 환경에서 여러 사용자가 같은 IP 공유 |
| User ID 기반 | 인증된 API | 토큰 탈취 시 피해자가 차단될 수 있음 |
| API Key 기반 | B2B API, 파트너사별 할당량 관리 | API Key 관리 체계 필요 |
| 엔드포인트 조합 | 특정 기능 보호 (로그인, 결제 등) | 키 개수 증가로 메모리 관리 주의 |

### 4. 사용자 경험을 위한 응답 설계

429 응답 시 `Retry-After` 헤더와 명확한 에러 메시지를 제공해야 한다. 클라이언트가 언제 재시도해야 하는지 알 수 없으면 불필요한 폴링이 발생하여 오히려 부하가 증가한다.

### 5. 알고리즘별 선택 기준

- **정확성이 중요**하고 메모리 여유가 있다면 → Sliding Window Log
- **버스트 트래픽을 허용**하되 평균 처리율을 제어하려면 → Token Bucket
- **일정한 처리 속도**가 필요한 내부 서비스 간 통신 → Leaky Bucket
- **구현 단순성과 성능** 모두 필요하다면 → Sliding Window Counter

---

## 정리

API Rate Limiting은 단순히 숫자를 세는 기능이 아니라, 서비스의 안정성과 공정한 자원 분배를 위한 아키텍처적 결정이다.

핵심 요약:
- **알고리즘 선택**: 비즈니스 요구사항(버스트 허용 여부, 정확성, 메모리)에 맞게 선택
- **원자성 보장**: Lua 스크립트로 분산 환경의 Race Condition 방지
- **Fallback 설계**: Redis 장애 시 서비스가 중단되지 않도록 대비
- **표준 응답 헤더**: `429 Too Many Requests`, `Retry-After` 헤더로 클라이언트 친화적 설계
- **Gateway 레벨 적용**: 마이크로서비스 환경에서는 중앙화된 Gateway에서 처리

실무 적용 시에는 먼저 현재 트래픽 패턴을 분석하고, 임계값을 점진적으로 조정하는 방식을 권장한다. 너무 엄격한 Rate Limit은 정상 사용자까지 차단할 수 있으며, 너무 느슨한 설정은 Rate Limiting의 의미 자체를 잃게 만든다. 모니터링과 알람 체계를 함께 구축하여 지속적으로 튜닝하는 것이 중요하다.