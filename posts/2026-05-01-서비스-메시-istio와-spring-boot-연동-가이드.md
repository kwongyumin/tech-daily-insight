# 서비스 메시 Istio와 Spring Boot 연동 가이드

## 개요

마이크로서비스 아키텍처가 보편화되면서 서비스 간 통신 관리는 점점 복잡해지고 있다. 수십, 수백 개의 서비스가 서로 통신하는 환경에서 트래픽 제어, 보안, 관측성(Observability)을 애플리케이션 코드 레벨에서 모두 처리하는 것은 한계가 있다.

**Istio**는 이러한 문제를 해결하기 위해 등장한 서비스 메시(Service Mesh) 솔루션이다. Kubernetes 위에서 동작하며, 애플리케이션 코드를 수정하지 않고도 서비스 간 통신에 대한 고급 기능을 제공한다. Spring Boot와 Istio를 함께 사용하면, Spring Cloud에서 직접 구현하던 Circuit Breaker, Retry, Load Balancing 등의 기능을 인프라 레이어로 위임할 수 있다.

이 글에서는 Istio의 핵심 개념을 정리하고, Spring Boot 애플리케이션을 Istio 환경에 연동하는 실전 가이드를 제공한다.

---

## 핵심 개념

### 서비스 메시란?

서비스 메시는 마이크로서비스 간 통신을 처리하는 전용 인프라 레이어다. 각 서비스 인스턴스 옆에 **사이드카 프록시(Sidecar Proxy)** 를 배치하여 모든 네트워크 트래픽을 가로채고 관리한다.

### Istio 아키텍처

Istio는 크게 **데이터 플레인(Data Plane)** 과 **컨트롤 플레인(Control Plane)** 으로 나뉜다.

- **데이터 플레인**: Envoy 프록시를 각 Pod에 사이드카로 주입. 실제 트래픽을 처리
- **컨트롤 플레인(Istiod)**: Pilot(트래픽 관리), Citadel(인증서/보안), Galley(설정 관리)를 통합한 단일 컴포넌트

### 주요 CRD(Custom Resource Definition)

| 리소스 | 역할 |
|---|---|
| `VirtualService` | 라우팅 규칙 정의 (헤더 기반, 가중치 기반 등) |
| `DestinationRule` | 트래픽 정책 정의 (로드밸런싱, Circuit Breaker) |
| `Gateway` | 외부 트래픽 진입점 관리 |
| `PeerAuthentication` | mTLS 정책 설정 |
| `AuthorizationPolicy` | 서비스 간 접근 제어 |

---

## 실전 예제

### 1. 환경 준비

로컬 Kubernetes 클러스터(minikube 또는 kind)에 Istio를 설치한다.

```bash
# istioctl 설치
curl -L https://istio.io/downloadIstio | sh -
cd istio-1.x.x
export PATH=$PWD/bin:$PATH

# demo 프로파일로 설치 (개발/테스트 환경)
istioctl install --set profile=demo -y

# 사이드카 자동 주입을 위한 네임스페이스 레이블 설정
kubectl label namespace default istio-injection=enabled
```

### 2. Spring Boot 애플리케이션 준비

두 개의 Spring Boot 서비스를 준비한다. `order-service`가 `product-service`를 호출하는 시나리오다.

**product-service의 `ProductController.java`**

```java
@RestController
@RequestMapping("/api/products")
public class ProductController {

    @GetMapping("/{id}")
    public ResponseEntity<ProductResponse> getProduct(@PathVariable Long id) {
        // 버전 정보를 응답에 포함 (카나리 배포 테스트용)
        String version = System.getenv().getOrDefault("APP_VERSION", "v1");
        ProductResponse response = ProductResponse.builder()
                .id(id)
                .name("Sample Product")
                .version(version)
                .build();
        return ResponseEntity.ok(response);
    }
}
```

**order-service의 `OrderService.java`**

```java
@Service
@RequiredArgsConstructor
public class OrderService {

    private final RestTemplate restTemplate;

    // Istio가 트래픽 제어를 담당하므로 별도의 Ribbon/Feign 로드밸런싱 불필요
    @Value("${product.service.url:http://product-service/api/products}")
    private String productServiceUrl;

    public OrderResponse createOrder(OrderRequest request) {
        // Kubernetes DNS를 통해 서비스 호출 (Istio가 사이드카에서 가로챔)
        ProductResponse product = restTemplate.getForObject(
                productServiceUrl + "/" + request.getProductId(),
                ProductResponse.class
        );

        return OrderResponse.builder()
                .orderId(UUID.randomUUID().toString())
                .product(product)
                .quantity(request.getQuantity())
                .build();
    }
}
```

> **중요**: Istio 환경에서는 Spring Cloud LoadBalancer나 Ribbon을 비활성화하고, Kubernetes 서비스 DNS를 직접 사용하는 것이 권장된다.

**`application.yml`에서 Spring Cloud LoadBalancer 비활성화**

```yaml
spring:
  cloud:
    loadbalancer:
      enabled: false

product:
  service:
    url: http://product-service/api/products
```

### 3. Kubernetes 배포 매니페스트

```yaml
# product-service Deployment (v1, v2 두 버전 배포)
apiVersion: apps/v1
kind: Deployment
metadata:
  name: product-service-v1
  labels:
    app: product-service
    version: v1
spec:
  replicas: 2
  selector:
    matchLabels:
      app: product-service
      version: v1
  template:
    metadata:
      labels:
        app: product-service
        version: v1
    spec:
      containers:
        - name: product-service
          image: myregistry/product-service:1.0.0
          env:
            - name: APP_VERSION
              value: "v1"
          ports:
            - containerPort: 8080
---
apiVersion: v1
kind: Service
metadata:
  name: product-service
spec:
  selector:
    app: product-service  # version 레이블 없이 모든 버전 포함
  ports:
    - port: 80
      targetPort: 8080
```

### 4. 트래픽 가중치 기반 라우팅 (카나리 배포)

`DestinationRule`로 서브셋을 정의하고, `VirtualService`로 트래픽을 분배한다.

```yaml
# DestinationRule: 서비스 버전별 서브셋 정의
apiVersion: networking.istio.io/v1beta1
kind: DestinationRule
metadata:
  name: product-service-dr
spec:
  host: product-service
  trafficPolicy:
    connectionPool:
      tcp:
        maxConnections: 100
      http:
        h2UpgradePolicy: UPGRADE
    outlierDetection:
      consecutive5xxErrors: 5
      interval: 10s
      baseEjectionTime: 30s
  subsets:
    - name: v1
      labels:
        version: v1
    - name: v2
      labels:
        version: v2
---
# VirtualService: 90% -> v1, 10% -> v2 (카나리)
apiVersion: networking.istio.io/v1beta1
kind: VirtualService
metadata:
  name: product-service-vs
spec:
  hosts:
    - product-service
  http:
    - match:
        - headers:
            x-canary-user:
              exact: "true"
      route:
        - destination:
            host: product-service
            subset: v2
    - route:
        - destination:
            host: product-service
            subset: v1
          weight: 90
        - destination:
            host: product-service
            subset: v2
          weight: 10
```

### 5. Circuit Breaker 설정

Istio의 `DestinationRule`에 Outlier Detection을 적용하면 Circuit Breaker 동작을 구현할 수 있다.

```yaml
apiVersion: networking.istio.io/v1beta1
kind: DestinationRule
metadata:
  name: product-service-circuit-breaker
spec:
  host: product-service
  trafficPolicy:
    connectionPool:
      tcp:
        maxConnections: 50
      http:
        http1MaxPendingRequests: 100
        maxRequestsPerConnection: 10
    outlierDetection:
      consecutive5xxErrors: 3       # 연속 5xx 에러 3회 시 이젝션
      consecutiveGatewayErrors: 3
      interval: 30s                  # 분석 주기
      baseEjectionTime: 60s         # 최소 이젝션 시간
      maxEjectionPercent: 50        # 최대 이젝션 비율
      minHealthPercent: 30
```

### 6. mTLS 설정

서비스 간 통신에 상호 TLS를 적용하여 보안을 강화한다.

```yaml
# 네임스페이스 전체에 STRICT mTLS 적용
apiVersion: security.istio.io/v1beta1
kind: PeerAuthentication
metadata:
  name: default
  namespace: production
spec:
  mtls:
    mode: STRICT
---
# 특정 서비스에만 접근 허용하는 AuthorizationPolicy
apiVersion: security.istio.io/v1beta1
kind: AuthorizationPolicy
metadata:
  name: product-service-authz
  namespace: production
spec:
  selector:
    matchLabels:
      app: product-service
  action: ALLOW
  rules:
    - from:
        - source:
            principals:
              - "cluster.local/ns/production/sa/order-service"
      to:
        - operation:
            methods: ["GET"]
            paths: ["/api/products/*"]
```

### 7. 관측성: Kiali, Jaeger 연동

Istio의 관측성 스택을 활성화하면 Spring Boot 애플리케이션의 트레이싱을 자동으로 수집할 수 있다. 단, **Trace Context 전파**를 위해 Spring Boot에서 헤더를 포워딩해야 한다.

```java
@Component
public class TracingHeaderFilter implements Filter {

    // Istio/Envoy가 사용하는 B3 및 W3C TraceContext 헤더
    private static final List<String> TRACE_HEADERS = List.of(
            "x-request-id",
            "x-b3-traceid",
            "x-b3-spanid",
            "x-b3-parentspanid",
            "x-b3-sampled",
            "x-b3-flags",
            "traceparent",
            "tracestate"
    );

    @Override
    public void doFilter(ServletRequest request, ServletResponse response, FilterChain chain)
            throws IOException, ServletException {
        HttpServletRequest httpRequest = (HttpServletRequest) request;
        // ThreadLocal 또는 MDC에 헤더 저장 후 downstream 호출 시 재사용
        Map<String, String> traceContext = new HashMap<>();
        TRACE_HEADERS.forEach(header -> {
            String value = httpRequest.getHeader(header);
            if (value != null) {
                traceContext.put(header, value);
            }
        });
        TraceContextHolder.set(traceContext);
        chain.doFilter(request, response);
        TraceContextHolder.clear();
    }
}
```

`RestTemplate`에서 트레이싱 헤더를 전파하는 인터셉터:

```java
@Bean
public RestTemplate restTemplate() {
    RestTemplate restTemplate = new RestTemplate();
    restTemplate.getInterceptors().add((request, body, execution) -> {
        Map<String, String> traceContext = TraceContextHolder.get();
        if (traceContext != null) {
            traceContext.forEach((k, v) -> request.getHeaders().add(k, v));
        }
        return execution.execute(request, body);
    });
    return restTemplate;
}
```

---

## 주의사항 및 트레이드오프

### 1. Spring Cloud와의 기능 중복 문제

Istio를 도입하면 Spring Cloud Netflix(Ribbon, Hystrix)나 Spring Cloud LoadBalancer와 기능이 중복된다. 이중 적용 시 예상치 못한 동작이 발생할 수 있다.

**권장 전략**: Istio 환경에서는 네트워크 관련 기능(LB, Circuit Breaker, Retry)을 Istio에 위임하고, Spring Cloud는 서비스 디스커버리 이외의 기능(Config, Vault 등)만 사용한다.

### 2. 사이드카 오버헤드

각 Pod에 Envoy 사이드카가 추가되므로 CPU/Memory 오버헤드가 발생한다. 일반적으로 Pod당 50~100m CPU, 50~128MB 메모리 추가 소비를 예상해야 한다. 수백 개의 Pod를 운영하는 경우 전체 클러스터 리소스 계획에 반드시 포함시켜야 한다.

### 3. 타임아웃 설정의 이중화

Spring Boot의 `RestTemplate` 타임아웃과 Istio `VirtualService`의 타임아웃이 독립적으로 동작한다. 두 설정이 불일치하면 Istio 타임아웃이 먼저 발동되거나, Spring 타임아웃이 Istio의 재시도 로직을 방해할 수 있다.

```yaml
# VirtualService에 타임아웃과 재시도 설정
http:
  - route:
      - destination:
          host: product-service
    timeout: 3s
    retries:
      attempts: 3
      perTryTimeout: 1s
      retryOn: gateway-error,connect-failure,retriable-4xx
```

Spring Boot의 `RestTemplate` 타임아웃은 Istio 설정보다 여유 있게 설정(`connectTimeout: 5s`, `readTimeout: 5s`)하여 Istio가 먼저 핸들링하도록 한다.

### 4. Istio 버전 호환성

Istio CRD는 버전마다 API 변경이 잦다. `networking.istio.io/v1alpha3`에서 `v1beta1`, `v1`으로 승격 중이므로, 클러스터 업그레이드 시 매니페스트 호환성을 반드시 검토해야 한다.

### 5. 디버깅 복잡도 증가

사이드카 프록시가 트래픽을 가로채기 때문에 문제 발생 시 원인 추적이 더 복잡해진다. `istioctl proxy-config` 명령어와 Envoy 액세스 로그를 적극 활용해야 한다.

```bash
# Envoy 설정 확인
istioctl proxy-config cluster <pod-name> -n production

# 라우팅 규칙 확인
istioctl analyze -n production

# 특정 Pod의 Envoy 로그 레벨 변경
istioctl proxy-config log <pod-name> --level debug
```

---

## 정리

Istio와 Spring Boot의 조합은 강력하지만, 단순히 도입한다고 바로 이점을 얻을 수 있는 것은 아니다. 핵심 포인트를 정리하면 다음과 같다.

| 항목 | 권장 사항 |
|---|---|
| 로드밸런싱 | Spring Cloud LB 비활성화, Istio 위임 |
| Circuit Breaker | Resilience4j 제거, DestinationRule Outlier Detection 사용 |
| 트레이싱 헤더 | B3/W3C 헤더 수동 전파 필수 |
| 타임아웃 | VirtualService < RestTemplate 순서로 설정 |
| mTLS | PERMISSIVE로 시작 후 점진적으로 STRICT 적용 |

Istio는 인프라 레이어에서 네트워크 복잡성을 추상화해주는 강력한 도구다. 하지만 Kubernetes와 네트워킹에 대한 깊은 이해 없이 도입하면 오히려 운영 부담이 커진다. 작은 서비스부터 점진적으로 적용하고, 팀 전체가 Envoy와 Istio CRD에 익숙해지는 과정을 거치는 것을 강력히 권장한다.