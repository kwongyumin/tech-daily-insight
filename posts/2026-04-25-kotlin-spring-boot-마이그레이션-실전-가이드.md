# Kotlin + Spring Boot 마이그레이션 실전 가이드

## 개요

Java로 운영 중인 Spring Boot 프로젝트를 Kotlin으로 마이그레이션하는 것은 이제 선택이 아닌 전략적 결정이 되고 있다. Kotlin은 JVM 위에서 동작하며 Java와 100% 상호 운용이 가능하기 때문에, 빅뱅 방식의 전면 교체가 아닌 점진적 마이그레이션이 가능하다는 것이 가장 큰 강점이다.

실제로 JetBrains, Gradle, Atlassian 등 수많은 기업이 Kotlin으로의 전환을 완료했고, Spring 공식 문서도 Kotlin을 first-class 언어로 지원하고 있다. 이 포스팅은 실제 프로덕션 코드를 기반으로 **점진적 마이그레이션 전략**, **Spring Boot와의 연동 패턴**, 그리고 **실무에서 자주 마주치는 트레이드오프**를 다룬다.

---

## 핵심 개념

### 왜 Kotlin인가?

단순히 문법이 간결해서가 아니다. 실무 관점에서 Kotlin이 Java보다 우위를 점하는 핵심 포인트는 다음과 같다.

- **Null Safety**: 컴파일 타임에 NPE를 방지하는 타입 시스템
- **Data Class**: `equals()`, `hashCode()`, `toString()`, `copy()` 자동 생성
- **Coroutine**: 경량 비동기 처리 (Spring WebFlux와의 시너지)
- **Extension Functions**: 기존 클래스를 수정하지 않고 기능 확장
- **Smart Cast & Sealed Class**: 더 안전하고 표현력 있는 타입 처리

### 점진적 마이그레이션 전략

Kotlin과 Java는 동일한 JVM 클래스패스에서 공존할 수 있다. 즉, **파일 단위로 하나씩 전환**하는 것이 가능하다.

권장 마이그레이션 순서:
1. **DTO/도메인 모델** → Data Class로 교체 (가장 안전하고 효과가 크다)
2. **유틸리티 클래스** → Extension Function과 Object로 교체
3. **서비스 레이어** → 비즈니스 로직 Kotlin화
4. **컨트롤러** → Kotlin + Spring MVC 또는 Router DSL 적용
5. **Repository/설정 파일** → 마지막에 전환

---

## 실전 예제

### 1. Gradle 빌드 설정

`build.gradle.kts`에 Kotlin 플러그인과 Spring 연동 플러그인을 추가한다.

```kotlin
plugins {
    id("org.springframework.boot") version "3.2.0"
    id("io.spring.dependency-management") version "1.1.4"
    kotlin("jvm") version "1.9.22"
    kotlin("plugin.spring") version "1.9.22"  // @Component 등 open class 처리
    kotlin("plugin.jpa") version "1.9.22"     // JPA Entity 기본 생성자 생성
}

dependencies {
    implementation("org.springframework.boot:spring-boot-starter-web")
    implementation("org.springframework.boot:spring-boot-starter-data-jpa")
    implementation("com.fasterxml.jackson.module:jackson-module-kotlin")
    implementation("org.jetbrains.kotlin:kotlin-reflect")
}

tasks.withType<KotlinCompile> {
    kotlinOptions {
        freeCompilerArgs += "-Xjsr305=strict"  // Spring의 @Nullable 힌트를 Kotlin 타입 시스템에 반영
        jvmTarget = "17"
    }
}
```

> `plugin.spring`은 Kotlin의 클래스를 기본적으로 `final`로 만드는 특성을 우회하여 Spring의 CGLIB 프록시가 정상 동작하게 한다. **이 플러그인 없이는 `@Transactional`, `@Cacheable` 등이 동작하지 않는다.**

---

### 2. Entity 클래스 마이그레이션

Java에서 Kotlin으로 JPA Entity를 전환할 때 주의할 점이 많다.

**Before (Java)**
```java
@Entity
@Table(name = "users")
public class User {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String email;
    private String name;

    // 기본 생성자, getter, setter 필요
    public User() {}
    // ...
}
```

**After (Kotlin)**
```kotlin
@Entity
@Table(name = "users")
class User(
    @Column(nullable = false, unique = true)
    val email: String,

    @Column(nullable = false)
    var name: String,

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    val id: Long = 0
) {
    // JPA는 기본 생성자가 필요 → plugin.jpa가 자동 생성
    // Data class 사용 비권장: equals/hashCode가 프록시 객체에서 문제 발생
}
```

> **주의**: JPA Entity에는 `data class` 사용을 피하라. Hibernate의 지연 로딩 프록시 객체에서 `equals()`와 `hashCode()`가 예기치 않게 동작할 수 있다.

---

### 3. DTO에 Data Class 적용

반면 DTO는 `data class`가 완벽한 선택이다.

```kotlin
data class CreateUserRequest(
    val email: String,
    val name: String,
    val age: Int
)

data class UserResponse(
    val id: Long,
    val email: String,
    val name: String
) {
    companion object {
        fun from(user: User): UserResponse = UserResponse(
            id = user.id,
            email = user.email,
            name = user.name
        )
    }
}
```

Jackson과의 연동을 위해 `jackson-module-kotlin` 의존성이 반드시 필요하며, `@JsonProperty` 없이도 Kotlin 프로퍼티 이름 그대로 직렬화된다.

---

### 4. 서비스 레이어: Null Safety와 Extension Function

```kotlin
@Service
@Transactional(readOnly = true)
class UserService(
    private val userRepository: UserRepository
) {
    fun getUser(id: Long): UserResponse {
        val user = userRepository.findById(id)
            .orElseThrow { EntityNotFoundException("User not found: $id") }
        return UserResponse.from(user)
    }

    @Transactional
    fun updateUserName(id: Long, newName: String): UserResponse {
        val user = userRepository.findById(id)
            .orElseThrow { EntityNotFoundException("User not found: $id") }
        user.name = newName
        return UserResponse.from(user)
    }
}

// Extension Function으로 Optional 처리를 간결하게
fun <T> Optional<T>.orThrow(exception: () -> Throwable): T =
    this.orElseThrow(exception)

// 사용
val user = userRepository.findById(id).orThrow { EntityNotFoundException("User not found: $id") }
```

---

### 5. 컨트롤러: Spring MVC + Kotlin

```kotlin
@RestController
@RequestMapping("/api/v1/users")
class UserController(
    private val userService: UserService
) {
    @GetMapping("/{id}")
    fun getUser(@PathVariable id: Long): ResponseEntity<UserResponse> {
        return ResponseEntity.ok(userService.getUser(id))
    }

    @PostMapping
    fun createUser(@RequestBody @Valid request: CreateUserRequest): ResponseEntity<UserResponse> {
        val response = userService.createUser(request)
        return ResponseEntity
            .created(URI.create("/api/v1/users/${response.id}"))
            .body(response)
    }
}
```

---

### 6. Coroutine + Spring WebFlux (고급)

Kotlin Coroutine은 Spring WebFlux와 함께 사용할 때 진가를 발휘한다.

```kotlin
@RestController
@RequestMapping("/api/v1/async/users")
class AsyncUserController(
    private val userService: AsyncUserService
) {
    @GetMapping("/{id}")
    suspend fun getUser(@PathVariable id: Long): UserResponse {
        return userService.getUser(id)  // suspend 함수 직접 호출
    }

    @GetMapping
    fun getAllUsers(): Flow<UserResponse> {
        return userService.getAllUsers()  // Flow로 스트리밍
    }
}

@Service
class AsyncUserService(
    private val userRepository: CoroutineUserRepository  // R2DBC 기반
) {
    suspend fun getUser(id: Long): UserResponse {
        return userRepository.findById(id)
            ?.let { UserResponse.from(it) }
            ?: throw EntityNotFoundException("User not found: $id")
    }

    fun getAllUsers(): Flow<UserResponse> = flow {
        userRepository.findAll().collect { user ->
            emit(UserResponse.from(user))
        }
    }
}
```

---

### 7. 전역 예외 처리

```kotlin
@RestControllerAdvice
class GlobalExceptionHandler {

    @ExceptionHandler(EntityNotFoundException::class)
    fun handleNotFound(e: EntityNotFoundException): ResponseEntity<ErrorResponse> =
        ResponseEntity.status(HttpStatus.NOT_FOUND)
            .body(ErrorResponse(code = "NOT_FOUND", message = e.message ?: "Resource not found"))

    @ExceptionHandler(MethodArgumentNotValidException::class)
    fun handleValidation(e: MethodArgumentNotValidException): ResponseEntity<ErrorResponse> {
        val errors = e.bindingResult.fieldErrors
            .associate { it.field to (it.defaultMessage ?: "Invalid value") }
        return ResponseEntity.badRequest()
            .body(ErrorResponse(code = "VALIDATION_FAILED", message = "Validation error", details = errors))
    }
}

data class ErrorResponse(
    val code: String,
    val message: String,
    val details: Map<String, String> = emptyMap(),
    val timestamp: LocalDateTime = LocalDateTime.now()
)
```

---

## 주의사항 및 트레이드오프

### ⚠️ Kotlin의 final 클래스 문제

Kotlin 클래스는 기본적으로 `final`이다. Spring의 AOP(CGLIB 기반)는 클래스를 상속하여 프록시를 생성하므로, `plugin.spring` 없이는 `@Transactional`, `@Async`, `@Cacheable`이 동작하지 않는다. 반드시 플러그인을 적용하라.

### ⚠️ Java와 Kotlin 혼용 시 컴파일 순서

Kotlin 컴파일러는 Java 소스를 참조할 수 있지만, Kotlin 파일이 먼저 컴파일된다. 두 언어가 서로를 참조하는 순환 의존이 있을 경우 컴파일 오류가 발생할 수 있다. 마이그레이션 초기에는 **Kotlin → Java 단방향 의존**을 유지하는 것이 안전하다.

### ⚠️ Spring Data JPA의 Repository 인터페이스

```kotlin
interface UserRepository : JpaRepository<User, Long> {
    fun findByEmail(email: String): User?  // nullable 반환 타입 명시
    fun findAllByNameContaining(name: String): List<User>
}
```

반환 타입을 `User?`로 선언하면 null 안전성을 컴파일 타임에 보장할 수 있다. Java에서는 이를 런타임에서야 확인했다.

### ⚠️ 테스트 코드 전환

Kotlin은 백틱(`` ` ``)을 이용한 함수명이 가능하여 테스트 가독성이 크게 향상된다.

```kotlin
@Test
fun `사용자 ID로 조회 시 존재하지 않으면 예외를 던진다`() {
    every { userRepository.findById(999L) } returns Optional.empty()

    assertThrows<EntityNotFoundException> {
        userService.getUser(999L)
    }
}
```

### 트레이드오프 정리

| 항목 | 장점 | 고려사항 |
|---|---|---|
| Null Safety | 런타임 NPE 감소 | Java API 호출 시 플랫폼 타입 주의 |
| Data Class | 보일러플레이트 감소 | JPA Entity에는 부적합 |
| Coroutine | 비동기 코드 간결화 | 학습 곡선, 디버깅 어려움 |
| 혼용 운영 | 점진적 마이그레이션 가능 | 팀 내 언어 통일 전까지 복잡도 증가 |

---

## 정리

Kotlin + Spring Boot 마이그레이션은 **"전부 아니면 전무"** 가 아니다. 핵심은 점진적 접근이다.

1. **빌드 설정부터 시작**: `plugin.spring`, `plugin.jpa` 누락 없이 설정
2. **DTO → 서비스 → 컨트롤러** 순서로 안전하게 전환
3. **JPA Entity는 일반 class 사용**, DTO는 data class 적극 활용
4. **Null Safety를 적극 활용**하되, Java 라이브러리의 플랫폼 타입에 주의
5. **Coroutine은 WebFlux 전환 이후**에 도입하는 것이 학습 부담을 줄임

팀 전체가 Kotlin에 익숙해지는 데는 시간이 필요하지만, 코드 품질과 개발 생산성 향상은 수치로 확인 가능한 수준이다. 지금 당장 새로운 기능이나 신규 마이크로서비스부터 Kotlin으로 작성해보는 것을 강력히 권장한다.