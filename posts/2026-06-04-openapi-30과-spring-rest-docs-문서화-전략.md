# OpenAPI 3.0과 Spring REST Docs 문서화 전략

## 개요

API 문서화는 백엔드 개발에서 종종 "필요하지만 귀찮은" 작업으로 취급받는다. Swagger UI를 올려놓고 프로덕션 코드에 어노테이션이 난무하거나, 반대로 Spring REST Docs로 테스트 기반 문서를 만들었지만 OpenAPI 스펙과 연계되지 않아 활용성이 떨어지는 경우를 실무에서 자주 마주친다.

이 글에서는 **Spring REST Docs**와 **OpenAPI 3.0**을 함께 활용하는 전략을 다룬다. 구체적으로는 `restdocs-api-spec` 라이브러리를 통해 REST Docs 테스트에서 OpenAPI 3.0 스펙을 자동 생성하고, 이를 Swagger UI와 통합하는 파이프라인을 구성한다. 프로덕션 코드를 오염시키지 않으면서도 살아있는 문서(Living Documentation)를 유지하는 방법을 실전 예제와 함께 살펴본다.

---

## 핵심 개념

### Spring REST Docs vs Swagger(springdoc-openapi)

두 접근 방식은 철학이 다르다.

| 구분 | Spring REST Docs | springdoc-openapi (Swagger) |
|------|-----------------|----------------------------|
| 문서 생성 기준 | 테스트 통과 여부 | 런타임 리플렉션/어노테이션 |
| 프로덕션 코드 오염 | 없음 | `@Operation`, `@ApiResponse` 등 추가 필요 |
| 정확성 | 테스트 실패 시 문서 미생성 → 높음 | 코드와 문서 불일치 가능 |
| UI 제공 | Asciidoc/Markdown 기반 정적 페이지 | Swagger UI (인터랙티브) |
| 학습 비용 | 상대적으로 높음 | 낮음 |

Spring REST Docs의 가장 큰 장점은 **테스트가 곧 문서의 계약**이라는 점이다. 테스트가 깨지면 문서도 생성되지 않으므로, API 변경 시 문서가 자연스럽게 갱신된다.

### restdocs-api-spec

[`restdocs-api-spec`](https://github.com/ePages-de/restdocs-api-spec)은 ePages에서 개발한 오픈소스 라이브러리로, Spring REST Docs 테스트 결과를 OpenAPI 2.0/3.0 스펙 파일로 변환해준다. 핵심 흐름은 다음과 같다.

```
테스트 실행 → REST Docs 스니펫 생성 → OpenAPI 3.0 YAML/JSON 생성 → Swagger UI 서빙
```

이 파이프라인을 통해 두 도구의 장점을 모두 취할 수 있다.

---

## 실전 예제

### 의존성 설정 (Gradle)

```kotlin
// build.gradle.kts
plugins {
    id("com.epages.restdocs-api-spec") version "0.19.2"
}

dependencies {
    testImplementation("org.springframework.restdocs:spring-restdocs-mockmvc")
    testImplementation("com.epages:restdocs-api-spec-mockmvc:0.19.2")
}

openapi3 {
    server("https://api.example.com")
    title = "Order Service API"
    description = "주문 서비스 REST API 명세서"
    version = "1.0.0"
    format = "yaml"
    outputDirectory = "build/api-spec"
}
```

### 도메인 및 컨트롤러 코드

```java
// OrderController.java
@RestController
@RequestMapping("/api/v1/orders")
@RequiredArgsConstructor
public class OrderController {

    private final OrderService orderService;

    @PostMapping
    public ResponseEntity<OrderResponse> createOrder(
            @RequestBody @Valid CreateOrderRequest request) {
        OrderResponse response = orderService.createOrder(request);
        return ResponseEntity.status(HttpStatus.CREATED).body(response);
    }

    @GetMapping("/{orderId}")
    public ResponseEntity<OrderResponse> getOrder(@PathVariable Long orderId) {
        return ResponseEntity.ok(orderService.findById(orderId));
    }
}
```

프로덕션 코드에는 Swagger 어노테이션이 전혀 없다. 깔끔하다.

### REST Docs + OpenAPI 스펙을 동시에 생성하는 테스트

핵심은 `ResourceSnippet`과 `ResourceSnippetParameters`를 활용하는 것이다.

```java
// OrderControllerDocTest.java
@WebMvcTest(OrderController.class)
@AutoConfigureRestDocs
@ExtendWith(MockitoExtension.class)
class OrderControllerDocTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private OrderService orderService;

    @Autowired
    private ObjectMapper objectMapper;

    @Test
    @DisplayName("주문 생성 API 문서화")
    void createOrder() throws Exception {
        // given
        CreateOrderRequest request = CreateOrderRequest.builder()
                .productId(1L)
                .quantity(2)
                .shippingAddress("서울시 강남구 테헤란로 123")
                .build();

        OrderResponse response = OrderResponse.builder()
                .orderId(100L)
                .status("PENDING")
                .totalAmount(50000)
                .createdAt(LocalDateTime.now())
                .build();

        given(orderService.createOrder(any())).willReturn(response);

        // when & then
        mockMvc.perform(post("/api/v1/orders")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.orderId").value(100L))
                .andDo(document("order-create",
                        resource(ResourceSnippetParameters.builder()
                                .tag("Order")
                                .summary("주문 생성")
                                .description("신규 주문을 생성합니다. 재고 확인 후 PENDING 상태로 생성됩니다.")
                                .requestFields(
                                        fieldWithPath("productId")
                                                .type(JsonFieldType.NUMBER)
                                                .description("상품 ID"),
                                        fieldWithPath("quantity")
                                                .type(JsonFieldType.NUMBER)
                                                .description("주문 수량 (1~100)"),
                                        fieldWithPath("shippingAddress")
                                                .type(JsonFieldType.STRING)
                                                .description("배송지 주소")
                                )
                                .responseFields(
                                        fieldWithPath("orderId")
                                                .type(JsonFieldType.NUMBER)
                                                .description("생성된 주문 ID"),
                                        fieldWithPath("status")
                                                .type(JsonFieldType.STRING)
                                                .description("주문 상태 (PENDING, CONFIRMED, SHIPPED, DELIVERED)"),
                                        fieldWithPath("totalAmount")
                                                .type(JsonFieldType.NUMBER)
                                                .description("총 결제 금액"),
                                        fieldWithPath("createdAt")
                                                .type(JsonFieldType.STRING)
                                                .description("주문 생성 시각 (ISO 8601)")
                                )
                                .responseSchema(Schema.schema("OrderResponse"))
                                .requestSchema(Schema.schema("CreateOrderRequest"))
                                .build()
                        )
                ));
    }

    @Test
    @DisplayName("주문 단건 조회 API 문서화")
    void getOrder() throws Exception {
        // given
        OrderResponse response = OrderResponse.builder()
                .orderId(100L)
                .status("CONFIRMED")
                .totalAmount(50000)
                .createdAt(LocalDateTime.now())
                .build();

        given(orderService.findById(100L)).willReturn(response);

        // when & then
        mockMvc.perform(get("/api/v1/orders/{orderId}", 100L)
                        .accept(MediaType.APPLICATION_JSON))
                .andExpect(status().isOk())
                .andDo(document("order-get",
                        resource(ResourceSnippetParameters.builder()
                                .tag("Order")
                                .summary("주문 단건 조회")
                                .pathParameters(
                                        parameterWithName("orderId").description("조회할 주문 ID")
                                )
                                .responseFields(
                                        fieldWithPath("orderId").description("주문 ID"),
                                        fieldWithPath("status").description("주문 상태"),
                                        fieldWithPath("totalAmount").description("총 결제 금액"),
                                        fieldWithPath("createdAt").description("주문 생성 시각")
                                )
                                .build()
                        )
                ));
    }
}
```

### OpenAPI 스펙 병합 및 Swagger UI 서빙

테스트 실행 후 `openapi3` Gradle 태스크를 실행하면 스니펫들이 하나의 YAML 파일로 병합된다.

```bash
./gradlew openapi3
# build/api-spec/openapi3.yaml 생성
```

생성된 파일을 애플리케이션에서 정적으로 서빙하거나, springdoc-openapi의 커스텀 URL 기능을 활용해 Swagger UI에 연결할 수 있다.

```kotlin
// build.gradle.kts - 스펙 파일을 리소스로 복사
tasks.register<Copy>("copyOasToResources") {
    dependsOn("openapi3")
    from("build/api-spec/openapi3.yaml")
    into("src/main/resources/static/docs")
}

tasks.named("processResources") {
    dependsOn("copyOasToResources")
}
```

```yaml
# application.yml
springdoc:
  swagger-ui:
    url: /docs/openapi3.yaml
    path: /swagger-ui.html
  api-docs:
    enabled: false  # 런타임 스캔 비활성화 (파일 기반으로만 동작)
```

이렇게 구성하면 **Swagger UI의 인터랙티브한 인터페이스**를 유지하면서도 **문서의 정확성은 테스트가 보장**하는 이상적인 구조가 완성된다.

---

## 주의사항 및 트레이드오프

### 1. 빌드 파이프라인 복잡도 증가

REST Docs → OpenAPI 변환은 별도의 Gradle 태스크 실행을 요구한다. CI/CD 파이프라인에서 `test → openapi3 → copyOasToResources → build` 순서를 명확히 정의하지 않으면 오래된 스펙 파일이 배포될 수 있다.

```kotlin
// 태스크 의존성을 명확하게 선언
tasks.named("build") {
    dependsOn("copyOasToResources")
}
```

### 2. 테스트 커버리지와 문서 커버리지의 갭

단위 테스트는 존재하지만 문서화 테스트(`@WebMvcTest` + `document()`)가 빠진 엔드포인트는 스펙에 나타나지 않는다. 이는 장점이기도 하지만(실제 테스트된 API만 노출), 개발자가 새 엔드포인트 추가 시 문서화 테스트를 함께 작성해야 한다는 팀 컨벤션이 필요하다. **ArchUnit** 같은 도구로 아키텍처 규칙을 강제하는 방법도 고려해볼 만하다.

### 3. 중첩 객체와 배열 처리

`fieldWithPath` 경로 표현에서 중첩 필드 처리 시 실수가 잦다.

```java
// 배열 내 객체 필드 처리 예시
fieldWithPath("items[].productId").description("상품 ID"),
fieldWithPath("items[].quantity").description("수량"),
fieldWithPath("items[].unitPrice").description("단가"),
```

특히 Optional 필드나 nullable 필드는 `.optional()`을 명시적으로 선언하지 않으면 테스트가 깨진다.

```java
fieldWithPath("couponCode")
    .type(JsonFieldType.STRING)
    .description("쿠폰 코드 (선택 사항)")
    .optional()
```

### 4. MockMvc vs RestAssured 선택

`@SpringBootTest` + RestAssured를 사용하는 통합 테스트 환경이라면 `restdocs-api-spec-restassured` 의존성을 활용할 수 있다. 단, 테스트 속도가 `@WebMvcTest`보다 현저히 느려지므로 팀의 테스트 전략에 맞게 선택해야 한다.

### 5. 버전 호환성

`restdocs-api-spec`의 버전은 Spring Boot 버전과 맞물린다. Spring Boot 3.x 환경에서는 0.17.x 이상을 사용해야 하며, Jakarta EE 네임스페이스 변경도 고려해야 한다.

---

## 정리

| 접근 방식 | 추천 상황 |
|-----------|----------|
| springdoc-openapi만 | 빠른 프로토타이핑, 소규모 팀, 문서 정확성보다 속도 우선 |
| Spring REST Docs만 | Asciidoc 기반 사용자 가이드 문서가 주 산출물인 경우 |
| **REST Docs + restdocs-api-spec** | 엔터프라이즈 환경, API 계약 정확성 중요, Swagger UI도 필요한 경우 |

Spring REST Docs와 OpenAPI 3.0을 결합하는 전략은 초기 설정 비용이 있지만, 중장기적으로 **코드-문서 불일치 문제를 구조적으로 차단**한다는 점에서 투자 가치가 높다. 특히 여러 팀이 API를 소비하는 MSA 환경이나, 외부 파트너에게 API를 공개해야 하는 상황에서 진가를 발휘한다.

핵심은 **문서화를 개발 프로세스의 일부로 강제하는 구조**를 만드는 것이다. "나중에 문서 업데이트할게요"라는 말이 필요 없는 시스템, 테스트가 통과하면 문서도 자동으로 최신화되는 파이프라인이 그 목표다.