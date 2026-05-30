# Spring Boot 3.x의 새로운 기능 완벽 가이드

## 개요

Spring Boot 3.x는 단순한 버전 업그레이드가 아닙니다. Java 17 베이스라인 채택, Jakarta EE 10 마이그레이션, GraalVM Native Image 공식 지원 등 생태계 전반에 걸친 근본적인 변화를 담고 있습니다. 2022년 11월에 출시된 이후 현재(3.2.x ~ 3.3.x)까지 꾸준히 발전해온 Spring Boot 3.x는 **클라우드 네이티브**, **관찰 가능성(Observability)**, **성능 최적화**라는 세 축을 중심으로 설계되었습니다.

이 글에서는 실무 현장에서 바로 적용 가능한 핵심 기능들을 코드 예제와 함께 상세히 살펴보겠습니다. Spring Boot 2.x에서 마이그레이션을 고려 중이거나, 이미 3.x를 사용하지만 새 기능을 충분히 활용하지 못하고 있다면 이 글이 좋은 나침반이 될 것입니다.

---

## 핵심 개념

### 1. Java 17 베이스라인과 Jakarta EE 10

Spring Boot 3.x는 **Java 17을 최소 요구사항**으로 설정했습니다. 이는 `javax.*` 패키지가 `jakarta.*`로 전면 교체됨을 의미합니다. 기존 `javax.persistence`, `javax.servlet` 등의 임포트를 모두 변경해야 합니다.

```java
// Before (Spring Boot 2.x)
import javax.persistence.Entity;
import javax.persistence.Id;
import javax.servlet.http.HttpServletRequest;

// After (Spring Boot 3.x)
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.servlet.http.HttpServletRequest;
```

### 2. GraalVM Native Image 공식 지원

Spring Boot 3.x는 **Spring Native**를 프레임워크 코어에 통합하여 GraalVM Native Image 빌드를 공식 지원합니다. AOT(Ahead-of-Time) 컴파일을 통해 JVM 없이 실행 가능한 네이티브 실행 파일을 생성할 수 있으며, 이는 컨테이너 환경에서 **기동 시간 수십~수백 ms 단축**, **메모리 사용량 대폭 감소**라는 이점을 제공합니다.

### 3. Micrometer 기반 관찰 가능성(Observability)

Spring Boot 3.x는 **Micrometer Observation API**를 전면 채택하여 메트릭, 트레이싱, 로깅을 통합된 방식으로 관리합니다. OpenTelemetry와의 연동이 대폭 간소화되었습니다.

---

## 실전 예제

### Virtual Threads (Project Loom) 통합

Spring Boot 3.2부터 **Java 21의 Virtual Threads**를 공식 지원합니다. 기존 톰캣의 스레드풀 모델에서 Virtual Thread 모델로 전환하면 I/O 바운드 애플리케이션의 처리량을 크게 향상시킬 수 있습니다.

```yaml
# application.yml
spring:
  threads:
    virtual:
      enabled: true
```

```java
@Configuration
public class ThreadConfig {

    // Virtual Threads를 명시적으로 활성화하는 방법
    @Bean
    public TomcatProtocolHandlerCustomizer<?> protocolHandlerVirtualThreadExecutorCustomizer() {
        return protocolHandler -> {
            protocolHandler.setExecutor(Executors.newVirtualThreadPerTaskExecutor());
        };
    }
}
```

```java
@RestController
@RequestMapping("/api/orders")
public class OrderController {

    private final OrderService orderService;

    @GetMapping("/{id}")
    public ResponseEntity<OrderResponse> getOrder(@PathVariable Long id) {
        // Virtual Thread 환경에서 blocking I/O가 발생해도
        // carrier thread를 점유하지 않으므로 처리량이 향상됨
        OrderResponse order = orderService.findById(id); // DB 조회
        return ResponseEntity.ok(order);
    }
}
```

> **주의**: Virtual Threads는 I/O 바운드 작업에 최적화되어 있습니다. CPU 바운드 작업에는 기존 스레드 모델이 더 적합합니다.

---

### GraalVM Native Image 빌드

```xml
<!-- pom.xml -->
<parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.3.0</version>
</parent>

<build>
    <plugins>
        <plugin>
            <groupId>org.graalvm.buildtools</groupId>
            <artifactId>native-maven-plugin</artifactId>
        </plugin>
    </plugins>
</build>
```

리플렉션을 사용하는 커스텀 클래스는 힌트를 명시적으로 등록해야 합니다.

```java
// Native Image에서 리플렉션이 필요한 경우 힌트 등록
@Configuration
@ImportRuntimeHints(OrderRuntimeHints.class)
public class NativeConfig {
}

public class OrderRuntimeHints implements RuntimeHintsRegistrar {

    @Override
    public void registerHints(RuntimeHints hints, ClassLoader classLoader) {
        // 리플렉션 힌트 등록
        hints.reflection()
            .registerType(OrderDto.class,
                MemberCategory.INVOKE_DECLARED_CONSTRUCTORS,
                MemberCategory.DECLARED_FIELDS);

        // 리소스 힌트 등록
        hints.resources()
            .registerPattern("data/*.json");
    }
}
```

```bash
# 네이티브 이미지 빌드 (GraalVM 설치 필요)
./mvnw -Pnative native:compile

# 빌드된 네이티브 실행 파일 실행
./target/my-application
```

---

### Micrometer Observation API 활용

```java
@Service
@RequiredArgsConstructor
public class PaymentService {

    private final ObservationRegistry observationRegistry;
    private final PaymentGateway paymentGateway;

    public PaymentResult processPayment(PaymentRequest request) {
        return Observation.createNotStarted("payment.process", observationRegistry)
            .lowCardinalityKeyValue("payment.method", request.getMethod())
            .highCardinalityKeyValue("payment.amount", String.valueOf(request.getAmount()))
            .observe(() -> {
                // 이 블록 내의 실행 시간, 성공/실패 여부가 자동으로 메트릭에 기록됨
                return paymentGateway.process(request);
            });
    }
}
```

```yaml
# application.yml - Actuator + Prometheus + Zipkin 통합 설정
management:
  endpoints:
    web:
      exposure:
        include: health, info, metrics, prometheus
  tracing:
    sampling:
      probability: 1.0  # 개발환경에서는 100% 샘플링
  zipkin:
    tracing:
      endpoint: http://localhost:9411/api/v2/spans
  metrics:
    distribution:
      percentiles-histogram:
        http.server.requests: true
```

---

### Spring Security 6.x 통합

Spring Boot 3.x와 함께 제공되는 **Spring Security 6.x**는 기존의 `WebSecurityConfigurerAdapter`를 완전히 제거하고 컴포넌트 기반 설정 방식을 표준화했습니다.

```java
@Configuration
@EnableWebSecurity
@EnableMethodSecurity  // @PreAuthorize, @PostAuthorize 활성화
public class SecurityConfig {

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        return http
            .csrf(AbstractHttpConfigurer::disable)
            .sessionManagement(session ->
                session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
            .authorizeHttpRequests(auth -> auth
                .requestMatchers("/api/public/**").permitAll()
                .requestMatchers("/api/admin/**").hasRole("ADMIN")
                .anyRequest().authenticated())
            .oauth2ResourceServer(oauth2 ->
                oauth2.jwt(jwt -> jwt.jwtAuthenticationConverter(jwtAuthConverter())))
            .build();
    }

    @Bean
    public JwtAuthenticationConverter jwtAuthConverter() {
        JwtGrantedAuthoritiesConverter converter = new JwtGrantedAuthoritiesConverter();
        converter.setAuthorityPrefix("ROLE_");
        converter.setAuthoritiesClaimName("roles");

        JwtAuthenticationConverter jwtConverter = new JwtAuthenticationConverter();
        jwtConverter.setJwtGrantedAuthoritiesConverter(converter);
        return jwtConverter;
    }
}
```

---

### Problem Details (RFC 7807) 표준 오류 응답

Spring Boot 3.x는 **RFC 7807 Problem Details** 스펙을 기본 지원합니다. 일관된 API 오류 응답 형식을 손쉽게 구현할 수 있습니다.

```yaml
spring:
  mvc:
    problemdetails:
      enabled: true
```

```java
@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(OrderNotFoundException.class)
    public ProblemDetail handleOrderNotFound(OrderNotFoundException ex, HttpServletRequest request) {
        ProblemDetail problem = ProblemDetail.forStatusAndDetail(
            HttpStatus.NOT_FOUND,
            ex.getMessage()
        );
        problem.setTitle("Order Not Found");
        problem.setType(URI.create("https://api.myapp.com/errors/order-not-found"));
        problem.setProperty("orderId", ex.getOrderId());
        problem.setProperty("timestamp", Instant.now());
        return problem;
    }
}
```

```json
// 응답 예시
{
  "type": "https://api.myapp.com/errors/order-not-found",
  "title": "Order Not Found",
  "status": 404,
  "detail": "Order with id 12345 does not exist",
  "orderId": 12345,
  "timestamp": "2024-06-01T10:30:00Z"
}
```

---

## 주의사항 및 트레이드오프

### 마이그레이션 시 체크리스트

| 항목 | 변경 내용 | 영향도 |
|------|-----------|--------|
| Java 버전 | 최소 Java 17 필요 | 높음 |
| 패키지명 | `javax.*` → `jakarta.*` | 높음 |
| Spring Security | `WebSecurityConfigurerAdapter` 제거 | 높음 |
| Actuator | 일부 엔드포인트 경로 변경 | 중간 |
| Logging 설정 | 일부 프로퍼티 키 변경 | 낮음 |

### GraalVM Native Image의 트레이드오프

- **장점**: 빠른 기동 시간 (수십~수백 ms), 낮은 메모리 사용량
- **단점**: 빌드 시간 대폭 증가 (5~20분), 동적 기능(리플렉션, 동적 프록시) 제한, 디버깅 난이도 상승
- **권장**: 짧은 실행 시간이 중요한 서버리스, CLI 도구 환경에 적합. 장시간 실행 서버에서는 JIT 최적화가 적용된 JVM이 장기적으로 더 높은 처리량을 제공할 수 있습니다.

### Virtual Threads 주의사항

- `synchronized` 블록 내에서 blocking 작업을 수행하면 **pinning** 현상이 발생하여 성능이 저하됩니다. `ReentrantLock`을 활용하세요.
- 스레드 로컬 변수(`ThreadLocal`) 남용을 지양하세요. Virtual Thread 수가 매우 많아질 수 있어 메모리 문제가 발생할 수 있습니다.

```java
// 나쁜 예 - synchronized 내 blocking
public synchronized void updateStock(Long productId) {
    // DB 조회 (blocking) - pinning 발생!
    Stock stock = repository.findById(productId);
}

// 좋은 예 - ReentrantLock 사용
private final ReentrantLock lock = new ReentrantLock();

public void updateStock(Long productId) {
    lock.lock();
    try {
        Stock stock = repository.findById(productId); // pinning 없음
    } finally {
        lock.unlock();
    }
}
```

---

## 정리

Spring Boot 3.x는 현대적인 클라우드 네이티브 환경에 최적화된 프레임워크로 발전했습니다. 핵심 변화를 정리하면 다음과 같습니다.

1. **Jakarta EE 10 전환**: 패키지명 변경은 번거롭지만 표준화된 엔터프라이즈 자바 생태계로의 전환을 의미합니다.
2. **GraalVM Native Image**: 서버리스와 컨테이너 환경에서 비용과 성능 양면에서 이점을 제공합니다.
3. **Virtual Threads**: 코드 변경 없이 I/O 바운드 애플리케이션의 처리량을 크게 향상시킬 수 있는 강력한 옵션입니다.
4. **Micrometer Observability**: 분산 시스템에서 필수적인 메트릭, 트레이싱을 일관된 방식으로 관리할 수 있습니다.
5. **Problem Details**: API 오류 응답의 표준화로 클라이언트 개발 경험을 개선할 수 있습니다.

Spring Boot 2.x에서의 마이그레이션은 초기 비용이 있지만, 장기적으로는 성능, 보안, 유지보수성 측면에서 상당한 이점을 얻을 수 있습니다. [공식 마이그레이션 가이드](https://github.com/spring-projects/spring-boot/wiki/Spring-Boot-3.0-Migration-Guide)와 `spring-boot-properties-migrator` 의존성을 활용하면 마이그레이션 부담을 줄일 수 있습니다.