# 서비스 디스커버리 패턴 Consul vs Eureka 비교

## 개요

마이크로서비스 아키텍처가 보편화되면서 서비스 디스커버리(Service Discovery)는 선택이 아닌 필수 인프라 컴포넌트가 되었다. 수십, 수백 개의 서비스 인스턴스가 동적으로 생성되고 소멸되는 환경에서 하드코딩된 IP/포트 정보로 서비스 간 통신을 관리하는 것은 불가능에 가깝다.

서비스 디스커버리는 크게 두 가지 패턴으로 나뉜다:

- **클라이언트 사이드 디스커버리(Client-Side Discovery)**: 클라이언트가 레지스트리에서 직접 서비스 목록을 조회하고 로드밸런싱을 담당
- **서버 사이드 디스커버리(Server-Side Discovery)**: 로드밸런서나 API 게이트웨이가 레지스트리를 조회하고 라우팅을 처리

이 두 패턴의 대표 구현체로 **Netflix Eureka**와 **HashiCorp Consul**이 자주 언급된다. 두 솔루션은 목적은 같지만 설계 철학, 기능 범위, 운영 특성에서 상당한 차이를 보인다. 이 글에서는 실무 관점에서 두 솔루션을 깊이 비교하고, 어떤 상황에서 무엇을 선택해야 하는지 판단 기준을 제시한다.

---

## 핵심 개념

### Eureka: Netflix OSS의 AP 시스템

Eureka는 Netflix가 AWS 환경에서 대규모 마이크로서비스를 운영하면서 탄생한 서비스 레지스트리다. Spring Cloud Netflix 프로젝트를 통해 Spring Boot 생태계와 자연스럽게 통합된다.

**핵심 특징:**
- **AP(Availability + Partition Tolerance)** 시스템 - CAP 이론에서 일관성(Consistency)보다 가용성을 우선
- 자기 보존 모드(Self-Preservation Mode): 네트워크 파티션 발생 시 등록 정보를 무효화하지 않고 유지
- Peer-to-Peer 복제 방식으로 Eureka Server 간 동기화
- Java 기반, Spring 생태계에 최적화

**동작 원리:**
```
[Service Instance] --register--> [Eureka Server]
[Service Instance] --heartbeat(30s)--> [Eureka Server]
[Client Service] --fetch registry--> [Eureka Server]
[Client Service] --cache locally--> [Local Registry Cache]
```

클라이언트는 레지스트리를 로컬에 캐싱하고 30초마다 갱신한다. Eureka Server가 일시적으로 다운되더라도 클라이언트는 캐시된 정보로 계속 동작할 수 있다.

### Consul: HashiCorp의 CP 지향 멀티 기능 솔루션

Consul은 HashiCorp가 개발한 서비스 메시 솔루션으로, 서비스 디스커버리 외에도 헬스 체크, 분산 KV 스토어, 서비스 메시(Envoy 사이드카 통합)까지 제공한다.

**핵심 특징:**
- **CP(Consistency + Partition Tolerance)** 지향 - Raft 합의 알고리즘으로 강한 일관성 보장
- 다양한 헬스 체크 방식: HTTP, TCP, Script, TTL, gRPC
- 멀티 데이터센터 지원이 기본 내장
- Go 언어로 작성, 단일 바이너리로 배포 가능
- DNS 인터페이스 제공으로 비-Java 서비스도 통합 가능

**Consul 클러스터 구성:**
```
[Server Node 1] <--Raft Consensus--> [Server Node 2]
       ^                                    ^
       |                                    |
[Server Node 3 (Leader)]                    |
       |                                    |
[Client Agent] --> [Service Registration]   |
[Client Agent] --gossip protocol---------> [Cluster]
```

---

## 실전 예제

### Eureka 설정 (Spring Boot)

**Eureka Server 설정:**

```xml
<!-- pom.xml -->
<dependency>
    <groupId>org.springframework.cloud</groupId>
    <artifactId>spring-cloud-starter-netflix-eureka-server</artifactId>
</dependency>
```

```java
// EurekaServerApplication.java
@SpringBootApplication
@EnableEurekaServer
public class EurekaServerApplication {
    public static void main(String[] args) {
        SpringApplication.run(EurekaServerApplication.class, args);
    }
}
```

```yaml
# application.yml (Eureka Server)
server:
  port: 8761

eureka:
  instance:
    hostname: localhost
  client:
    register-with-eureka: false
    fetch-registry: false
  server:
    enable-self-preservation: true
    eviction-interval-timer-in-ms: 5000
    # 자기 보존 모드 임계값: 85% 미만이면 보존 모드 진입
    renewal-percent-threshold: 0.85
```

**Eureka Client (마이크로서비스) 설정:**

```yaml
# application.yml (Client Service)
spring:
  application:
    name: order-service

eureka:
  client:
    service-url:
      defaultZone: http://eureka-server1:8761/eureka/,http://eureka-server2:8762/eureka/
    registry-fetch-interval-seconds: 30
    instance-info-replication-interval-seconds: 30
  instance:
    prefer-ip-address: true
    lease-renewal-interval-in-seconds: 10
    lease-expiration-duration-in-seconds: 30
    metadata-map:
      version: "1.2.0"
      zone: "ap-northeast-2a"
```

**Feign Client로 서비스 호출:**

```java
@FeignClient(name = "product-service", configuration = FeignConfig.class)
public interface ProductServiceClient {

    @GetMapping("/api/products/{id}")
    ProductResponse getProduct(@PathVariable("id") Long id);

    @PostMapping("/api/products/bulk")
    List<ProductResponse> getProducts(@RequestBody List<Long> ids);
}

@Service
@RequiredArgsConstructor
public class OrderService {

    private final ProductServiceClient productServiceClient;
    private final DiscoveryClient discoveryClient;

    public OrderResponse createOrder(OrderRequest request) {
        // Eureka에 등록된 인스턴스 목록 직접 조회
        List<ServiceInstance> instances = discoveryClient.getInstances("product-service");
        log.info("Available product-service instances: {}", instances.size());

        // Feign이 Ribbon을 통해 자동 로드밸런싱
        ProductResponse product = productServiceClient.getProduct(request.getProductId());
        // ...
    }
}
```

---

### Consul 설정

**Docker Compose로 Consul 클러스터 구성:**

```yaml
# docker-compose.yml
version: '3.8'
services:
  consul-server1:
    image: hashicorp/consul:1.17
    command: >
      consul agent -server -bootstrap-expect=3
      -datacenter=dc1
      -node=consul-server1
      -bind=0.0.0.0
      -client=0.0.0.0
      -retry-join=consul-server2
      -retry-join=consul-server3
      -ui
    ports:
      - "8500:8500"
      - "8600:8600/udp"

  consul-server2:
    image: hashicorp/consul:1.17
    command: >
      consul agent -server -bootstrap-expect=3
      -datacenter=dc1
      -node=consul-server2
      -bind=0.0.0.0
      -client=0.0.0.0
      -retry-join=consul-server1

  consul-server3:
    image: hashicorp/consul:1.17
    command: >
      consul agent -server -bootstrap-expect=3
      -datacenter=dc1
      -node=consul-server3
      -bind=0.0.0.0
      -client=0.0.0.0
      -retry-join=consul-server1
```

**Spring Boot + Consul 통합:**

```xml
<dependency>
    <groupId>org.springframework.cloud</groupId>
    <artifactId>spring-cloud-starter-consul-discovery</artifactId>
</dependency>
<dependency>
    <groupId>org.springframework.cloud</groupId>
    <artifactId>spring-cloud-starter-consul-config</artifactId>
</dependency>
```

```yaml
# application.yml (Consul Client)
spring:
  application:
    name: order-service
  cloud:
    consul:
      host: consul-server1
      port: 8500
      discovery:
        prefer-ip-address: true
        health-check-interval: 10s
        health-check-timeout: 5s
        health-check-critical-timeout: 30s
        tags:
          - version=1.2.0
          - zone=ap-northeast-2a
      config:
        enabled: true
        prefix: config
        default-context: application
        format: YAML
```

**Consul REST API를 활용한 서비스 조회:**

```java
@Component
@RequiredArgsConstructor
public class ConsulServiceRegistry {

    private final ConsulClient consulClient;

    public List<ServiceInstance> getHealthyInstances(String serviceName) {
        Response<List<HealthService>> response = consulClient.getHealthServices(
            serviceName,
            true,  // 헬시한 인스턴스만
            QueryParams.DEFAULT
        );

        return response.getValue().stream()
            .map(hs -> new ConsulServiceInstance(
                hs.getService().getId(),
                hs.getService().getAddress(),
                hs.getService().getPort(),
                hs.getService().getTags()
            ))
            .collect(Collectors.toList());
    }

    // Consul KV Store 활용
    public void putConfig(String key, String value) {
        consulClient.setKVValue("config/order-service/" + key, value);
    }

    public String getConfig(String key) {
        Response<GetValue> response = consulClient.getKVValue("config/order-service/" + key);
        return response.getValue() != null
            ? response.getValue().getDecodedValue()
            : null;
    }
}
```

**Consul Watch를 이용한 실시간 변경 감지:**

```java
@Component
public class ConsulServiceWatcher implements CommandLineRunner {

    private final ConsulClient consulClient;
    private final LoadBalancerRegistry loadBalancerRegistry;

    @Override
    public void run(String... args) {
        // Long Polling으로 서비스 변경 감지
        new Thread(() -> {
            long lastIndex = 0;
            while (true) {
                try {
                    QueryParams params = QueryParams.Builder.builder()
                        .withWaitTime(30)       // 30초 블로킹 폴링
                        .withIndex(lastIndex)
                        .build();

                    Response<List<HealthService>> response =
                        consulClient.getHealthServices("product-service", true, params);

                    lastIndex = response.getConsulIndex();
                    // 변경 감지 시 로드밸런서 갱신
                    loadBalancerRegistry.updateInstances(
                        "product-service",
                        response.getValue()
                    );
                } catch (Exception e) {
                    log.error("Consul watch error", e);
                    Thread.sleep(5000);
                }
            }
        }).start();
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. CAP 이론 관점의 선택

| 상황 | 추천 |
|------|------|
| 네트워크 파티션 시 가용성이 더 중요 | **Eureka** |
| 오래된 서비스 정보로 인한 장애가 더 위험 | **Consul** |
| 금융/결제 등 데이터 정합성이 중요한 도메인 | **Consul** |
| Netflix 스타일의 탄력적 서비스 설계 | **Eureka** |

### 2. 운영 복잡도

**Eureka의 자기 보존 모드 함정:**

자기 보존 모드는 장점이자 함정이다. 개발/테스트 환경에서는 반드시 비활성화해야 한다. 그렇지 않으면 종료된 서비스 인스턴스가 레지스트리에 계속 남아 클라이언트가 죽은 서비스로 요청을 보내는 상황이 발생한다.

```yaml
# 개발 환경에서는 자기 보존 모드 비활성화
eureka:
  server:
    enable-self-preservation: false
    eviction-interval-timer-in-ms: 3000
```

**Consul의 Raft 쿼럼 문제:**

Consul은 서버 노드 과반수(쿼럼)가 살아있어야 정상 동작한다. 3노드 클러스터에서 2개가 다운되면 클러스터 전체가 읽기 전용이 되거나 중단된다. 운영 환경에서는 반드시 5노드 이상을 권장한다.

### 3. 헬스 체크 설계

Consul의 헬스 체크는 Eureka보다 훨씬 정교하지만, 잘못 설정하면 부작용이 크다.

```hcl
# consul service definition (HCL)
service {
  name = "order-service"
  port = 8080

  check {
    http     = "http://localhost:8080/actuator/health"
    interval = "10s"
    timeout  = "3s"
    # 이 값이 너무 짧으면 배포 중 순간적인 헬스체크 실패로 서비스가 내려갈 수 있음
    deregister_critical_service_after = "60s"
  }
}
```

`deregister_critical_service_after` 값은 롤링 배포 시간보다 충분히 길게 설정해야 한다.

### 4. 생태계와 언어 독립성

- **Eureka**: Java/Spring 생태계에 최적화. 비-JVM 서비스 통합 시 추가 작업 필요
- **Consul**: DNS 인터페이스, HTTP API, 다양한 SDK 제공으로 폴리글랏 환경에 적합

### 5. Eureka 2.0의 현실

Netflix는 Eureka 2.0 개발을 사실상 중단했다. Spring Cloud Netflix의 유지보수 모드 전환 가능성도 고려해야 한다. 장기적인 관점에서 신규 프로젝트라면 Consul 또는 Kubernetes 기반 서비스 디스커버리(CoreDNS + kube-proxy)를 검토하는 것이 현명하다.

---

## 정리

두 솔루션의 핵심 차이를 한 문장으로 요약하면: **Eureka는 Spring 마이크로서비스를 위한 가용성 중심의 심플한 레지스트리**이고, **Consul은 멀티 언어/멀티 데이터센터 환경을 위한 일관성 중심의 풀스택 서비스 네트워킹 플랫폼**이다.

| 비교 항목 | Eureka | Consul |
|-----------|--------|--------|
| CAP 이론 | AP | CP |
| 헬스 체크 | 하트비트 기반 | HTTP/TCP/Script/TTL 등 다양 |
| KV 스토어 | 없음 | 내장 |
| 멀티 DC | 제한적 | 기본 지원 |
| 서비스 메시 | 없음 | Connect (Envoy) |
| 언어 지원 | Java 중심 | 폴리글랏 |
| 운영 복잡도 | 낮음 | 중간~높음 |
| 커뮤니티 활성도 | 유지보수 모드 | 활발 |

**선택 가이드:**

- Spring Boot 기반 Java 모노레포, 빠른 프로토타이핑, 팀의 Spring 숙련도가 높다면 → **Eureka**
- 폴리글랏 마이크로서비스, 멀티 클라우드/데이터센터, 서비스 메시로의 확장을 고려한다면 → **Consul**
- Kubernetes 환경이라면 → **Consul 또는 네이티브 K8s 서비스 디스커버리**를 먼저 검토

기술 선택은 항상 현재의 팀 역량, 시스템 규모, 비즈니스 요구사항을 함께 고려해야 한다. 완벽한 솔루션은 없다. 트레이드오프를 명확히 이해하고 선택하는 것이 시니어 개발자의 역할이다.