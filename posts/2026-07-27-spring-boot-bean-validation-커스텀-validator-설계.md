# Spring Boot Bean Validation 커스텀 Validator 설계

## 개요

Spring Boot 애플리케이션을 개발하다 보면 `@NotNull`, `@Size`, `@Email` 같은 표준 Bean Validation 어노테이션만으로는 비즈니스 요구사항을 충족하기 어려운 상황을 자주 마주하게 됩니다. 예를 들어 "한국 사업자등록번호 형식 검증", "특정 도메인 이메일만 허용", "DB를 조회해서 중복 여부 확인" 같은 요구사항은 표준 어노테이션으로 처리할 수 없습니다.

이럴 때 커스텀 Validator를 설계하면 검증 로직을 재사용 가능하고, 선언적인 방식으로 관리할 수 있습니다. 이 글에서는 커스텀 Validator의 핵심 구조부터 실무에서 마주치는 복잡한 시나리오(Spring Bean 주입, 다중 필드 검증, 조건부 검증)까지 다루겠습니다.

---

## 핵심 개념

### Bean Validation의 동작 원리

Bean Validation(JSR-380)은 어노테이션 기반의 선언적 검증 명세입니다. Spring Boot에서는 `spring-boot-starter-validation` 의존성을 통해 Hibernate Validator 구현체를 사용합니다.

커스텀 Validator를 만들기 위해서는 두 가지 요소가 필요합니다.

1. **커스텀 어노테이션** - `@Constraint`로 Validator 클래스를 연결
2. **ConstraintValidator 구현체** - 실제 검증 로직 담당

```java
public interface ConstraintValidator<A extends Annotation, T> {
    default void initialize(A constraintAnnotation) {}
    boolean isValid(T value, ConstraintValidatorContext context);
}
```

`initialize()`는 어노테이션의 속성값을 읽어 초기화할 때 사용하고, `isValid()`가 실제 검증 로직의 핵심입니다. `null` 값 처리는 별도로 `@NotNull`에 위임하는 것이 관례입니다.

---

## 실전 예제

### 예제 1: 단순 포맷 검증 - 사업자등록번호

가장 기본적인 패턴부터 시작합니다. 한국 사업자등록번호(10자리, `000-00-00000` 형식)를 검증하는 커스텀 어노테이션입니다.

**어노테이션 정의**

```java
@Target({ElementType.FIELD, ElementType.PARAMETER})
@Retention(RetentionPolicy.RUNTIME)
@Constraint(validatedBy = BusinessNumberValidator.class)
@Documented
public @interface ValidBusinessNumber {
    String message() default "유효하지 않은 사업자등록번호 형식입니다.";
    Class<?>[] groups() default {};
    Class<? extends Payload>[] payload() default {};
}
```

**Validator 구현체**

```java
public class BusinessNumberValidator 
        implements ConstraintValidator<ValidBusinessNumber, String> {

    private static final Pattern BUSINESS_NUMBER_PATTERN =
            Pattern.compile("^\\d{3}-\\d{2}-\\d{5}$");

    @Override
    public boolean isValid(String value, ConstraintValidatorContext context) {
        if (value == null) {
            return true; // null 체크는 @NotNull에 위임
        }
        return BUSINESS_NUMBER_PATTERN.matcher(value).matches() 
               && isValidChecksum(value.replaceAll("-", ""));
    }

    private boolean isValidChecksum(String digits) {
        int[] weights = {1, 3, 7, 1, 3, 7, 1, 3, 5};
        int sum = 0;
        for (int i = 0; i < 9; i++) {
            sum += (digits.charAt(i) - '0') * weights[i];
        }
        sum += (int) Math.floor((digits.charAt(8) - '0') * 5 / 10.0);
        int checkDigit = (10 - (sum % 10)) % 10;
        return checkDigit == (digits.charAt(9) - '0');
    }
}
```

**사용 예시**

```java
public class CompanyRegisterRequest {
    @NotBlank
    @ValidBusinessNumber
    private String businessNumber;
}
```

---

### 예제 2: Spring Bean 주입 - 중복 이메일 검증

실무에서 매우 흔한 패턴입니다. Validator가 Spring 컨텍스트에서 관리되면 `@Autowired`나 생성자 주입을 통해 Repository, Service를 사용할 수 있습니다.

```java
@Target({ElementType.FIELD})
@Retention(RetentionPolicy.RUNTIME)
@Constraint(validatedBy = UniqueEmailValidator.class)
@Documented
public @interface UniqueEmail {
    String message() default "이미 사용 중인 이메일입니다.";
    Class<?>[] groups() default {};
    Class<? extends Payload>[] payload() default {};
}
```

```java
@Component
public class UniqueEmailValidator 
        implements ConstraintValidator<UniqueEmail, String> {

    private final UserRepository userRepository;

    public UniqueEmailValidator(UserRepository userRepository) {
        this.userRepository = userRepository;
    }

    @Override
    public boolean isValid(String email, ConstraintValidatorContext context) {
        if (email == null || email.isBlank()) {
            return true;
        }
        return !userRepository.existsByEmail(email);
    }
}
```

> **핵심 포인트**: `@Component`를 붙이면 Spring이 Validator를 Bean으로 관리하여 의존성 주입이 가능합니다. Spring Boot에서는 별도 설정 없이 자동으로 Spring 관리 Validator를 탐색합니다.

---

### 예제 3: 다중 필드 검증 - 클래스 레벨 Validator

비밀번호 확인, 날짜 범위 검증처럼 **두 필드 이상을 비교**해야 할 경우 클래스 레벨에 어노테이션을 적용합니다.

```java
@Target({ElementType.TYPE})
@Retention(RetentionPolicy.RUNTIME)
@Constraint(validatedBy = PasswordMatchValidator.class)
@Documented
public @interface PasswordMatch {
    String message() default "비밀번호와 비밀번호 확인이 일치하지 않습니다.";
    String password() default "password";
    String confirmPassword() default "confirmPassword";
    Class<?>[] groups() default {};
    Class<? extends Payload>[] payload() default {};
}
```

```java
public class PasswordMatchValidator 
        implements ConstraintValidator<PasswordMatch, Object> {

    private String passwordField;
    private String confirmPasswordField;

    @Override
    public void initialize(PasswordMatch constraintAnnotation) {
        this.passwordField = constraintAnnotation.password();
        this.confirmPasswordField = constraintAnnotation.confirmPassword();
    }

    @Override
    public boolean isValid(Object target, ConstraintValidatorContext context) {
        try {
            BeanWrapper beanWrapper = new BeanWrapperImpl(target);
            Object password = beanWrapper.getPropertyValue(passwordField);
            Object confirmPassword = beanWrapper.getPropertyValue(confirmPasswordField);

            boolean isValid = Objects.equals(password, confirmPassword);

            if (!isValid) {
                // 특정 필드에 에러 메시지 바인딩
                context.disableDefaultConstraintViolation();
                context.buildConstraintViolationWithTemplate(context.getDefaultConstraintMessageTemplate())
                       .addPropertyNode(confirmPasswordField)
                       .addConstraintViolation();
            }
            return isValid;
        } catch (Exception e) {
            return false;
        }
    }
}
```

**DTO에 적용**

```java
@PasswordMatch
public class ChangePasswordRequest {
    @NotBlank
    @Size(min = 8, max = 20)
    private String password;

    @NotBlank
    private String confirmPassword;
}
```

---

### 예제 4: 커스텀 에러 메시지와 파라미터화

어노테이션에 속성을 추가해 동적인 메시지를 구성할 수 있습니다.

```java
@Target({ElementType.FIELD})
@Retention(RetentionPolicy.RUNTIME)
@Constraint(validatedBy = AllowedValuesValidator.class)
@Documented
public @interface AllowedValues {
    String[] values();
    String message() default "허용되지 않는 값입니다. 허용 값: {values}";
    Class<?>[] groups() default {};
    Class<? extends Payload>[] payload() default {};
}
```

```java
public class AllowedValuesValidator 
        implements ConstraintValidator<AllowedValues, String> {

    private Set<String> allowedValues;

    @Override
    public void initialize(AllowedValues constraintAnnotation) {
        this.allowedValues = Set.of(constraintAnnotation.values());
    }

    @Override
    public boolean isValid(String value, ConstraintValidatorContext context) {
        if (value == null) return true;

        if (!allowedValues.contains(value)) {
            context.disableDefaultConstraintViolation();
            context.buildConstraintViolationWithTemplate(
                    "허용되지 않는 값입니다. 허용 값: " + allowedValues)
                   .addConstraintViolation();
            return false;
        }
        return true;
    }
}
```

```java
public class OrderRequest {
    @AllowedValues(values = {"PENDING", "CONFIRMED", "CANCELLED"})
    private String status;
}
```

---

## 주의사항 및 트레이드오프

### 1. 트랜잭션 경계 문제

DB 조회가 필요한 Validator에서 트랜잭션을 직접 시작하는 것은 위험합니다. Validator는 일반적으로 Controller 계층에서 실행되므로, 서비스 레이어의 트랜잭션과 분리될 수 있습니다.

```java
// ❌ Validator 내부에서 직접 트랜잭션 조작은 피할 것
@Transactional(readOnly = true)
public boolean isValid(...) { ... }

// ✅ Repository의 기존 메서드 활용, 트랜잭션은 서비스에서 관리
```

중복 검증 같은 경우 **DB 유니크 제약 조건**을 최종 방어선으로 반드시 함께 사용해야 합니다. Race Condition으로 인해 Validator 통과 후 INSERT 시점에 중복이 발생할 수 있기 때문입니다.

### 2. 성능 고려

DB를 조회하는 Validator는 매 요청마다 쿼리가 발생합니다. 배치 처리나 대용량 요청에서는 **검증 로직을 서비스 계층으로 이동**하는 것이 더 적합할 수 있습니다. Validator는 단순하고 빠른 검증에 집중하는 것이 좋습니다.

### 3. 테스트 전략

커스텀 Validator는 단위 테스트와 통합 테스트를 함께 작성해야 합니다.

```java
@ExtendWith(MockitoExtension.class)
class UniqueEmailValidatorTest {

    @Mock
    private UserRepository userRepository;

    @InjectMocks
    private UniqueEmailValidator validator;

    @Mock
    private ConstraintValidatorContext context;

    @Test
    void 중복_이메일이면_검증_실패() {
        given(userRepository.existsByEmail("test@example.com")).willReturn(true);
        assertThat(validator.isValid("test@example.com", context)).isFalse();
    }

    @Test
    void 사용_가능한_이메일이면_검증_성공() {
        given(userRepository.existsByEmail("new@example.com")).willReturn(false);
        assertThat(validator.isValid("new@example.com", context)).isTrue();
    }
}
```

### 4. 검증 순서와 그룹화

여러 검증이 연쇄적으로 일어나야 할 때는 `@GroupSequence`를 활용합니다. 예를 들어 포맷 검증 통과 후에만 DB 중복 검증을 수행하는 식으로 불필요한 DB 쿼리를 줄일 수 있습니다.

```java
public interface FormatCheck {}
public interface DatabaseCheck {}

@GroupSequence({FormatCheck.class, DatabaseCheck.class})
public interface ValidationOrder {}
```

### 5. 재사용성과 단일 책임

커스텀 Validator 하나가 너무 많은 책임을 지지 않도록 주의해야 합니다. 복잡한 비즈니스 로직이 포함된다면 Validator는 서비스 메서드를 호출하는 얇은 레이어로 유지하고, 실제 로직은 서비스에 위임하는 것이 유지보수성 측면에서 유리합니다.

---

## 정리

| 항목 | 권장 방식 |
|------|-----------|
| null 처리 | Validator에서 `true` 반환, `@NotNull`에 위임 |
| DB 조회 | `@Component` 등록 후 Repository 주입 |
| 다중 필드 검증 | 클래스 레벨 어노테이션 사용 |
| 에러 메시지 위치 | `addPropertyNode()`로 특정 필드에 바인딩 |
| 트랜잭션 | 서비스 레이어에서 관리, Validator 내부 지양 |

커스텀 Validator는 선언적 검증의 강점을 최대한 살리면서 비즈니스 규칙을 코드 전반에 분산시키지 않는 훌륭한 패턴입니다. 단, 복잡도가 올라갈수록 서비스 레이어와의 역할 분리를 명확히 해야 하며, 성능과 트랜잭션 경계를 항상 염두에 두고 설계해야 합니다.

표준 어노테이션으로 해결되지 않는 요구사항을 만날 때, 커스텀 Validator를 적극적으로 도입해보세요. 코드의 표현력과 재사용성이 크게 높아질 것입니다.