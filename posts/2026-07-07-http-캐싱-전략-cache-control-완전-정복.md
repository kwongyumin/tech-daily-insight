# HTTP 캐싱 전략 Cache-Control 완전 정복

## 개요

웹 서비스 성능 최적화를 논할 때 빠지지 않는 주제가 바로 HTTP 캐싱이다. 적절한 캐싱 전략은 서버 부하를 줄이고, 네트워크 비용을 절감하며, 사용자 경험을 극적으로 개선한다. 그러나 잘못된 캐싱 설정은 오래된 데이터를 사용자에게 노출시키거나, 캐싱이 되어야 할 리소스가 매번 서버를 거치게 만든다.

`Cache-Control` 헤더는 HTTP/1.1에서 정의된 강력한 캐싱 제어 메커니즘이다. `Expires`나 `Pragma` 같은 구형 헤더를 대체하며, 클라이언트와 중간 프록시(CDN, 리버스 프록시)의 캐싱 동작을 세밀하게 제어할 수 있다. 이 글에서는 `Cache-Control`의 핵심 디렉티브부터 Spring Boot를 활용한 실전 구현, 그리고 실무에서 반드시 고려해야 할 트레이드오프까지 깊이 있게 다룬다.

---

## 핵심 개념

### Cache-Control 디렉티브 분류

`Cache-Control` 디렉티브는 **응답(Response)** 과 **요청(Request)** 에 각각 사용할 수 있으며, 역할에 따라 크게 **캐시 가능 여부**, **만료 정책**, **재검증 정책**으로 구분된다.

#### 캐시 가능 여부

| 디렉티브 | 설명 |
|---|---|
| `public` | 모든 캐시(클라이언트, CDN, 프록시)에 저장 가능 |
| `private` | 클라이언트(브라우저)에만 저장, CDN/프록시는 캐싱 불가 |
| `no-store` | 어떠한 캐시에도 저장 금지 (가장 강력한 캐싱 비활성화) |
| `no-cache` | 저장은 하되, 사용 전 반드시 서버에 재검증 요청 |

> **no-cache vs no-store**: 많은 개발자가 혼동하는 부분이다. `no-cache`는 캐시를 **저장하지 않는 게 아니라**, 매번 서버에 유효성을 확인하는 것이다. `no-store`가 진정한 의미의 "캐싱 안 함"이다.

#### 만료(Freshness) 정책

| 디렉티브 | 설명 |
|---|---|
| `max-age=<seconds>` | 응답을 캐시할 최대 시간(초) |
| `s-maxage=<seconds>` | 공유 캐시(CDN, 프록시)에 대한 max-age (우선순위 높음) |
| `stale-while-revalidate=<seconds>` | 만료 후에도 stale 응답을 제공하면서 백그라운드에서 재검증 |
| `stale-if-error=<seconds>` | 서버 오류 시 만료된 캐시를 허용 범위 내에서 사용 |

#### 재검증 정책

| 디렉티브 | 설명 |
|---|---|
| `must-revalidate` | 만료된 캐시는 반드시 서버에 재검증, 실패 시 504 반환 |
| `proxy-revalidate` | 공유 캐시에만 적용되는 must-revalidate |
| `immutable` | 콘텐츠가 절대 변경되지 않음을 명시, 불필요한 재검증 방지 |

### 조건부 요청과 ETag

`Cache-Control`과 함께 동작하는 **조건부 요청(Conditional Request)** 도 중요하다. 캐시가 만료되었을 때 클라이언트는 전체 콘텐츠를 다시 받는 대신, 변경 여부만 확인할 수 있다.

```
# ETag 기반 재검증
클라이언트 → 서버: GET /api/products
서버 → 클라이언트: 200 OK, ETag: "abc123", Cache-Control: max-age=60

# 60초 후 캐시 만료 시
클라이언트 → 서버: GET /api/products, If-None-Match: "abc123"
서버 → 클라이언트: 304 Not Modified (변경 없음, 바디 없음)
```

---

## 실전 예제

### Spring Boot에서 Cache-Control 설정

#### 정적 리소스 캐싱 설정

```java
@Configuration
@EnableWebMvc
public class WebMvcConfig implements WebMvcConfigurer {

    @Override
    public void addResourceHandlers(ResourceHandlerRegistry registry) {
        // 해시값이 포함된 정적 파일 (JS, CSS) - 장기 캐싱
        registry.addResourceHandler("/static/**")
                .addResourceLocations("classpath:/static/")
                .setCacheControl(CacheControl.maxAge(365, TimeUnit.DAYS)
                        .cachePublic()
                        .immutable());

        // 이미지 리소스 - 중간 캐싱
        registry.addResourceHandler("/images/**")
                .addResourceLocations("classpath:/images/")
                .setCacheControl(CacheControl.maxAge(30, TimeUnit.DAYS)
                        .cachePublic());
    }
}
```

#### REST API 응답 캐싱 설정

```java
@RestController
@RequestMapping("/api")
public class ProductController {

    @GetMapping("/products/{id}")
    public ResponseEntity<Product> getProduct(
            @PathVariable Long id,
            HttpServletRequest request) {

        Product product = productService.findById(id);

        // ETag 생성 (버전 또는 수정 시간 기반)
        String etag = "\"" + product.getVersion() + "\"";

        // 조건부 요청 처리
        String ifNoneMatch = request.getHeader("If-None-Match");
        if (etag.equals(ifNoneMatch)) {
            return ResponseEntity.status(HttpStatus.NOT_MODIFIED)
                    .eTag(etag)
                    .build();
        }

        CacheControl cacheControl = CacheControl
                .maxAge(60, TimeUnit.SECONDS)   // 1분 신선도
                .staleWhileRevalidate(30, TimeUnit.SECONDS)  // 만료 후 30초 허용
                .cachePublic();

        return ResponseEntity.ok()
                .cacheControl(cacheControl)
                .eTag(etag)
                .body(product);
    }

    // 사용자 개인 정보 - private 캐싱
    @GetMapping("/users/{id}/profile")
    public ResponseEntity<UserProfile> getUserProfile(@PathVariable Long id) {
        UserProfile profile = userService.getProfile(id);

        return ResponseEntity.ok()
                .cacheControl(CacheControl.maxAge(5, TimeUnit.MINUTES).cachePrivate())
                .body(profile);
    }

    // 민감 정보 - 캐싱 완전 비활성화
    @GetMapping("/users/{id}/payment")
    public ResponseEntity<PaymentInfo> getPaymentInfo(@PathVariable Long id) {
        PaymentInfo info = paymentService.getInfo(id);

        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(info);
    }
}
```

#### ShallowEtagHeaderFilter를 활용한 자동 ETag 처리

```java
@Configuration
public class CacheConfig {

    /**
     * Spring의 ShallowEtagHeaderFilter를 등록하면
     * 응답 바디의 MD5 해시를 자동으로 ETag로 생성한다.
     * 단, 응답 바디를 버퍼링하므로 대용량 응답에는 주의가 필요하다.
     */
    @Bean
    public FilterRegistrationBean<ShallowEtagHeaderFilter> shallowEtagHeaderFilter() {
        FilterRegistrationBean<ShallowEtagHeaderFilter> registration =
                new FilterRegistrationBean<>(new ShallowEtagHeaderFilter());
        registration.addUrlPatterns("/api/products/*");
        registration.setName("etagFilter");
        return registration;
    }
}
```

### Nginx에서 Cache-Control 설정

```nginx
server {
    listen 80;
    server_name example.com;

    # 해시 기반 정적 파일 - 장기 캐싱
    location ~* \.(js|css)$ {
        root /var/www/html;
        add_header Cache-Control "public, max-age=31536000, immutable";
    }

    # HTML - 캐싱 비활성화 (항상 최신 JS/CSS 참조 보장)
    location ~* \.html$ {
        root /var/www/html;
        add_header Cache-Control "no-cache";
    }

    # API 응답 프록시 - 공유 캐시 설정
    location /api/ {
        proxy_pass http://backend:8080;
        proxy_cache api_cache;
        proxy_cache_valid 200 60s;
        # 백엔드의 Cache-Control 헤더를 클라이언트에 전달
        proxy_pass_header Cache-Control;
    }
}
```

### CDN 연동 시 s-maxage 활용

```java
@GetMapping("/api/catalog")
public ResponseEntity<List<Product>> getCatalog() {
    List<Product> catalog = catalogService.getAll();

    // CDN에는 10분, 브라우저에는 1분 캐싱
    // s-maxage는 public 캐시(CDN)에 우선 적용됨
    String cacheControlHeader = "public, max-age=60, s-maxage=600, stale-while-revalidate=30";

    return ResponseEntity.ok()
            .header(HttpHeaders.CACHE_CONTROL, cacheControlHeader)
            .body(catalog);
}
```

---

## 주의사항 및 트레이드오프

### 1. 캐시 무효화(Cache Invalidation) 문제

> "There are only two hard things in Computer Science: cache invalidation and naming things." — Phil Karlton

`max-age`를 길게 설정할수록 서버 부하는 줄지만, **배포 시 갱신 문제**가 발생한다. 이를 해결하는 대표적인 패턴은 **캐시 버스팅(Cache Busting)** 이다.

```html
<!-- 파일 내용의 해시를 파일명에 포함 → 내용 변경 시 URL 자체가 바뀜 -->
<link rel="stylesheet" href="/static/app.a3f5c2d1.css">
<script src="/static/bundle.7b2e9f4a.js"></script>
```

Webpack, Vite 등 현대 번들러는 이를 자동으로 처리한다. 이 패턴을 사용하면 **HTML은 no-cache**, **JS/CSS는 immutable**로 설정하는 전략이 최적이다.

### 2. private vs public 혼용 위험

인증이 필요한 API에 실수로 `public` 캐싱을 설정하면 CDN이 특정 사용자의 개인 데이터를 다른 사용자에게 반환할 수 있다.

```java
// ❌ 위험: 인증된 사용자 데이터에 public 캐싱
@GetMapping("/api/my-orders")
public ResponseEntity<List<Order>> getMyOrders(@AuthenticationPrincipal User user) {
    return ResponseEntity.ok()
            .cacheControl(CacheControl.maxAge(5, TimeUnit.MINUTES).cachePublic()) // 위험!
            .body(orderService.findByUser(user));
}

// ✅ 올바름: private 또는 no-store 사용
@GetMapping("/api/my-orders")
public ResponseEntity<List<Order>> getMyOrders(@AuthenticationPrincipal User user) {
    return ResponseEntity.ok()
            .cacheControl(CacheControl.maxAge(5, TimeUnit.MINUTES).cachePrivate())
            .body(orderService.findByUser(user));
}
```

### 3. Vary 헤더와의 조합

`Authorization`, `Accept-Encoding`, `Accept-Language` 등 요청 헤더에 따라 응답이 달라지는 경우, `Vary` 헤더를 반드시 함께 설정해야 캐시 오염을 방지할 수 있다.

```java
return ResponseEntity.ok()
        .cacheControl(CacheControl.maxAge(60, TimeUnit.SECONDS).cachePublic())
        .header(HttpHeaders.VARY, "Accept-Encoding", "Accept-Language")
        .body(data);
```

### 4. stale-while-revalidate 활용 시 주의점

`stale-while-revalidate`는 성능과 신선도 사이의 절충안으로 훌륭하지만, **금융 거래, 재고 수량** 등 실시간성이 중요한 데이터에는 적합하지 않다. 이 디렉티브는 데이터가 약간 오래되어도 괜찮은 카탈로그, 블로그 포스트, 공지사항 등에 적합하다.

### 5. must-revalidate vs no-cache

```
# must-revalidate: 캐시가 신선하면 서버 확인 없이 사용, 만료 시에만 재검증
Cache-Control: max-age=3600, must-revalidate

# no-cache: 항상 서버에 재검증 (max-age=0, must-revalidate와 동일한 효과)
Cache-Control: no-cache
```

네트워크 가용성이 낮은 환경에서 `must-revalidate`를 설정하면 만료된 캐시를 절대 사용하지 않으므로 오프라인 상황에서 `504 Gateway Timeout`이 발생할 수 있다.

---

## 정리

효과적인 `Cache-Control` 전략을 위한 핵심 원칙을 정리하면 다음과 같다.

| 리소스 유형 | 권장 Cache-Control |
|---|---|
| 해시 포함 JS/CSS | `public, max-age=31536000, immutable` |
| HTML 파일 | `no-cache` |
| API (공개 데이터) | `public, max-age=60, s-maxage=300, stale-while-revalidate=30` |
| API (사용자 데이터) | `private, max-age=300` |
| 민감 정보 (결제 등) | `no-store` |
| CDN 우선 캐싱 | `public, s-maxage=86400, max-age=3600` |

캐싱 전략은 **"얼마나 오래 캐시할 것인가"** 와 **"언제 무효화할 것인가"** 사이의 균형이다. 리소스의 성격과 변경 빈도, 민감도를 고려하여 각각 다른 정책을 적용하고, CDN과 브라우저 캐시를 계층적으로 활용하는 것이 실무 최적 전략이다.

마지막으로, 캐싱 설정 후에는 반드시 Chrome DevTools의 Network 탭이나 `curl -I` 명령으로 실제 응답 헤더를 확인하고, CDN 환경에서는 퍼지(Purge) 메커니즘도 함께 설계해두길 권장한다.