# Server-driven UI 백엔드 설계

## 개요

모바일 앱을 운영하다 보면 반복적으로 마주치는 문제가 있다. 홈 화면 레이아웃 하나 바꾸려고 iOS/Android 배포를 동시에 나가야 하거나, 앱스토어 심사 대기로 긴급 수정이 며칠씩 지연되는 상황이다. 이 문제를 근본적으로 해결하려는 접근이 **Server-driven UI(SDUI)**다.

SDUI는 UI의 구조와 데이터를 서버가 정의하고, 클라이언트는 이를 해석해 렌더링하는 패턴이다. 클라이언트는 렌더링 엔진 역할만 하고, "무엇을 어떻게 보여줄지"에 대한 결정권이 서버로 넘어온다. Airbnb, Lyft, 카카오, 배달의민족 등 다양한 팀이 이 패턴을 적용한 사례를 공개했고, 이제는 충분히 검증된 아키텍처 선택지가 됐다.

이 글에서는 SDUI의 핵심 개념을 정리하고, Spring Boot 기반의 백엔드에서 어떻게 설계할지를 실전 관점에서 다룬다.

---

## 핵심 개념

### 컴포넌트 트리 구조

SDUI의 응답은 기본적으로 **컴포넌트 트리**다. 클라이언트가 사전에 알고 있는 컴포넌트 타입(`type` 필드)과 그 속성(`props`)을 JSON으로 내려주고, 클라이언트는 이를 실제 네이티브/웹 컴포넌트로 매핑해 렌더링한다.

```json
{
  "type": "Screen",
  "props": {
    "title": "오늘의 추천"
  },
  "children": [
    {
      "type": "Banner",
      "props": {
        "imageUrl": "https://cdn.example.com/banner.jpg",
        "deeplink": "app://promotion/123"
      }
    },
    {
      "type": "ProductList",
      "props": {
        "layout": "horizontal_scroll",
        "items": [
          { "id": "p001", "name": "상품 A", "price": 12000 }
        ]
      }
    }
  ]
}
```

이 구조가 단순해 보이지만, 실제로는 **버전 관리**, **확장성**, **컴포넌트 계약**이 설계의 핵심이다.

### 클라이언트-서버 컴포넌트 계약

서버가 내려보낼 수 있는 컴포넌트는 클라이언트가 구현한 것만 사용할 수 있다. 따라서 신규 컴포넌트를 추가하려면 클라이언트 배포가 선행되어야 한다. 이를 위해 **컴포넌트 레지스트리**를 관리하는 것이 일반적이다.

컴포넌트 계약의 원칙:
- 서버는 클라이언트가 모르는 `type`을 내려보내지 않는다 (또는 클라이언트가 unknown 타입을 안전하게 skip한다)
- `props`에서 필수 필드가 누락되면 클라이언트가 graceful fallback 처리해야 한다
- 클라이언트 버전별로 지원 가능한 컴포넌트 목록을 서버가 인지해야 한다

---

## 실전 예제: Spring Boot 기반 SDUI 백엔드

### 도메인 모델 설계

먼저 서버 측에서 컴포넌트 트리를 표현하는 Java 도메인 모델을 정의한다.

```java
// 기본 컴포넌트 인터페이스
public interface UiComponent {
    String getType();
}

// 공통 추상 클래스
@JsonTypeInfo(use = JsonTypeInfo.Id.NAME, property = "type")
@JsonSubTypes({
    @JsonSubTypes.Type(value = BannerComponent.class, name = "Banner"),
    @JsonSubTypes.Type(value = ProductListComponent.class, name = "ProductList"),
    @JsonSubTypes.Type(value = SectionHeaderComponent.class, name = "SectionHeader")
})
public abstract class BaseComponent implements UiComponent {
    protected String type;
    protected Map<String, Object> tracking; // 분석용 트래킹 정보

    public Map<String, Object> getTracking() { return tracking; }
}

// Banner 컴포넌트
@Getter
@Builder
public class BannerComponent extends BaseComponent {
    private String imageUrl;
    private String deeplink;
    private String altText;

    @Override
    public String getType() { return "Banner"; }
}

// ProductList 컴포넌트
@Getter
@Builder
public class ProductListComponent extends BaseComponent {
    private String layout; // "horizontal_scroll" | "grid_2col"
    private List<ProductItem> items;
    private String moreDeeplink;

    @Override
    public String getType() { return "ProductList"; }
}
```

### 화면 응답 구조

```java
@Getter
@Builder
public class ScreenResponse {
    private String screenId;
    private String title;
    private List<BaseComponent> components;
    private RefreshPolicy refreshPolicy;
    private String nextCursor; // 무한 스크롤용

    @Getter
    @Builder
    public static class RefreshPolicy {
        private int ttlSeconds;       // 클라이언트 캐시 TTL
        private String cacheKey;      // 캐시 무효화 키
    }
}
```

### 컴포넌트 어셈블러 패턴

각 섹션을 조립하는 로직은 **어셈블러**로 분리한다. 이렇게 하면 A/B 테스트, 개인화, 조건부 노출 로직이 섞이더라도 역할이 명확해진다.

```java
public interface ComponentAssembler<T> {
    BaseComponent assemble(T context);
    boolean supports(ScreenContext context); // 노출 조건 판단
}

@Component
@RequiredArgsConstructor
public class BannerAssembler implements ComponentAssembler<BannerContext> {

    private final BannerRepository bannerRepository;
    private final AbTestService abTestService;

    @Override
    public BaseComponent assemble(BannerContext ctx) {
        String variant = abTestService.getVariant(ctx.getUserId(), "home_banner_v2");
        Banner banner = bannerRepository.findActiveBannerByVariant(variant);

        return BannerComponent.builder()
            .imageUrl(banner.getImageUrl())
            .deeplink(banner.getDeeplink())
            .tracking(Map.of(
                "event", "banner_impression",
                "bannerId", banner.getId(),
                "variant", variant
            ))
            .build();
    }

    @Override
    public boolean supports(ScreenContext context) {
        return context.getSection() == ScreenSection.HOME_TOP;
    }
}
```

### 화면 조립 서비스

```java
@Service
@RequiredArgsConstructor
public class HomeScreenService {

    private final List<ComponentAssembler<?>> assemblers;
    private final ClientCapabilityResolver capabilityResolver;

    public ScreenResponse buildHomeScreen(ScreenRequest request) {
        // 클라이언트 버전별 지원 컴포넌트 확인
        Set<String> supportedTypes = capabilityResolver
            .getSupportedComponents(request.getAppVersion());

        ScreenContext context = ScreenContext.builder()
            .userId(request.getUserId())
            .appVersion(request.getAppVersion())
            .section(ScreenSection.HOME_TOP)
            .build();

        List<BaseComponent> components = assemblers.stream()
            .filter(a -> a.supports(context))
            .map(a -> assembleWithFallback(a, context))
            .filter(Objects::nonNull)
            .filter(c -> supportedTypes.contains(c.getType())) // 미지원 컴포넌트 필터
            .collect(Collectors.toList());

        return ScreenResponse.builder()
            .screenId("home_v2")
            .title("홈")
            .components(components)
            .refreshPolicy(RefreshPolicy.builder()
                .ttlSeconds(30)
                .cacheKey("home:" + request.getUserId())
                .build())
            .build();
    }

    private BaseComponent assembleWithFallback(ComponentAssembler assembler, ScreenContext ctx) {
        try {
            return assembler.assemble(ctx);
        } catch (Exception e) {
            log.error("Component assembly failed: {}", assembler.getClass().getSimpleName(), e);
            return null; // 실패한 컴포넌트는 조용히 제외
        }
    }
}
```

### API 엔드포인트

```java
@RestController
@RequestMapping("/api/v1/screens")
@RequiredArgsConstructor
public class ScreenController {

    private final HomeScreenService homeScreenService;

    @GetMapping("/home")
    public ResponseEntity<ScreenResponse> getHomeScreen(
        @RequestHeader("X-App-Version") String appVersion,
        @RequestHeader("X-User-Id") String userId,
        @RequestParam(required = false) String cursor
    ) {
        ScreenRequest request = ScreenRequest.builder()
            .appVersion(appVersion)
            .userId(userId)
            .cursor(cursor)
            .build();

        ScreenResponse response = homeScreenService.buildHomeScreen(request);

        return ResponseEntity.ok()
            .cacheControl(CacheControl.maxAge(
                response.getRefreshPolicy().getTtlSeconds(), TimeUnit.SECONDS))
            .body(response);
    }
}
```

---

## 클라이언트 버전 관리 전략

SDUI에서 가장 까다로운 부분이 **클라이언트 버전 호환성**이다. 실무에서 자주 쓰는 전략을 정리한다.

### Capability Negotiation

클라이언트가 요청 헤더에 자신이 지원하는 컴포넌트 목록을 넣어 보내거나, 서버가 앱 버전을 기반으로 capability 테이블을 조회하는 방식이다.

```java
@Component
public class ClientCapabilityResolver {

    // 버전별 지원 컴포넌트 매핑 (실제로는 DB 또는 설정 파일로 관리)
    private static final Map<String, Set<String>> VERSION_CAPABILITY = Map.of(
        "3.0.0", Set.of("Banner", "ProductList", "SectionHeader", "VideoCard"),
        "2.5.0", Set.of("Banner", "ProductList", "SectionHeader"),
        "2.0.0", Set.of("Banner", "ProductList")
    );

    public Set<String> getSupportedComponents(String appVersion) {
        return VERSION_CAPABILITY.entrySet().stream()
            .filter(e -> isVersionAtLeast(appVersion, e.getKey()))
            .flatMap(e -> e.getValue().stream())
            .collect(Collectors.toSet());
    }

    private boolean isVersionAtLeast(String current, String minimum) {
        // Semantic versioning 비교 로직
        return new ComparableVersion(current)
            .compareTo(new ComparableVersion(minimum)) >= 0;
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. 응답 크기와 오버페칭

컴포넌트를 많이 내려보낼수록 응답이 커진다. 특히 개인화된 데이터가 포함되면 CDN 캐싱이 어려워진다. **골격(skeleton) 응답과 동적 데이터를 분리**하거나, 클라이언트가 섹션별로 API를 쪼개서 호출하는 방식을 고려해야 한다.

### 2. 디버깅 복잡도 증가

문제가 생겼을 때 "서버가 내려준 데이터"와 "클라이언트 렌더링 로직" 어느 쪽 문제인지 구분이 어려울 수 있다. **응답에 컴포넌트 메타데이터(버전, 실험 ID 등)를 포함**해 추적 가능성을 확보하는 것이 중요하다.

### 3. 컴포넌트 계약 파괴

서버가 기존 `props`의 필드 타입을 바꾸거나 필수 필드를 추가하면 구버전 클라이언트가 크래시할 수 있다. **모든 `props` 변경은 하위 호환을 유지**하고, breaking change가 필요하면 새 컴포넌트 타입으로 분리해야 한다.

### 4. 성능: 직렬화 비용

컴포넌트 트리를 매 요청마다 조립하면 CPU 비용이 상당하다. **화면 단위 응답을 Redis에 캐싱**하되, 개인화 파트는 캐시 키에 사용자 세그먼트를 포함시키는 방식으로 균형을 맞춰야 한다.

### 5. SDUI가 적합하지 않은 경우

- 복잡한 인터랙션(드래그, 애니메이션 흐름)이 많은 화면
- 초당 수십 번 상태가 바뀌는 실시간 UI
- 클라이언트 성능이 중요한 저사양 디바이스 타겟

---

## 정리

SDUI 백엔드 설계의 핵심을 요약하면 다음과 같다.

| 관심사 | 핵심 원칙 |
|---|---|
| 컴포넌트 계약 | 하위 호환 유지, 클라이언트 버전별 capability 관리 |
| 조립 로직 | Assembler 패턴으로 관심사 분리 |
| 캐싱 전략 | 골격과 동적 데이터 분리, TTL 명시 |
| 안정성 | 컴포넌트 조립 실패를 격리하고 graceful degradation |
| 추적성 | 모든 컴포넌트에 트래킹 메타데이터 포함 |

SDUI는 "서버가 모든 것을 제어한다"는 철학보다 **"변경 빈도가 높은 부분의 제어권을 서버로 올린다"**는 관점으로 접근할 때 가장 효과적이다. 처음부터 전체 화면을 SDUI로 만들려 하기보다, 배너·추천 섹션처럼 자주 바뀌는 영역부터 점진적으로 도입하고, 컴포넌트 계약과 버전 관리 체계를 먼저 탄탄히 잡는 것을 권장한다.
