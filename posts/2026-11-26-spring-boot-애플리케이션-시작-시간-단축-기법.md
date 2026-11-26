# Spring Boot 애플리케이션 시작 시간 단축 기법

## 개요

마이크로서비스 아키텍처와 컨테이너 기반 배포가 보편화되면서 애플리케이션의 시작 시간(startup time)은 단순한 편의의 문제가 아니라 운영 효율성과 직결되는 핵심 지표가 되었다. 오토스케일링 환경에서 새 인스턴스가 트래픽을 받기까지 걸리는 시간, CI/CD 파이프라인에서 통합 테스트가 완료되는 속도, 서버리스 환경에서의 콜드 스타트 지연 — 이 모든 상황에서 시작 시간은 직접적인 비용과 사용자 경험에 영향을 미친다.

Spring Boot는 편리한 자동 설정(auto-configuration)과 풍부한 생태계를 제공하지만, 그 이면에는 클래스패스 스캔, 빈 초기화, 프록시 생성 등 상당한 오버헤드가 존재한다. 이 글에서는 Spring Boot 애플리케이션의 시작 시간을 실질적으로 단축할 수 있는 기법들을 원리부터 실전 적용까지 체계적으로 다룬다.

---

## 핵심 개념: Spring Boot 시작 과정 이해

최적화를 시작하기 전에 무엇이 시간을 소요하는지 파악해야 한다. Spring Boot 애플리케이션 시작은 크게 다음 단계로 구성된다.

1. **JVM 초기화**: 클래스 로딩, JIT 컴파일러 준비
2. **ApplicationContext 생성**: BeanDefinition 등록, 클래스패스 스캔
3. **빈 초기화**: 의존성 주입, `@PostConstruct`, `InitializingBean` 실행
4. **내장 서버 기동**: Tomcat/Netty 포트 바인딩
5. **ApplicationRunner / CommandLineRunner 실행**

각 단계의 소요 시간을 측정하지 않고 최적화를 시도하는 것은 무모하다. 먼저 측정부터 시작하자.

---

## 실전 예제

### 1. 시작 시간 측정 및 프로파일링

Spring Boot 2.5부터 제공되는 `ApplicationStartup` API를 활용하면 각 단계의 소요 시간을 세밀하게 추적할 수 있다.

```java
@SpringBootApplication
public class MyApplication {
    public static void main(String[] args) {
        SpringApplication app = new SpringApplication(MyApplication.class);
        app.setApplicationStartup(new BufferingApplicationStartup(2048));
        app.run(args);
    }
}
```

`/actuator/startup` 엔드포인트를 활성화하면 JSON 형태로 각 단계별 소요 시간을 확인할 수다.

```yaml
# application.yml
management:
  endpoints:
    web:
      exposure:
        include: startup, info, health
```

더 간단하게는 `-Ddebug` 플래그나 `--debug` 옵션으로 자동 설정 리포트를 출력해 불필요하게 활성화된 설정을 파악할 수 있다.

---

### 2. 불필요한 Auto-Configuration 제외

Spring Boot의 자동 설정은 편리하지만, 실제로 사용하지 않는 설정까지 로드되는 경우가 많다. `@SpringBootApplication`의 `exclude` 속성이나 `spring.autoconfigure.exclude` 프로퍼티로 제거할 수 있다.

```java
@SpringBootApplication(exclude = {
    DataSourceAutoConfiguration.class,
    HibernateJpaAutoConfiguration.class,
    SecurityAutoConfiguration.class,
    JmxAutoConfiguration.class,
    TaskExecutionAutoConfiguration.class
})
public class MyApplication {
    public static void main(String[] args) {
        SpringApplication.run(MyApplication.class, args);
    }
}
```

또는 프로퍼티 파일로 관리하면 환경별로 유연하게 적용할 수 있다.

```yaml
spring:
  autoconfigure:
    exclude:
      - org.springframework.boot.autoconfigure.jmx.JmxAutoConfiguration
      - org.springframework.boot.autoconfigure.admin.SpringApplicationAdminJmxAutoConfiguration
```

---

### 3. 컴포넌트 스캔 범위 최소화

`@SpringBootApplication`은 해당 클래스가 위치한 패키지를 기준으로 하위 전체를 스캔한다. 패키지 구조가 복잡하고 클래스 수가 많을수록 스캔 비용이 증가한다.

```java
// 나쁜 예: 루트 패키지에 메인 클래스 위치
// com.example.MyApplication → com.example.** 전체 스캔

// 좋은 예: 스캔 범위를 명시적으로 제한
@SpringBootApplication(scanBasePackages = {
    "com.example.core",
    "com.example.api"
})
public class MyApplication {
    public static void main(String[] args) {
        SpringApplication.run(MyApplication.class, args);
    }
}
```

더 나아가 `@ComponentScan` 대신 명시적 `@Import`를 사용하면 스캔 자체를 없앨 수 있다.

```java
@SpringBootApplication(scanBasePackages = {})
@Import({
    WebLayerConfig.class,
    ServiceLayerConfig.class,
    RepositoryLayerConfig.class
})
public class MyApplication {
    public static void main(String[] args) {
        SpringApplication.run(MyApplication.class, args);
    }
}
```

---

### 4. Lazy Initialization 활성화

Spring Boot 2.2부터 전역 지연 초기화(lazy initialization)를 지원한다. 모든 빈을 애플리케이션 시작 시가 아닌 최초 사용 시점에 초기화한다.

```yaml
spring:
  main:
    lazy-initialization: true
```

특정 빈만 즉시 초기화하려면 `@Lazy(false)`를 사용한다.

```java
@Service
@Lazy(false) // 애플리케이션 시작 시 반드시 초기화해야 하는 빈
public class CriticalStartupService implements InitializingBean {

    @Override
    public void afterPropertiesSet() {
        // DB 커넥션 풀 워밍업, 캐시 프리로딩 등
    }
}
```

---

### 5. Spring AOT와 GraalVM Native Image

Spring Boot 3.x와 GraalVM을 함께 사용하면 가장 극적인 시작 시간 단축을 달성할 수 있다. AOT(Ahead-of-Time) 컴파일로 런타임 리플렉션과 클래스패스 스캔을 빌드 타임으로 이동한다.

```xml
<!-- pom.xml -->
<plugin>
    <groupId>org.graalvm.buildtools</groupId>
    <artifactId>native-maven-plugin</artifactId>
    <configuration>
        <imageName>my-application</imageName>
        <metadataRepository>
            <enabled>true</enabled>
        </metadataRepository>
        <buildArgs>
            <buildArg>--initialize-at-build-time=org.slf4j</buildArg>
        </buildArgs>
    </configuration>
    <executions>
        <execution>
            <id>build-native</id>
            <goals>
                <goal>compile-no-fork</goal>
            </goals>
            <phase>package</phase>
        </execution>
    </executions>
</plugin>
```

AOT 최적화만 활용하고 싶다면 (Native Image 없이) JVM 모드에서도 적용 가능하다.

```bash
# AOT 처리 후 JVM으로 실행
mvn spring-boot:process-aot
java -Dspring.aot.enabled=true -jar my-application.jar
```

---

### 6. JVM 튜닝 옵션 적용

```bash
# 컨테이너 환경에 최적화된 JVM 옵션
java \
  -XX:TieredStopAtLevel=1 \          # JIT 컴파일 단계 제한 (시작 빠름, 처리량 감소)
  -Xverify:none \                     # 바이트코드 검증 비활성화 (보안 주의)
  -XX:+UseSerialGC \                  # 단일 코어 환경에서 GC 오버헤드 감소
  -XX:MaxRAMPercentage=75.0 \
  -jar my-application.jar
```

단, `-XX:TieredStopAtLevel=1`은 시작 시간을 단축하지만 장기 실행 성능을 저하시키므로 용도에 맞게 선택해야 한다.

Spring Boot 3.2부터는 CDS(Class Data Sharing)를 공식 지원한다.

```bash
# CDS 아카이브 생성
java -Djarmode=tools -jar my-application.jar extract
java -XX:ArchiveClassesAtExit=application.jsa \
     -Dspring.context.exit=onRefresh \
     -jar my-application.jar

# CDS 적용하여 실행
java -XX:SharedArchiveFile=application.jsa -jar my-application.jar
```

---

### 7. 빈 초기화 비동기 처리

무거운 초기화 작업을 비동기로 처리해 애플리케이션 기동을 블로킹하지 않도록 한다.

```java
@Configuration
public class AsyncInitConfig {

    @Bean
    public ApplicationRunner asyncInitRunner(HeavyService heavyService) {
        return args -> CompletableFuture.runAsync(heavyService::initialize);
    }
}

@Service
public class HeavyService {

    private volatile boolean initialized = false;

    public void initialize() {
        // 외부 API 연결, 대용량 데이터 캐싱 등 무거운 작업
        // 초기화 완료 후 플래그 설정
        initialized = true;
    }

    public void process(Request request) {
        if (!initialized) {
            throw new ServiceUnavailableException("Service is warming up");
        }
        // 실제 처리 로직
    }
}
```

---

## 주의사항 및 트레이드오프

### Lazy Initialization의 함정

전역 지연 초기화를 활성화하면 시작 시간은 줄어들지만, **첫 번째 요청에 초기화 비용이 전가**된다. 초기 사용자가 느린 응답을 받을 수 있고, 더 심각한 문제는 **시작 시 감지되어야 할 빈 설정 오류가 첫 요청 시점에 발생**한다는 것이다. 프로덕션에서 예상치 못한 `BeanCreationException`이 발생할 수 있다.

| 기법 | 시작 시간 단축 | 처리량 영향 | 주의사항 |
|------|--------------|-----------|---------|
| Auto-Config 제외 | 중간 | 없음 | 필요한 설정 누락 위험 |
| Lazy Init | 높음 | 첫 요청 지연 | 오류 감지 지연 |
| AOT/Native | 매우 높음 | 피크 처리량 감소 가능 | 리플렉션 제약 |
| TieredStopAtLevel=1 | 중간 | 장기 처리량 감소 | 단기 실행에만 적합 |
| CDS | 중간 | 없음 | 빌드 파이프라인 복잡도 증가 |

### Native Image의 현실적 제약

GraalVM Native Image는 리플렉션, 동적 프록시, 클래스패스 스캔을 제한한다. 많은 서드파티 라이브러리가 아직 완전히 지원되지 않으며, 특히 **동적으로 클래스를 로드하는 라이브러리**는 별도의 힌트 파일 작성이 필요하다. 또한 빌드 시간이 크게 증가하고 메모리 사용량이 많아 CI 환경 요구사항이 높아진다.

### 측정 없는 최적화의 위험

각 기법의 효과는 애플리케이션 특성에 따라 크게 다르다. 의존성이 많고 빈 수가 적은 경우와 빈 수가 많고 의존성이 단순한 경우는 병목 지점 자체가 다르다. 반드시 `ApplicationStartup` API나 `-Dspring.jmx.enabled=false`, JVM 플래이트(-verbose:class) 등으로 실측 후 적용해야 한다.

---

## 정리

Spring Boot 시작 시간 최적화는 단일 마법 솔루션이 아니라 **측정 → 병목 파악 → 선택적 적용**의 반복 과정이다.

빠르게 적용 가능하면서 리스크가 낮은 순으로 접근하면 다음 순서를 권장한다.

1. **측정 먼저**: `ApplicationStartup` API로 병목 단계 파악
2. **불필요한 Auto-Configuration 제거**: 사이드 이펙트 없이 즉시 효과
3. **컴포넌트 스캔 범위 최소화**: 대규모 멀티모듈 프로젝트에서 효과적
4. **CDS 적용**: JVM 환경에서 비교적 안전하게 적용 가능
5. **Lazy Initialization**: 충분한 테스트 후 프로덕션 적용
6. **AOT/Native Image**: 서버리스, 짧은 수명 컨테이너 등 명확한 이유가 있을 때

컨테이너 오토스케일링 환경이라면 시작 시간과 함께 **워밍업 완료까지의 처리량 곡선**도 함께 고려해야 한다. 빠르게 뜨더라도 JIT 컴파일 전까지 처리량이 낮다면 헬스체크와 트래픽 분산 전략을 함께 설계해야 비로소 완전한 최적화라 할 수 있다.
