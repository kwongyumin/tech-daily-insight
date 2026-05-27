# 서버리스 아키텍처 AWS Lambda와 Spring Cloud Function

## 개요

서버리스(Serverless) 아키텍처는 "서버가 없다"는 의미가 아니라, 개발자가 서버 인프라 관리에서 해방되어 비즈니스 로직에만 집중할 수 있는 패러다임이다. AWS Lambda는 그 대표 주자로, 이벤트 기반의 함수 실행 환경을 제공한다. 여기에 Spring 생태계를 그대로 활용할 수 있는 **Spring Cloud Function**을 결합하면, 익숙한 Spring 방식으로 서버리스 애플리케이션을 구축할 수 있다.

이 글에서는 Spring Cloud Function의 핵심 개념부터 AWS Lambda 배포까지의 실전 흐름을 다루고, 콜드 스타트(Cold Start) 등 실무에서 반드시 고려해야 할 트레이드오프까지 짚어본다.

---

## 핵심 개념

### AWS Lambda란?

AWS Lambda는 코드 실행에 필요한 서버를 AWS가 자동으로 프로비저닝하고 관리하는 FaaS(Function as a Service) 플랫폼이다. 요청이 들어올 때만 함수가 실행되고, 실행 시간과 요청 수에 따라 과금된다.

- **이벤트 트리거**: API Gateway, S3, SQS, DynamoDB Streams 등 다양한 AWS 서비스와 연동
- **자동 스케일링**: 동시 요청 수에 따라 자동으로 인스턴스 생성/소멸
- **과금 모델**: 요청 수(100만 건당 $0.20) + 실행 시간(GB-초 단위)

### Spring Cloud Function이란?

Spring Cloud Function은 비즈니스 로직을 **Java의 표준 함수형 인터페이스**(`Function`, `Consumer`, `Supplier`)로 표현하고, 이를 다양한 플랫폼(AWS Lambda, Azure Functions, GCP Functions, HTTP 엔드포인트)에 배포할 수 있게 해주는 프레임워크다.

핵심 철학은 **플랫폼 독립성(Platform Independence)**이다. 동일한 비즈니스 로직을 로컬 HTTP 서버로 테스트하고, 그대로 Lambda에 배포할 수 있다.

```
┌─────────────────────────────────────────┐
│         Spring Cloud Function           │
│                                         │
│  Function<I, O>  Consumer<I>  Supplier<O>│
└──────────────┬──────────────────────────┘
               │ Adapter
    ┌──────────┼──────────┐
    │          │          │
  AWS       Azure      HTTP
 Lambda   Functions  Endpoint
```

---

## 실전 예제

### 프로젝트 설정

`build.gradle` 또는 `pom.xml`에 필요한 의존성을 추가한다.

```groovy
// build.gradle
plugins {
    id 'org.springframework.boot' version '3.2.0'
    id 'io.spring.dependency-management' version '1.1.4'
    id 'java'
}

dependencies {
    implementation 'org.springframework.cloud:spring-cloud-function-context'
    implementation 'org.springframework.cloud:spring-cloud-function-adapter-aws'
    implementation 'com.amazonaws:aws-lambda-java-events:3.11.3'
    implementation 'com.amazonaws:aws-lambda-java-core:1.2.3'

    // 경량화를 위해 web starter 제외
    // implementation 'org.springframework.boot:spring-boot-starter-web'
}

dependencyManagement {
    imports {
        mavenBom "org.springframework.cloud:spring-cloud-dependencies:2023.0.0"
    }
}

// Lambda 배포용 thin jar 생성
task buildZip(type: Zip) {
    from compileJava
    from processResources
    into('lib') {
        from configurations.runtimeClasspath
    }
}
```

### 비즈니스 로직 구현

주문 처리 Lambda를 예시로 구현해보자.

```java
// OrderRequest.java
public record OrderRequest(
    String orderId,
    String customerId,
    List<OrderItem> items,
    BigDecimal totalAmount
) {}

// OrderResponse.java
public record OrderResponse(
    String orderId,
    String status,
    String message,
    LocalDateTime processedAt
) {}
```

```java
// OrderProcessingFunction.java
@Component
public class OrderProcessingFunction implements Function<OrderRequest, OrderResponse> {

    private final OrderValidator validator;
    private final OrderRepository orderRepository;
    private final NotificationService notificationService;

    public OrderProcessingFunction(
            OrderValidator validator,
            OrderRepository orderRepository,
            NotificationService notificationService) {
        this.validator = validator;
        this.orderRepository = orderRepository;
        this.notificationService = notificationService;
    }

    @Override
    public OrderResponse apply(OrderRequest request) {
        // 유효성 검증
        validator.validate(request);

        // 주문 저장
        Order order = orderRepository.save(Order.from(request));

        // 비동기 알림 (SNS/SQS 등을 활용하는 것이 좋지만 예시로 단순화)
        notificationService.sendOrderConfirmation(order);

        return new OrderResponse(
            order.getId(),
            "CONFIRMED",
            "주문이 성공적으로 처리되었습니다.",
            LocalDateTime.now()
        );
    }
}
```

```java
// Application.java
@SpringBootApplication
public class OrderLambdaApplication {
    public static void main(String[] args) {
        SpringApplication.run(OrderLambdaApplication.class, args);
    }

    // Function Bean을 명시적으로 등록 (여러 함수가 있을 경우 구분 필요)
    @Bean
    public Function<OrderRequest, OrderResponse> processOrder(
            OrderProcessingFunction orderProcessingFunction) {
        return orderProcessingFunction;
    }
}
```

### Lambda 핸들러 설정

Spring Cloud Function은 AWS Lambda 어댑터를 통해 자동으로 핸들러를 생성한다.

```java
// SpringBootApiGatewayRequestHandler를 그대로 사용 (API Gateway 연동)
// Lambda 핸들러 설정값: org.springframework.cloud.function.adapter.aws.FunctionInvoker::handleRequest
```

`application.yml`에서 실행할 함수를 지정한다.

```yaml
# application.yml
spring:
  cloud:
    function:
      definition: processOrder  # Bean 이름과 일치해야 함
  main:
    web-application-type: none  # 서버리스 환경에서 웹 서버 비활성화
    lazy-initialization: true   # 콜드 스타트 최적화

logging:
  level:
    org.springframework.cloud.function: DEBUG
```

### SQS 트리거를 활용한 이벤트 기반 처리

API Gateway 외에 SQS 이벤트를 처리하는 Consumer 예시다.

```java
@Component
public class OrderEventConsumer implements Consumer<SQSEvent> {

    private static final Logger log = LoggerFactory.getLogger(OrderEventConsumer.class);
    private final ObjectMapper objectMapper;
    private final OrderProcessingFunction orderProcessor;

    // 생성자 주입 생략...

    @Override
    public void accept(SQSEvent sqsEvent) {
        sqsEvent.getRecords().forEach(record -> {
            try {
                OrderRequest request = objectMapper.readValue(
                    record.getBody(),
                    OrderRequest.class
                );
                OrderResponse response = orderProcessor.apply(request);
                log.info("Order processed: orderId={}, status={}",
                    response.orderId(), response.status());
            } catch (Exception e) {
                log.error("Failed to process order: messageId={}",
                    record.getMessageId(), e);
                // DLQ(Dead Letter Queue)로 메시지가 이동하도록 예외를 다시 던짐
                throw new RuntimeException("Order processing failed", e);
            }
        });
    }
}
```

### 함수 조합 (Function Composition)

Spring Cloud Function의 강력한 기능 중 하나는 함수 조합이다.

```java
@Bean
public Function<OrderRequest, String> validateAndProcess(
        Function<OrderRequest, OrderRequest> validateOrder,
        Function<OrderRequest, OrderResponse> processOrder,
        Function<OrderResponse, String> formatResponse) {
    // 파이프라인: 검증 → 처리 → 포맷
    return validateOrder
        .andThen(processOrder)
        .andThen(formatResponse);
}
```

`application.yml`에서도 선언적으로 조합할 수 있다.

```yaml
spring:
  cloud:
    function:
      definition: validateOrder|processOrder|formatResponse
```

---

## 주의사항 및 트레이드오프

### 1. 콜드 스타트 (Cold Start)

서버리스 환경에서 가장 큰 난관이다. Spring Boot의 무거운 ApplicationContext 초기화는 콜드 스타트를 **수 초 단위**로 만들 수 있다.

**완화 전략:**

| 전략 | 효과 | 복잡도 |
|------|------|--------|
| `spring.main.lazy-initialization=true` | 중간 | 낮음 |
| GraalVM Native Image 빌드 | 높음 | 높음 |
| Provisioned Concurrency (AWS) | 높음 | 중간 (비용 증가) |
| 의존성 최소화 (불필요한 Auto-configuration 제거) | 중간 | 중간 |

```java
// 불필요한 Auto-configuration 제거
@SpringBootApplication(exclude = {
    DataSourceAutoConfiguration.class,
    HibernateJpaAutoConfiguration.class,
    SecurityAutoConfiguration.class
})
public class OrderLambdaApplication { ... }
```

### 2. 상태 관리와 동시성

Lambda 인스턴스는 재사용될 수 있으므로, **인스턴스 변수에 상태를 저장하면 안 된다.** 동시 요청은 별도 인스턴스에서 처리되므로 인스턴스 간 상태 공유도 불가능하다. 상태는 반드시 외부 스토리지(RDS, DynamoDB, ElastiCache)를 활용해야 한다.

### 3. 타임아웃과 리소스 제약

Lambda의 기본 타임아웃은 3초, 최대 15분이다. DB 커넥션 풀 설정도 일반 서버와 다르게 접근해야 한다.

```yaml
# RDS Proxy 또는 커넥션 풀 최소화
spring:
  datasource:
    hikari:
      maximum-pool-size: 2      # Lambda 인스턴스당 최소한의 커넥션
      connection-timeout: 5000  # 빠른 실패
      idle-timeout: 600000
```

### 4. 언제 서버리스가 적합하지 않은가?

- **지속적인 고트래픽**: 항상 켜져 있는 서버가 비용상 유리
- **낮은 레이턴시 요구**: 콜드 스타트가 SLA를 위반할 수 있음
- **복잡한 상태 관리**: WebSocket, 장기 실행 프로세스
- **대용량 페이로드**: Lambda의 요청/응답 페이로드 제한(6MB 동기, 256KB 비동기)

### 5. 로컬 테스트 전략

Spring Cloud Function의 장점은 로컬에서 HTTP로 먼저 테스트할 수 있다는 점이다.

```bash
# spring-cloud-function-web 의존성 추가 시 로컬 HTTP 테스트 가능
curl -X POST http://localhost:8080/processOrder \
  -H "Content-Type: application/json" \
  -d '{"orderId":"ORD-001","customerId":"CUST-123","items":[],"totalAmount":50000}'
```

---

## 정리

Spring Cloud Function과 AWS Lambda의 조합은 Spring 생태계에 익숙한 백엔드 개발자에게 서버리스 진입 장벽을 크게 낮춰준다.

**핵심 정리:**

1. **Spring Cloud Function**은 비즈니스 로직을 `Function`, `Consumer`, `Supplier`로 추상화하여 플랫폼 독립성을 제공한다.
2. **콜드 스타트**는 실무에서 가장 큰 문제이며, Lazy Initialization, Native Image, Provisioned Concurrency로 완화할 수 있다.
3. **함수 조합**을 통해 복잡한 처리 파이프라인을 선언적으로 구성할 수 있다.
4. **상태 비저장(Stateless)** 설계를 철저히 지켜야 하며, 외부 스토리지를 적극 활용해야 한다.
5. **적합한 워크로드**를 선별하는 것이 중요하다. 이벤트 기반, 간헐적 트래픽, 배치 처리 등에 서버리스가 빛을 발한다.

서버리스는 만능이 아니다. 하지만 올바른 워크로드에 적용했을 때 인프라 운영 부담을 획기적으로 줄이고 개발 생산성을 높여준다. Spring Cloud Function은 그 여정을 더 친숙하고 안전하게 만들어주는 훌륭한 도구다.