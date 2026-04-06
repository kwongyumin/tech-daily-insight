# Zero Downtime 배포 블루/그린 카나리 전략

## 개요

서비스가 성장할수록 배포는 점점 더 중요한 문제가 된다. 새벽 2시에 배포하던 시절은 지났다. 사용자는 전 세계에 분산되어 있고, 서비스는 24/7 운영되어야 한다. **Zero Downtime 배포**는 이제 선택이 아닌 필수다.

이번 포스팅에서는 실무에서 가장 많이 활용되는 두 가지 배포 전략인 **블루/그린(Blue/Green)** 과 **카나리(Canary)** 배포를 심층적으로 다룬다. 단순한 개념 설명을 넘어, Kubernetes와 Nginx를 활용한 실전 예제와 함께 각 전략의 트레이드오프까지 살펴본다.

---

## 핵심 개념

### 블루/그린 배포 (Blue/Green Deployment)

블루/그린 배포는 **두 개의 동일한 프로덕션 환경**을 유지하는 전략이다.

- **Blue**: 현재 운영 중인 환경 (구버전)
- **Green**: 새 버전이 배포된 환경 (신버전)

트래픽은 한 번에 Blue에서 Green으로 전환된다. 문제가 생기면 즉시 Blue로 롤백이 가능하다.

```
사용자 트래픽
     │
     ▼
  [Load Balancer]
     │          └──────────────────┐
     ▼ (배포 전)                   ▼ (배포 후)
  [Blue v1.0]                 [Green v1.1]
  (Active)                    (Active)
```

**장점:**
- 롤백이 즉각적 (DNS/LB 스위치만으로 복구)
- 신버전을 충분히 검증 후 전환 가능
- 다운타임 제로

**단점:**
- 인프라 비용이 2배
- DB 스키마 마이그레이션 처리가 까다로움
- 전환 순간 세션 유실 가능성

---

### 카나리 배포 (Canary Deployment)

카나리 배포는 **일부 사용자에게만 신버전을 점진적으로 노출**하는 전략이다. 광부들이 유독가스 감지를 위해 카나리아 새를 데려간 데서 유래했다.

```
사용자 트래픽 100%
        │
        ▼
  [Load Balancer]
   ┌────┴────────────────┐
   │ 90%                 │ 10%
   ▼                     ▼
[v1.0 Stable]        [v1.1 Canary]
```

트래픽 비율을 5% → 25% → 50% → 100%로 단계적으로 올리면서 모니터링한다.

**장점:**
- 실제 트래픽으로 검증 가능
- 장애 영향 범위 최소화
- A/B 테스트와 결합 용이

**단점:**
- 두 버전이 동시에 운영되므로 API 하위 호환성 필수
- 모니터링/관찰 체계가 잘 갖춰져야 함
- 전략 설계와 운영이 복잡

---

## 실전 예제

### 1. Kubernetes 블루/그린 배포

Kubernetes에서 블루/그린 배포는 `Service`의 `selector`를 변경하는 방식으로 구현한다.

**Blue Deployment (현재 운영 중)**

```yaml
# blue-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app-blue
  labels:
    app: myapp
    version: blue
spec:
  replicas: 3
  selector:
    matchLabels:
      app: myapp
      version: blue
  template:
    metadata:
      labels:
        app: myapp
        version: blue
    spec:
      containers:
      - name: myapp
        image: myapp:1.0.0
        ports:
        - containerPort: 8080
        readinessProbe:
          httpGet:
            path: /actuator/health
            port: 8080
          initialDelaySeconds: 10
          periodSeconds: 5
```

**Service (현재 Blue로 라우팅)**

```yaml
# service.yaml
apiVersion: v1
kind: Service
metadata:
  name: myapp-service
spec:
  selector:
    app: myapp
    version: blue   # 이 값만 변경하면 Green으로 스위치
  ports:
  - protocol: TCP
    port: 80
    targetPort: 8080
```

**Green Deployment 적용 및 전환 스크립트**

```bash
#!/bin/bash
# blue-green-switch.sh

NEW_VERSION="green"
OLD_VERSION="blue"
IMAGE_TAG="1.1.0"

echo "🚀 Green 환경 배포 시작..."
kubectl apply -f green-deployment.yaml

echo "⏳ Green Pod Ready 대기..."
kubectl rollout status deployment/app-${NEW_VERSION} --timeout=120s

if [ $? -ne 0 ]; then
  echo "❌ Green 배포 실패. 롤백하지 않아도 Blue가 계속 운영됩니다."
  exit 1
fi

echo "🔀 트래픽을 Green으로 전환..."
kubectl patch service myapp-service \
  -p "{\"spec\":{\"selector\":{\"version\":\"${NEW_VERSION}\"}}}"

echo "✅ 전환 완료. Blue 환경은 30분 후 정리합니다."
# 롤백 필요시: kubectl patch service myapp-service -p '{"spec":{"selector":{"version":"blue"}}}'
```

---

### 2. Kubernetes 카나리 배포 (Argo Rollouts 활용)

실무에서는 Argo Rollouts나 Flagger 같은 전문 도구를 많이 쓴다. 아래는 Argo Rollouts를 사용한 카나리 배포 예시다.

```yaml
# canary-rollout.yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: myapp-rollout
spec:
  replicas: 10
  strategy:
    canary:
      steps:
      - setWeight: 10        # 1단계: 트래픽 10%
      - pause: {duration: 5m}
      - analysis:            # 자동 분석 실행
          templates:
          - templateName: success-rate
      - setWeight: 30        # 2단계: 트래픽 30%
      - pause: {duration: 10m}
      - setWeight: 60        # 3단계: 트래픽 60%
      - pause: {duration: 10m}
      - setWeight: 100       # 최종 전환
  selector:
    matchLabels:
      app: myapp
  template:
    metadata:
      labels:
        app: myapp
    spec:
      containers:
      - name: myapp
        image: myapp:1.1.0
        ports:
        - containerPort: 8080
```

**자동 분석 템플릿 (Prometheus 기반)**

```yaml
# analysis-template.yaml
apiVersion: argoproj.io/v1alpha1
kind: AnalysisTemplate
metadata:
  name: success-rate
spec:
  metrics:
  - name: success-rate
    interval: 1m
    successCondition: result[0] >= 0.95   # 성공률 95% 이상
    failureLimit: 3
    provider:
      prometheus:
        address: http://prometheus:9090
        query: |
          sum(rate(http_requests_total{
            job="myapp",
            status!~"5.."
          }[5m])) /
          sum(rate(http_requests_total{
            job="myapp"
          }[5m]))
```

---

### 3. Nginx 기반 카나리 (가중치 라우팅)

Kubernetes 없이 Nginx Ingress로 카나리를 구현하는 방법이다.

```nginx
# nginx.conf
upstream stable {
    server stable-app:8080 weight=9;  # 90% 트래픽
}

upstream canary {
    server canary-app:8080 weight=1;  # 10% 트래픽
}

# split_clients를 활용한 결정적 라우팅
split_clients "${remote_addr}${http_user_agent}" $upstream_pool {
    10%     canary;
    *       stable;
}

server {
    listen 80;

    location / {
        proxy_pass http://$upstream_pool;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        
        # 카나리 버전 식별을 위한 헤더
        add_header X-Served-By $upstream_addr always;
    }
}
```

**Kubernetes Nginx Ingress Annotation 방식**

```yaml
# canary-ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: myapp-canary
  annotations:
    nginx.ingress.kubernetes.io/canary: "true"
    nginx.ingress.kubernetes.io/canary-weight: "10"
    # 특정 헤더로 카나리 강제 라우팅 (QA 테스트용)
    nginx.ingress.kubernetes.io/canary-by-header: "X-Canary"
    nginx.ingress.kubernetes.io/canary-by-header-value: "true"
spec:
  rules:
  - host: myapp.example.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: myapp-canary-service
            port:
              number: 80
```

---

## 주의사항 및 트레이드오프

### DB 스키마 마이그레이션

두 전략 모두 **DB 변경이 가장 큰 복병**이다. 두 버전이 동시에 같은 DB를 바라보므로 하위 호환성이 깨지는 변경은 위험하다.

**권장 패턴: Expand-Contract (팽창-수축)**

```
1단계 (Expand):   새 컬럼 추가 (nullable), 구버전은 무시
2단계 (Migration): 신버전 배포, 데이터 백필
3단계 (Contract): 구버전 완전 제거 후 NOT NULL 제약 추가
```

### 세션 및 스티키 세션

카나리 배포 시, 같은 사용자가 매 요청마다 다른 버전을 만나면 UX가 깨질 수 있다. 이를 방지하려면:

```yaml
# Nginx 쿠키 기반 스티키 세션
nginx.ingress.kubernetes.io/affinity: "cookie"
nginx.ingress.kubernetes.io/session-cookie-name: "SERVERID"
nginx.ingress.kubernetes.io/session-cookie-expires: "172800"
```

### 전략 선택 기준

| 항목 | 블루/그린 | 카나리 |
|------|-----------|--------|
| 롤백 속도 | ⚡ 즉각적 | 🐢 단계적 |
| 인프라 비용 | 💰 높음 (2배) | 💰 낮음 |
| 리스크 범위 | 전체 | 일부 |
| 운영 복잡도 | 중간 | 높음 |
| DB 마이그레이션 | 복잡 | 복잡 |
| 적합한 상황 | 대규모 변경, 빠른 전환 | 점진적 검증, 불확실한 변경 |

### 관찰 가능성(Observability)이 전제 조건

카나리 배포는 제대로 된 모니터링 없이는 의미가 없다. 최소한 다음은 갖춰야 한다:

- **메트릭**: 버전별 에러율, 레이턴시 (Prometheus + Grafana)
- **로그**: 버전 태깅된 구조화 로그 (ELK, Loki)
- **트레이싱**: 분산 추적으로 버전 간 성능 비교 (Jaeger, Tempo)

```java
// Spring Boot - 버전 정보를 MDC에 주입
@Component
public class VersionFilter implements Filter {
    
    @Value("${app.version:unknown}")
    private String appVersion;
    
    @Override
    public void doFilter(ServletRequest request, ServletResponse response, 
                         FilterChain chain) throws IOException, ServletException {
        try {
            MDC.put("app_version", appVersion);
            MDC.put("trace_id", UUID.randomUUID().toString());
            chain.doFilter(request, response);
        } finally {
            MDC.clear();
        }
    }
}
```

---

## 정리

블루/그린과 카나리, 어느 것이 더 낫다고 단정할 수 없다. 서비스의 특성, 팀의 역량, 인프라 상황에 따라 선택이 달라진다.

**실무 추천 접근법:**

1. **처음 Zero Downtime 배포를 도입한다면** → 블루/그린으로 시작하라. 개념이 단순하고 롤백이 명확하다.
2. **서비스가 안정화되고 모니터링 체계가 갖춰졌다면** → 카나리로 전환하라. 실제 사용자 트래픽으로 검증하는 것이 가장 확실하다.
3. **대형 조직이라면** → 두 전략을 조합하라. 마이크로서비스별로 전략을 다르게 가져가는 것도 좋다.

배포는 기술적 문제인 동시에 **팀의 신뢰 문제**다. 롤백이 쉽고 영향 범위가 제어될수록, 팀은 더 자주, 더 작은 단위로 배포하게 된다. 그것이 결국 시스템 안정성을 높이는 선순환으로 이어진다.

> "배포가 두렵다면, 더 자주 배포하라." — Continuous Delivery 원칙