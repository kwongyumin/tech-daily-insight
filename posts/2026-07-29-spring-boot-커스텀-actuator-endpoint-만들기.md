# Spring Boot 커스텀 Actuator Endpoint 만들기

## 개요

Spring Boot Actuator는 애플리케이션의 상태를 모니터링하고 관리하기 위한 프로덕션 레디 기능들을 제공합니다. `/actuator/health`, `/actuator/metrics`, `/actuator/info` 같은 기본 엔드포인트는 익숙하게 사용하고 있을 텐데, 실무에서는 이것만으로 부족한 경우가 종종 생깁니다.

예를 들어, 외부 서비스와의 연결 상태를 즉시 점검하고 싶거나, 캐시를 수동으로 초기화하거나, 실행 중인 배치 잡의 현황을 확인하고 싶을 때 커스텀 Actuator Endpoint가 진가를 발휘합니다. 이 글에서는 `@Endpoint` 어노테이션을 활용해 실무에서 바로 활용 가능한 커스텀 엔드포인트를 만드는 방법을 단계별로 설명합니다.

---

## 핵심 개념

### Actuator Endpoint의 동작 방식

Spring Boot Actuator는 내부적으로 `EndpointDiscoverer`가 `@Endpoint`, `@WebEndpoint`, `@JmxEndpoint` 등의 어노테이션이 붙은 빈을 스캔하여 등록합니다. 각 엔드포인트는 **Operation**으로 구성되며, HTTP 메서드와 매핑 어노테이션의 대응 관계는 다음과 같습니다.

| 어노테이션 | HTTP 메서드 | 설명 |
|---|---|---|
| `@ReadOperation` | GET | 데이터 조회 |
| `@WriteOperation` | POST | 상태 변경 |
| `@DeleteOperation` | DELETE | 리소스 삭제 |

### 주요 어노테이션 종류

- **`@Endpoint`**: HTTP와 JMX 모두에서 노출되는 기술 중립적인 엔드포인트
- **`@WebEndpoint`**: HTTP를 통해서만 노출되는 엔드포인트
- **`@JmxEndpoint`**: JMX를 통해서만 노출되는 엔드포인트
- **`@EndpointWebExtension`**: 기존 엔드포인트를 웹 특화 방식으로 확장

실무에서는 대부분 HTTP 기반으로 사용하므로 `@Endpoint`나 `@WebEndpoint`를 주로 사용하게 됩니다.

---

## 실전 예제

### 예제 1: 외부 서비스 연결 상태 점검 엔드포인트

가장 흔한 사용 사례입니다. 서비스에서 의존하는 외부 API나 서드파티 시스템의 연결 상태를 한 번에 확인하는 엔드포인트입니다.

```java
@Component
@Endpoint(id = "external-services")
public class ExternalServiceEndpoint {

    private final Map<String, ExternalServiceChecker> serviceCheckers;

    public ExternalServiceEndpoint(Map<String, ExternalServiceChecker> serviceCheckers) {
        this.serviceCheckers = serviceCheckers;
    }

    @ReadOperation
    public Map<String, ServiceStatus> checkAll() {
        return serviceCheckers.entrySet().stream()
            .collect(Collectors.toMap(
                Map.Entry::getKey,
                entry -> checkService(entry.getValue())
            ));
    }

    @ReadOperation
    public ServiceStatus checkSingle(@Selector String serviceName) {
        ExternalServiceChecker checker = serviceCheckers.get(serviceName);
        if (checker == null) {
            throw new IllegalArgumentException("Unknown service: " + serviceName);
        }
        return checkService(checker);
    }

    private ServiceStatus checkService(ExternalServiceChecker checker) {
        long start = System.currentTimeMillis();
        try {
            boolean available = checker.ping();
            long latency = System.currentTimeMillis() - start;
            return ServiceStatus.of(available ? "UP" : "DOWN", latency, null);
        } catch (Exception e) {
            long latency = System.currentTimeMillis() - start;
            return ServiceStatus.of("ERROR", latency, e.getMessage());
        }
    }

    public record ServiceStatus(String status, long latencyMs, String errorMessage) {
        public static ServiceStatus of(String status, long latencyMs, String errorMessage) {
            return new ServiceStatus(status, latencyMs, errorMessage);
        }
    }
}
```

위 코드에서 `@Selector`는 경로 변수처럼 동작합니다. `/actuator/external-services/payment-api` 요청 시 `serviceName`에 `payment-api`가 바인딩됩니다.

---

### 예제 2: 캐시 관리 엔드포인트 (쓰기/삭제 작업 포함)

운영 중 특정 캐시만 선택적으로 초기화해야 할 때 유용합니다.

```java
@Component
@Endpoint(id = "cache-manager")
public class CacheManagerEndpoint {

    private final CacheManager cacheManager;

    public CacheManagerEndpoint(CacheManager cacheManager) {
        this.cacheManager = cacheManager;
    }

    @ReadOperation
    public Map<String, CacheInfo> getCacheStats() {
        return cacheManager.getCacheNames().stream()
            .collect(Collectors.toMap(
                name -> name,
                name -> {
                    Cache cache = cacheManager.getCache(name);
                    return buildCacheInfo(name, cache);
                }
            ));
    }

    @WriteOperation
    public Map<String, String> evictCache(@Selector String cacheName,
                                          @Nullable String key) {
        Cache cache = cacheManager.getCache(cacheName);
        if (cache == null) {
            return Map.of("result", "CACHE_NOT_FOUND", "cacheName", cacheName);
        }

        if (key != null && !key.isBlank()) {
            cache.evict(key);
            return Map.of("result", "EVICTED_KEY", "cacheName", cacheName, "key", key);
        } else {
            cache.clear();
            return Map.of("result", "CLEARED", "cacheName", cacheName);
        }
    }

    @DeleteOperation
    public void clearAllCaches() {
        cacheManager.getCacheNames()
            .forEach(name -> {
                Cache cache = cacheManager.getCache(name);
                if (cache != null) {
                    cache.clear();
                }
            });
    }

    private CacheInfo buildCacheInfo(String name, Cache cache) {
        // CaffeineCache 등 구체 타입으로 캐스팅하여 통계 수집 가능
        return new CacheInfo(name, cache != null ? "ACTIVE" : "UNAVAILABLE");
    }

    public record CacheInfo(String name, String status) {}
}
```

이 엔드포인트를 통해 `POST /actuator/cache-manager/product-cache?key=12345` 요청으로 특정 상품 캐시 항목만 무효화할 수 있습니다.

---

### 예제 3: 피처 플래그 관리 엔드포인트

A/B 테스트나 카나리 배포 환경에서 런타임에 피처 플래그를 토글하는 예제입니다.

```java
@Component
@Endpoint(id = "feature-flags")
public class FeatureFlagEndpoint {

    private final ConcurrentHashMap<String, Boolean> flags = new ConcurrentHashMap<>();

    // 초기 플래그는 외부 설정이나 DB에서 로드한다고 가정
    public FeatureFlagEndpoint(@Value("${feature.flags.new-payment-flow:false}") boolean newPaymentFlow,
                                @Value("${feature.flags.dark-mode:true}") boolean darkMode) {
        flags.put("new-payment-flow", newPaymentFlow);
        flags.put("dark-mode", darkMode);
    }

    @ReadOperation
    public Map<String, Boolean> getAllFlags() {
        return Collections.unmodifiableMap(flags);
    }

    @ReadOperation
    public Map<String, Object> getFlag(@Selector String flagName) {
        if (!flags.containsKey(flagName)) {
            return Map.of("error", "Flag not found: " + flagName);
        }
        return Map.of("flag", flagName, "enabled", flags.get(flagName));
    }

    @WriteOperation
    public Map<String, Object> updateFlag(@Selector String flagName,
                                           boolean enabled) {
        if (!flags.containsKey(flagName)) {
            return Map.of("result", "FLAG_NOT_FOUND", "flag", flagName);
        }
        boolean previous = flags.put(flagName, enabled);
        return Map.of(
            "result", "UPDATED",
            "flag", flagName,
            "previous", previous,
            "current", enabled,
            "updatedAt", Instant.now().toString()
        );
    }
}
```

`POST /actuator/feature-flags/new-payment-flow` 요청 시 Body에 `{"enabled": true}`를 전달하면 런타임에 즉시 반영됩니다.

---

### 보안 설정: 엔드포인트 접근 제어

커스텀 엔드포인트는 반드시 접근 제어를 해야 합니다. `application.yml` 설정과 Spring Security를 함께 사용합니다.

```yaml
management:
  endpoints:
    web:
      exposure:
        include: health, info, external-services, cache-manager, feature-flags
  endpoint:
    cache-manager:
      enabled: true
    feature-flags:
      enabled: true
  server:
    port: 8081  # 관리 포트 분리 권장
```

```java
@Configuration
@EnableWebSecurity
public class ActuatorSecurityConfig {

    @Bean
    @Order(1)
    public SecurityFilterChain actuatorSecurityFilterChain(HttpSecurity http) throws Exception {
        http
            .securityMatcher(EndpointRequest.toAnyEndpoint())
            .authorizeHttpRequests(auth -> auth
                .requestMatchers(EndpointRequest.to(HealthEndpoint.class, InfoEndpoint.class))
                    .permitAll()
                .requestMatchers(EndpointRequest.to("cache-manager", "feature-flags"))
                    .hasRole("ADMIN")
                .anyRequest()
                    .hasRole("OPS")
            )
            .httpBasic(Customizer.withDefaults());

        return http.build();
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. 상태 변경 작업의 멱등성 보장

`@WriteOperation`으로 캐시 초기화나 피처 플래그 변경을 구현할 때, 동시 요청에 의한 경쟁 조건(Race Condition)을 반드시 고려해야 합니다. `ConcurrentHashMap`의 원자적 연산(`computeIfPresent`, `replace`)을 활용하거나, 분산 환경이라면 Redis나 DB 레벨의 락을 사용하세요.

### 2. 엔드포인트 ID 네이밍 규칙

Actuator 엔드포인트 ID는 **영문 소문자와 하이픈만** 허용됩니다. 언더스코어나 카멜케이스를 사용하면 애플리케이션 구동 시 예외가 발생합니다. 또한 기존 Actuator 기본 엔드포인트 이름(`health`, `metrics`, `info` 등)과 충돌하지 않도록 주의하세요.

### 3. 응답 직렬화 이슈

복잡한 객체를 반환할 때 Jackson 직렬화가 예상치 못하게 동작할 수 있습니다. 특히 `Optional`, 순환 참조, `Instant` 같은 타입은 `application.yml`의 Jackson 설정이나 `@JsonSerialize` 어노테이션으로 명시적으로 처리하는 것이 좋습니다.

### 4. 성능 비용

`@ReadOperation`이 실행될 때마다 외부 서비스를 실제로 호출하는 방식은 모니터링 도구가 주기적으로 폴링할 경우 외부 서비스에 불필요한 부하를 줄 수 있습니다. 결과를 일정 시간 캐싱하거나, `@Cacheable`을 엔드포인트 메서드에 적용하는 방식을 검토하세요.

```java
@ReadOperation
@Cacheable(value = "actuator-external-check", key = "'all'")
public Map<String, ServiceStatus> checkAll() {
    // 외부 서비스 호출
}
```

### 5. 분산 환경에서의 일관성 문제

피처 플래그나 설정 값을 인스턴스 메모리에만 저장하는 방식은 멀티 인스턴스 환경에서 각 인스턴스의 상태가 달라지는 문제가 생깁니다. 실무에서는 Redis나 별도의 Feature Flag 서비스(LaunchDarkly, Unleash 등)와 연동하는 것을 권장합니다.

---

## 정리

Spring Boot 커스텀 Actuator Endpoint는 단순한 모니터링을 넘어 **런타임 운영 도구**로 활용할 수 있습니다. 핵심 내용을 정리하면 다음과 같습니다.

- `@Endpoint` + `@ReadOperation`/`@WriteOperation`/`@DeleteOperation`으로 선언적으로 구현
- `@Selector`를 사용해 경로 변수 방식의 동적 엔드포인트 지원
- 보안은 선택이 아닌 **필수**, 특히 쓰기/삭제 작업은 반드시 인증/인가 처리
- 응답 객체는 Java Record나 불변 DTO를 사용해 직렬화 예측 가능성을 높임
- 분산 환경을 고려해 상태를 인스턴스 로컬에만 저장하는 것을 지양

운영팀이 별도의 대시보드 없이도 API 한 번으로 시스템 상태를 파악하고 제어할 수 있게 되면, 장애 대응 속도가 눈에 띄게 빨라집니다. 기존에 관리자 페이지나 별도 스크립트로 처리하던 작업들을 Actuator Endpoint로 통합해 보세요.