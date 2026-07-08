# API Gateway Rate Limiting 알고리즘 비교 (Token Bucket vs Sliding Window)

## 개요

트래픽이 폭발적으로 증가하거나 악의적인 요청이 쏟아질 때, API Gateway의 Rate Limiting은 시스템을 지키는 첫 번째 방어선이다. 하지만 "Rate Limiting을 적용한다"는 말 뒤에는 어떤 알고리즘을 선택하느냐에 따라 동작 방식, 버스트 허용 범위, 메모리 사용량이 크게 달라진다.

실무에서 가장 많이 비교되는 두 알고리즘은 **Token Bucket**과 **Sliding Window**다. 이 두 방식은 각각 다른 철학을 가지고 있으며, 잘못 선택하면 정상 트래픽을 과도하게 막거나 반대로 스파이크 트래픽에 무방비 상태가 되는 상황이 생긴다.

이 글에서는 두 알고리즘의 동작 원리를 깊이 이해하고, Spring Boot 환경에서의 실전 구현 예제와 함께 각 방식의 트레이드오프를 분석한다.

---

## 핵심 개념

### Token Bucket 알고리즘

Token Bucket은 이름 그대로 **토큰이 담긴 버킷**을 상상하면 이해하기 쉽다.

- 버킷에는 최대 `capacity`개의 토큰이 담긴다.
- 일정 주기(`refill rate`)마다 토큰이 채워진다.
- 요청이 들어올 때마다 토큰 하나를 소비한다.
- 토큰이 없으면 요청을 거절한다.

```
[버킷 상태] capacity=10, refill=2/초
T=0s  : 토큰 10개 (가득)
T=0s  : 요청 5개 → 토큰 5개 소비 → 남은 토큰: 5
T=1s  : 리필 +2 → 토큰 7개
T=1s  : 요청 8개 → 토큰 7개 소비 → 남은 토큰: 0, 거절 1개
```

핵심은 **버스트 트래픽을 일정 수준 허용**한다는 점이다. 버킷이 가득 찬 상태라면 `capacity`만큼의 요청을 순간적으로 처리할 수 있다.

### Sliding Window 알고리즘

Sliding Window는 **현재 시점 기준으로 N초 이전까지의 요청 수**를 실시간으로 추적한다.

- 고정된 윈도우(Fixed Window)의 경계 시점 문제를 해결한다.
- 현재 시각에서 윈도우 크기만큼 과거를 바라보며 요청 수를 카운트한다.
- 카운트가 한도를 초과하면 요청을 거절한다.

```
[Sliding Window] limit=10/60s
현재 시각: T=90s
윈도우 범위: T=30s ~ T=90s
이 구간의 요청 수: 7개
→ 3개까지 추가 허용
```

Fixed Window의 경우 59초에 10개, 61초에 10개 요청이 들어오면 2초 안에 20개가 처리되는 문제가 있다. Sliding Window는 이 문제를 정밀하게 해결한다.

### 두 알고리즘 비교 요약

| 항목 | Token Bucket | Sliding Window |
|---|---|---|
| 버스트 허용 | ✅ 버킷 용량만큼 허용 | ⚠️ 제한적 허용 |
| 정확도 | 중간 | 높음 |
| 메모리 사용 | 낮음 (버킷 상태만 저장) | 높음 (타임스탬프 저장) |
| 구현 복잡도 | 낮음 | 중간~높음 |
| 경계 문제 | 없음 | 없음 |
| 적합한 사용 사례 | API 호출, 스트리밍 | 로그인 시도, 결제 요청 |

---

## 실전 예제

### Redis 기반 Token Bucket 구현

Redis의 원자적 연산을 활용해 분산 환경에서도 안전하게 동작하는 Token Bucket을 구현한다.

```java
@Component
public class TokenBucketRateLimiter {

    private final RedisTemplate<String, String> redisTemplate;
    private final long capacity;
    private final long refillRate; // 초당 토큰 수

    public TokenBucketRateLimiter(RedisTemplate<String, String> redisTemplate) {
        this.redisTemplate = redisTemplate;
        this.capacity = 10L;
        this.refillRate = 2L;
    }

    public boolean tryAcquire(String clientId) {
        String script = """
            local key = KEYS[1]
            local capacity = tonumber(ARGV[1])
            local refill_rate = tonumber(ARGV[2])
            local now = tonumber(ARGV[3])
            local requested = tonumber(ARGV[4])
            
            local last_refill = tonumber(redis.call('HGET', key, 'last_refill') or now)
            local tokens = tonumber(redis.call('HGET', key, 'tokens') or capacity)
            
            -- 경과 시간에 따라 토큰 리필
            local elapsed = now - last_refill
            local new_tokens = math.min(capacity, tokens + (elapsed * refill_rate))
            
            if new_tokens >= requested then
                redis.call('HSET', key, 'tokens', new_tokens - requested)
                redis.call('HSET', key, 'last_refill', now)
                redis.call('EXPIRE', key, 3600)
                return 1
            else
                redis.call('HSET', key, 'tokens', new_tokens)
                redis.call('HSET', key, 'last_refill', now)
                redis.call('EXPIRE', key, 3600)
                return 0
            end
        """;

        String key = "rate_limit:token_bucket:" + clientId;
        long now = System.currentTimeMillis() / 1000;

        DefaultRedisScript<Long> redisScript = new DefaultRedisScript<>();
        redisScript.setScriptText(script);
        redisScript.setResultType(Long.class);

        Long result = redisTemplate.execute(
            redisScript,
            Collections.singletonList(key),
            String.valueOf(capacity),
            String.valueOf(refillRate),
            String.valueOf(now),
            "1"
        );

        return Long.valueOf(1L).equals(result);
    }
}
```

### Redis 기반 Sliding Window 구현

Sorted Set을 활용해 타임스탬프 기반의 Sliding Window를 구현한다.

```java
@Component
public class SlidingWindowRateLimiter {

    private final RedisTemplate<String, String> redisTemplate;
    private final long windowSizeSeconds;
    private final long maxRequests;

    public SlidingWindowRateLimiter(RedisTemplate<String, String> redisTemplate) {
        this.redisTemplate = redisTemplate;
        this.windowSizeSeconds = 60L;
        this.maxRequests = 10L;
    }

    public boolean tryAcquire(String clientId) {
        String script = """
            local key = KEYS[1]
            local now = tonumber(ARGV[1])
            local window_size = tonumber(ARGV[2])
            local max_requests = tonumber(ARGV[3])
            local request_id = ARGV[4]
            
            -- 윈도우 범위 밖의 오래된 요청 제거
            local window_start = now - window_size * 1000
            redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)
            
            -- 현재 윈도우 내 요청 수 확인
            local current_count = redis.call('ZCARD', key)
            
            if current_count < max_requests then
                -- 현재 요청 추가 (score=타임스탬프, value=unique_id)
                redis.call('ZADD', key, now, request_id)
                redis.call('EXPIRE', key, window_size + 1)
                return 1
            else
                return 0
            end
        """;

        String key = "rate_limit:sliding_window:" + clientId;
        long now = System.currentTimeMillis();
        String requestId = now + ":" + UUID.randomUUID();

        DefaultRedisScript<Long> redisScript = new DefaultRedisScript<>();
        redisScript.setScriptText(script);
        redisScript.setResultType(Long.class);

        Long result = redisTemplate.execute(
            redisScript,
            Collections.singletonList(key),
            String.valueOf(now),
            String.valueOf(windowSizeSeconds),
            String.valueOf(maxRequests),
            requestId
        );

        return Long.valueOf(1L).equals(result);
    }
}
```

### Spring Gateway Filter로 통합하기

```java
@Component
public class RateLimitingFilter implements GlobalFilter, Ordered {

    private final TokenBucketRateLimiter tokenBucketLimiter;
    private final SlidingWindowRateLimiter slidingWindowLimiter;

    public RateLimitingFilter(
        TokenBucketRateLimiter tokenBucketLimiter,
        SlidingWindowRateLimiter slidingWindowLimiter
    ) {
        this.tokenBucketLimiter = tokenBucketLimiter;
        this.slidingWindowLimiter = slidingWindowLimiter;
    }

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        String clientId = extractClientId(exchange);
        String path = exchange.getRequest().getPath().value();

        boolean allowed;

        // 경로별 알고리즘 분기
        if (path.startsWith("/api/auth") || path.startsWith("/api/payment")) {
            // 민감한 엔드포인트: 정밀한 Sliding Window 적용
            allowed = slidingWindowLimiter.tryAcquire(clientId);
        } else {
            // 일반 API: 버스트 허용하는 Token Bucket 적용
            allowed = tokenBucketLimiter.tryAcquire(clientId);
        }

        if (!allowed) {
            exchange.getResponse().setStatusCode(HttpStatus.TOO_MANY_REQUESTS);
            exchange.getResponse().getHeaders()
                .add("Retry-After", "60");
            return exchange.getResponse().setComplete();
        }

        return chain.filter(exchange);
    }

    private String extractClientId(ServerWebExchange exchange) {
        // API Key 또는 IP 기반 클라이언트 식별
        String apiKey = exchange.getRequest().getHeaders().getFirst("X-API-Key");
        if (apiKey != null) return "apikey:" + apiKey;

        InetSocketAddress remoteAddress = exchange.getRequest().getRemoteAddress();
        return "ip:" + (remoteAddress != null ? remoteAddress.getAddress().getHostAddress() : "unknown");
    }

    @Override
    public int getOrder() {
        return Ordered.HIGHEST_PRECEDENCE;
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. 분산 환경에서의 Race Condition

Redis Lua 스크립트를 사용하면 원자성을 보장할 수 있지만, Redis 클러스터 환경에서는 키가 여러 노드에 분산될 수 있다. 이 경우 `{clientId}` 해시 태그를 키에 포함시켜 동일 슬롯에 저장되도록 강제해야 한다.

```java
// 해시 태그를 사용해 같은 슬롯 보장
String key = "rate_limit:{" + clientId + "}:token_bucket";
```

### 2. Token Bucket의 버스트 허용 양날의 검

버스트를 허용한다는 것은 공격자도 버킷이 찰 때까지 기다렸다가 한 번에 폭발적인 요청을 보낼 수 있음을 의미한다. 민감한 엔드포인트에 Token Bucket을 단독으로 사용하는 것은 위험할 수 있다. 이런 경우 **Token Bucket + 동시 접속 수 제한(Concurrency Limit)** 을 병행하는 것이 좋다.

### 3. Sliding Window의 메모리 문제

Sorted Set에 모든 요청의 타임스탬프를 저장하는 방식은, 요청 빈도가 높을수록 메모리 사용량이 급증한다. 이를 해결하기 위해 **Sliding Window Counter** 방식을 고려할 수 있다.

```
현재 윈도우 가중치 = (현재 윈도우 요청 수) + (이전 윈도우 요청 수) × 이전 윈도우 남은 비율
```

이 방식은 정확도를 약간 희생하지만 메모리 사용량을 O(1)로 줄일 수 있다.

### 4. 알고리즘 선택 가이드

```
요청의 균일성이 중요한가?
    YES → Sliding Window
    NO  ↓
버스트 트래픽을 처리해야 하는가?
    YES → Token Bucket (capacity 조절)
    NO  → Fixed Window (단순 구현, 낮은 비용)

민감한 보안 엔드포인트인가?
    YES → Sliding Window + Concurrency Limit 병행
    NO  → Token Bucket으로 충분
```

### 5. Retry-After 헤더와 클라이언트 협력

Rate Limit 응답 시 `Retry-After` 헤더를 정확히 계산해 제공하면 클라이언트가 불필요한 재시도를 줄일 수 있다. Token Bucket의 경우 다음 토큰이 채워질 시간, Sliding Window의 경우 윈도우 내 가장 오래된 요청이 만료될 시간을 계산해 반환하는 것이 좋다.

---

## 정리

| 상황 | 추천 알고리즘 |
|---|---|
| 일반 REST API, 버스트 OK | Token Bucket |
| 로그인/인증/결제 | Sliding Window |
| 단순하고 낮은 비용 원할 때 | Fixed Window |
| 높은 정확도 + 메모리 효율 | Sliding Window Counter |

**Token Bucket**은 유연하고 구현이 간단하며 버스트 트래픽을 자연스럽게 처리한다. 반면 **Sliding Window**는 더 정밀하게 요청을 제어하지만, 완전한 구현은 메모리 비용이 따른다.

실무에서는 단일 알고리즘을 전체에 적용하기보다, 엔드포인트의 특성과 보안 요구사항에 따라 **두 알고리즘을 혼용**하는 전략이 효과적이다. Redis Lua 스크립트를 통한 원자적 처리와 해시 태그를 통한 클러스터 호환성까지 챙긴다면, 프로덕션 환경에서도 안정적으로 동작하는 Rate Limiter를 구축할 수 있다.

Rate Limiting은 단순히 요청을 막는 기능이 아니라, 서비스의 안정성과 공정한 자원 배분을 위한 핵심 인프라다. 알고리즘의 동작 원리를 정확히 이해하고 선택하는 것이 그 첫걸음이다.