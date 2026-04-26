# OAuth 2.0과 OpenID Connect 인증 흐름 완전 이해

## 개요

현대 웹 서비스에서 소셜 로그인, API 접근 제어, SSO(Single Sign-On) 구현은 거의 필수가 되었다. 이 모든 것의 근간에 **OAuth 2.0**과 **OpenID Connect(OIDC)**가 있다. 그런데 실무에서 이 두 프로토콜을 혼동하거나 잘못 구현하는 경우를 빈번하게 목격한다.

OAuth 2.0은 **인가(Authorization)** 프로토콜이다. "이 앱이 내 구글 드라이브에 접근해도 돼?"라는 위임 문제를 해결한다. 반면 OpenID Connect는 OAuth 2.0 위에 **인증(Authentication)** 레이어를 추가한 프로토콜이다. "이 사용자가 누구인가?"라는 신원 확인 문제를 다룬다.

이 글에서는 두 프로토콜의 내부 동작 원리와 실제 구현까지 깊이 있게 다룬다.

---

## 핵심 개념

### OAuth 2.0 구성 요소

OAuth 2.0에는 4가지 역할이 존재한다.

| 역할 | 설명 | 예시 |
|---|---|---|
| **Resource Owner** | 자원의 소유자 | 최종 사용자 |
| **Client** | 자원에 접근하려는 앱 | 우리가 만드는 서비스 |
| **Authorization Server** | 토큰을 발급하는 서버 | Google, Kakao, Keycloak |
| **Resource Server** | 보호된 자원을 제공하는 서버 | Google API, 우리 API 서버 |

### OAuth 2.0 Grant Type

OAuth 2.0은 상황에 따라 여러 인가 방식(Grant Type)을 제공한다.

- **Authorization Code Grant**: 웹 서버 앱에 가장 적합하며, 보안성이 가장 높다.
- **PKCE (Proof Key for Code Exchange)**: SPA, 모바일 앱용 Authorization Code의 보안 강화 버전.
- **Client Credentials Grant**: 서버 간 통신(M2M)에 사용.
- **Device Authorization Grant**: TV, IoT 장치처럼 입력이 제한된 환경용.
- **Implicit / Resource Owner Password Credentials**: **레거시**, 현재는 사용 지양.

### Authorization Code Flow 상세 흐름

```
+--------+                               +---------------+
|        |--(1) Authorization Request -->|               |
|        |                               | Authorization |
|        |<-(2) Authorization Code  -----|    Server     |
|        |                               +---------------+
| Client |
|        |                               +---------------+
|        |--(3) Token Request ---------->|               |
|        |   (code + client_secret)      | Authorization |
|        |<-(4) Access Token + ID Token--|    Server     |
|        |                               +---------------+
|        |
|        |                               +---------------+
|        |--(5) API Request (Bearer) --->|    Resource   |
|        |<-(6) Protected Resource  -----|    Server     |
+--------+                               +---------------+
```

### OpenID Connect가 추가하는 것

OIDC는 OAuth 2.0 흐름에 다음 세 가지를 추가한다.

1. **ID Token**: JWT 형식의 사용자 신원 정보 토큰
2. **UserInfo Endpoint**: 사용자 상세 정보를 조회하는 표준 엔드포인트
3. **Discovery Document**: `/.well-known/openid-configuration`에서 메타데이터 자동 조회

**ID Token의 핵심 Claim**

```json
{
  "iss": "https://accounts.google.com",
  "sub": "110169484474386276334",
  "aud": "your-client-id",
  "exp": 1716239022,
  "iat": 1716235422,
  "nonce": "random-nonce-value",
  "email": "user@example.com",
  "email_verified": true,
  "name": "Hong Gildong"
}
```

> **핵심 구분**: Access Token은 Resource Server를 위한 것이고, ID Token은 Client(우리 서비스)가 사용자를 식별하기 위한 것이다. ID Token을 API 호출 Bearer 토큰으로 사용하면 안 된다.

---

## 실전 예제

### Spring Boot + Spring Security로 OIDC 로그인 구현

가장 일반적인 시나리오인 Google OIDC 연동을 Spring Boot로 구현한다.

**의존성 추가 (build.gradle)**

```groovy
implementation 'org.springframework.boot:spring-boot-starter-security'
implementation 'org.springframework.boot:spring-boot-starter-oauth2-client'
```

**application.yml 설정**

```yaml
spring:
  security:
    oauth2:
      client:
        registration:
          google:
            client-id: ${GOOGLE_CLIENT_ID}
            client-secret: ${GOOGLE_CLIENT_SECRET}
            scope:
              - openid
              - email
              - profile
            redirect-uri: "{baseUrl}/login/oauth2/code/{registrationId}"
        provider:
          google:
            issuer-uri: https://accounts.google.com
```

**Security 설정**

```java
@Configuration
@EnableWebSecurity
public class SecurityConfig {

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        http
            .authorizeHttpRequests(auth -> auth
                .requestMatchers("/", "/public/**").permitAll()
                .anyRequest().authenticated()
            )
            .oauth2Login(oauth2 -> oauth2
                .loginPage("/login")
                .userInfoEndpoint(userInfo -> userInfo
                    .oidcUserService(oidcUserService())
                )
                .successHandler(authenticationSuccessHandler())
            )
            .logout(logout -> logout
                .logoutSuccessUrl("/")
                .invalidateHttpSession(true)
                .deleteCookies("JSESSIONID")
            );
        return http.build();
    }

    @Bean
    public OidcUserService oidcUserService() {
        OidcUserService delegate = new OidcUserService();
        return new OidcUserService() {
            @Override
            public OidcUser loadUser(OidcUserRequest request) throws OAuth2AuthenticationException {
                OidcUser oidcUser = delegate.loadUser(request);
                // ID Token의 Claims에서 사용자 정보 추출
                String sub = oidcUser.getSubject();
                String email = oidcUser.getEmail();
                // 내부 DB와 연동하는 로직
                return oidcUser;
            }
        };
    }
}
```

### PKCE Flow 구현 (SPA 백엔드 API)

SPA 환경에서는 `client_secret`을 안전하게 보관할 수 없으므로 PKCE를 사용해야 한다.

```java
@RestController
@RequestMapping("/auth")
public class PkceAuthController {

    // Code Verifier 생성
    public String generateCodeVerifier() throws Exception {
        SecureRandom sr = new SecureRandom();
        byte[] code = new byte[32];
        sr.nextBytes(code);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(code);
    }

    // Code Challenge 생성 (S256 방식)
    public String generateCodeChallenge(String codeVerifier) throws Exception {
        byte[] bytes = codeVerifier.getBytes(StandardCharsets.US_ASCII);
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        md.update(bytes, 0, bytes.length);
        byte[] digest = md.digest();
        return Base64.getUrlEncoder().withoutPadding().encodeToString(digest);
    }

    @GetMapping("/pkce-init")
    public Map<String, String> initPkce(HttpSession session) throws Exception {
        String codeVerifier = generateCodeVerifier();
        String codeChallenge = generateCodeChallenge(codeVerifier);
        
        // code_verifier는 서버 세션에 안전하게 보관
        session.setAttribute("code_verifier", codeVerifier);
        
        // 클라이언트에는 code_challenge만 반환
        return Map.of(
            "code_challenge", codeChallenge,
            "code_challenge_method", "S256"
        );
    }

    @PostMapping("/token")
    public ResponseEntity<?> exchangeToken(
            @RequestParam String code,
            HttpSession session) {
        
        String codeVerifier = (String) session.getAttribute("code_verifier");
        if (codeVerifier == null) {
            return ResponseEntity.status(400).body("Invalid session");
        }
        
        // Authorization Server에 code + code_verifier로 토큰 요청
        // client_secret 없이 code_verifier만으로 검증 가능
        // ... 토큰 교환 로직
        session.removeAttribute("code_verifier");
        return ResponseEntity.ok("token exchanged");
    }
}
```

### Client Credentials Flow (서버 간 M2M 통신)

```java
@Service
public class M2MTokenService {

    private final WebClient webClient;
    private volatile String cachedToken;
    private volatile Instant tokenExpiry;

    public M2MTokenService(WebClient.Builder builder) {
        this.webClient = builder.baseUrl("https://auth.example.com").build();
    }

    public String getAccessToken() {
        // 토큰 캐싱으로 불필요한 요청 방지
        if (cachedToken != null && Instant.now().isBefore(tokenExpiry.minusSeconds(30))) {
            return cachedToken;
        }
        return fetchNewToken();
    }

    private synchronized String fetchNewToken() {
        TokenResponse response = webClient.post()
            .uri("/oauth/token")
            .contentType(MediaType.APPLICATION_FORM_URLENCODED)
            .bodyValue("grant_type=client_credentials" +
                       "&client_id=" + clientId +
                       "&client_secret=" + clientSecret +
                       "&scope=internal:read internal:write")
            .retrieve()
            .bodyToMono(TokenResponse.class)
            .block();

        this.cachedToken = response.getAccessToken();
        this.tokenExpiry = Instant.now().plusSeconds(response.getExpiresIn());
        return this.cachedToken;
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. State 파라미터는 반드시 검증하라

Authorization Request에 `state` 파라미터를 포함하고, 콜백에서 반드시 검증해야 한다. 이를 생략하면 **CSRF 공격**에 취약해진다. Spring Security는 기본적으로 처리해주지만, 커스텀 구현 시에는 직접 챙겨야 한다.

```java
// 요청 시 state 생성 및 세션 저장
String state = UUID.randomUUID().toString();
session.setAttribute("oauth_state", state);

// 콜백에서 검증
String returnedState = request.getParameter("state");
String savedState = (String) session.getAttribute("oauth_state");
if (!returnedState.equals(savedState)) {
    throw new SecurityException("State mismatch - possible CSRF attack");
}
```

### 2. ID Token 검증을 절대 생략하지 마라

ID Token을 신뢰하려면 다음 사항을 반드시 검증해야 한다.

- `iss` (발급자): 신뢰하는 Authorization Server인지 확인
- `aud` (대상): 우리의 `client_id`가 포함되어 있는지 확인
- `exp` (만료시간): 토큰이 만료되지 않았는지 확인
- `nonce`: 재전송 공격 방지를 위한 일회성 값 검증
- **서명 검증**: Authorization Server의 공개키로 JWT 서명을 반드시 검증

### 3. Access Token을 로컬 스토리지에 저장하지 마라

| 저장소 | XSS 취약성 | CSRF 취약성 | 권장 여부 |
|---|---|---|---|
| LocalStorage | 높음 | 없음 | ❌ |
| SessionStorage | 높음 | 없음 | ❌ |
| HttpOnly Cookie | 없음 | 있음 (SameSite로 완화) | ✅ |
| Memory (in-app) | 낮음 | 없음 | ✅ (SPA 단기) |

### 4. Refresh Token 로테이션

Refresh Token이 탈취되었을 때의 피해를 최소화하기 위해, **Refresh Token Rotation** 정책을 적용해야 한다. 토큰을 갱신할 때마다 새로운 Refresh Token을 발급하고 이전 것을 무효화한다. 만약 이미 사용된 Refresh Token으로 요청이 오면 해당 세션의 모든 토큰을 즉시 폐기해야 한다.

### 5. OAuth 2.0을 인증(Authentication)으로 오해하는 문제

OAuth 2.0만으로는 사용자 신원을 확인할 수 없다. Access Token이 있다고 해서 그것이 특정 사용자를 의미하지는 않는다. **신원 확인이 필요하다면 반드시 OIDC를 사용**하고 ID Token의 `sub` Claim을 사용자 식별자로 활용해야 한다.

---

## 정리

OAuth 2.0과 OpenID Connect를 구분하고 올바르게 사용하는 것은 보안의 기초다.

- **OAuth 2.0**: 제3자 앱에게 특정 권한을 위임하는 **인가** 프레임워크
- **OpenID Connect**: OAuth 2.0 위에 **인증** 레이어를 추가한 표준, ID Token으로 사용자 신원 확인
- **Grant Type 선택**: 웹 서버 앱은 Authorization Code, SPA/모바일은 PKCE, 서버 간은 Client Credentials
- **보안 필수 체크리스트**: State 검증, Nonce 검증, ID Token 서명 검증, Secure HttpOnly 쿠키 사용

실무에서는 이 프로토콜을 직접 구현하기보다 Keycloak, Spring Authorization Server, Auth0 같은 검증된 솔루션을 활용하되, **내부 동작 원리를 정확히 이해**하고 있어야 트러블슈팅과 보안 감사에서 당황하지 않는다.

프로토콜의 세부 명세는 [RFC 6749 (OAuth 2.0)](https://www.rfc-editor.org/rfc/rfc6749), [RFC 7636 (PKCE)](https://www.rfc-editor.org/rfc/rfc7636), [OpenID Connect Core 1.0](https://openid.net/specs/openid-connect-core-1_0.html)을 직접 참고하는 것을 강력히 권장한다.