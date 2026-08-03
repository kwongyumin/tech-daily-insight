# 무중단 배포를 위한 Kubernetes Rolling Update 세부 튜닝

## 개요

프로덕션 환경에서 배포는 단순히 코드를 올리는 행위가 아닙니다. 잘못 설정된 Rolling Update 하나가 수천 건의 요청 실패로 이어지고, 온콜 알람이 새벽에 울릴 수 있습니다. Kubernetes의 Rolling Update는 기본적으로 무중단 배포를 지원하지만, **기본값(default)만으로는 실제 프로덕션 트래픽을 안정적으로 처리하기 어렵습니다.**

이 글에서는 단순한 `kubectl rollout` 소개가 아닌, `maxSurge`, `maxUnavailable`, `readinessProbe`, `terminationGracePeriodSeconds`, PodDisruptionBudget 등 실무에서 자주 간과하는 파라미터를 깊게 파고들고, 실전에서 바로 적용할 수 있는 튜닝 전략을 다룹니다.

---

## 핵심 개념

### Rolling Update 동작 원리

Kubernetes의 Deployment는 기본 전략으로 `RollingUpdate`를 사용합니다. 이 방식은 구버전 Pod를 점진적으로 줄이면서 신버전 Pod를 늘리는 방식입니다. 이 과정에서 아래 두 파라미터가 핵심 역할을 합니다.

| 파라미터 | 설명 | 기본값 |
|---|---|---|
| `maxSurge` | 배포 중 **추가로 생성 가능한** Pod 수 (또는 비율) | 25% |
| `maxUnavailable` | 배포 중 **사용 불가 허용** Pod 수 (또는 비율) | 25% |

예를 들어 `replicas: 10`에서 둘 다 기본값이면, 배포 중 최대 12개 Pod가 존재할 수 있고, 최소 7개 Pod는 항상 서비스 가능 상태를 유지합니다.

### Readiness Probe의 역할

Rolling Update에서 신규 Pod가 `Ready` 상태가 되어야 구버전 Pod가 종료됩니다. **Readiness Probe가 없거나 느슨하게 설정되면, 실제로 트래픽을 받을 준비가 안 된 Pod가 Service에 등록되어 요청 실패가 발생합니다.**

### Graceful Shutdown과 terminationGracePeriodSeconds

Pod 종료 시 Kubernetes는 다음 순서로 동작합니다.

1. Pod에 `SIGTERM` 신호 전송
2. `terminationGracePeriodSeconds` 동안 대기
3. 타임아웃 초과 시 `SIGKILL` 강제 종료

이 시간 동안 애플리케이션이 처리 중인 요청을 완료하고 연결을 정상 종료해야 합니다. 기본값 30초가 충분하지 않은 경우가 많습니다.

---

## 실전 예제

### 기본 Deployment 튜닝

아래는 Spring Boot 애플리케이션을 배포하는 실무형 Deployment 예시입니다.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-spring-app
  namespace: production
spec:
  replicas: 10
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 2          # 절댓값 권장 (예측 가능성)
      maxUnavailable: 0    # 무중단 배포의 핵심: 항상 10개 유지
  selector:
    matchLabels:
      app: my-spring-app
  template:
    metadata:
      labels:
        app: my-spring-app
    spec:
      terminationGracePeriodSeconds: 60  # 기본 30초에서 상향
      containers:
        - name: app
          image: my-registry/my-spring-app:v2.1.0
          ports:
            - containerPort: 8080
          
          # Readiness Probe: 트래픽 수신 준비 여부 판단
          readinessProbe:
            httpGet:
              path: /actuator/health/readiness
              port: 8080
            initialDelaySeconds: 20   # 앱 기동 시간 고려
            periodSeconds: 5
            failureThreshold: 3
            successThreshold: 1
          
          # Liveness Probe: 교착 상태 감지 및 재시작
          livenessProbe:
            httpGet:
              path: /actuator/health/liveness
              port: 8080
            initialDelaySeconds: 30
            periodSeconds: 10
            failureThreshold: 5
          
          # Startup Probe: 초기 기동 시간이 긴 앱에 필수
          startupProbe:
            httpGet:
              path: /actuator/health
              port: 8080
            failureThreshold: 30      # 최대 150초(30 * 5s) 대기
            periodSeconds: 5
          
          resources:
            requests:
              cpu: "500m"
              memory: "512Mi"
            limits:
              cpu: "1000m"
              memory: "1Gi"
          
          lifecycle:
            preStop:
              exec:
                # Service에서 제거될 때까지 대기 후 종료
                command: ["/bin/sh", "-c", "sleep 5"]
```

### maxUnavailable: 0 설정의 의미

`maxUnavailable: 0`은 **배포 중 항상 `replicas` 수만큼의 Pod가 Ready 상태를 유지**한다는 의미입니다. 단, 이 경우 신규 Pod가 Ready가 될 때까지 구버전 Pod가 종료되지 않으므로 **배포 시간이 길어질 수 있습니다.** 속도와 안정성 사이의 균형이 필요합니다.

```bash
# 배포 진행 상황 실시간 모니터링
kubectl rollout status deployment/my-spring-app -n production --watch

# 배포 이력 확인
kubectl rollout history deployment/my-spring-app -n production

# 문제 발생 시 즉시 롤백
kubectl rollout undo deployment/my-spring-app -n production

# 특정 리비전으로 롤백
kubectl rollout undo deployment/my-spring-app -n production --to-revision=3
```

### PodDisruptionBudget 설정

Rolling Update뿐 아니라 노드 드레인, 클러스터 업그레이드 등 **자발적 중단(voluntary disruption)** 상황에서도 가용성을 보장하려면 PDB가 필수입니다.

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: my-spring-app-pdb
  namespace: production
spec:
  # minAvailable과 maxUnavailable 중 하나만 설정
  minAvailable: 8          # 최소 8개 Pod는 항상 유지
  # maxUnavailable: 2      # 또는 최대 2개까지 동시 중단 허용
  selector:
    matchLabels:
      app: my-spring-app
```

> ⚠️ **PDB와 Rolling Update의 상호작용**: PDB의 `minAvailable`이 너무 높으면 노드 드레인이 블로킹될 수 있습니다. 항상 `replicas` 수보다 낮게 설정하세요.

### Spring Boot Graceful Shutdown 설정

Kubernetes의 `terminationGracePeriodSeconds`와 Spring Boot의 Graceful Shutdown이 함께 동작해야 합니다.

```yaml
# application.yml
server:
  shutdown: graceful  # Spring Boot 2.3+

spring:
  lifecycle:
    timeout-per-shutdown-phase: 50s  # terminationGracePeriodSeconds보다 짧게 설정
```

`terminationGracePeriodSeconds: 60`으로 설정했다면, Spring의 shutdown timeout은 반드시 그보다 짧아야 합니다. 그래야 `SIGKILL`이 오기 전에 Spring이 먼저 정상 종료됩니다.

### preStop Hook 활용

Kubernetes에서 Pod를 Service 엔드포인트에서 제거하는 작업과 `SIGTERM` 전송이 **동시에** 발생합니다. 이 타이밍 이슈로 인해 이미 제거 중인 Pod에 요청이 들어올 수 있습니다. `preStop` 훅에 짧은 sleep을 추가하면 이를 완화할 수 있습니다.

```yaml
lifecycle:
  preStop:
    exec:
      command: ["/bin/sh", "-c", "sleep 10 && kill -SIGTERM 1"]
```

이 경우 `terminationGracePeriodSeconds`는 `preStop` 실행 시간도 포함하므로, 전체 타임아웃을 고려해 상향 조정해야 합니다.

---

## 주의사항 및 트레이드오프

### 1. maxUnavailable: 0 + maxSurge: 0 조합은 금지

두 값을 모두 0으로 설정하면 배포 자체가 진행되지 않습니다. Kubernetes가 이를 검증하여 오류를 반환하지만, `%` 단위로 설정 시 반올림 이슈로 실질적으로 0이 되는 경우가 있으니 주의하세요.

### 2. Readiness Probe 실패 임계값 조정

```yaml
readinessProbe:
  failureThreshold: 3
  periodSeconds: 5
```

위 설정에서 Pod는 15초 실패 후 Ready 상태에서 제거됩니다. 순간적인 스파이크나 GC pause로 인한 일시적 응답 지연이 잦은 서비스라면 `failureThreshold`를 높이거나 `periodSeconds`를 늘려야 합니다. 반대로 너무 느슨하면 실제 장애 상황에서도 트래픽이 계속 유입됩니다.

### 3. 배포 속도 vs 안정성 트레이드오프

| 설정 | 배포 속도 | 안정성 | 리소스 사용 |
|---|---|---|---|
| `maxSurge: 50%, maxUnavailable: 0` | 빠름 | 높음 | 추가 50% 필요 |
| `maxSurge: 0, maxUnavailable: 25%` | 보통 | 낮음 | 추가 없음 |
| `maxSurge: 1, maxUnavailable: 0` | 느림 | 최고 | 최소 추가 |

리소스 제약이 있는 환경에서 `maxSurge`를 크게 설정하면 노드 리소스 부족으로 Pending 상태의 Pod가 발생할 수 있습니다.

### 4. minReadySeconds로 조기 트래픽 유입 방지

```yaml
spec:
  minReadySeconds: 30  # Ready 후 30초 대기 후 다음 Pod 교체
```

이 설정은 Pod가 Ready 상태가 된 직후 바로 다음 롤아웃을 진행하지 않고, 일정 시간 안정적으로 동작하는지 확인 후 진행하도록 합니다. 배포 시간이 늘어나지만 문제가 있는 버전의 전파를 늦출 수 있습니다.

### 5. Horizontal Pod Autoscaler(HPA)와의 충돌

HPA가 활성화된 상태에서 배포 시, HPA가 `replicas`를 변경하면 Rolling Update의 계산 기준이 달라질 수 있습니다. 배포 중 HPA를 일시적으로 `minReplicas`를 고정하거나, ArgoCD 같은 GitOps 도구를 통해 배포 자동화 파이프라인에서 관리하는 것을 권장합니다.

---

## 정리

무중단 배포는 단순히 `RollingUpdate` 전략을 선택하는 것에서 끝나지 않습니다. 실무에서 안정적인 Rolling Update를 위해 반드시 챙겨야 할 체크리스트를 정리합니다.

**✅ Rolling Update 튜닝 체크리스트**

- [ ] `maxUnavailable: 0` 설정으로 서비스 가용 Pod 수 보장
- [ ] `maxSurge`를 절댓값으로 설정해 예측 가능한 리소스 사용
- [ ] `readinessProbe` 경로, 임계값, 딜레이 정밀 설정
- [ ] `startupProbe`로 초기 기동 시간이 긴 앱 보호
- [ ] `terminationGracePeriodSeconds`를 애플리케이션 종료 시간 이상으로 설정
- [ ] Spring Boot `server.shutdown: graceful` 활성화
- [ ] `preStop` 훅에 sleep 추가로 엔드포인트 제거 타이밍 보정
- [ ] `PodDisruptionBudget` 설정으로 클러스터 운영 중 가용성 보장
- [ ] `minReadySeconds`로 조기 전파 방지
- [ ] HPA와의 상호작용 확인

Kubernetes의 Rolling Update는 설정 하나하나가 서비스 가용성에 직결됩니다. 오늘 소개한 파라미터들을 프로덕션 환경에 맞게 점진적으로 적용하고, 배포 파이프라인에서 `kubectl rollout status`를 반드시 확인하는 습관을 들이시기 바랍니다.