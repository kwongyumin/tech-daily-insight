# Kubernetes HPA/VPA 오토스케일링 전략과 실전 튜닝

## 개요

프로덕션 환경에서 트래픽은 예측하기 어렵다. 이벤트 기반 트래픽 폭증, 새벽 시간대의 급격한 감소, 배치 작업의 간헐적 부하 등 다양한 패턴이 존재한다. 이 모든 상황에 수동으로 대응하는 것은 현실적으로 불가능하다.

Kubernetes는 이를 위해 **HPA(Horizontal Pod Autoscaler)**와 **VPA(Vertical Pod Autoscaler)** 두 가지 핵심 오토스케일링 메커니즘을 제공한다. 그러나 단순히 기본 설정만 적용해서는 원하는 효과를 얻기 어렵다. 실무에서는 메트릭 선택, 쿨다운 설정, 리소스 요청 최적화 등 세밀한 튜닝이 필수다.

이 글에서는 HPA와 VPA의 핵심 개념부터 실전에서 바로 적용 가능한 설정과 트레이드오프까지 깊이 있게 다룬다.

---

## 핵심 개념

### HPA (Horizontal Pod Autoscaler)

HPA는 Pod의 **개수**를 조절한다. CPU, 메모리 사용률 또는 커스텀 메트릭을 기반으로 Replica 수를 자동으로 증감시킨다.

**동작 원리:**
1. Metrics Server 또는 Custom Metrics API에서 메트릭 수집
2. 현재 메트릭과 목표값 비교
3. `desiredReplicas = ceil(currentReplicas × (currentMetric / desiredMetric))` 계산
4. Deployment/StatefulSet의 Replica 수 조정

**스케일링 조건:**
- Scale Out: 메트릭이 목표값을 초과할 때
- Scale In: 메트릭이 목표값 미만으로 일정 시간 유지될 때 (기본 5분)

### VPA (Vertical Pod Autoscaler)

VPA는 Pod의 **리소스 요청(Request/Limit)**을 조절한다. 실제 사용량을 분석해 CPU와 메모리의 적정값을 권고하거나 자동 적용한다.

**VPA 운영 모드:**
| 모드 | 설명 |
|------|------|
| `Off` | 권고값만 생성, 자동 적용 없음 |
| `Initial` | Pod 생성 시에만 적용 |
| `Auto` | 실시간으로 Pod 재시작하여 적용 |
| `Recreate` | Auto와 동일하나 명시적 재시작 |

---

## 실전 예제

### 1. HPA 기본 설정 (CPU 기반)

가장 기본적인 CPU 기반 HPA 설정이다.

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: api-server-hpa
  namespace: production
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: api-server
  minReplicas: 3
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 60
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 30
      policies:
        - type: Percent
          value: 100
          periodSeconds: 60
    scaleDown:
      stabilizationWindowSeconds: 300
      policies:
        - type: Pods
          value: 2
          periodSeconds: 60
```

**핵심 포인트:**
- `averageUtilization: 60` — 60%를 목표로 설정해 버퍼를 확보한다. 80~90%로 설정하면 스케일 아웃 전에 이미 성능 저하가 발생할 수 있다.
- `scaleUp.stabilizationWindowSeconds: 30` — 빠른 스케일 아웃을 위해 30초로 설정
- `scaleDown.stabilizationWindowSeconds: 300` — 불필요한 스케일 인을 방지하기 위해 5분 유지

### 2. 커스텀 메트릭 기반 HPA (Prometheus Adapter 활용)

RPS(Requests Per Second) 기반으로 스케일링하는 예제다.

```yaml
# Prometheus Adapter 규칙 설정
apiVersion: v1
kind: ConfigMap
metadata:
  name: prometheus-adapter-config
  namespace: monitoring
data:
  config.yaml: |
    rules:
    - seriesQuery: 'http_requests_total{namespace!="",pod!=""}'
      resources:
        overrides:
          namespace:
            resource: namespace
          pod:
            resource: pod
      name:
        matches: "^(.*)_total$"
        as: "${1}_per_second"
      metricsQuery: 'sum(rate(<<.Series>>{<<.LabelMatchers>>}[2m])) by (<<.GroupBy>>)'
```

```yaml
# 커스텀 메트릭 기반 HPA
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: api-server-rps-hpa
  namespace: production
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: api-server
  minReplicas: 3
  maxReplicas: 50
  metrics:
    - type: Pods
      pods:
        metric:
          name: http_requests_per_second
        target:
          type: AverageValue
          averageValue: "500"   # Pod당 500 RPS 목표
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

복수의 메트릭을 설정할 경우, Kubernetes는 **각 메트릭 기준으로 계산한 Replica 수 중 최댓값**을 채택한다.

### 3. VPA 설정 (추천 모드)

프로덕션 초기에는 `Off` 모드로 권고값을 모니터링한 뒤, `Auto`로 전환하는 것을 권장한다.

```yaml
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata:
  name: api-server-vpa
  namespace: production
spec:
  targetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: api-server
  updatePolicy:
    updateMode: "Off"   # 초기에는 권고값만 확인
  resourcePolicy:
    containerPolicies:
      - containerName: api-server
        minAllowed:
          cpu: 100m
          memory: 256Mi
        maxAllowed:
          cpu: 4
          memory: 8Gi
        controlledResources:
          - cpu
          - memory
        controlledValues: RequestsAndLimits
```

**VPA 권고값 확인:**
```bash
kubectl describe vpa api-server-vpa -n production

# 출력 예시:
# Recommendation:
#   Container Recommendations:
#     Container Name: api-server
#     Lower Bound:
#       Cpu: 200m
#       Memory: 512Mi
#     Target:
#       Cpu: 800m
#       Memory: 1536Mi
#     Upper Bound:
#       Cpu: 2000m
#       Memory: 4Gi
```

### 4. Deployment 리소스 요청 최적화

VPA 권고값을 반영한 Deployment 설정 예시다.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-server
  namespace: production
spec:
  replicas: 3
  selector:
    matchLabels:
      app: api-server
  template:
    metadata:
      labels:
        app: api-server
    spec:
      containers:
        - name: api-server
          image: my-api-server:latest
          resources:
            requests:
              cpu: "800m"       # VPA Target 값 적용
              memory: "1536Mi"
            limits:
              cpu: "2000m"      # VPA Upper Bound 적용
              memory: "3Gi"
          readinessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 10
            periodSeconds: 5
          livenessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 30
            periodSeconds: 10
```

---

## 주의사항 및 트레이드오프

### 1. HPA와 VPA 동시 사용 시 충돌 주의

HPA와 VPA를 함께 사용할 경우 CPU 메트릭에서 충돌이 발생할 수 있다.

- **HPA가 CPU 기준으로 스케일 아웃** 중인데, **VPA가 CPU Request를 올리면** HPA의 계산 기준이 바뀌어 예측 불가한 동작이 발생한다.

**권장 조합:**
```
HPA (CPU/Memory 기반) + VPA (Off 모드, 분석 용도)
HPA (커스텀 메트릭 기반) + VPA (Auto 모드, CPU/Memory 조정)
```

CPU 기반 HPA와 VPA Auto 모드를 동시에 사용하려면 VPA의 `controlledResources`에서 CPU를 제외해야 한다.

```yaml
resourcePolicy:
  containerPolicies:
    - containerName: api-server
      controlledResources:
        - memory   # CPU는 HPA가 담당하므로 메모리만 VPA가 관리
```

### 2. 스케일 인 시 트래픽 드롭 방지

갑작스러운 Pod 종료로 인한 요청 실패를 막으려면 `preStop` 훅과 `terminationGracePeriodSeconds`를 반드시 설정해야 한다.

```yaml
spec:
  template:
    spec:
      terminationGracePeriodSeconds: 60
      containers:
        - name: api-server
          lifecycle:
            preStop:
              exec:
                command: ["/bin/sh", "-c", "sleep 15"]
```

`sleep 15`는 로드밸런서가 해당 Pod를 엔드포인트에서 제거하는 데 걸리는 시간을 확보하기 위함이다.

### 3. Metrics Server 지연 문제

Metrics Server는 기본적으로 **15초 간격**으로 메트릭을 수집하며, HPA는 이를 **30초마다** 평가한다. 따라서 트래픽 폭증 시 실제 스케일 아웃까지 최대 1~2분의 지연이 발생할 수 있다.

**대응 전략:**
- `minReplicas`를 충분히 설정해 초기 버퍼 확보
- Predictive Scaling을 위한 KEDA(Kubernetes Event-Driven Autoscaler) 도입 검토
- PodDisruptionBudget(PDB)으로 최소 가용 Pod 수 보장

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: api-server-pdb
  namespace: production
spec:
  minAvailable: 2
  selector:
    matchLabels:
      app: api-server
```

### 4. 노드 리소스와의 정합성

HPA로 Pod 수가 늘어나도 **노드에 여유 리소스가 없으면 Pending 상태**가 된다. Cluster Autoscaler와 함께 운영해야 완전한 오토스케일링이 가능하다.

```bash
# 노드별 리소스 현황 확인
kubectl describe nodes | grep -A 5 "Allocated resources"

# Pending Pod 원인 확인
kubectl get events --field-selector reason=FailedScheduling -n production
```

---

## 정리

| 구분 | HPA | VPA |
|------|-----|-----|
| 스케일링 방향 | 수평 (Pod 수 조절) | 수직 (리소스 크기 조절) |
| 적합한 워크로드 | 무상태(Stateless) 서비스 | 단일 인스턴스, DB, ML 워크로드 |
| 재시작 필요 여부 | 불필요 | Auto/Recreate 모드 시 필요 |
| 반응 속도 | 빠름 (분 단위) | 느림 (재시작 필요) |
| 주요 리스크 | 과도한 스케일 인/아웃 | Pod 재시작으로 인한 일시적 다운 |

**핵심 권장사항을 정리하면:**

1. **CPU 목표치는 60~70%로** 설정해 스케일 아웃 전 여유를 확보하라
2. **scaleDown은 보수적으로**, scaleUp은 공격적으로 설정하라
3. **HPA + VPA 동시 사용 시** CPU 관리 주체를 명확히 분리하라
4. **VPA는 Off 모드로 먼저 관찰**하고, 안정화 후 Auto로 전환하라
5. **preStop 훅과 PDB**로 스케일 인 시 가용성을 보장하라
6. **Cluster Autoscaler**와 함께 운영해 노드 레벨까지 자동화하라

오토스케일링은 설정 한 번으로 완성되지 않는다. 실제 트래픽 패턴을 지속적으로 모니터링하고, 메트릭과 임계값을 반복적으로 조정하는 것이 진정한 실전 튜닝이다. 특히 대규모 이벤트나 배포 전에는 반드시 부하 테스트를 통해 스케일링 동작을 검증하는 습관을 들이자.