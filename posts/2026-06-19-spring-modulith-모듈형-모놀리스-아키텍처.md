# Spring Modulith 모듈형 모놀리스 아키텍처

## 개요

마이크로서비스 아키텍처(MSA)가 업계 표준처럼 여겨지던 시절이 있었다. 그러나 실무에서 MSA를 도입한 팀들이 공통적으로 마주치는 문제들이 있다. 분산 트랜잭션, 서비스 간 통신 복잡도, 운영 오버헤드, 그리고 지나치게 잘게 쪼개진 서비스로 인한 네트워크 지연 등이다.

이에 대한 반성으로 "모듈형 모놀리스(Modular Monolith)"라는 개념이 다시 주목받고 있다. 하나의 배포 단위를 유지하면서도 내부를 명확한 모듈로 분리하여 관심사 분리와 높은 응집도를 달성하는 방식이다. **Spring Modulith**는 Spring Boot 애플리케이션에서 이 패턴을 구조적으로 지원하는 공식 프레임워크다.

이 글에서는 Spring Modulith의 핵심 개념부터 실전 적용 방법, 그리고 실무에서 고려해야 할 트레이드오프까지 깊이 있게 다룬다.

---

## 핵심 개념

### 애플리케이션 모듈 (Application Module)

Spring Modulith에서 모듈은 **최상위 패키지 단위**로 정의된다. 각 모듈은 명시적으로 공개한 API만 외부에 노출하고, 내부 구현은 철저히 캡슐화된다.

```
com.example.shop
├── order/           ← Order 모듈
│   ├── OrderService.java      (공개 API)
│   ├── OrderController.java   (공개 API)
│   └── internal/
│       ├── OrderRepository.java  (내부 구현)
│       └── OrderValidator.java   (내부 구현)
├── inventory/       ← Inventory 모듈
│   ├── InventoryService.java  (공개 API)
│   └── internal/
│       └── StockRepository.java  (내부 구현)
└── payment/         ← Payment 모듈
    └── PaymentService.java    (공개 API)
```

`internal` 패키지 하위에 위치한 클래스들은 다른 모듈에서 직접 참조할 수 없다. 이를 **모듈 경계(Module Boundary)**라 부른다.

### 의존성 검증 (Dependency Verification)

Spring Modulith는 테스트 시점에 모듈 간 의존성을 자동으로 검증한다. 허용되지 않은 의존성이 발견되면 빌드가 실패한다. 이는 아키텍처가 코드와 함께 썩어가는 "아키텍처 부패(Architecture Erosion)"를 방지하는 핵심 메커니즘이다.

### 이벤트 기반 통신 (Event-Driven Communication)

모듈 간 결합도를 낮추기 위해 Spring Modulith는 **Application Event**를 활용한 비동기 통신을 권장한다. `@ApplicationModuleListener` 어노테이션으로 이벤트를 구독하며, 이벤트 퍼블리싱은 트랜잭션이 커밋된 이후에 처리하도록 보장한다.

---

## 실전 예제

### 의존성 설정

```xml
<dependency>
    <groupId>org.springframework.experimental</groupId>
    <artifactId>spring-modulith-starter-core</artifactId>
    <version>1.2.0</version>
</dependency>
<dependency>
    <groupId>org.springframework.experimental</groupId>
    <artifactId>spring-modulith-starter-test</artifactId>
    <version>1.2.0</version>
    <scope>test</scope>
</dependency>
<!-- 이벤트 퍼시스턴스가 필요한 경우 -->
<dependency>
    <groupId>org.springframework.experimental</groupId>
    <artifactId>spring-modulith-events-api</artifactId>
    <version>1.2.0</version>
</dependency>
```

### 모듈 구조 구현

**Order 모듈 공개 API**

```java
// com/example/shop/order/OrderService.java
package com.example.shop.order;

import com.example.shop.order.internal.OrderRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
public class OrderService {

    private final OrderRepository orderRepository;
    private final ApplicationEventPublisher eventPublisher;

    @Transactional
    public Order placeOrder(OrderRequest request) {
        Order order = Order.create(request);
        Order savedOrder = orderRepository.save(order);

        // 트랜잭션 커밋 후 이벤트 발행
        eventPublisher.publishEvent(new OrderPlacedEvent(savedOrder.getId(), savedOrder.getItems()));

        return savedOrder;
    }
}
```

**Order 모듈 이벤트 (공개 계약)**

```java
// com/example/shop/order/OrderPlacedEvent.java
package com.example.shop.order;

import java.util.List;
import java.util.UUID;

public record OrderPlacedEvent(UUID orderId, List<OrderItem> items) {}
```

**Inventory 모듈 - 이벤트 구독**

```java
// com/example/shop/inventory/InventoryService.java
package com.example.shop.inventory;

import com.example.shop.order.OrderPlacedEvent;
import lombok.RequiredArgsConstructor;
import org.springframework.modulith.events.ApplicationModuleListener;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class InventoryService {

    private final StockManager stockManager;

    @ApplicationModuleListener
    public void onOrderPlaced(OrderPlacedEvent event) {
        event.items().forEach(item ->
            stockManager.deductStock(item.productId(), item.quantity())
        );
    }

    public StockInfo getStockInfo(String productId) {
        return stockManager.getStock(productId);
    }
}
```

> `@ApplicationModuleListener`는 내부적으로 `@TransactionalEventListener(phase = AFTER_COMMIT)`와 `@Async`를 조합한 것으로, 주문 트랜잭션이 성공적으로 커밋된 이후에만 재고 차감이 실행됨을 보장한다.

### 모듈 의존성 테스트

Spring Modulith의 가장 강력한 기능 중 하나는 **구조적 검증 테스트**다.

```java
// src/test/java/com/example/shop/ModularityTests.java
package com.example.shop;

import org.junit.jupiter.api.Test;
import org.springframework.modulith.core.ApplicationModules;
import org.springframework.modulith.docs.Documenter;

class ModularityTests {

    ApplicationModules modules = ApplicationModules.of(ShopApplication.class);

    @Test
    void verifiesModularStructure() {
        // 허용되지 않은 의존성, 순환 참조 등을 자동 검증
        modules.verify();
    }

    @Test
    void writeDocumentationSnippets() {
        // 모듈 구조 문서 자동 생성 (PlantUML, AsciiDoc)
        new Documenter(modules)
            .writeModulesAsPlantUml()
            .writeIndividualModulesAsPlantUml();
    }
}
```

이 테스트 하나로 다음 사항들이 검증된다:
- 모듈 간 허용되지 않은 직접 참조 (internal 패키지 접근)
- 순환 의존성
- 명시적으로 허용하지 않은 모듈 간 참조

### 명시적 모듈 허용 설정

특별한 경우 허용된 의존성을 명시적으로 선언할 수 있다.

```java
// com/example/shop/payment/package-info.java
@org.springframework.modulith.ApplicationModule(
    allowedDependencies = {"order", "notification"}
)
package com.example.shop.payment;
```

### 이벤트 퍼시스턴스 (Event Externalization)

메시지 유실을 방지하기 위해 이벤트를 DB에 저장했다가 발행하는 **Transactional Outbox 패턴**을 내장 지원한다.

```java
// application.yml
spring:
  modulith:
    events:
      jdbc:
        schema-initialization:
          enabled: true
```

```java
// 이벤트를 외부 메시지 브로커로 전달하려는 경우
@Externalized("orders.placed") // Kafka 토픽 혹은 RabbitMQ Exchange 이름
public record OrderPlacedEvent(UUID orderId, List<OrderItem> items) {}
```

이 설정만으로 이벤트가 `event_publication` 테이블에 저장되고, 성공적으로 처리된 경우에만 완료 처리된다. 처리 실패 시 재시도가 가능하다.

### 통합 테스트 (시나리오 테스트)

```java
@SpringBootTest
@ApplicationModuleTest(mode = STANDALONE)  // 해당 모듈만 격리하여 테스트
class OrderModuleTests {

    @Autowired
    OrderService orderService;

    @MockBean
    InventoryService inventoryService; // 다른 모듈은 Mock 처리

    @Test
    void placingOrderPublishesEvent() {
        var request = new OrderRequest(/* ... */);

        AssertablePublishedEvents events = eventPublisher -> {
            orderService.placeOrder(request);
        };

        events.assertThat()
            .contains(OrderPlacedEvent.class)
            .matching(event -> event.orderId() != null);
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. 분산 트랜잭션 문제는 여전히 존재한다

`@ApplicationModuleListener`는 AFTER_COMMIT 이후에 실행되기 때문에 **재고 차감과 주문 생성이 하나의 ACID 트랜잭션으로 묶이지 않는다**. 결과적 일관성(Eventual Consistency)을 받아들여야 한다. 주문은 성공했지만 재고 차감 이벤트가 실패하는 시나리오를 항상 고려해야 한다.

이 경우 이벤트 퍼시스턴스와 재시도 메커니즘을 반드시 함께 구성하고, 보상 트랜잭션(Compensating Transaction) 로직도 설계해야 한다.

### 2. 패키지 구조 컨벤션 강제

Spring Modulith는 패키지 구조에 강하게 의존한다. 팀원 모두가 `internal` 패키지 규칙을 이해하고 준수해야 하며, 레거시 코드베이스에 도입할 때는 상당한 리팩토링이 필요할 수 있다.

### 3. MSA 전환 용이성 vs 복잡도

Spring Modulith의 장점 중 하나는 각 모듈을 필요 시 마이크로서비스로 추출하기 쉬운 구조라는 점이다. 그러나 이것이 오히려 "언젠가는 쪼갤 것"이라는 막연한 기대를 심어줄 수 있다. 모듈 경계를 잘못 설계하면 추출 비용이 오히려 더 커진다.

**도메인 주도 설계(DDD)의 Bounded Context** 개념과 함께 적용하여 모듈 경계를 신중하게 정의할 것을 권장한다.

### 4. 성능 고려사항

이벤트 기반 비동기 통신은 쓰레드 풀 관리가 필요하다. `@ApplicationModuleListener`는 기본적으로 별도 쓰레드에서 실행되므로, 과도한 이벤트 발생 시 쓰레드 고갈이 발생할 수 있다. 적절한 `TaskExecutor` 설정이 필요하다.

```java
@Configuration
public class AsyncConfig {

    @Bean("modulithTaskExecutor")
    public TaskExecutor modulithTaskExecutor() {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(10);
        executor.setMaxPoolSize(50);
        executor.setQueueCapacity(500);
        executor.setThreadNamePrefix("modulith-");
        return executor;
    }
}
```

### 5. 관찰 가능성(Observability)

모놀리스 구조지만 이벤트 흐름을 추적하기 어려울 수 있다. Spring Modulith는 Spring Boot Actuator와 통합하여 모듈 정보를 노출하지만, 이벤트 체인 추적을 위해 **Micrometer Tracing**과 함께 사용하는 것이 실무에서 필수적이다.

---

## 정리

| 항목 | 내용 |
|------|------|
| **적합한 팀 규모** | 소~중규모 팀 (5~15명) |
| **적합한 도메인** | 명확한 경계 정의가 가능한 복잡한 비즈니스 로직 |
| **주요 장점** | 단순한 배포, 강제된 모듈 경계, 낮은 운영 복잡도 |
| **주요 단점** | 결과적 일관성 관리, 패키지 규칙 강제, 스케일링 한계 |

Spring Modulith는 MSA의 복잡도 없이 잘 구조화된 시스템을 구축하고 싶은 팀에게 현실적인 대안이다. 특히 초기 스타트업이나 중견 기업에서 빠르게 성장하는 도메인을 관리할 때 그 진가를 발휘한다.

핵심은 **"지금 당장 필요하지 않은 복잡도를 도입하지 않되, 미래의 변화를 수용할 수 있는 구조를 갖추는 것"**이다. Spring Modulith는 바로 그 균형점을 찾는 데 훌륭한 도구가 된다.

아키텍처는 코드로 검증되어야 한다. `modules.verify()` 한 줄이 수십 장의 아키텍처 문서보다 강력하다는 사실을 기억하자.