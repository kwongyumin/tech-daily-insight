# Spring AOP 프록시 내부 동작과 Self-Invocation 함정

## 개요

Spring AOP를 사용하다 보면 분명히 `@Transactional`이나 커스텀 `@Aspect`를 붙였는데 동작하지 않는 황당한 상황을 마주친 경험이 있을 것이다. 로그를 찍어봐도 메서드는 호출되고 있고, 어노테이션도 제대로 붙어있는데 트랜잭션은 시작되지 않거나, 캐시는 적용되지 않는다.

이 문제의 원인은 대부분 **Self-Invocation(자기 호출)** 이다. Spring AOP가 프록시 기반으로 동작한다는 사실을 이해하지 못하면 이 함정에 반드시 빠지게 된다. 이 글에서는 Spring AOP 프록시의 내부 동작 원리부터 Self-Invocation 문제의 본질, 그리고 실무에서 이를 회피하는 방법까지 깊게 파고든다.

---

## 핵심 개념

### Spring AOP는 프록시다

Spring AOP는 AspectJ처럼 바이트코드를 직접 조작하지 않는다. 대신 **런타임에 프록시 객체를 생성**하여 타겟 객체를 감싼다. 스프링 컨테이너에서 빈을 주입받을 때, 실제로 받는 것은 타겟 객체가 아닌 프록시 객체다.

프록시 생성 방식은 두 가지다.

- **JDK Dynamic Proxy**: 타겟 클래스가 인터페이스를 구현한 경우 사용. `java.lang.reflect.Proxy`를 이용해 인터페이스 기반 프록시를 생성한다.
- **CGLIB Proxy**: 타겟 클래스가 인터페이스를 구현하지 않거나, `proxyTargetClass = true` 옵션을 설정한 경우 사용. 클래스를 상속하여 프록시를 생성한다.

Spring Boot 2.x부터는 `spring.aop.proxy-target-class=true`가 기본값이므로 대부분 CGLIB 프록시가 사용된다.

### 프록시 호출 흐름

외부에서 빈의 메서드를 호출하면 다음과 같은 흐름이 발생한다.

```
외부 호출자 → 프록시 객체 → Advice 체인 실행 → 타겟 객체의 실제 메서드
```

핵심은 **어드바이스(Advice)는 프록시 레이어에서 적용**된다는 것이다. 타겟 객체 내부에서 `this.method()`를 호출하면 프록시를 거치지 않고 타겟 객체의 메서드가 직접 호출되므로, AOP가 적용되지 않는다.

### Self-Invocation이란

Self-Invocation이란 **같은 클래스 내부에서 자기 자신의 다른 메서드를 호출하는 것**이다. `this` 키워드를 통한 호출이 대표적이다.

```
타겟 객체.methodA() 내부에서 this.methodB() 호출
→ 프록시를 우회 → methodB()에 적용된 AOP 어드바이스 무시
```

---

## 실전 예제

### 문제 상황: @Transactional Self-Invocation

가장 흔하게 마주치는 케이스는 `@Transactional`이다.

```java
@Service
public class OrderService {

    private final OrderRepository orderRepository;
    private final NotificationService notificationService;

    public OrderService(OrderRepository orderRepository,
                        NotificationService notificationService) {
        this.orderRepository = orderRepository;
        this.notificationService = notificationService;
    }

    // 외부에서 이 메서드를 호출
    public void processOrder(Long orderId) {
        // 이 내부 호출은 프록시를 거치지 않는다!
        this.saveOrder(orderId);
    }

    @Transactional
    public void saveOrder(Long orderId) {
        // 트랜잭션이 적용될 것 같지만... 적용되지 않는다
        orderRepository.save(new Order(orderId));
        notificationService.sendNotification(orderId);
    }
}
```

위 코드에서 `processOrder()`는 `@Transactional`이 없고, `saveOrder()`에는 있다. 외부에서 `processOrder()`를 호출하면 `this.saveOrder()`는 프록시를 거치지 않으므로 트랜잭션이 시작되지 않는다.

### 해결책 1: 메서드 분리 (별도 빈으로 추출)

가장 권장되는 방법이다. 트랜잭션이 필요한 로직을 별도 서비스로 추출한다.

```java
@Service
public class OrderService {

    private final OrderSaveService orderSaveService;

    public OrderService(OrderSaveService orderSaveService) {
        this.orderSaveService = orderSaveService;
    }

    public void processOrder(Long orderId) {
        // 다른 빈을 통한 호출 → 프록시를 거친다
        orderSaveService.saveOrder(orderId);
    }
}

@Service
public class OrderSaveService {

    private final OrderRepository orderRepository;

    public OrderSaveService(OrderRepository orderRepository) {
        this.orderRepository = orderRepository;
    }

    @Transactional
    public void saveOrder(Long orderId) {
        orderRepository.save(new Order(orderId));
    }
}
```

### 해결책 2: ApplicationContext를 이용한 Self 프록시 참조

의존성 분리가 어려운 경우, 자기 자신의 프록시를 ApplicationContext에서 꺼내 사용할 수 있다.

```java
@Service
public class OrderService implements ApplicationContextAware {

    private ApplicationContext applicationContext;
    private final OrderRepository orderRepository;

    public OrderService(OrderRepository orderRepository) {
        this.orderRepository = orderRepository;
    }

    @Override
    public void setApplicationContext(ApplicationContext applicationContext) {
        this.applicationContext = applicationContext;
    }

    public void processOrder(Long orderId) {
        // 프록시 객체를 통한 호출
        OrderService proxy = applicationContext.getBean(OrderService.class);
        proxy.saveOrder(orderId);
    }

    @Transactional
    public void saveOrder(Long orderId) {
        orderRepository.save(new Order(orderId));
    }
}
```

### 해결책 3: @Lazy Self 주입

Spring 4.3 이상에서는 자기 자신을 `@Lazy`로 주입받아 사용할 수 있다. 순환 참조를 `@Lazy`로 지연 초기화하여 해결한다.

```java
@Service
public class OrderService {

    private final OrderRepository orderRepository;
    private final OrderService self; // self 프록시 참조

    public OrderService(OrderRepository orderRepository,
                        @Lazy OrderService self) {
        this.orderRepository = orderRepository;
        this.self = self;
    }

    public void processOrder(Long orderId) {
        // self는 프록시 객체이므로 AOP가 적용된다
        self.saveOrder(orderId);
    }

    @Transactional
    public void saveOrder(Long orderId) {
        orderRepository.save(new Order(orderId));
    }
}
```

### 커스텀 Aspect에서의 Self-Invocation 확인

`@Transactional` 외에도 커스텀 Aspect에서 동일한 문제가 발생한다.

```java
@Aspect
@Component
public class LoggingAspect {

    @Around("@annotation(Loggable)")
    public Object around(ProceedingJoinPoint joinPoint) throws Throwable {
        System.out.println("Before: " + joinPoint.getSignature().getName());
        Object result = joinPoint.proceed();
        System.out.println("After: " + joinPoint.getSignature().getName());
        return result;
    }
}

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
public @interface Loggable {}

@Service
public class ReportService {

    public void generateReport() {
        System.out.println("Generate Report 호출");
        // Self-Invocation: @Loggable이 적용되지 않는다!
        this.exportReport();
    }

    @Loggable
    public void exportReport() {
        System.out.println("Export Report 실행");
    }
}
```

`exportReport()`를 외부에서 직접 호출하면 로그가 찍히지만, `generateReport()` → `this.exportReport()`로 호출하면 Advice가 동작하지 않는다.

### AopContext를 이용한 현재 프록시 접근

Spring은 `AopContext.currentProxy()`를 통해 현재 실행 중인 프록시 객체에 접근하는 방법을 제공한다. 단, `@EnableAspectJAutoProxy(exposeProxy = true)` 설정이 필요하다.

```java
@Configuration
@EnableAspectJAutoProxy(exposeProxy = true)
public class AopConfig {}

@Service
public class ReportService {

    public void generateReport() {
        // 현재 프록시를 통한 호출
        ((ReportService) AopContext.currentProxy()).exportReport();
    }

    @Loggable
    public void exportReport() {
        System.out.println("Export Report 실행");
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. 아키텍처 복잡성 증가

Self-Invocation 회피를 위해 클래스를 지나치게 분리하면 오히려 코드가 파편화된다. 단순한 유틸성 메서드 하나를 위해 별도 서비스 빈을 만드는 것은 과설계다. Self-Invocation이 발생했을 때 실제로 AOP 어드바이스가 필요한지를 먼저 냉정하게 판단해야 한다.

### 2. AopContext 사용의 단점

`AopContext.currentProxy()`는 ThreadLocal을 사용하며, `exposeProxy = true` 설정이 있어야 한다. 이 방식은 Spring AOP에 강하게 결합되므로 테스트가 어려워지고, 코드 가독성도 떨어진다. 일반적으로 권장하지 않는다.

### 3. @Lazy Self 주입의 주의점

`@Lazy` Self 주입은 순환 의존성을 허용하는 방식이므로, 팀 컨벤션이나 아키텍처 규칙에 위배될 수 있다. 또한 Bean 생성 순서에 따른 미묘한 버그가 발생할 여지가 있어 팀 전체가 이 패턴을 이해하고 있어야 한다.

### 4. 트랜잭션 전파 속성 혼동

Self-Invocation 문제를 인식한 개발자 중 일부는 `@Transactional(propagation = Propagation.REQUIRES_NEW)`을 붙이면 해결될 것이라 기대한다. 그러나 AOP 자체가 적용되지 않으므로 전파 속성은 의미가 없다. 전파 속성은 이미 트랜잭션이 시작된 상황에서의 동작 방식이지, AOP 우회 문제를 해결하지 않는다.

### 5. AspectJ 위빙을 고려할 시점

프록시 기반 AOP의 한계를 근본적으로 해결하려면 **컴파일 타임 또는 로드 타임 AspectJ 위빙**을 도입하는 방법이 있다. AspectJ는 바이트코드를 직접 조작하므로 Self-Invocation 문제가 발생하지 않는다. 다만 빌드 설정이 복잡해지고, Spring AOP와는 다른 러닝 커브가 존재한다. 도메인 전반에 AOP를 광범위하게 적용해야 하는 경우에만 고려를 권장한다.

```xml
<!-- Maven AspectJ 컴파일 타임 위빙 설정 예시 -->
<plugin>
    <groupId>org.codehaus.mojo</groupId>
    <artifactId>aspectj-maven-plugin</artifactId>
    <version>1.14.0</version>
    <configuration>
        <complianceLevel>17</complianceLevel>
        <source>17</source>
        <target>17</target>
    </configuration>
    <executions>
        <execution>
            <goals>
                <goal>compile</goal>
                <goal>test-compile</goal>
            </goals>
        </execution>
    </executions>
</plugin>
```

---

## 정리

Spring AOP의 Self-Invocation 함정은 **프록시 기반 동작 원리를 이해하지 못할 때 반드시 만나는 버그**다. 핵심 원칙을 정리하면 다음과 같다.

| 상황 | 권장 해결책 |
|------|------------|
| 논리적으로 분리 가능한 경우 | 별도 서비스 빈으로 추출 |
| 빠른 해결이 필요한 경우 | `@Lazy` Self 주입 |
| 컨텍스트 접근이 이미 있는 경우 | `ApplicationContext.getBean()` |
| AOP를 광범위하게 적용하는 경우 | AspectJ 위빙 도입 검토 |

가장 중요한 것은 **"AOP 어드바이스는 항상 프록시 객체를 통해야만 동작한다"** 는 원칙을 팀 전체가 공유하는 것이다. 코드 리뷰에서 같은 클래스 내 메서드 간 호출에 AOP 어노테이션이 혼재하는 패턴을 발견하면, Self-Invocation 문제를 먼저 의심하는 습관을 들이자.

프레임워크의 동작 원리를 이해하는 개발자와 그렇지 않은 개발자의 차이는, 바로 이런 종류의 보이지 않는 함정을 빠르게 식별하고 근본적으로 해결하는 능력에서 드러난다.