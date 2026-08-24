# Spring Security 6.x OAuth2/OIDC 설정 가이드 — 보안 및 규정 준수

## 보안 및 규정 준수 관점에서의 심화 구성 전략

---

## 개요

Spring Security 6.x의 OAuth2/OIDC 기본 설정법은 이미 잘 알려져 있다. 이 글은 그 다음 단계를 다룬다. **GDPR, SOC 2, ISO 27001, NIST SP 800-63B** 등 실제 규정 준수 환경에서 OAuth2/OIDC를 운영할 때 마주치는 보안 리스크, 구체적인 수치 기반 설정 결정, 그리고 트레이드오프를 중심으로 논의한다.

기본 설정 튜토리얼을 기대한다면 공식 문서가 훨씬 낫다. 이 글은 **"왜 이 값인가?", "이 설정을 잘못하면 어떤 규정을 위반하는가?", "어떤 선택이 보안과 UX 사이에서 어느 쪽을 희생하는가?"** 에 집중한다.

---

## 핵심 개념: 규정 준수 관점에서 놓치기 쉬운 OAuth2/OIDC 보안 요소

### 1. 토큰 수명 설정과 NIST 가이드라인

NIST SP 800-63B는 세션 및 토큰 수명에 대해 명확한 권고를 제시한다. Access Token의 수명은 **최대 1시간(3600초)** 이내를 권장하며, 고위험 작업(금융 트랜잭션, 개인정보 변경)의 경우 **15분 이하**를 요구하는 기업 보안 정책이 일반적이다.

Spring Security의 기본 설정은 이 기준을 **명시적으로 강제하지 않는다.** Authorization Server 측에서 발급된 토큰의 `exp` 클레임을 Resource Server가 어떻게 검증하는지 직접 제어해야 한다.

```java
@Configuration
@EnableWebSecurity
public class ResourceServerSecurityConfig {

    @Bean
    public SecurityFilterChain securityFilterChain(HttpSecurity http) throws Exception {
        http
            .oauth2ResourceServer(oauth2 -> oauth2
                .jwt(jwt -> jwt
                    .jwtAuthenticationConverter(jwtAuthenticationConverter())
                    .decoder(strictJwtDecoder())
                )
            );
        return http.build();
    }

    @Bean
    public JwtDecoder strictJwtDecoder() {
        // 토큰 수명 상한선을 Resource Server 레벨에서도 강제
        NimbusJwtDecoder decoder = NimbusJwtDecoder
            .withJwkSetUri("https://auth-server/.well-known/jwks.json")
            .build();

        // 클록 스큐: 서버 간 시간 차이 허용 범위 - NIST는 5분 이내 권고
        // 너무 크면 만료된 토큰을 유효로 판단할 위험
        OAuth2TokenValidator<Jwt> clockSkewValidator =
            new JwtTimestampValidator(Duration.ofSeconds(30)); // 30초로 엄격 설정

        // 최대 토큰 수명 강제 검증 (Authorization Server 설정과 무관하게 Resource Server 자체 정책 적용)
        OAuth2TokenValidator<Jwt> maxLifetimeValidator = jwt -> {
            Instant issuedAt = jwt.getIssuedAt();
            Instant expiresAt = jwt.getExpiresAt();
            if (issuedAt != null && expiresAt != null) {
                long lifetimeSeconds = Duration.between(issuedAt, expiresAt).getSeconds();
                if (lifetimeSeconds > 3600) { // 1시간 초과 토큰 거부
                    return OAuth2TokenValidatorResult.failure(
                        new OAuth2Error("invalid_token", 
                            "Token lifetime exceeds maximum allowed duration of 3600 seconds", null)
                    );
                }
            }
            return OAuth2TokenValidatorResult.success();
        };

        decoder.setJwtValidator(new DelegatingOAuth2TokenValidator<>(
            JwtValidators.createDefault(),
            clockSkewValidator,
            maxLifetimeValidator
        ));

        return decoder;
    }
}
```

### 2. PKCE 강제 적용과 Authorization Code Injection 방어

PKCE(Proof Key for Code Exchange)는 OAuth 2.1 드래프트에서 **모든 클라이언트 유형에 필수**로 격상되었다. 공공기관 및 금융권 프로젝트에서는 현재도 PKCE 없는 Authorization Code Flow를 보안 취약점으로 분류한다.

Spring Security 6.x에서는 PKCE를 클라이언트 측 옵션으로 두지 말고, **서버 측에서 강제해야 한다.**

```java
// Authorization Server 설정 (Spring Authorization Server 1.x 기준)
@Bean
public RegisteredClientRepository registeredClientRepository() {
    RegisteredClient client = RegisteredClient.withId(UUID.randomUUID().toString())
        .clientId("secure-client")
        .clientAuthenticationMethod(ClientAuthenticationMethod.NONE) // Public client
        .authorizationGrantType(AuthorizationGrantType.AUTHORIZATION_CODE)
        .redirectUri("https://app.example.com/callback")
        .scope(OidcScopes.OPENID)
        .scope(OidcScopes.PROFILE)
        .clientSettings(ClientSettings.builder()
            .requireProofKey(true)           // PKCE 강제 - 이 줄이 핵심
            .requireAuthorizationConsent(true) // 명시적 동의 강제 (GDPR Article 7)
            .build()
        )
        .tokenSettings(TokenSettings.builder()
            .accessTokenTimeToLive(Duration.ofMinutes(15))   // 고위험 환경
            .refreshTokenTimeToLive(Duration.ofHours(8))     // 업무 시간 기준
            .reuseRefreshTokens(false)   // Refresh Token Rotation 강제
            .build()
        )
        .build();

    return new InMemoryRegisteredClientRepository(client);
}
```

`reuseRefreshTokens(false)` 설정은 **Refresh Token Rotation**을 활성화한다. 탈취된 Refresh Token이 사용될 경우 감지 가능해지며, 이는 SOC 2 Type II의 "모니터링 및 이상 감지" 요건과 직결된다.

---

## 실전 예제: 규정 준수 감사를 위한 보안 이벤트 로깅

### GDPR과 감사 로그 요건

GDPR Article 30(처리 활동 기록)과 ISO 27001 A.12.4(이벤트 로깅)는 인증/인가 이벤트에 대한 상세 로그를 요구한다. Spring Security의 기본 로그는 이 기준을 충족하지 못한다.

```java
@Component
@Slf4j
public class OAuth2SecurityAuditListener {

    private final AuditEventRepository auditEventRepository;

    // 인증 성공 이벤트 - 누가, 언제, 어디서, 어떤 방법으로
    @EventListener
    public void onAuthenticationSuccess(AuthenticationSuccessEvent event) {
        Authentication auth = event.getAuthentication();
        
        if (auth instanceof OAuth2AuthenticationToken oauthToken) {
            Map<String, Object> auditData = new LinkedHashMap<>();
            auditData.put("event_type", "OAUTH2_LOGIN_SUCCESS");
            auditData.put("timestamp", Instant.now().toString());
            auditData.put("subject", oauthToken.getName());
            auditData.put("provider", oauthToken.getAuthorizedClientRegistrationId());
            auditData.put("granted_authorities", 
                oauthToken.getAuthorities().stream()
                    .map(GrantedAuthority::getAuthority)
                    .collect(Collectors.toList()));
            
            // 주의: IP 주소는 GDPR상 개인정보 - 로그 보존 정책과 함께 관리 필요
            // auditData.put("client_ip", resolveClientIp()); // 별도 동의/정책 필요
            
            log.info("SECURITY_AUDIT: {}", objectToJson(auditData));
            // 구조화된 로그를 SIEM 시스템(Splunk, ELK)으로 전송
        }
    }

    // 토큰 검증 실패 - 잠재적 공격 시도 감지
    @EventListener
    public void onAuthenticationFailure(AbstractAuthenticationFailureEvent event) {
        Map<String, Object> auditData = new LinkedHashMap<>();
        auditData.put("event_type", "OAUTH2_TOKEN_VALIDATION_FAILURE");
        auditData.put("timestamp", Instant.now().toString());
        auditData.put("failure_reason", event.getException().getMessage());
        auditData.put("exception_type", event.getException().getClass().getSimpleName());
        
        // Brute force / Token stuffing 감지를 위한 알림 트리거
        log.warn("SECURITY_AUDIT_ALERT: {}", objectToJson(auditData));
    }
}
```

### 토큰 스코프 최소권한 원칙(Principle of Least Privilege) 강제

```java
@Bean
public SecurityFilterChain apiSecurityFilterChain(HttpSecurity http) throws Exception {
    http
        .securityMatcher("/api/**")
        .authorizeHttpRequests(auth -> auth
            // 스코프 기반 + 역할 기반 이중 검증
            // PCI-DSS 3.2.1: 결제 데이터 접근은 명시적 스코프 필수
            .requestMatchers("/api/payments/**")
                .access(new WebExpressionAuthorizationManager(
                    "hasAuthority('SCOPE_payments:write') and hasRole('VERIFIED_MERCHANT')"
                ))
            // HIPAA: 의료 데이터 접근 - 감사 로그 + 스코프 모두 필요
            .requestMatchers("/api/health-records/**")
                .access(new WebExpressionAuthorizationManager(
                    "hasAuthority('SCOPE_phi:read') and hasAuthority('SCOPE_audit:enabled')"
                ))
            .requestMatchers("/api/public/**").permitAll()
            .anyRequest().authenticated()
        )
        .oauth2ResourceServer(oauth2 -> oauth2
            .jwt(jwt -> jwt.jwtAuthenticationConverter(scopeAwareConverter()))
            // 401 vs 403 명확히 구분 - 일부 규정은 적절한 오류 응답 코드를 요구
            .authenticationEntryPoint(new BearerTokenAuthenticationEntryPoint())
        );
    
    return http.build();
}
```

---

## 주의사항 및 트레이드오프

### 1. JWK 캐싱: 보안 vs 가용성의 딜레마

Spring Security는 기본적으로 JWK Set을 캐싱한다. 문제는 **키 교체(Key Rotation)** 시나리오다.

| 캐시 TTL | 보안 리스크 | 가용성 영향 |
|---------|------------|-----------|
| 0 (캐시 없음) | 최소 | Authorization Server 장애 시 전체 서비스 중단 |
| 5분 | 낮음 | 키 교체 후 최대 5분간 구 키로 서명된 토큰 허용 |
| 1시간 | 중간 | 침해 사고 시 즉각 키 폐기 불가 |
| 24시간 | 높음 | 사실상 키 교체 불가 |

**권고**: 5분 캐시 + 키 교체 이벤트 웹훅으로 강제 갱신하는 하이브리드 방식. 금융권은 대부분 5분 이하를 요구한다.

```java
@Bean
public JwtDecoder jwtDecoderWithControlledCaching() {
    return NimbusJwtDecoder
        .withJwkSetUri("https://auth-server/.well-known/jwks.json")
        .jwsAlgorithm(SignatureAlgorithm.RS256) // 알고리즘 명시 강제 (alg:none 공격 방어)
        .cache(Cache.builder()
            .maximumSize(100)
            .expireAfterWrite(Duration.ofMinutes(5)) // 5분 캐시
            .build()
        )
        .build();
}
```

### 2. `alg: none` 및 알고리즘 혼용 공격 방어

JWT 알고리즘을 명시하지 않으면 `alg: none` 공격 또는 RS256/HS256 혼용 공격에 노출된다. **모든 프로덕션 환경에서 허용 알고리즘을 화이트리스트로 명시해야 한다.** HMAC 기반(HS256)은 Authorization Server와 Resource Server가 시크릿을 공유해야 해 **마이크로서비스 환경에서는 권장하지 않는다.** RSA 또는 ECDSA 기반을 사용해야 한다.

### 3. OIDC UserInfo 엔드포인트 호출의 함정

```java
// 잘못된 패턴: UserInfo 엔드포인트를 매 요청마다 호출
// - 네트워크 레이턴시 증가
// - Authorization Server 부하 집중
// - GDPR 관점: 불필요한 개인정보 전송 최소화 원칙 위반 가능성

// 올바른 패턴: ID Token 클레임 우선 사용, UserInfo는 추가 클레임이 필요할 때만
@Bean
public OAuth2UserService<OidcUserRequest, OidcUser> oidcUserService() {
    OidcUserService delegate = new OidcUserService();
    // 요청할 추가 클레임 최소화 - 데이터 최소화 원칙(GDPR Article 5(1)(c))
    delegate.setAccessibleScopes(Set.of(OidcScopes.OPENID)); // profile, email 등 선택적 포함
    return delegate;
}
```

### 4. State 파라미터와 CSRF 방어

OAuth2 Authorization Code Flow에서 `state` 파라미터는 CSRF 방어의 핵심이다. Spring Security는 기본적으로 이를 처리하지만, **세션리스(stateless) 아키텍처**에서 이를 비활성화하는 경우가 있다. 이는 중대한 보안 취약점이다.

세션리스 환경에서는 `state`를 서명된 JWT로 대체하고, PKCE와 함께 사용하는 패턴을 적용해야 한다. 이것이 OAuth 2.1이 PKCE를 보편화하려는 이유이기도 하다.

---

## 정리

규정 준수 관점에서 Spring Security 6.x OAuth2/OIDC 설정의 핵심은 다음과 같다.

- **토큰 수명**: NIST 기준에 맞게 Resource Server 레벨에서도 강제 검증 (1시간 이하, 고위험 15분 이하)
- **PKCE**: 클라이언트 선택이 아닌 서버 강제. Refresh Token Rotation과 함께 적용
- **감사 로그**: GDPR/ISO 27001 요건을 충족하는 구조화된 보안 이벤트 로그 체계 구축
- **최소 권한**: 스코프 기반 접근 제어를 역할(Role)과 분리하여 이중 검증
- **알고리즘 명시**: `alg: none` 및 혼용 공격 차단을 위한 화이트리스트 적용
- **JWK 캐싱**: 보안(단기 캐시)과 가용성(장애 대응) 사이의 트레이드오프를 명확히 결정

보안 설정은 "켜고 끄는" 스위치가 아니라, **각 규정의 요구사항과 서비스 특성에 따라 수치와 정책을 명확하게 결정하는 엔지니어링 과제**다. 기본값을 믿지 말고, 각 설정의 보안 함의를 이해하고 문서화하라.