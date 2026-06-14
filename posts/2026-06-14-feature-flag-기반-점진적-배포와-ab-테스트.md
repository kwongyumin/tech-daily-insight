# Feature Flag 기반 점진적 배포와 A/B 테스트

## 개요

현대 소프트웨어 개발에서 배포는 단순히 "코드를 서버에 올리는 행위"가 아니다. 대규모 트래픽을 처리하는 서비스라면 배포 한 번이 수십만 명의 사용자 경험에 직접적인 영향을 미친다. 이런 환경에서 **Feature Flag(피처 플래그)** 는 위험을 최소화하면서 새로운 기능을 점진적으로 롤아웃하거나, A/B 테스트를 통해 데이터 기반 의사결정을 내릴 수 있게 해주는 핵심 도구로 자리잡았다.

Feature Flag는 코드 배포와 기능 활성화를 분리하는 기법이다. 이미 코드는 프로덕션에 배포되어 있지만, 실제 기능은 특정 조건(사용자 비율, 특정 그룹, 지역 등)에 따라 선택적으로 노출된다. Netflix, Facebook, Google 같은 빅테크 기업들이 수년 전부터 이 전략을 핵심 배포 파이프라인으로 채택하고 있으며, 국내 주요 IT 기업들도 점차 도입을 확대하고 있다.

이 글에서는 Feature Flag의 핵심 개념부터 Spring Boot 기반의 실전 구현, 그리고 A/B 테스트 연계 방법까지 실무적인 관점에서 다룬다.

---

## 핵심 개념

### Feature Flag의 유형

Feature Flag는 사용 목적에 따라 크게 네 가지로 분류할 수 있다.

| 유형 | 목적 | 생명주기 |
|------|------|----------|
| **Release Flag** | 점진적 기능 롤아웃 | 단기 (배포 완료 후 제거) |
| **Experiment Flag** | A/B 테스트, 가설 검증 | 중기 (실험 종료 후 제거) |
| **Ops Flag** | 긴급 kill switch, 서킷브레이커 | 장기 또는 영구 |
| **Permission Flag** | 특정 사용자 그룹 기능 제한 | 장기 |

### 점진적 배포(Progressive Delivery)

점진적 배포는 전체 사용자에게 한 번에 기능을 오픈하는 대신, **Canary Release** 또는 **Percentage Rollout** 방식으로 위험을 분산시킨다.

```
배포 흐름 예시:
1% → 5% → 10% → 25% → 50% → 100%
```

각 단계에서 에러율, 응답 시간, 비즈니스 메트릭(전환율, 이탈률 등)을 모니터링하고, 이상 징후가 감지되면 즉시 롤백한다.

### A/B 테스트와 Feature Flag의 관계

A/B 테스트는 두 가지 이상의 변형(Variant)을 동시에 운영하며 어떤 것이 더 나은 결과를 만드는지 통계적으로 검증하는 방법이다. Feature Flag는 A/B 테스트를 구현하는 **인프라 레이어** 역할을 한다. 사용자를 Control Group(기존 로직)과 Treatment Group(새 로직)으로 나누고, 각 그룹의 행동 데이터를 수집해 통계적 유의성을 판단한다.

---

## 실전 예제

### 1. Spring Boot에서 Feature Flag 구현

먼저 간단한 Feature Flag 서비스를 직접 구현해보자. 프로덕션 환경에서는 LaunchDarkly, Unleash, Flagsmith 같은 전문 도구를 사용하는 것이 좋지만, 원리를 이해하기 위해 직접 구현하는 것도 중요하다.

**의존성 추가 (build.gradle)**

```groovy
dependencies {
    implementation 'org.springframework.boot:spring-boot-starter-web'
    implementation 'org.springframework.boot:spring-boot-starter-data-redis'
    implementation 'com.fasterxml.jackson.core:jackson-databind'
}
```

**Feature Flag 도메인 모델**

```java
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class FeatureFlag {
    private String key;
    private boolean enabled;
    private int rolloutPercentage; // 0-100
    private Set<String> enabledUserIds; // 특정 사용자 화이트리스트
    private Map<String, String> variants; // A/B 테스트 변형
}
```

**Feature Flag 서비스**

```java
@Service
@RequiredArgsConstructor
@Slf4j
public class FeatureFlagService {

    private final RedisTemplate<String, FeatureFlag> redisTemplate;
    private static final String FLAG_PREFIX = "feature:flag:";

    /**
     * 특정 사용자에게 Feature Flag가 활성화되어 있는지 확인
     */
    public boolean isEnabled(String flagKey, String userId) {
        FeatureFlag flag = getFlag(flagKey);

        if (flag == null || !flag.isEnabled()) {
            return false;
        }

        // 화이트리스트 사용자 우선 확인
        if (flag.getEnabledUserIds() != null
                && flag.getEnabledUserIds().contains(userId)) {
            return true;
        }

        // 롤아웃 비율 기반 판단 (사용자 ID의 해시값을 활용해 일관성 보장)
        return isInRolloutGroup(userId, flag.getRolloutPercentage());
    }

    /**
     * A/B 테스트 변형(variant) 반환
     */
    public String getVariant(String flagKey, String userId) {
        FeatureFlag flag = getFlag(flagKey);

        if (flag == null || !flag.isEnabled() || flag.getVariants() == null) {
            return "control";
        }

        // 사용자 해시 기반으로 일관된 variant 할당
        int hash = Math.abs(userId.hashCode()) % 100;
        int cumulative = 0;

        for (Map.Entry<String, String> entry : flag.getVariants().entrySet()) {
            int weight = Integer.parseInt(entry.getValue());
            cumulative += weight;
            if (hash < cumulative) {
                return entry.getKey();
            }
        }

        return "control";
    }

    private boolean isInRolloutGroup(String userId, int percentage) {
        // MurmurHash 또는 단순 hashCode 사용 (일관성 있는 버킷팅)
        int bucket = Math.abs((userId + "rollout").hashCode()) % 100;
        return bucket < percentage;
    }

    private FeatureFlag getFlag(String flagKey) {
        return redisTemplate.opsForValue().get(FLAG_PREFIX + flagKey);
    }

    public void saveFlag(FeatureFlag flag) {
        redisTemplate.opsForValue().set(FLAG_PREFIX + flag.getKey(), flag);
    }
}
```

### 2. 실제 비즈니스 로직에 적용

```java
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/checkout")
public class CheckoutController {

    private final FeatureFlagService featureFlagService;
    private final LegacyCheckoutService legacyCheckoutService;
    private final NewCheckoutService newCheckoutService;
    private final MetricsService metricsService;

    @PostMapping
    public ResponseEntity<CheckoutResponse> checkout(
            @RequestBody CheckoutRequest request,
            @AuthenticationPrincipal UserDetails userDetails) {

        String userId = userDetails.getUsername();
        String variant = featureFlagService.getVariant("new-checkout-flow", userId);

        long startTime = System.currentTimeMillis();
        CheckoutResponse response;

        try {
            response = switch (variant) {
                case "new-ui" -> newCheckoutService.process(request);
                case "express" -> newCheckoutService.processExpress(request);
                default -> legacyCheckoutService.process(request);
            };

            // A/B 테스트 메트릭 기록
            metricsService.record("checkout.success", userId, variant,
                    System.currentTimeMillis() - startTime);

        } catch (Exception e) {
            log.error("Checkout failed for user={}, variant={}", userId, variant, e);
            metricsService.record("checkout.failure", userId, variant, 0);
            // 실패 시 레거시로 폴백
            response = legacyCheckoutService.process(request);
        }

        return ResponseEntity.ok(response);
    }
}
```

### 3. Unleash 오픈소스 연동

프로덕션에서 직접 구현보다 검증된 오픈소스를 활용하는 것을 권장한다. **Unleash**는 자체 호스팅 가능한 Feature Flag 플랫폼으로, Spring Boot와 쉽게 연동된다.

```groovy
// build.gradle
implementation 'io.getunleash:unleash-client-java:8.0.0'
```

```java
@Configuration
public class UnleashConfig {

    @Bean
    public Unleash unleash() {
        UnleashConfig config = UnleashConfig.newBuilder()
                .appName("my-service")
                .instanceId("instance-1")
                .unleashAPI("http://unleash-server:4242/api")
                .customHttpHeader("Authorization", "*:development.your-token")
                .build();

        return new DefaultUnleash(config);
    }
}

@Service
@RequiredArgsConstructor
public class ProductService {

    private final Unleash unleash;

    public ProductResponse getProduct(String productId, String userId) {
        UnleashContext context = UnleashContext.newBuilder()
                .userId(userId)
                .build();

        if (unleash.isEnabled("new-product-page", context)) {
            return getEnhancedProductDetail(productId);
        }

        return getLegacyProductDetail(productId);
    }
}
```

### 4. A/B 테스트 결과 분석을 위한 이벤트 수집

```java
@Component
@RequiredArgsConstructor
public class ExperimentTracker {

    private final KafkaTemplate<String, ExperimentEvent> kafkaTemplate;

    public void track(String experimentKey, String userId,
                      String variant, String eventType, Map<String, Object> properties) {

        ExperimentEvent event = ExperimentEvent.builder()
                .experimentKey(experimentKey)
                .userId(userId)
                .variant(variant)
                .eventType(eventType)
                .properties(properties)
                .timestamp(Instant.now())
                .build();

        // Kafka를 통해 데이터 파이프라인으로 전송
        kafkaTemplate.send("experiment-events", userId, event);
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. 플래그 부채(Flag Debt)

Feature Flag의 가장 큰 적은 **관리되지 않는 플래그의 누적**이다. 실험이 끝났음에도 코드에서 제거되지 않은 플래그들은 코드 가독성을 해치고, 예측 불가능한 동작을 유발할 수 있다.

```
✅ 권장 실천사항
- 모든 플래그에 만료일(expiry date) 설정
- 플래그 생성 시 Jira/Linear 이슈 연결
- 분기별 플래그 감사(audit) 수행
- 완료된 실험 플래그는 PR 머지 전 제거
```

### 2. 조합 폭발(Combinatorial Explosion)

여러 Feature Flag가 동시에 활성화되면 테스트해야 할 조합의 수가 기하급수적으로 늘어난다. Flag A(ON/OFF) × Flag B(ON/OFF) × Flag C(ON/OFF) = 최대 8가지 조합이 되며, 플래그가 늘어날수록 QA 비용이 폭증한다.

**완화 전략:** 상호 의존적인 플래그는 하나의 상위 플래그로 묶거나, 플래그 간 의존성을 명시적으로 문서화한다.

### 3. 일관성(Consistency) 보장

동일한 사용자가 세션이 바뀌어도 동일한 variant를 경험해야 한다. 해시 기반 버킷팅을 사용하면 대부분 해결되지만, **롤아웃 비율 변경 시** 일부 사용자의 그룹이 바뀔 수 있다. UX에 민감한 실험이라면 Redis에 사용자-variant 매핑을 영속화하는 방식을 고려하라.

### 4. 성능 오버헤드

모든 요청마다 Flag 상태를 조회하면 레이턴시가 추가된다. Redis 캐싱을 기본으로 하되, **로컬 인메모리 캐시(Caffeine 등)** 를 L1 캐시로 두고 TTL 기반으로 주기적 갱신하는 계층형 캐시 전략을 권장한다.

```java
@Bean
public Cache<String, FeatureFlag> localFlagCache() {
    return Caffeine.newBuilder()
            .expireAfterWrite(30, TimeUnit.SECONDS) // 30초마다 갱신
            .maximumSize(1000)
            .build();
}
```

### 5. 통계적 유의성 함정

A/B 테스트는 **충분한 샘플 크기**가 확보되기 전에 결론을 내리면 위험하다. 초반에 우연히 좋아 보이는 데이터만 보고 조기 종료하는 것은 "Peeking Problem"으로 알려진 통계적 오류다. 최소 신뢰도 95%, 검정력 80%를 기준으로 사전에 필요한 샘플 크기를 계산하고, 그 전에는 실험을 조기 종료하지 않는 것이 원칙이다.

---

## 정리

Feature Flag 기반 점진적 배포와 A/B 테스트는 단순한 기술 트렌드가 아니라, **배포 리스크를 줄이고 데이터 기반으로 제품을 개선하는 문화**의 핵심 도구다.

| 전통적 배포 | Feature Flag 기반 배포 |
|------------|----------------------|
| 코드 배포 = 기능 활성화 | 코드 배포 ≠ 기능 활성화 |
| All-or-Nothing 롤아웃 | 점진적 롤아웃 |
| 롤백 = 재배포 | 롤백 = 플래그 토글 (수초 내) |
| 직관 기반 의사결정 | 데이터 기반 의사결정 |

실무 도입을 시작한다면 다음 순서를 권장한다.

1. **Kill Switch**부터 시작 — 가장 단순하지만 효과가 크다
2. **Canary Release** 적용 — 5~10%에서 시작해 모니터링 체계 구축
3. **A/B 테스트 파이프라인** 구성 — 이벤트 수집, 분석 환경 선행 구축
4. **플래그 거버넌스** 정립 — 생성/종료/제거 프로세스 문서화

완벽한 시스템보다 **작게 시작해서 팀의 문화로 만드는 것**이 Feature Flag 도입의 핵심이다. 코드는 언제든 배포하되, 기능은 준비됐을 때 켠다 — 이것이 현대 배포 전략의 본질이다.