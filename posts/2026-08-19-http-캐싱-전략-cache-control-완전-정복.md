# HTTP 캐싱 전략 Cache-Control 완전 정복

## 테스트 전략과 신뢰성 검증 관점에서 다시 보는 Cache-Control

---

## 개요: "동작하는 캐시"와 "신뢰할 수 있는 캐시"는 다르다

Cache-Control 설정을 해봤다면, 브라우저 DevTools에서 `from cache`를 확인하고 "됐다!"고 생각한 경험이 있을 것이다. 하지만 실무에서 캐시 버그는 가장 재현하기 어려운 클래스에 속한다. CDN 엣지 서버마다 동작이 다르고, 프록시 레이어가 끼면 예측이 더 어려워지며, 배포 직후 stale 콘텐츠가 수십만 사용자에게 노출되는 사고는 생각보다 자주 일어난다.

이 글은 Cache-Control 헤더의 기본 문법을 다루지 않는다. **설정이 실제로 의도대로 동작하는지 검증하는 방법**, **캐시 계층별 신뢰성을 어떻게 테스트할 것인지**, 그리고 **트레이드오프를 수치로 이해하는 방법**에 집중한다.

---

## 핵심 개념: 캐시 신뢰성 검증의 세 가지 차원

캐시 전략을 검증할 때는 다음 세 가지 차원을 분리해서 생각해야 한다.

### 1. 헤더 정합성 (Header Correctness)
서버가 올바른 Cache-Control 헤더를 응답으로 내려보내는가?

### 2. 캐시 계층별 동작 일치 (Layer Behavior Consistency)
브라우저 캐시, CDN(엣지), 리버스 프록시(Nginx/Varnish), 애플리케이션 레벨 캐시 각각이 동일한 정책을 따르는가?

### 3. 무효화 시점 정확성 (Invalidation Timeliness)
콘텐츠가 변경되었을 때 캐시가 제때 무효화되고 새 버전이 서빙되는가?

이 세 가지를 모두 커버하지 않으면 "동작하는 것처럼 보이는 캐시"에 불과하다.

---

## 실전 예제

### 1. 서버 사이드 헤더 정합성 테스트 (Spring Boot + MockMvc)

컨트롤러 레벨에서 Cache-Control 헤더가 정확히 설정되는지 단위 테스트로 검증하는 것이 출발점이다.

```java
@WebMvcTest(ProductController.class)
class ProductControllerCacheTest {

    @Autowired
    private MockMvc mockMvc;

    @Test
    @DisplayName("상품 상세 API는 public, max-age=300, stale-while-revalidate=60을 반환해야 한다")
    void productDetail_shouldHaveCorrectCacheHeaders() throws Exception {
        mockMvc.perform(get("/api/products/1"))
            .andExpect(status().isOk())
            .andExpect(header().string(
                HttpHeaders.CACHE_CONTROL,
                "public, max-age=300, stale-while-revalidate=60"
            ))
            .andExpect(header().exists(HttpHeaders.ETAG))
            .andExpect(header().exists(HttpHeaders.LAST_MODIFIED));
    }

    @Test
    @DisplayName("인증이 필요한 마이페이지 API는 no-store를 반환해야 한다")
    void myPage_shouldHaveNoCacheHeader() throws Exception {
        mockMvc.perform(get("/api/users/me")
                .header("Authorization", "Bearer test-token"))
            .andExpect(status().isOk())
            .andExpect(header().string(
                HttpHeaders.CACHE_CONTROL,
                "no-store"
            ));
    }

    @Test
    @DisplayName("ETag 기반 조건부 요청: 콘텐츠 미변경 시 304를 반환해야 한다")
    void conditionalRequest_withMatchingETag_shouldReturn304() throws Exception {
        // 1단계: 최초 응답에서 ETag 수집
        MvcResult result = mockMvc.perform(get("/api/products/1"))
            .andExpect(status().isOk())
            .andReturn();

        String etag = result.getResponse().getHeader(HttpHeaders.ETAG);
        assertThat(etag).isNotNull();

        // 2단계: If-None-Match 헤더로 조건부 요청
        mockMvc.perform(get("/api/products/1")
                .header(HttpHeaders.IF_NONE_MATCH, etag))
            .andExpect(status().isNotModified())
            .andExpect(header().doesNotExist(HttpHeaders.CONTENT_TYPE)); // 바디 없음 검증
    }
}
```

> **포인트**: `no-cache`와 `no-store`를 혼동해서 잘못 설정하는 케이스가 매우 흔하다. `no-cache`는 "캐시하되 항상 재검증하라"는 의미이고, `no-store`는 "저장조차 하지 마라"다. 민감한 데이터에는 반드시 `no-store`를 테스트로 강제해야 한다.

---

### 2. CDN 계층 동작 검증 (Integration Test with RestAssured + WireMock)

CDN이 끼는 순간 브라우저 DevTools만으로는 검증이 불충분하다. CDN이 `Vary` 헤더를 올바르게 처리하는지, `s-maxage`와 `max-age`를 구분하는지 반드시 통합 테스트로 확인해야 한다.

```java
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ExtendWith(WireMockExtension.class)
class CdnCacheBehaviorTest {

    // WireMock이 CDN 오리진 서버 역할
    @RegisterExtension
    static WireMockExtension originServer = WireMockExtension.newInstance()
        .options(wireMockConfig().dynamicPort())
        .build();

    @Test
    @DisplayName("s-maxage는 CDN TTL에만 적용되고, max-age는 브라우저 TTL에 적용된다")
    void smaxage_shouldApplyOnlyToCdnLayer() {
        originServer.stubFor(get("/api/articles/1")
            .willReturn(aResponse()
                .withHeader("Cache-Control", "public, max-age=60, s-maxage=3600")
                .withHeader("Content-Type", "application/json")
                .withBody("{\"title\": \"test\"}")));

        Response response = RestAssured.given()
            .baseUri(originServer.baseUrl())
            .get("/api/articles/1");

        String cacheControl = response.getHeader("Cache-Control");

        // s-maxage가 CDN용으로 포함되어 있는지 확인
        assertThat(cacheControl).contains("s-maxage=3600");
        // 브라우저 TTL은 별도로 제한되어 있는지 확인
        assertThat(cacheControl).contains("max-age=60");

        // CDN이 s-maxage를 무시하고 max-age만 사용하면 캐시가 1분마다 만료되어 오리진 부하 급증
        // → 이 테스트가 실패하면 CDN 설정 재검토 필요
    }

    @Test
    @DisplayName("Vary: Accept-Encoding 설정 시 gzip/non-gzip 응답이 별도 캐시되어야 한다")
    void vary_acceptEncoding_shouldCacheSeparately() {
        originServer.stubFor(get("/api/data")
            .withHeader("Accept-Encoding", containing("gzip"))
            .willReturn(aResponse()
                .withHeader("Cache-Control", "public, max-age=600")
                .withHeader("Vary", "Accept-Encoding")
                .withHeader("Content-Encoding", "gzip")
                .withBody("compressed-body")));

        // Vary 헤더 없이 캐싱하면 gzip 응답을 non-gzip 클라이언트에 서빙하는 사고 발생
        Response gzipResponse = RestAssured.given()
            .baseUri(originServer.baseUrl())
            .header("Accept-Encoding", "gzip")
            .get("/api/data");

        assertThat(gzipResponse.getHeader("Vary")).isEqualTo("Accept-Encoding");
        assertThat(gzipResponse.getHeader("Content-Encoding")).isEqualTo("gzip");
    }
}
```

---

### 3. 캐시 무효화 지연 측정 스크립트 (Shell + curl)

배포 후 캐시가 실제로 무효화되는 데 걸리는 시간을 측정하는 것은 신뢰성 검증의 핵심이다. 이를 CI/CD 파이프라인에 포함하면 배포 후 캐시 전파 상태를 자동으로 확인할 수 있다.

```bash
#!/bin/bash
# cache-invalidation-check.sh
# 배포 후 CDN 캐시 무효화 확인 스크립트

TARGET_URL="${1:-https://api.example.com/api/products/1}"
EXPECTED_VERSION="${2:-v2.1.0}"
MAX_WAIT_SECONDS=120
INTERVAL=5
ELAPSED=0

echo "=== Cache Invalidation Verification ==="
echo "Target: $TARGET_URL"
echo "Expected Version: $EXPECTED_VERSION"
echo "Max wait: ${MAX_WAIT_SECONDS}s"
echo "======================================="

while [ $ELAPSED -lt $MAX_WAIT_SECONDS ]; do
    RESPONSE=$(curl -sI "$TARGET_URL" \
        -H "Cache-Control: no-cache" \
        -H "Pragma: no-cache")

    AGE=$(echo "$RESPONSE" | grep -i "^age:" | awk '{print $2}' | tr -d '\r')
    CF_CACHE=$(echo "$RESPONSE" | grep -i "^cf-cache-status:" | awk '{print $2}' | tr -d '\r')
    X_VERSION=$(echo "$RESPONSE" | grep -i "^x-app-version:" | awk '{print $2}' | tr -d '\r')

    echo "[${ELAPSED}s] Age: ${AGE:-N/A} | CDN Status: ${CF_CACHE:-N/A} | Version: ${X_VERSION:-N/A}"

    if [ "$X_VERSION" = "$EXPECTED_VERSION" ]; then
        echo "✅ Cache invalidated successfully at ${ELAPSED}s"
        exit 0
    fi

    sleep $INTERVAL
    ELAPSED=$((ELAPSED + INTERVAL))
done

echo "❌ Cache invalidation timeout after ${MAX_WAIT_SECONDS}s"
echo "   Last seen version: ${X_VERSION:-unknown}"
exit 1
```

이 스크립트를 GitHub Actions의 post-deployment 단계에 추가하면, 캐시 무효화가 실패한 경우 알림을 받을 수 있다.

---

## 주의사항 및 트레이드오프

### 트레이드오프 1: TTL 길이 vs 신선도 보장

| TTL 설정 | 캐시 히트율 (예상) | 무효화 지연 | 오리진 부하 |
|----------|------------------|-------------|-------------|
| max-age=60 | ~70% | 최대 60초 | 중간 |
| max-age=300 | ~85% | 최대 5분 | 낮음 |
| max-age=3600 | ~95% | 최대 1시간 | 매우 낮음 |
| immutable (1년) | ~99% | 없음(파일명 변경) | 극히 낮음 |

**실무 권장**: 자주 바뀌지 않는 API 응답은 `stale-while-revalidate`를 함께 사용해 UX를 희생하지 않으면서 오리진 부하를 줄여라. `max-age=60, stale-while-revalidate=300`이면, 60초 이후 최대 300초까지는 오래된 콘텐츠를 서빙하면서 백그라운드에서 갱신한다.

### 트레이드오프 2: ETag vs Last-Modified

ETag는 콘텐츠 기반 해시라 정확하지만, 클러스터 환경에서 서버마다 다른 ETag를 생성할 수 있다. `Last-Modified`는 분산 환경에서 더 일관적이지만 시간 해상도가 1초 단위라 동일 초 내 변경은 감지 못한다.

```java
// 분산 환경에서 ETag 일관성을 보장하는 방법: DB 버전 기반 ETag 생성
@GetMapping("/api/products/{id}")
public ResponseEntity<Product> getProduct(@PathVariable Long id,
                                           WebRequest request) {
    Product product = productService.findById(id);
    
    // DB의 updated_at + version 조합으로 ETag 생성 → 서버 독립적으로 동일 값 보장
    String etag = "\"" + product.getVersion() + "-" + 
                  product.getUpdatedAt().toEpochMilli() + "\"";
    
    if (request.checkNotModified(etag)) {
        return ResponseEntity.status(HttpStatus.NOT_MODIFIED).build();
    }
    
    return ResponseEntity.ok()
        .cacheControl(CacheControl.maxAge(300, TimeUnit.SECONDS)
            .staleWhileRevalidate(60, TimeUnit.SECONDS))
        .eTag(etag)
        .body(product);
}
```

### 주의사항: `private` + `no-store` 혼용 실수

보안에 민감한 응답에서 가장 흔한 실수는 `Cache-Control: private`만 설정하는 것이다. `private`은 공유 캐시(CDN, 프록시)에는 저장하지 말라는 의미지만, **브라우저 캐시에는 저장된다**. 개인 금융 데이터, 세션 기반 응답은 반드시 `no-store`여야 한다.

```java
// ❌ 잘못된 설정: 브라우저 캐시에는 저장됨
CacheControl.noCache(); // "no-cache"

// ✅ 올바른 설정: 어디에도 저장 안 됨
CacheControl.noStore(); // "no-store"

// 테스트로 강제하기
assertThat(response.getHeader("Cache-Control"))
    .as("민감한 데이터는 반드시 no-store여야 합니다")
    .isEqualTo("no-store");
```

### 주의사항: CDN purge API 호출의 비동기성

Cloudflare, AWS CloudFront 등 대부분의 CDN은 purge 요청 후 실제 엣지 서버에 전파되기까지 **평균 5~30초**, 최악의 경우 수 분이 걸린다. 배포 직후 헬스체크가 통과해도 실제 사용자에게는 구버전이 서빙될 수 있다. 위에서 소개한 무효화 지연 측정 스크립트가 바로 이 상황을 감지한다.

---

## 정리

Cache-Control은 설정하는 것보다 **검증하는 것이 더 어렵다**. 실무에서 캐시 신뢰성을 확보하려면 다음 체크리스트를 CI/CD에 통합하는 것을 강력히 권장한다.

| 검증 항목 | 테스트 방법 | 자동화 여부 |
|-----------|-------------|-------------|
| 헤더 정합성 | MockMvc 단위 테스트 | ✅ CI 필수 |
| 조건부 요청(ETag/304) | MockMvc 통합 테스트 | ✅ CI 필수 |
| 민감 데이터 no-store | MockMvc 보안 테스트 | ✅ CI 필수 |
| CDN s-maxage 동작 | RestAssured + WireMock | ✅ CI 권장 |
| 캐시 무효화 지연 | Shell 스크립트 | ✅ 배포 후 자동 실행 |
| 분산 환경 ETag 일관성 | 클러스터 E2E 테스트 | ⚠️ 스테이징 환경에서 주기적 실행 |

캐시는 성능 최적화 도구이기도 하지만, 잘못 다루면 사용자에게 잘못된 데이터를 보여주는 신뢰성 문제가 된다. **"빠른 캐시"보다 "믿을 수 있는 캐시"가 먼저다.**