# Spring Boot Configuration Properties 검증과 타입-세이프 설정

## 개요

Spring Boot 애플리케이션을 운영하다 보면 설정값 관련 장애를 종종 경험하게 된다. `application.yml`에 오타가 생겨 포트가 잘못 바인딩되거나, 타임아웃 값이 문자열로 들어와 런타임에 `NumberFormatException`이 터지는 상황이 대표적이다. 더 심각한 경우는 필수 설정값이 누락된 채 애플리케이션이 기동되었다가 운영 중에 NullPointerException이 발생하는 케이스다.

이런 문제를 **컴파일 타임 혹은 애플리케이션 기동 시점**에 잡아낼 수 있는 도구가 바로 `@ConfigurationProperties`다. `@Value` 어노테이션 방식과 비교했을 때 타입 안전성, 검증 기능, IDE 자동완성 지원 등 실무에서 체감하는 이점이 명확하다. 이 글에서는 `@ConfigurationProperties`의 핵심 개념부터 Bean Validation을 이용한 검증, 중첩 객체 구성, 그리고 실무에서 마주치는 트레이드오프까지 깊이 있게 다룬다.

---

## 핵심 개념

### @Value vs @ConfigurationProperties

`@Value`는 단순하고 빠르지만 프로퍼티가 많아질수록 유지보수가 힘들어진다.

```java
// @Value 방식 - 분산되고 검증이 어렵다
@Component
public class LegacyService {

    @Value("${api.endpoint}")
    private String endpoint;

    @Value("${api.timeout:5000}")
    private int timeout;

    @Value("${api.retry-count:3}")
    private int retryCount;
}
```

`@ConfigurationProperties`는 관련 설정을 하나의 객체로 묶어 응집도를 높이고, 타입 변환과 검증을 프레임워크 레벨에서 처리한다.

```java
// @ConfigurationProperties 방식 - 응집도가 높고 검증 가능
@ConfigurationProperties(prefix = "api")
public record ApiProperties(
    String endpoint,
    Duration timeout,
    int retryCount
) {}
```

### 바인딩 규칙 (Relaxed Binding)

Spring Boot는 프로퍼티 이름을 유연하게 바인딩한다. `retry-count`, `retryCount`, `RETRY_COUNT`, `retry_count` 모두 동일한 필드에 매핑된다. 이 덕분에 환경변수(대문자 언더스코어)와 YAML(케밥 케이스)을 혼용하는 환경에서도 별도 처리 없이 동작한다.

---

## 실전 예제

### 1. 기본 설정 클래스 구성

Spring Boot 2.2부터 `@ConstructorBinding`이 도입되었고, 3.x부터는 Java Record와 함께 사용하는 것이 권장된다.

**application.yml**

```yaml
external:
  payment:
    base-url: https://payment.example.com
    api-key: secret-key-12345
    connect-timeout: 3s
    read-timeout: 10s
    max-retries: 3
    retry-delay: 500ms
    supported-currencies:
      - KRW
      - USD
      - EUR
```

**PaymentProperties.java**

```java
import jakarta.validation.constraints.*;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;
import org.springframework.validation.annotation.Validated;

import java.time.Duration;
import java.util.List;

@Validated
@ConfigurationProperties(prefix = "external.payment")
public record PaymentProperties(

    @NotBlank(message = "Payment base URL은 필수입니다")
    @Pattern(regexp = "^https://.*", message = "HTTPS 프로토콜만 허용됩니다")
    String baseUrl,

    @NotBlank(message = "API Key는 필수입니다")
    @Size(min = 10, message = "API Key는 최소 10자 이상이어야 합니다")
    String apiKey,

    @NotNull
    @DurationMin(seconds = 1)
    @DefaultValue("3s")
    Duration connectTimeout,

    @NotNull
    @DefaultValue("10s")
    Duration readTimeout,

    @Min(value = 0, message = "재시도 횟수는 0 이상이어야 합니다")
    @Max(value = 10, message = "재시도 횟수는 10 이하여야 합니다")
    @DefaultValue("3")
    int maxRetries,

    @DefaultValue("500ms")
    Duration retryDelay,

    @NotEmpty(message = "지원 통화는 최소 1개 이상이어야 합니다")
    List<String> supportedCurrencies
) {}
```

**PaymentPropertiesConfig.java**

```java
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableConfigurationProperties(PaymentProperties.class)
public class PaymentPropertiesConfig {
}
```

> Spring Boot 3.x에서는 `@ConfigurationPropertiesScan`을 사용하면 별도 `@EnableConfigurationProperties` 없이 자동 스캔도 가능하다.

---

### 2. 중첩 객체와 복잡한 구조

실무에서는 단일 프로퍼티 클래스보다 계층형 구조가 더 자주 쓰인다.

```yaml
infrastructure:
  database:
    primary:
      url: jdbc:postgresql://primary-db:5432/app
      username: app_user
      password: ${DB_PASSWORD}
      pool:
        minimum-idle: 5
        maximum-pool-size: 20
        connection-timeout: 30000
        idle-timeout: 600000
    replica:
      url: jdbc:postgresql://replica-db:5432/app
      username: app_readonly
      password: ${DB_READONLY_PASSWORD}
      pool:
        minimum-idle: 2
        maximum-pool-size: 10
        connection-timeout: 30000
        idle-timeout: 600000
```

```java
@Validated
@ConfigurationProperties(prefix = "infrastructure.database")
public record DatabaseProperties(
    @Valid @NotNull DataSourceConfig primary,
    @Valid DataSourceConfig replica
) {
    public record DataSourceConfig(
        @NotBlank String url,
        @NotBlank String username,
        @NotBlank String password,
        @Valid @NotNull PoolConfig pool
    ) {}

    public record PoolConfig(
        @Min(1) @DefaultValue("5") int minimumIdle,
        @Min(1) @Max(100) @DefaultValue("20") int maximumPoolSize,
        @Positive @DefaultValue("30000") long connectionTimeout,
        @Positive @DefaultValue("600000") long idleTimeout
    ) {}
}
```

중첩 객체에 `@Valid`를 붙여야 내부 객체의 제약 조건도 함께 검증된다는 점을 놓치기 쉽다.

---

### 3. 커스텀 검증기 구현

Bean Validation의 기본 어노테이션으로 표현하기 어려운 비즈니스 규칙은 커스텀 `Validator`로 처리한다.

```java
import org.springframework.boot.context.properties.ConfigurationPropertiesBinding;
import org.springframework.stereotype.Component;
import org.springframework.validation.Errors;
import org.springframework.validation.Validator;

@Component
@ConfigurationPropertiesBinding
public class PaymentPropertiesValidator implements Validator {

    @Override
    public boolean supports(Class<?> clazz) {
        return PaymentProperties.class.isAssignableFrom(clazz);
    }

    @Override
    public void validate(Object target, Errors errors) {
        PaymentProperties props = (PaymentProperties) target;

        // 재시도 딜레이는 커넥션 타임아웃보다 짧아야 한다
        if (props.retryDelay() != null && props.connectTimeout() != null) {
            if (props.retryDelay().compareTo(props.connectTimeout()) >= 0) {
                errors.rejectValue(
                    "retryDelay",
                    "invalid.retryDelay",
                    "retryDelay는 connectTimeout보다 짧아야 합니다"
                );
            }
        }

        // KRW는 반드시 포함되어야 하는 비즈니스 규칙
        if (props.supportedCurrencies() != null
                && !props.supportedCurrencies().contains("KRW")) {
            errors.rejectValue(
                "supportedCurrencies",
                "missing.KRW",
                "KRW는 반드시 지원 통화에 포함되어야 합니다"
            );
        }
    }
}
```

---

### 4. IDE 자동완성을 위한 메타데이터 생성

`spring-boot-configuration-processor` 의존성을 추가하면 컴파일 시점에 `META-INF/spring-configuration-metadata.json`이 자동 생성된다. 이를 통해 IntelliJ, VSCode에서 `application.yml` 작성 시 자동완성과 문서 힌트를 제공받을 수 있다.

```gradle
// build.gradle
dependencies {
    annotationProcessor 'org.springframework.boot:spring-boot-configuration-processor'
}

// 프로세서가 Record의 compact constructor를 인식하도록 설정
compileJava.inputs.files(processResources.outputs)
```

```xml
<!-- Maven -->
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-configuration-processor</artifactId>
    <optional>true</optional>
</dependency>
```

---

### 5. 테스트 작성

설정 검증 로직은 반드시 테스트로 보호해야 한다.

```java
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.TestPropertySource;

import static org.assertj.core.api.Assertions.*;

@SpringBootTest
@EnableConfigurationProperties(PaymentProperties.class)
@TestPropertySource(properties = {
    "external.payment.base-url=https://payment.example.com",
    "external.payment.api-key=secret-key-12345",
    "external.payment.supported-currencies=KRW,USD"
})
class PaymentPropertiesTest {

    @Autowired
    private PaymentProperties properties;

    @Test
    void 정상_설정값이_올바르게_바인딩된다() {
        assertThat(properties.baseUrl()).isEqualTo("https://payment.example.com");
        assertThat(properties.maxRetries()).isEqualTo(3); // 기본값 확인
        assertThat(properties.supportedCurrencies()).contains("KRW", "USD");
    }

    @Test
    void HTTP_URL_사용시_기동에_실패한다() {
        // @SpringBootTest with invalid properties
        assertThatThrownBy(() -> {
            // AnnotationConfigApplicationContext를 이용한 단위 테스트
        }).isInstanceOf(Exception.class);
    }
}

// 바인딩 단위 테스트를 위한 슬라이스 테스트
@ExtendWith(SpringExtension.class)
class PaymentPropertiesBindingTest {

    @Test
    void Duration_타입이_올바르게_변환된다() {
        ApplicationContextRunner runner = new ApplicationContextRunner()
            .withPropertyValues(
                "external.payment.base-url=https://example.com",
                "external.payment.api-key=test-key-1234",
                "external.payment.connect-timeout=5s",
                "external.payment.supported-currencies=KRW"
            )
            .withUserConfiguration(PaymentPropertiesConfig.class);

        runner.run(context -> {
            PaymentProperties props = context.getBean(PaymentProperties.class);
            assertThat(props.connectTimeout()).isEqualTo(Duration.ofSeconds(5));
        });
    }

    @Test
    void API_KEY_누락시_컨텍스트_로딩에_실패한다() {
        ApplicationContextRunner runner = new ApplicationContextRunner()
            .withPropertyValues("external.payment.base-url=https://example.com")
            .withUserConfiguration(PaymentPropertiesConfig.class);

        runner.run(context ->
            assertThat(context).hasFailed()
                .getFailure()
                .hasMessageContaining("API Key는 필수입니다")
        );
    }
}
```

---

## 주의사항 및 트레이드오프

### 민감 정보 처리

`@ConfigurationProperties` 빈은 Spring Actuator의 `/actuator/configprops` 엔드포인트에 노출될 수 있다. 비밀번호나 API 키 같은 민감 정보는 반드시 마스킹 처리가 필요하다.

```java
// SanitizableData를 활용하거나 @NestedConfigurationProperty 주의
// actuator 설정에서 민감 키워드를 지정한다
```

```yaml
management:
  endpoint:
    configprops:
      show-values: never  # Spring Boot 3.x 권장 설정
  sanitize:
    additional-keys: "api-key,password,secret,token"
```

### Record vs 일반 클래스 선택

Java Record는 불변성을 보장하고 코드가 간결하지만, `@PostConstruct`나 파생 필드 계산이 필요한 경우에는 일반 클래스가 더 적합하다. 또한 Spring Boot 2.x 환경에서는 Record 지원이 제한적이므로 팀의 Java 버전을 고려해야 한다.

### 순환 의존성 주의

`@ConfigurationProperties` 클래스가 다른 Bean을 주입받으려 할 때 순환 의존성이 발생할 수 있다. 설정 클래스는 순수한 데이터 홀더 역할로 제한하고, 비즈니스 로직은 별도 Service 클래스에서 처리하는 것이 원칙이다.

### 프로퍼티 변경 감지

`@ConfigurationProperties`는 기본적으로 애플리케이션 기동 시 한 번만 바인딩된다. Spring Cloud Config나 Kubernetes ConfigMap 변경을 실시간 반영하려면 `@RefreshScope`와 함께 사용해야 하며, 이때 검증 로직이 리프레시 시점에도 실행된다는 점을 확인해야 한다.

---

## 정리

`@ConfigurationProperties`는 단순히 설정값을 읽어오는 도구를 넘어 **설정의 신뢰성을 애플리케이션 기동 시점에 보장**하는 메커니즘이다. 핵심을 정리하면 다음과 같다.

| 항목 | 권장 방식 |
|------|-----------|
| 기본 구조 | Java Record + `@ConfigurationProperties` |
| 검증 | `@Validated` + Bean Validation 어노테이션 |
| 복잡한 규칙 | `@ConfigurationPropertiesBinding` 커스텀 Validator |
| 중첩 객체 | `@Valid` 어노테이션으로 전파 |
| IDE 지원 | `spring-boot-configuration-processor` 추가 |
| 테스트 | `ApplicationContextRunner` 슬라이스 테스트 |
| 보안 | Actuator `show-values: never` 설정 |

`@Value`로 점재해 있는 기존 설정들을 `@ConfigurationProperties`로 마이그레이션하면 즉각적인 생산성 향상을 체감할 수 있다. 특히 환경마다 설정이 달라지는 MSA 환경이나 CI/CD 파이프라인에서 잘못된 설정으로 인한 배포 실패를 기동 단계에서 조기에 차단할 수 있다는 점이 가장 큰 실무적 가치다.