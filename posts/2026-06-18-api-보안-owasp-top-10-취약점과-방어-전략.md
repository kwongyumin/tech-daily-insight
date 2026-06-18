# API 보안 OWASP Top 10 취약점과 방어 전략

## 개요

API는 현대 애플리케이션 아키텍처의 핵심입니다. 마이크로서비스, 모바일 앱, SPA(Single Page Application) 등 거의 모든 서비스가 API를 통해 데이터를 주고받습니다. 그만큼 API는 공격자에게 가장 매력적인 공격 벡터가 되었습니다.

OWASP(Open Web Application Security Project)는 2019년에 이어 **2023년 OWASP API Security Top 10**을 업데이트하며 API 보안의 중요성을 강조했습니다. 기존 웹 취약점과 달리 API 고유의 특성을 반영한 이 목록은 실무 개발자라면 반드시 숙지해야 합니다.

이 포스팅에서는 OWASP API Security Top 10 중 핵심 취약점을 실제 Spring Boot 예제와 함께 살펴보고, 즉시 적용 가능한 방어 전략을 공유합니다.

---

## 핵심 개념: OWASP API Security Top 10 (2023)

| 순위 | 취약점 |
|------|--------|
| API1 | Broken Object Level Authorization (BOLA) |
| API2 | Broken Authentication |
| API3 | Broken Object Property Level Authorization |
| API4 | Unrestricted Resource Consumption |
| API5 | Broken Function Level Authorization |
| API6 | Unrestricted Access to Sensitive Business Flows |
| API7 | Server Side Request Forgery (SSRF) |
| API8 | Security Misconfiguration |
| API9 | Improper Inventory Management |
| API10 | Unsafe Consumption of APIs |

실무에서 가장 자주 마주치는 API1, API2, API3, API4, API5를 중심으로 깊게 다루겠습니다.

---

## 실전 예제

### API1: Broken Object Level Authorization (BOLA)

BOLA는 과거 IDOR(Insecure Direct Object Reference)로 불리던 취약점입니다. 사용자가 자신의 권한 밖의 리소스에 접근할 수 있는 경우입니다.

**취약한 코드 예시:**

```java
@GetMapping("/api/orders/{orderId}")
public ResponseEntity<Order> getOrder(@PathVariable Long orderId) {
    // 누가 요청했는지 검증하지 않음!
    Order order = orderRepository.findById(orderId)
        .orElseThrow(() -> new NotFoundException("Order not found"));
    return ResponseEntity.ok(order);
}
```

공격자는 `orderId`를 1, 2, 3... 순차적으로 변경하며 타인의 주문 정보를 모두 조회할 수 있습니다.

**방어 코드:**

```java
@GetMapping("/api/orders/{orderId}")
public ResponseEntity<Order> getOrder(
        @PathVariable Long orderId,
        @AuthenticationPrincipal UserDetails userDetails) {
    
    Order order = orderRepository.findById(orderId)
        .orElseThrow(() -> new NotFoundException("Order not found"));
    
    // 현재 인증된 사용자와 리소스 소유자 검증
    if (!order.getUserId().equals(userDetails.getUserId())) {
        throw new AccessDeniedException("접근 권한이 없습니다.");
    }
    
    return ResponseEntity.ok(order);
}
```

더 나아가 쿼리 자체에서 소유권을 검증하는 방식이 더 안전합니다:

```java
@Query("SELECT o FROM Order o WHERE o.id = :orderId AND o.userId = :userId")
Optional<Order> findByIdAndUserId(
    @Param("orderId") Long orderId, 
    @Param("userId") Long userId
);
```

---

### API2: Broken Authentication

약한 인증 메커니즘, 토큰 탈취, 무차별 대입 공격 등이 포함됩니다.

**JWT 토큰 검증 강화 예시:**

```java
@Component
public class JwtTokenValidator {

    @Value("${jwt.secret}")
    private String secret;

    public Claims validateToken(String token) {
        try {
            return Jwts.parserBuilder()
                .setSigningKey(Keys.hmacShaKeyFor(secret.getBytes(StandardCharsets.UTF_8)))
                .build()
                .parseClaimsJws(token)
                .getBody();
        } catch (ExpiredJwtException e) {
            throw new AuthException("토큰이 만료되었습니다.");
        } catch (JwtException e) {
            // 구체적인 에러 메시지 노출 금지 - 공격자에게 힌트를 줄 수 있음
            throw new AuthException("유효하지 않은 토큰입니다.");
        }
    }
}
```

**Rate Limiting으로 무차별 대입 공격 방어 (Spring + Bucket4j):**

```java
@Component
public class RateLimitFilter extends OncePerRequestFilter {

    private final Map<String, Bucket> buckets = new ConcurrentHashMap<>();

    @Override
    protected void doFilterInternal(HttpServletRequest request,
            HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {

        String ip = request.getRemoteAddr();
        Bucket bucket = buckets.computeIfAbsent(ip, this::createBucket);

        if (bucket.tryConsume(1)) {
            chain.doFilter(request, response);
        } else {
            response.setStatus(HttpStatus.TOO_MANY_REQUESTS.value());
            response.getWriter().write("{\"error\": \"Too many requests\"}");
        }
    }

    private Bucket createBucket(String ip) {
        // 1분에 최대 20회 요청 허용
        return Bucket.builder()
            .addLimit(Bandwidth.classic(20, Refill.greedy(20, Duration.ofMinutes(1))))
            .build();
    }
}
```

---

### API3: Broken Object Property Level Authorization

응답에 민감한 필드를 포함하거나(과도한 데이터 노출), 수정해서는 안 되는 필드를 수정할 수 있는 경우(Mass Assignment)입니다.

**Mass Assignment 취약점:**

```java
// 취약한 코드 - 클라이언트가 role, isAdmin 등을 임의로 변경 가능
@PutMapping("/api/users/{id}")
public ResponseEntity<User> updateUser(@PathVariable Long id, 
                                        @RequestBody User user) {
    // user 객체에 role, isAdmin 등이 포함될 수 있음!
    return ResponseEntity.ok(userRepository.save(user));
}
```

**DTO를 활용한 방어:**

```java
// 수정 가능한 필드만 포함하는 DTO
public record UserUpdateRequest(
    @NotBlank String name,
    @Email String email,
    String phoneNumber
) {
    // role, isAdmin, password 등 민감 필드 제외
}

@PutMapping("/api/users/{id}")
public ResponseEntity<UserResponse> updateUser(
        @PathVariable Long id,
        @Valid @RequestBody UserUpdateRequest request,
        @AuthenticationPrincipal UserDetails userDetails) {

    User user = userRepository.findById(id)
        .orElseThrow(() -> new NotFoundException("User not found"));

    // 명시적으로 허용된 필드만 업데이트
    user.updateProfile(request.name(), request.email(), request.phoneNumber());
    
    // 응답도 DTO로 변환 - 민감 정보 제외
    return ResponseEntity.ok(UserResponse.from(userRepository.save(user)));
}
```

**응답 데이터 최소화 (Projection 활용):**

```java
// 필요한 필드만 노출하는 Projection
public interface UserSummary {
    Long getId();
    String getName();
    String getEmail();
    // password, internalNotes, creditCardInfo 등 제외
}

@Query("SELECT u FROM User u WHERE u.id = :id")
Optional<UserSummary> findSummaryById(@Param("id") Long id);
```

---

### API4: Unrestricted Resource Consumption

요청 크기, 페이지네이션 한계 없음, 과도한 파일 업로드 등 리소스를 무제한으로 소비할 수 있는 취약점입니다.

```java
@RestController
@RequestMapping("/api/products")
public class ProductController {

    private static final int MAX_PAGE_SIZE = 100;

    @GetMapping
    public ResponseEntity<Page<ProductResponse>> getProducts(
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size) {
        
        // 페이지 크기 제한으로 DB 과부하 방지
        int validatedSize = Math.min(size, MAX_PAGE_SIZE);
        Pageable pageable = PageRequest.of(page, validatedSize);
        
        return ResponseEntity.ok(productService.findAll(pageable));
    }
}
```

**파일 업로드 크기 및 타입 제한:**

```yaml
# application.yml
spring:
  servlet:
    multipart:
      max-file-size: 10MB
      max-request-size: 50MB
```

```java
@PostMapping("/api/files/upload")
public ResponseEntity<String> uploadFile(@RequestParam("file") MultipartFile file) {
    
    // MIME 타입 화이트리스트 검증
    List<String> allowedTypes = List.of("image/jpeg", "image/png", "application/pdf");
    String contentType = file.getContentType();
    
    if (!allowedTypes.contains(contentType)) {
        throw new BadRequestException("허용되지 않는 파일 형식입니다.");
    }
    
    // 파일 확장자 이중 검증 (Content-Type 스푸핑 방지)
    String originalFilename = file.getOriginalFilename();
    String extension = FilenameUtils.getExtension(originalFilename).toLowerCase();
    
    if (!List.of("jpg", "jpeg", "png", "pdf").contains(extension)) {
        throw new BadRequestException("허용되지 않는 파일 확장자입니다.");
    }
    
    return ResponseEntity.ok(fileService.store(file));
}
```

---

### API5: Broken Function Level Authorization

일반 사용자가 관리자 기능에 접근하거나 권한 없는 기능을 호출할 수 있는 경우입니다.

```java
@Configuration
@EnableWebSecurity
public class SecurityConfig {

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        http
            .authorizeHttpRequests(auth -> auth
                // 공개 엔드포인트
                .requestMatchers(HttpMethod.GET, "/api/products/**").permitAll()
                .requestMatchers("/api/auth/**").permitAll()
                
                // 인증된 사용자 전용
                .requestMatchers("/api/orders/**").hasRole("USER")
                
                // 관리자 전용 - URL 패턴으로 명시적 제한
                .requestMatchers("/api/admin/**").hasRole("ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/**").hasRole("ADMIN")
                
                // 나머지는 모두 인증 필요 (기본 거부)
                .anyRequest().authenticated()
            );
        
        return http.build();
    }
}
```

메서드 레벨 보안도 병행하면 더욱 안전합니다:

```java
@Service
public class UserService {

    @PreAuthorize("hasRole('ADMIN') or #userId == authentication.principal.id")
    public void deleteUser(Long userId) {
        userRepository.deleteById(userId);
    }

    @PreAuthorize("hasRole('ADMIN')")
    public List<User> getAllUsersWithSensitiveData() {
        return userRepository.findAllWithSensitiveInfo();
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. 보안 vs 성능
Rate Limiting과 요청 검증은 성능에 영향을 줍니다. 인메모리 기반의 Bucket4j는 단일 서버에서 효과적이지만, **멀티 인스턴스 환경에서는 Redis 기반 분산 Rate Limiting**이 필요합니다. 트래픽 규모에 맞는 구현을 선택하세요.

### 2. 지나친 에러 메시지 숨기기
보안을 위해 에러를 모두 뭉뚱그리면 개발 생산성이 떨어집니다. **개발 환경에서는 상세 에러, 프로덕션에서는 최소화된 에러**를 반환하는 프로파일 분리 전략이 유효합니다.

### 3. DTO 관리 비용
엔티티마다 Request/Response DTO를 분리하면 코드량이 늘어납니다. **MapStruct 등 매핑 라이브러리**를 활용하면 보일러플레이트를 줄이면서 안전하게 관리할 수 있습니다.

### 4. JWT 토큰 무효화 문제
JWT는 stateless 특성상 토큰 강제 무효화가 어렵습니다. **Refresh Token Rotation + Revocation 목록을 Redis에 관리**하거나, 짧은 만료 시간(15분)을 설정하는 방식으로 리스크를 줄이세요.

### 5. OWASP Top 10만으로는 부족
OWASP API Top 10은 출발점입니다. API Gateway에서의 WAF 적용, TLS 강제화, CORS 정책 설정, Secrets Management(Vault, AWS Secrets Manager 등)까지 **계층적 방어(Defense in Depth)** 전략이 필요합니다.

---

## 정리

OWASP API Security Top 10은 단순히 알아두는 리스트가 아니라, **코드 리뷰와 보안 감사의 체크리스트**로 활용해야 합니다.

| 취약점 | 핵심 방어 전략 |
|--------|----------------|
| BOLA | 소유권 검증을 쿼리 레벨에서 처리 |
| Broken Auth | JWT 검증 강화 + Rate Limiting |
| Property Auth | DTO 분리 + 응답 최소화 |
| Resource Consumption | 페이지 크기 제한 + 파일 타입 검증 |
| Function Auth | URL + 메서드 레벨 이중 권한 검증 |

실무에서는 **보안을 기능 개발 이후에 추가하는 것이 아니라, 설계 단계부터 녹여내는 Shift-Left Security** 접근법이 가장 효과적입니다. PR 리뷰 시 OWASP 체크리스트를 팀 문화로 정착시키고, SAST 도구(SonarQube, Checkmarx)를 CI/CD 파이프라인에 통합하는 것을 강력히 권장합니다.

보안은 한 번 적용하고 끝나는 것이 아닙니다. 위협 모델은 계속 진화하므로, 정기적인 보안 리뷰와 침투 테스트를 통해 방어 전략도 함께 업데이트해야 합니다.