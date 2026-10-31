# Spark on Kubernetes 운영과 리소스 격리

## 개요

Apache Spark를 Kubernetes 위에서 운영하는 패턴은 이제 많은 조직에서 표준적인 선택지가 되었다. YARN 기반의 Hadoop 클러스터 대신 Kubernetes를 선택하는 이유는 명확하다. 컨테이너 기반의 일관된 실행 환경, 클라우드 네이티브 생태계와의 통합, 그리고 서비스 워크로드와 배치 워크로드를 하나의 클러스터에서 관리할 수 있다는 운영 효율성이다.

하지만 "Spark를 Kubernetes에 올렸다"는 것과 "제대로 운영한다"는 것 사이에는 상당한 간극이 존재한다. 특히 **리소스 격리**는 멀티테넌트 환경에서 Spark 잡을 안정적으로 운영하기 위한 핵심 과제다. 드라이버가 과도한 메모리를 점유하거나, 익스큐터가 노드의 CPU를 독점하는 상황은 Kubernetes 기반 Spark 운영에서 흔히 겪는 문제다.

이 글에서는 Spark on Kubernetes의 아키텍처를 짚어보고, 리소스 격리를 위한 실전 설정과 설계 트레이드오프를 다룬다.

---

## 핵심 개념

### Spark on Kubernetes 아키텍처

Spark on Kubernetes는 크게 두 가지 배포 모드를 지원한다.

- **Client 모드**: 드라이버가 클러스터 외부(혹은 제출 노드)에서 실행되고, 익스큐터만 Kubernetes Pod으로 생성된다.
- **Cluster 모드**: 드라이버도 Kubernetes Pod으로 생성되며, 익스큐터 Pod들을 직접 관리한다.

프로덕션 환경에서는 대부분 **Cluster 모드**를 사용한다. 드라이버가 클러스터 안에 있으므로 네트워크 지연이 줄고, 제출 클라이언트가 죽어도 잡이 영향을 받지 않는다.

```
[spark-submit] 
     │
     ▼
[Kubernetes API Server]
     │ creates
     ▼
[Driver Pod]
     │ creates via K8s API
     ├──▶ [Executor Pod 1]
     ├──▶ [Executor Pod 2]
     └──▶ [Executor Pod N]
```

### 리소스 격리의 세 가지 레이어

Spark on Kubernetes에서 리소스 격리는 세 레이어에서 이루어진다.

1. **Namespace 수준**: 팀/프로젝트별로 Namespace를 분리하고 ResourceQuota를 적용한다.
2. **Pod 수준**: 드라이버/익스큐터 Pod에 `resources.requests`와 `resources.limits`를 명시한다.
3. **JVM 수준**: Spark의 `spark.executor.memory`, `spark.executor.memoryOverhead` 등을 통해 JVM 힙과 오프힙 메모리를 제어한다.

이 세 레이어가 서로 맞지 않으면 OOMKilled나 CPU Throttling 문제가 발생한다.

---

## 실전 예제

### 1. Namespace와 ResourceQuota 설정

멀티테넌트 Spark 환경에서는 팀별로 Namespace를 분리하고 쿼터를 설정하는 것이 기본이다.

```yaml
# namespace-spark-team-a.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: spark-team-a
  labels:
    team: team-a
---
apiVersion: v1
kind: ResourceQuota
metadata:
  name: spark-quota
  namespace: spark-team-a
spec:
  hard:
    requests.cpu: "40"
    requests.memory: 160Gi
    limits.cpu: "80"
    limits.memory: 320Gi
    pods: "50"
---
apiVersion: v1
kind: LimitRange
metadata:
  name: spark-limit-range
  namespace: spark-team-a
spec:
  limits:
  - type: Pod
    max:
      cpu: "16"
      memory: 64Gi
    min:
      cpu: "1"
      memory: 1Gi
```

### 2. ServiceAccount와 RBAC 설정

드라이버 Pod이 Kubernetes API를 통해 익스큐터 Pod을 생성/삭제하려면 적절한 RBAC 권한이 필요하다.

```yaml
# spark-rbac.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: spark-driver
  namespace: spark-team-a
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: spark-role
  namespace: spark-team-a
rules:
- apiGroups: [""]
  resources: ["pods", "services", "configmaps", "persistentvolumeclaims"]
  verbs: ["create", "get", "list", "watch", "delete", "patch"]
- apiGroups: [""]
  resources: ["pods/log"]
  verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: spark-role-binding
  namespace: spark-team-a
subjects:
- kind: ServiceAccount
  name: spark-driver
  namespace: spark-team-a
roleRef:
  kind: Role
  name: spark-role
  apiGroup: rbac.authorization.k8s.io
```

### 3. Spark 잡 제출 설정

실제 잡을 제출할 때의 핵심 설정이다. 메모리 설정은 특히 중요한데, Kubernetes의 `limits.memory`는 JVM 힙 + 오프힙 + memoryOverhead를 모두 합산한 값보다 커야 한다.

```bash
spark-submit \
  --master k8s://https://<k8s-api-server>:6443 \
  --deploy-mode cluster \
  --name spark-etl-job \
  --class com.example.SparkETLJob \
  --conf spark.kubernetes.namespace=spark-team-a \
  --conf spark.kubernetes.authenticate.driver.serviceAccountName=spark-driver \
  --conf spark.kubernetes.container.image=my-registry/spark:3.4.1 \
  # 드라이버 리소스
  --conf spark.driver.cores=2 \
  --conf spark.driver.memory=4g \
  --conf spark.driver.memoryOverhead=1g \
  --conf spark.kubernetes.driver.limit.cores=4 \
  --conf spark.kubernetes.driver.request.cores=2 \
  # 익스큐터 리소스
  --conf spark.executor.instances=10 \
  --conf spark.executor.cores=4 \
  --conf spark.executor.memory=8g \
  --conf spark.executor.memoryOverhead=2g \
  --conf spark.kubernetes.executor.limit.cores=4 \
  --conf spark.kubernetes.executor.request.cores=4 \
  # 동적 할당
  --conf spark.dynamicAllocation.enabled=true \
  --conf spark.dynamicAllocation.minExecutors=2 \
  --conf spark.dynamicAllocation.maxExecutors=20 \
  --conf spark.dynamicAllocation.shuffleTracking.enabled=true \
  # 노드 어피니티 및 톨러레이션
  --conf spark.kubernetes.node.selector.workload-type=spark \
  --conf spark.kubernetes.executor.annotation.cluster-autoscaler.kubernetes.io/safe-to-evict=false \
  local:///opt/spark/jars/spark-etl-job.jar
```

### 4. 메모리 계산 공식

Kubernetes Pod에 설정해야 할 실제 메모리 limits를 계산하는 공식이다.

```
Pod Memory Limit >= spark.executor.memory 
                  + spark.executor.memoryOverhead
                  + spark.memory.offHeap.size (오프힙 사용 시)

예시:
  spark.executor.memory = 8g
  spark.executor.memoryOverhead = 2g (기본값: max(executor.memory * 0.1, 384MB))
  
  → Pod Memory Limit >= 10g (여유분 포함 시 10~11g 권장)
```

### 5. PriorityClass를 활용한 잡 우선순위 설정

배치 잡과 대화형 쿼리(예: Zeppelin, Jupyter)가 같은 클러스터를 사용하는 경우, PriorityClass로 우선순위를 명시할 수 있다.

```yaml
# priority-classes.yaml
apiVersion: scheduling.k8s.io/v1
kind: PriorityClass
metadata:
  name: spark-interactive-high
value: 1000
globalDefault: false
description: "대화형 Spark 쿼리 - 높은 우선순위"
---
apiVersion: scheduling.k8s.io/v1
kind: PriorityClass
metadata:
  name: spark-batch-low
value: 100
globalDefault: false
description: "배치 Spark 잡 - 낮은 우선순위"
```

```bash
# 잡 제출 시 PriorityClass 적용
--conf spark.kubernetes.driver.priorityClassName=spark-interactive-high \
--conf spark.kubernetes.executor.priorityClassName=spark-interactive-high
```

### 6. Spark Operator 활용

반복적으로 잡을 제출하거나 스케줄링이 필요한 경우, [spark-on-k8s-operator](https://github.com/GoogleCloudPlatform/spark-on-k8s-operator)를 활용하면 SparkApplication CRD로 선언적으로 관리할 수 있다.

```yaml
# spark-application.yaml
apiVersion: sparkoperator.k8s.io/v1beta2
kind: SparkApplication
metadata:
  name: spark-etl-job
  namespace: spark-team-a
spec:
  type: Scala
  mode: cluster
  image: my-registry/spark:3.4.1
  imagePullPolicy: Always
  mainClass: com.example.SparkETLJob
  mainApplicationFile: local:///opt/spark/jars/spark-etl-job.jar
  sparkVersion: "3.4.1"
  driver:
    cores: 2
    coreLimit: "4000m"
    memory: "4096m"
    memoryOverhead: "1024m"
    serviceAccount: spark-driver
    labels:
      version: "3.4.1"
  executor:
    cores: 4
    instances: 5
    memory: "8192m"
    memoryOverhead: "2048m"
    labels:
      version: "3.4.1"
  dynamicAllocation:
    enabled: true
    initialExecutors: 2
    minExecutors: 2
    maxExecutors: 20
  monitoring:
    exposeDriverMetrics: true
    exposeExecutorMetrics: true
    prometheus:
      jmxExporterJar: /prometheus/jmx_prometheus_javaagent.jar
      port: 8090
```

---

## 주의사항 및 트레이드오프

### Requests vs Limits: 어떻게 설정할 것인가

CPU와 메모리에 대해 `requests`와 `limits`를 어떻게 설정하느냐에 따라 동작이 크게 달라진다.

| 설정 | 동작 | 권장 시나리오 |
|------|------|---------------|
| requests == limits | Guaranteed QoS. 스케줄링 안정적, 노드 리소스 낭비 가능 | SLA가 엄격한 운영 잡 |
| requests < limits | Burstable QoS. 유연하지만 노이즈 이웃 문제 발생 가능 | 개발/테스트 환경 |
| limits 없음 | BestEffort QoS. OOM 위험 높음 | 권장하지 않음 |

**CPU의 경우**: `limits`를 너무 타이트하게 잡으면 CPU Throttling이 발생한다. Spark 익스큐터는 GC 등으로 순간적인 CPU 스파이크가 발생하므로, `limits`를 `requests`의 1.5~2배로 설정하는 것이 일반적이다.

**메모리의 경우**: 메모리는 압축이 불가능한 리소스다. `limits`를 초과하면 즉시 OOMKilled된다. 따라서 `requests`와 `limits`를 동일하게 설정(Guaranteed)하거나, 계산된 메모리 총합에 10~20% 여유를 두는 것이 안전하다.

### 동적 할당(Dynamic Allocation)의 함정

`dynamicAllocation.enabled=true`와 Kubernetes를 함께 사용할 때 주의할 점이 있다. 기본적으로 동적 할당은 External Shuffle Service를 필요로 하는데, Kubernetes 환경에서는 이를 운영하기 복잡하다.

대신 **Shuffle Tracking** 기능(`spark.dynamicAllocation.shuffleTracking.enabled=true`)을 사용하면 External Shuffle Service 없이도 동적 할당이 가능하지만, 셔플 데이터를 보유한 익스큐터는 즉시 해제되지 않아 메모리 회수가 지연될 수 있다.

### 노드 격리 전략

Spark 잡은 리소스 집약적이기 때문에, 가능하면 **전용 노드 풀**을 구성하는 것이 좋다.

```yaml
# Spark 전용 노드에 Taint 설정
kubectl taint nodes spark-node-1 workload=spark:NoSchedule

# 잡 제출 시 Toleration 설정
--conf spark.kubernetes.driver.tolerations=[{"key":"workload","operator":"Equal","value":"spark","effect":"NoSchedule"}]
--conf spark.kubernetes.executor.tolerations=[{"key":"workload","operator":"Equal","value":"spark","effect":"NoSchedule"}]
```

이렇게 하면 Spark Pod 외의 일반 워크로드가 Spark 노드에 스케줄되지 않아 리소스 간섭을 최소화할 수 있다.

### Shuffle Storage 문제

Kubernetes 환경에서 셔플 데이터는 기본적으로 익스큐터 Pod의 로컬 디렉터리에 저장된다. Pod이 재시작되거나 선점(preemption)되면 셔플 데이터가 유실되어 잡 전체가 재시도된다.

이를 완화하기 위해 **Persistent Volume**을 익스큐터에 마운트하거나, **Remote Shuffle Service**(예: Apache Uniffle, Celeborn)를 도입하는 방법을 검토할 필요가 있다. 다만 이는 운영 복잡도를 높이므로, 잡의 실행 시간과 셔플 데이터 크기를 기준으로 도입 여부를 판단해야 한다.

---

## 정리

Spark on Kubernetes는 유연한 리소스 관리와 클라우드 네이티브 생태계와의 통합이라는 강력한 장점을 제공하지만, 그만큼 고려해야 할 설정 포인트가 많다.

핵심을 요약하면 다음과 같다.

- **Namespace + ResourceQuota**로 팀/프로젝트 수준의 리소스 상한을 설정한다.
- **메모리 계산**은 JVM 힙, memoryOverhead, 오프힙을 모두 합산해 Pod limits에 반영한다.
- **CPU requests와 limits**는 스파이크를 고려해 적절한 버퍼를 둔다.
- **동적 할당**은 ShuffleTracking을 통해 활성화하고, 셔플 데이터 유실 리스크를 인지한다.
- **노드 Taint/Toleration**으로 Spark 전용 노드를 격리해 노이즈 이웃 문제를 방지한다.
- **Spark Operator**를 활용하면 잡 관리와 모니터링이 훨씬 수월해진다.

운영 초기에는 설정이 복잡하게 느껴질 수 있지만, 이 레이어들을 체계적으로 구성해 두면 Spark 워크로드를 Kubernetes 위에서 안정적이고 효율적으로 운영할 수 있다.
