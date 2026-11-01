# Kotlin DSL로 테스트·문서화 반복 코드 줄이기

## 개요

Java/Spring 프로젝트를 운영하다 보면, 테스트 코드와 API 문서화 코드에서 반복 패턴이 끊임없이 등장한다. MockMvc 요청을 작성할 때마다 `.perform()`, `.andExpect()`, `.andDo()` 체인을 손으로 타이핑하고, REST Docs 스니펫을 기술할 때도 `fieldWithPath()`를 줄마다 나열한다. 프로젝트가 커질수록 이 보일러플레이트는 수백 줄에 달하고, 수정이 필요할 때마다 여러 파일을 일관성 없이 고쳐야 하는 부담이 생긴다.

Kotlin DSL은 이 문제에 대한 우아한 해법이다. Kotlin의 람다 수신자(lambda with receiver), 중위 함수(infix function), 확장 함수(extension function)를 조합하면, 기존 Java API를 감싸는 선언적이고 읽기 좋은 DSL을 만들 수 있다. 이 글에서는 실무에서 자주 마주치는 MockMvc 테스트와 Spring REST Docs 코드를 예제로, Kotlin DSL을 어떻게 설계하고 적용할 수 있는지 구체적으로 살펴본다.

---

## 핵심 개념

### 람다 수신자(Lambda with Receiver)

Kotlin DSL의 핵심 메커니즘이다. 함수 타입에 수신자 타입을 지정하면, 블록 내부에서 `this` 없이 수신자 객체의 멤버에 접근할 수 있다.

```kotlin
fun buildRequest(block: RequestSpec.() -> Unit): RequestSpec {
    return RequestSpec().apply(block)
}

// 사용 측
val spec = buildRequest {
    url = "/api/users"
    method = HttpMethod.GET
}
```

`RequestSpec.() -> Unit` 타입의 블록 안에서는 `RequestSpec`의 프로퍼티와 함수를 직접 호출할 수 있어, XML 설정이나 Builder 패턴보다 훨씬 자연스러운 계층 구조를 표현할 수 있다.

### 확장 함수(Extension Function)

기존 클래스를 수정하지 않고 새 기능을 추가할 수 있어, 외부 라이브러리 위에 DSL 레이어를 얹기에 적합하다.

```kotlin
fun MockMvc.get(url: String, block: MockHttpServletRequestDsl.() -> Unit = {}): ResultActions {
    return perform(MockMvcRequestBuilders.get(url).apply { /* ... */ })
}
```

### `@DslMarker`로 스코프 제어

DSL이 복잡해지면 외부 스코프의 수신자가 내부 블록에서 암묵적으로 접근되어 혼란이 생길 수 있다. `@DslMarker` 어노테이션으로 이를 방지한다.

```kotlin
@DslMarker
annotation class RestDocsDsl

@RestDocsDsl
class FieldsBuilder { /* ... */ }
```

---

## 실전 예제

### 1. MockMvc 요청 DSL 만들기

아래는 반복적인 MockMvc 테스트 코드의 전형적인 모습이다.

```kotlin
// DSL 없이 작성한 테스트
mockMvc.perform(
    post("/api/users")
        .contentType(MediaType.APPLICATION_JSON)
        .content(objectMapper.writeValueAsString(request))
        .header("Authorization", "Bearer $token")
)
    .andExpect(status().isCreated)
    .andExpect(jsonPath("$.id").isNumber)
    .andExpect(jsonPath("$.name").value("홍길동"))
```

이 패턴이 수십 개의 테스트에 퍼져 있으면 헤더 추가나 공통 설정 변경이 매우 번거롭다. DSL로 감싸보자.

```kotlin
// MockMvcDsl.kt
@DslMarker
annotation class MockMvcDslMarker

@MockMvcDslMarker
class MockMvcRequestBuilder(private val mockMvc: MockMvc) {
    var url: String = ""
    var method: HttpMethod = HttpMethod.GET
    var body: Any? = null
    val headers: MutableMap<String, String> = mutableMapOf()
    val expects: MutableList<ResultMatcher> = mutableListOf()

    private val objectMapper = ObjectMapper().apply {
        registerModule(JavaTimeModule())
    }

    fun header(key: String, value: String) {
        headers[key] = value
    }

    fun expect(matcher: ResultMatcher) {
        expects.add(matcher)
    }

    fun execute(): ResultActions {
        val requestBuilder = when (method) {
            HttpMethod.GET -> MockMvcRequestBuilders.get(url)
            HttpMethod.POST -> MockMvcRequestBuilders.post(url)
            HttpMethod.PUT -> MockMvcRequestBuilders.put(url)
            HttpMethod.DELETE -> MockMvcRequestBuilders.delete(url)
            else -> throw IllegalArgumentException("Unsupported method: $method")
        }.apply {
            contentType(MediaType.APPLICATION_JSON)
            body?.let { content(objectMapper.writeValueAsString(it)) }
            headers.forEach { (k, v) -> header(k, v) }
        }

        return mockMvc.perform(requestBuilder).apply {
            expects.forEach { andExpect(it) }
        }
    }
}

fun MockMvc.request(block: MockMvcRequestBuilder.() -> Unit): ResultActions {
    return MockMvcRequestBuilder(this).apply(block).execute()
}
```

이제 테스트 코드는 훨씬 선언적으로 바뀐다.

```kotlin
// 테스트에서 사용
@Test
fun `사용자 생성 성공`() {
    val request = CreateUserRequest(name = "홍길동", email = "hong@example.com")

    mockMvc.request {
        method = HttpMethod.POST
        url = "/api/users"
        body = request
        header("Authorization", "Bearer $token")

        expect(status().isCreated)
        expect(jsonPath("$.id").isNumber)
        expect(jsonPath("$.name").value("홍길동"))
    }
}
```

### 2. Spring REST Docs 필드 기술 DSL

REST Docs를 사용할 때 가장 반복이 심한 부분은 `fieldWithPath()` 나열이다.

```kotlin
// DSL 없이 작성한 REST Docs
resultActions.andDo(
    document(
        "create-user",
        requestFields(
            fieldWithPath("name").type(JsonFieldType.STRING).description("사용자 이름"),
            fieldWithPath("email").type(JsonFieldType.STRING).description("이메일 주소"),
            fieldWithPath("age").type(JsonFieldType.NUMBER).description("나이").optional()
        ),
        responseFields(
            fieldWithPath("id").type(JsonFieldType.NUMBER).description("생성된 사용자 ID"),
            fieldWithPath("name").type(JsonFieldType.STRING).description("사용자 이름"),
            fieldWithPath("createdAt").type(JsonFieldType.STRING).description("생성 일시")
        )
    )
)
```

아래 DSL을 정의하면 중복을 크게 줄일 수 있다.

```kotlin
// RestDocsDsl.kt
@DslMarker
annotation class RestDocsDslMarker

@RestDocsDslMarker
class FieldSpecBuilder {
    private val fields: MutableList<FieldDescriptor> = mutableListOf()

    infix fun String.means(description: String): FieldDescriptor {
        val descriptor = PayloadDocumentation.fieldWithPath(this).description(description)
        fields.add(descriptor)
        return descriptor
    }

    infix fun FieldDescriptor.typeAs(type: JsonFieldType): FieldDescriptor {
        return this.type(type).also {
            val idx = fields.indexOf(this)
            if (idx >= 0) fields[idx] = it
        }
    }

    fun FieldDescriptor.optional(): FieldDescriptor {
        return this.optional().also {
            val idx = fields.indexOf(this)
            if (idx >= 0) fields[idx] = it
        }
    }

    fun build(): List<FieldDescriptor> = fields.toList()
}

fun requestFields(block: FieldSpecBuilder.() -> Unit): RequestFieldsSnippet {
    val fields = FieldSpecBuilder().apply(block).build()
    return PayloadDocumentation.requestFields(fields)
}

fun responseFields(block: FieldSpecBuilder.() -> Unit): ResponseFieldsSnippet {
    val fields = FieldSpecBuilder().apply(block).build()
    return PayloadDocumentation.responseFields(fields)
}
```

사용 시에는 다음처럼 간결해진다.

```kotlin
resultActions.andDo(
    document(
        "create-user",
        requestFields {
            "name" means "사용자 이름" typeAs STRING
            "email" means "이메일 주소" typeAs STRING
            "age" means "나이" typeAs NUMBER optional()
        },
        responseFields {
            "id" means "생성된 사용자 ID" typeAs NUMBER
            "name" means "사용자 이름" typeAs STRING
            "createdAt" means "생성 일시" typeAs STRING
        }
    )
)
```

### 3. 두 DSL 통합하기

테스트와 문서화를 하나의 블록으로 묶으면 응집도가 더 높아진다.

```kotlin
fun MockMvc.requestAndDocument(
    identifier: String,
    block: DocumentedRequestBuilder.() -> Unit
): ResultActions {
    return DocumentedRequestBuilder(this, identifier).apply(block).execute()
}

@MockMvcDslMarker
class DocumentedRequestBuilder(
    private val mockMvc: MockMvc,
    private val identifier: String
) {
    private val requestBuilder = MockMvcRequestBuilder(mockMvc)
    private val snippets: MutableList<Snippet> = mutableListOf()

    fun request(block: MockMvcRequestBuilder.() -> Unit) {
        requestBuilder.apply(block)
    }

    fun document(block: SnippetBuilder.() -> Unit) {
        snippets.addAll(SnippetBuilder().apply(block).build())
    }

    fun execute(): ResultActions {
        return requestBuilder.execute()
            .andDo(MockMvcRestDocumentation.document(identifier, *snippets.toTypedArray()))
    }
}
```

최종 테스트 코드는 아래와 같이 완성된다.

```kotlin
@Test
fun `사용자 생성 - 문서화 포함`() {
    mockMvc.requestAndDocument("create-user") {
        request {
            method = HttpMethod.POST
            url = "/api/users"
            body = CreateUserRequest(name = "홍길동", email = "hong@example.com")
            header("Authorization", "Bearer $token")
            expect(status().isCreated)
        }
        document {
            requestFields {
                "name" means "사용자 이름" typeAs STRING
                "email" means "이메일 주소" typeAs STRING
            }
            responseFields {
                "id" means "생성된 사용자 ID" typeAs NUMBER
                "name" means "사용자 이름" typeAs STRING
            }
        }
    }
}
```

---

## 주의사항 및 트레이드오프

### 학습 곡선과 팀 컨벤션

DSL은 강력하지만 팀원 모두가 Kotlin DSL 설계 원리를 이해해야 한다. 중위 함수나 람다 수신자에 익숙하지 않은 팀원이 DSL을 처음 접하면 "이 코드가 어디서 동작하는지" 파악하기 어렵다. **DSL 작성자는 반드시 내부 구현 파일에 충분한 주석과 사용 예제를 남겨야 한다.**

### 과도한 추상화의 위험

DSL이 너무 많은 것을 숨기면, 오류 발생 시 디버깅이 어려워진다. 특히 `apply`, `also`, `run` 스코프 함수가 중첩되면 스택 트레이스를 역추적하기 까다롭다. DSL 내부에서 예외를 잡아 의미 있는 메시지로 감싸는 처리를 고려하라.

```kotlin
fun execute(): ResultActions {
    return try {
        requestBuilder.execute()
    } catch (e: Exception) {
        throw IllegalStateException("DSL 요청 실행 중 오류 발생: url=$url, method=$method", e)
    }
}
```

### Java 프로젝트와의 혼용

Java 파일에서 Kotlin DSL을 호출하면 람다 문법이 어색해지고 장점이 상당히 희석된다. DSL은 Kotlin 테스트 파일에서만 사용하도록 범위를 제한하거나, 팀이 Kotlin으로 전환하는 로드맵을 갖고 있을 때 도입하는 것이 좋다.

### 버전 호환성

Spring Boot 버전, MockMvc, REST Docs의 API는 버전마다 다소 차이가 있다. DSL 레이어가 특정 버전의 내부 API에 강하게 의존하면, 라이브러리 업그레이드 시 DSL 전체를 수정해야 할 수 있다. **공개 API만 사용하고, 내부 구현 클래스에는 직접 의존하지 않는 것이 원칙이다.**

### 테스트 가독성이 최우선

DSL을 만드는 목적은 코드 줄 수를 줄이는 게 아니라 **의도를 명확하게 표현**하는 것이다. DSL이 오히려 의도를 숨기거나 흐름을 파악하기 어렵게 만든다면, 원래의 Fluent API로 돌아가는 것도 훌륭한 선택이다.

---

## 정리

Kotlin DSL은 반복적인 테스트·문서화 코드를 줄이는 데 매우 효과적인 도구다. 핵심은 세 가지다.

1. **람다 수신자**로 블록 내부에서 컨텍스트를 자연스럽게 접근하게 한다.
2. **확장 함수와 중위 함수**로 기존 라이브러리 위에 얇은 DSL 레이어를 추가한다.
3. **`@DslMarker`**로 스코프 오염을 막아 DSL을 안전하게 유지한다.

무엇보다 중요한 것은 DSL이 팀의 공통 언어가 되어야 한다는 점이다. 잘 설계된 DSL은 테스트 코드를 명세서처럼 읽히게 하고, 신규 팀원이 API 구조를 파악하는 시간을 단축시킨다. 처음에는 작은 범위, 예를 들어 특정 도메인의 테스트 픽스처나 자주 쓰는 요청 패턴부터 DSL로 감싸보고, 팀의 피드백을 받아 점진적으로 확장하는 방식을 권장한다.
