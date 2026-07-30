# 컨테이너 리소스 Limit/Request 설정과 OOMKill 방지

## 개요

Kubernetes 환경에서 운영하다 보면 어느 날 갑자기 Pod가 죽어있는 경험을 하게 된다. 로그를 확인해보면 `OOMKilled` 상태 코드와 함께 컨테이너가 종료된 것을 확인할 수 있다. OOMKill(Out Of Memory Kill)은 컨테이너가 설정된 메모리 한계를 초과했을 때 커널이 프로세스를 강제로 종료하는 현상이다.

이 문제는 단순히 메모리를 늘리는 것으로 해결되지 않는다. 리소스 Request와 Limit의 차이를 정확히 이해하고, JVM 기반 애플리케이션의 메모리 특성을 파악하며, 적절한 모니터링 체계를 갖추는 것이 근본적인 해결책이다.

이 포스팅에서는 실무에서 자주 마주치는 OOMKill 원인과 방지 전략을 구체적인 예제와 함께 다룬다.

---

## 핵심 개념

### Request vs Limit

Kubernetes에서 리소스 설정은 두 가지 개념으로 나뉜다.

- **Request**: 컨테이너가 스케줄링될 때 노드에서 **보장받는 최소 리소스** 양이다. 스케줄러는 Request 값을 기준으로 Pod를 배치할 노드를 결정한다.
- **Limit**: 컨테이너가 **사용할 수 있는 최대 리소스** 양이다. 이 값을 초과하면 CPU는 스로틀링되고, 메모리는 OOMKill이 발생한다.

```
Request ≤ 실제 사용량 ≤ Limit
```

이 두 값의 차이를 **버스트(Burst)** 구간이라고 한다. Request와 Limit을 동일하게 설정하면 `Guaranteed` QoS 클래스가 부여되어 가장 안정적으로 운영된다.

### QoS 클래스

| QoS 클래스 | 조건 | 특징 |
|---|---|---|
| Guaranteed | Request == Limit (CPU, Memory 모두) | 가장 마지막에 OOMKill 대상이 됨 |
| Burstable | Request < Limit | 메모리 부족 시 중간 우선순위로 종료 |
| BestEffort | Request, Limit 미설정 | 가장 먼저 OOMKill 대상이 됨 |

### CPU와 메모리의 차이

CPU와 메모리는 Limit 초과 시 동작 방식이 다르다는 점을 반드시 인지해야 한다.

- **CPU Limit 초과**: 스로틀링(Throttling) 발생. 프로세스는 죽지 않지만 응답이 느려진다.
- **Memory Limit 초과**: OOMKill 발생. 컨테이너가 즉시 종료된다.

이 차이 때문에 메모리 Limit 설정은 CPU보다 훨씬 신중하게 접근해야 한다.

---

## 실전 예제

### 기본 리소스 설정

```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: spring-api-server
spec:
  replicas: 3
  selector:
    matchLabels:
      app: spring-api-server
  template:
    metadata:
      labels:
        app: spring-api-server
    spec:
      containers:
        - name: api
          image: my-registry/spring-api:latest
          resources:
            requests:
              memory: "512Mi"
              cpu: "250m"
            limits:
              memory: "1Gi"
              cpu: "1000m"
          env:
            - name: JAVA_OPTS
              value: "-Xms256m -Xmx512m -XX:MaxMetaspaceSize=128m -XX:+UseContainerSupport"
```

### JVM 메모리 계산 공식

Spring Boot 애플리케이션의 경우, JVM 힙 메모리만 생각하다가 OOMKill을 당하는 경우가 많다. JVM이 실제로 사용하는 메모리는 훨씬 복잡하다.

```
전체 JVM 메모리 = Heap + Metaspace + CodeCache + DirectMemory + ThreadStack + JVM Overhead
```

실무에서 사용하는 계산 공식:

```bash
# 컨테이너 메모리 Limit = 1Gi (1024Mi) 기준
# Heap         : 512Mi  (-Xmx512m)
# Metaspace    : 128Mi  (-XX:MaxMetaspaceSize=128m)
# CodeCache    : 64Mi   (-XX:ReservedCodeCacheSize=64m)
# DirectMemory : 64Mi   (-XX:MaxDirectMemorySize=64m)
# ThreadStack  : ~50Mi  (스레드 수 * 스택 사이즈)
# JVM Overhead : ~50Mi
# 합계         : ~868Mi → 1Gi Limit에 안정적으로 수용 가능
```

### UseContainerSupport 설정

JDK 8u191 이전에는 JVM이 컨테이너의 메모리 제한을 인식하지 못하고 호스트 전체 메모리 기준으로 힙을 설정했다. 이 문제를 해결하는 핵심 플래그가 `UseContainerSupport`다.

```yaml
env:
  - name: JAVA_TOOL_OPTIONS
    value: >-
      -XX:+UseContainerSupport
      -XX:InitialRAMPercentage=40.0
      -XX:MaxRAMPercentage=70.0
      -XX:MinRAMPercentage=30.0
      -XX:MaxMetaspaceSize=128m
      -XX:+UseG1GC
      -XX:+HeapDumpOnOutOfMemoryError
      -XX:HeapDumpPath=/tmp/heapdump.hprof
```

`MaxRAMPercentage=70.0` 설정은 컨테이너 메모리 Limit의 70%를 Heap Max로 사용하겠다는 의미다. 나머지 30%는 Non-Heap 영역으로 남겨둔다.

### LimitRange로 기본값 강제화

팀 전체에 리소스 설정을 강제하려면 `LimitRange`를 활용하자.

```yaml
# limitrange.yaml
apiVersion: v1
kind: LimitRange
metadata:
  name: default-limit-range
  namespace: production
spec:
  limits:
    - type: Container
      default:
        memory: "512Mi"
        cpu: "500m"
      defaultRequest:
        memory: "256Mi"
        cpu: "100m"
      max:
        memory: "4Gi"
        cpu: "4000m"
      min:
        memory: "64Mi"
        cpu: "50m"
```

### ResourceQuota로 네임스페이스 총량 제한

```yaml
# resourcequota.yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: production-quota
  namespace: production
spec:
  hard:
    requests.cpu: "20"
    requests.memory: "40Gi"
    limits.cpu: "40"
    limits.memory: "80Gi"
    pods: "50"
```

### Vertical Pod Autoscaler(VPA) 활용

VPA는 실제 사용 패턴을 기반으로 Request/Limit을 자동으로 추천해주는 도구다.

```yaml
# vpa.yaml
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata:
  name: spring-api-vpa
spec:
  targetRef:
    apiVersion: "apps/v1"
    kind: Deployment
    name: spring-api-server
  updatePolicy:
    updateMode: "Off"  # 초기에는 Off로 설정하여 추천값만 확인
  resourcePolicy:
    containerPolicies:
      - containerName: api
        minAllowed:
          cpu: "100m"
          memory: "256Mi"
        maxAllowed:
          cpu: "2000m"
          memory: "2Gi"
        controlledResources: ["cpu", "memory"]
```

VPA 추천값 확인:

```bash
kubectl describe vpa spring-api-vpa -n production

# 출력 예시
# Recommendation:
#   Container Recommendations:
#     Container Name: api
#     Lower Bound:
#       Cpu: 150m
#       Memory: 384Mi
#     Target:
#       Cpu: 300m
#       Memory: 640Mi
#     Upper Bound:
#       Cpu: 800m
#       Memory: 1200Mi
```

### OOMKill 탐지 및 알림 스크립트

```bash
#!/bin/bash
# oomkill-monitor.sh - OOMKill 발생 시 Slack 알림

NAMESPACE=${1:-"production"}
SLACK_WEBHOOK=${SLACK_WEBHOOK_URL}

while true; do
  OOMKILLED_PODS=$(kubectl get pods -n "${NAMESPACE}" \
    --field-selector=status.phase=Running \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{range .status.containerStatuses[*]}{.lastState.terminated.reason}{"\t"}{end}{"\n"}{end}' \
    | grep "OOMKilled")

  if [ -n "${OOMKILLED_PODS}" ]; then
    MESSAGE="🚨 OOMKill 감지!\nNamespace: ${NAMESPACE}\n${OOMKILLED_PODS}"
    curl -s -X POST "${SLACK_WEBHOOK}" \
      -H 'Content-type: application/json' \
      --data "{\"text\":\"${MESSAGE}\"}"
  fi

  sleep 60
done
```

### Prometheus 알림 규칙 설정

```yaml
# prometheus-rules.yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: oom-kill-alerts
  namespace: monitoring
spec:
  groups:
    - name: oom-kill
      rules:
        - alert: ContainerOOMKilled
          expr: |
            kube_pod_container_status_last_terminated_reason{reason="OOMKilled"} == 1
          for: 0m
          labels:
            severity: critical
          annotations:
            summary: "컨테이너 OOMKill 발생"
            description: "Pod {{ $labels.pod }}의 컨테이너 {{ $labels.container }}가 OOMKill로 종료되었습니다."

        - alert: HighMemoryUsage
          expr: |
            (container_memory_working_set_bytes{container!=""} 
              / on(pod, container, namespace) 
              kube_pod_container_resource_limits{resource="memory"}) > 0.85
          for: 5m
          labels:
            severity: warning
          annotations:
            summary: "컨테이너 메모리 사용률 85% 초과"
            description: "{{ $labels.namespace }}/{{ $labels.pod }} 메모리 사용률이 {{ $value | humanizePercentage }}입니다."
```

---

## 주의사항 및 트레이드오프

### Request/Limit 비율 설정의 함정

Request와 Limit의 비율을 너무 크게 벌리면 예기치 않은 문제가 발생한다.

```
❌ 잘못된 예시
  Request: 128Mi
  Limit:   4Gi   (32배 차이)

✅ 권장 예시
  Request: 512Mi
  Limit:   1Gi   (2배 이내)
```

비율이 너무 크면 노드에 오버커밋(Overcommit)이 발생하고, 여러 컨테이너가 동시에 버스트 구간에 진입했을 때 노드 전체가 메모리 부족 상태에 빠질 수 있다.

### CPU Throttling의 숨겨진 위험

많은 팀이 OOMKill에만 집중하다가 CPU 스로틀링을 놓친다. CPU Limit을 너무 낮게 설정하면 P99 레이턴시가 급격히 증가한다.

```bash
# CPU 스로틀링 확인
kubectl exec -it <pod-name> -- cat /sys/fs/cgroup/cpu/cpu.stat

# 또는 Prometheus 쿼리
rate(container_cpu_cfs_throttled_seconds_total[5m]) 
  / rate(container_cpu_cfs_periods_total[5m]) > 0.25
```

스로틀링 비율이 25%를 넘는다면 CPU Limit 상향이 필요한 신호다.

### Java 애플리케이션의 특수성

Spring Boot 애플리케이션은 몇 가지 추가 고려사항이 있다.

1. **Startup 시 메모리 급증**: 초기화 과정에서 메모리 사용량이 일시적으로 치솟는다. Liveness Probe의 `initialDelaySeconds`를 충분히 설정하자.
2. **GC 후 메모리 반환**: G1GC는 힙을 OS에 즉시 반환하지 않는다. `XX:+ZGenerational` (JDK21)이나 `ZGC`는 더 적극적으로 반환한다.
3. **Thread Pool 크기**: Tomcat/Netty의 스레드 수가 늘어나면 스택 메모리도 증가한다.

### Ephemeral Storage도 챙기자

메모리와 CPU에만 집중하다가 ephemeral storage 부족으로 Pod가 Evict되는 경우도 있다.

```yaml
resources:
  requests:
    memory: "512Mi"
    cpu: "250m"
    ephemeral-storage: "1Gi"
  limits:
    memory: "1Gi"
    cpu: "1000m"
    ephemeral-storage: "2Gi"
```

---

## 정리

OOMKill 방지는 단순히 메모리 Limit을 늘리는 것이 아니라 전체적인 리소스 관리 전략의 문제다. 핵심 체크리스트를 정리하면 다음과 같다.

| 항목 | 권장 설정 |
|---|---|
| JVM Container Support | `-XX:+UseContainerSupport` 필수 |
| Heap 비율 | `MaxRAMPercentage=65~75%` |
| Request/Limit 비율 | 2배 이내 유지 |
| QoS 클래스 | 중요 서비스는 `Guaranteed` 권장 |
| 메모리 여유 마진 | Non-Heap 포함 20~30% 여유 확보 |
| 모니터링 | OOMKill 알림 + 메모리 85% 경고 설정 |
| LimitRange | 네임스페이스별 기본값 강제화 |
| VPA | `Off` 모드로 추천값 수집 후 적용 |

가장 중요한 것은 **실제 사용 패턴을 측정하는 것**이다. 초기에는 VPA를 `Off` 모드로 배포하여 충분한 데이터를 수집하고, 이를 기반으로 점진적으로 Request/Limit을 조정해 나가는 접근이 가장 안전하다. 운영 환경에서 리소스 설정은 한 번 하고 끝나는 작업이 아니라, 트래픽 패턴 변화에 맞춰 지속적으로 튜닝해야 하는 살아있는 설정임을 잊지 말자.