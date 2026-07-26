# Spring Boot 멀티모듈 프로젝트 구조 설계

## 개요

실무에서 프로젝트 규모가 커질수록 단일 모듈(Single Module) 구조의 한계에 부딪히는 경험을 자주 하게 된다. 도메인 간 의존성이 엉키고, 빌드 시간이 늘어나며, 코드 재사용성도 떨어진다. 이러한 문제를 해결하기 위한 실용적인 접근이 바로 **멀티모듈 프로젝트 구조**다.

멀티모듈은 단순히 패키지를 나누는 것과는 다르다. 각 모듈이 독립적인 컴파일 단위를 가지며, 명시적인 의존 관계 선언을 통해 아키텍처 경계를 강제할 수 있다. 특히 팀 규모가 커지고 여러 서비스가 공통 로직을 공유해야 하는 환경에서 멀티모듈 구조는 선택이 아닌 필수가 된다.

이번 포스팅에서는 Spring Boot 기반의 멀티모듈 프로젝트를 실전에서 어떻게 설계하고, 어떤 기준으로 모듈을 나눠야 하는지, 그리고 흔히 빠지는 함정까지 다루어본다.

---

## 핵심 개념

### 모듈 분리의 기준

멀티모듈 설계에서 가장 어려운 부분은 "어떤 기준으로 모듈을 나눌 것인가"다. 일반적으로 아래 세 가지 기준을 조합해서 사용한다.

1. **기술 계층 기준**: `core`, `api`, `domain`, `infrastructure` 등 기술적 역할로 분리
2. **도메인 기준**: `member`, `order`, `payment` 등 비즈니스 도메인으로 분리
3. **배포 단위 기준**: 실제로 독립 배포되는 애플리케이션 단위로 분리

실무에서는 이 세 가지를 조합한 **계층형 + 도메인 혼합 구조**를 많이 사용한다.

### 의존 방향의 원칙

모듈 간 의존성은 반드시 **단방향**을 유지해야 한다. 순환 의존(Circular Dependency)이 발생하는 순간 멀티모듈 구조의 이점이 사라진다.

```
app-api → domain → core
app-admin → domain → core
           ↑
      (절대 역방향 금지)
```

---

## 실전 예제

### 프로젝트 디렉터리 구조

실무에서 자주 사용하는 구조를 기준으로 설명한다.

```
my-service/
├── build.gradle (루트)
├── settings.gradle
├── app-api/           ← 사용자 API 서버 (Spring Boot 실행 모듈)
├── app-admin/         ← 관리자 API 서버
├── domain/            ← 비즈니스 도메인 로직, JPA 엔티티
├── infrastructure/    ← 외부 연동 (Redis, S3, 외부 API 등)
└── core/              ← 공통 유틸, 공통 예외, 공통 DTO
```

### settings.gradle 설정

```groovy
rootProject.name = 'my-service'

include ':core'
include ':domain'
include ':infrastructure'
include ':app-api'
include ':app-admin'
```

### 루트 build.gradle 설정

```groovy
buildscript {
    ext {
        springBootVersion = '3.2.0'
        dependencyManagementVersion = '1.1.4'
    }
}

plugins {
    id 'java'
    id 'org.springframework.boot' version "${springBootVersion}" apply false
    id 'io.spring.dependency-management' version "${dependencyManagementVersion}" apply false
}

// 모든 서브모듈에 공통 적용
subprojects {
    apply plugin: 'java'
    apply plugin: 'io.spring.dependency-management'

    group = 'com.example'
    version = '0.0.1-SNAPSHOT'

    java {
        sourceCompatibility = JavaVersion.VERSION_21
    }

    repositories {
        mavenCentral()
    }

    dependencyManagement {
        imports {
            mavenBom "org.springframework.boot:spring-boot-dependencies:${springBootVersion}"
        }
    }

    dependencies {
        compileOnly 'org.projectlombok:lombok'
        annotationProcessor 'org.projectlombok:lombok'
        testImplementation 'org.springframework.boot:spring-boot-starter-test'
    }
}
```

### core 모듈 설정 (build.gradle)

공통 예외, 공통 응답 DTO, 유틸 클래스 등이 위치한다. 다른 모듈에 의존하지 않는 순수한 Java 모듈이다.

```groovy
// core/build.gradle
dependencies {
    implementation 'com.fasterxml.jackson.core:jackson-databind'
}
```

```java
// core/src/main/java/com/example/core/exception/BusinessException.java
public class BusinessException extends RuntimeException {
    private final ErrorCode errorCode;

    public BusinessException(ErrorCode errorCode) {
        super(errorCode.getMessage());
        this.errorCode = errorCode;
    }

    public int getStatus() {
        return errorCode.getStatus();
    }
}
```

```java
// core/src/main/java/com/example/core/response/ApiResponse.java
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class ApiResponse<T> {
    private int status;
    private String message;
    private T data;

    public static <T> ApiResponse<T> success(T data) {
        ApiResponse<T> response = new ApiResponse<>();
        response.status = 200;
        response.message = "OK";
        response.data = data;
        return response;
    }

    public static ApiResponse<Void> fail(int status, String message) {
        ApiResponse<Void> response = new ApiResponse<>();
        response.status = status;
        response.message = message;
        return response;
    }
}
```

### domain 모듈 설정

JPA 엔티티, Repository 인터페이스, 도메인 서비스가 위치한다. `core` 모듈에만 의존한다.

```groovy
// domain/build.gradle
dependencies {
    implementation project(':core')
    implementation 'org.springframework.boot:spring-boot-starter-data-jpa'
    runtimeOnly 'com.mysql:mysql-connector-j'
}
```

```java
// domain/src/main/java/com/example/domain/member/Member.java
@Entity
@Table(name = "members")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Member {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false, unique = true)
    private String email;

    @Column(nullable = false)
    private String name;

    @Enumerated(EnumType.STRING)
    private MemberStatus status;

    public static Member create(String email, String name) {
        Member member = new Member();
        member.email = email;
        member.name = name;
        member.status = MemberStatus.ACTIVE;
        return member;
    }
}
```

```java
// domain/src/main/java/com/example/domain/member/MemberService.java
@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class MemberService {
    private final MemberRepository memberRepository;

    @Transactional
    public Member register(String email, String name) {
        if (memberRepository.existsByEmail(email)) {
            throw new BusinessException(ErrorCode.DUPLICATE_EMAIL);
        }
        return memberRepository.save(Member.create(email, name));
    }

    public Member findById(Long id) {
        return memberRepository.findById(id)
            .orElseThrow(() -> new BusinessException(ErrorCode.MEMBER_NOT_FOUND));
    }
}
```

### app-api 모듈 설정

Spring Boot 애플리케이션의 실제 진입점. `domain`과 `infrastructure`에 의존한다.

```groovy
// app-api/build.gradle
apply plugin: 'org.springframework.boot'

dependencies {
    implementation project(':domain')
    implementation project(':infrastructure')
    implementation project(':core')
    implementation 'org.springframework.boot:spring-boot-starter-web'
    implementation 'org.springframework.boot:spring-boot-starter-validation'
}

// app-api만 실행 가능한 Fat JAR 생성
bootJar { enabled = true }
jar { enabled = false }
```

> **포인트**: `bootJar`는 실행 모듈(app-api, app-admin)에만 활성화하고, 나머지 모듈은 `jar { enabled = true }`, `bootJar { enabled = false }`로 설정해야 한다.

```java
// app-api/src/main/java/com/example/api/member/MemberController.java
@RestController
@RequestMapping("/api/v1/members")
@RequiredArgsConstructor
public class MemberController {
    private final MemberService memberService;

    @PostMapping
    public ResponseEntity<ApiResponse<MemberResponse>> register(
        @RequestBody @Valid MemberRegisterRequest request
    ) {
        Member member = memberService.register(request.getEmail(), request.getName());
        return ResponseEntity.ok(ApiResponse.success(MemberResponse.from(member)));
    }
}
```

### JPA 엔티티 스캔 설정 주의사항

`app-api` 모듈의 `@SpringBootApplication`은 기본적으로 자신의 패키지만 스캔한다. `domain` 모듈의 엔티티와 레포지토리를 인식시키려면 명시적 스캔 경로 설정이 필요하다.

```java
@SpringBootApplication(
    scanBasePackages = {
        "com.example.api",
        "com.example.domain",
        "com.example.infrastructure",
        "com.example.core"
    }
)
@EnableJpaRepositories(basePackages = "com.example.domain")
@EntityScan(basePackages = "com.example.domain")
public class ApiApplication {
    public static void main(String[] args) {
        SpringApplication.run(ApiApplication.class, args);
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. 모듈 과분리 안티패턴

처음 멀티모듈을 도입할 때 가장 흔한 실수는 **과도한 분리**다. 모듈이 10개가 넘어가면 오히려 의존성 관리가 복잡해지고, 간단한 기능 하나를 추가하기 위해 여러 모듈을 동시에 수정해야 하는 상황이 발생한다. **처음에는 3~5개의 모듈로 시작**해서 필요에 따라 늘려가는 전략을 권장한다.

### 2. 공통 모듈의 비대화

`core` 또는 `common` 모듈이 비대해지는 현상도 주의해야 한다. "어디에 놓을지 모르겠으니 일단 core에"라는 생각이 쌓이면, core 모듈이 사실상 모든 것에 의존하는 모놀리식 덩어리가 된다. **공통 모듈에는 정말 범용적인 코드만** 위치시켜야 한다.

### 3. 테스트 환경 구성의 복잡도

각 모듈이 독립적인 컴파일 단위이므로, 통합 테스트 작성 시 여러 모듈의 Bean을 동시에 올려야 하는 상황이 생긴다. 이 경우 별도의 `test-support` 모듈을 두어 테스트 픽스처와 공통 테스트 설정을 관리하는 패턴을 사용한다.

```groovy
// test-support/build.gradle (테스트 전용 모듈)
dependencies {
    implementation project(':domain')
    implementation 'org.springframework.boot:spring-boot-starter-test'
    implementation 'com.h2database:h2'
}
```

### 4. 빌드 캐싱 활용

Gradle의 `--parallel` 옵션과 빌드 캐시를 활용하면 멀티모듈의 빌드 시간을 크게 단축할 수 있다. `gradle.properties`에 아래 설정을 추가하자.

```properties
# gradle.properties
org.gradle.parallel=true
org.gradle.caching=true
org.gradle.daemon=true
org.gradle.jvmargs=-Xmx2048m
```

### 5. 트레이드오프 정리

| 구분 | 장점 | 단점 |
|------|------|------|
| 멀티모듈 | 아키텍처 경계 강제, 코드 재사용, 독립 빌드 | 초기 설정 복잡, 학습 비용 |
| 단일 모듈 | 단순한 구조, 빠른 초기 개발 | 의존성 관리 어려움, 확장성 한계 |

---

## 정리

Spring Boot 멀티모듈 프로젝트 설계의 핵심은 **경계를 기술이 아닌 도메인과 책임으로 나누는 것**이다. 모듈 구조가 곧 아키텍처 문서가 되고, 잘못된 의존성은 컴파일 단계에서 차단된다는 강력한 이점이 있다.

실무 적용 시 체크리스트를 정리하면 다음과 같다.

- [ ] 모듈 간 의존 방향이 단방향인가?
- [ ] 실행 모듈(app-*)에만 `bootJar`가 활성화되어 있는가?
- [ ] `@SpringBootApplication`의 컴포넌트 스캔 범위가 올바르게 설정되어 있는가?
- [ ] `core` 모듈이 다른 내부 모듈에 의존하지 않는가?
- [ ] Gradle 병렬 빌드 및 캐싱이 활성화되어 있는가?

멀티모듈은 도입하는 것보다 **일관된 원칙으로 유지하는 것**이 더 중요하다. 팀 전체가 모듈 분리 기준에 동의하고, 코드 리뷰 단계에서 경계 위반을 잡아내는 문화를 만들어야 비로소 멀티모듈의 진가를 발휘할 수 있다.