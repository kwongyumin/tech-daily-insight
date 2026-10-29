# 멀티 AZ Kubernetes 클러스터로 초고가용성 구성하기

## 개요

서비스 장애는 언제나 예고 없이 찾아온다. 단일 데이터센터 또는 단일 가용 영역(Availability Zone, AZ)에 모든 워크로드를 배치했을 때, 해당 AZ에 전력 장애나 네트워크 이슈가 발생하면 서비스 전체가 다운된다. 이 문제를 해결하기 위한 가장 일반적인 접근법이 바로 **멀티 AZ(Multi-AZ) 구성**이다.

Kubernetes는 클러스터 자체가 선언적 배포와 자동 복구를 지원하기 때문에, 멀티 AZ 환경과 결합하면 강력한 고가용성(High Availability, HA)을 달성할 수 있다. 하지만 단순히 여러 AZ에 노드를 분산시키는 것만으로는 충분하지 않다. Control Plane 이중화, Pod 분산 전략, 스토리지 처리, 네트워크 레이턴시 관리 등 고려해야 할 요소가 생각보다 많다.

이 글에서는 멀티 AZ Kubernetes 클러스터를 실제로 구성할 때 알아야 할 핵심 개념과 실전 예제를 정리한다.

---

## 핵심 개념

### 1. Control Plane의 이중화

Kubernetes Control Plane(API Server, etcd, Controller Manager, Scheduler)은 클러스터의 뇌(Brain)에 해당한다. 이 컴포넌트들이 단일 AZ에만 존재한다면 해당 AZ 장애 시 클러스터 전체가 마비된다.

- **etcd**: 홀수 개(최소 3개)의 멤버로 구성하며, 각각 다른 AZ에 배치해야 한다. etcd는 Raft 합의 알고리즘을 사용하므로 과반수 노드가 살아있어야 정상 동작한다.
- **API Server**: 무상태(stateless)이므로 여러 AZ에 복수 배치 후 Load Balancer로 앞단에서 분산한다.
- **Controller Manager / Scheduler**: Leader Election을 통해 Active-Standby로 동작하므로, 여러 인스턴스를 배치해두면 자동으로 페일오버된다.

관리형 Kubernetes(EKS, GKE, AKS 등)를 사용하면 Control Plane HA는 클라우드 벤더가 자동으로 처리해준다. 자체 구축(Self-managed) 환경이라면 직접 신경 써야 한다.

### 2. Node 분산과 레이블 전략

워커 노드를 AZ별로 균등하게 분산시키고, 노드에 AZ 정보를 레이블로 태깅해야 이후 Pod 스케줄링 전략을 적용할 수 있다.

```bash
# 노드에 AZ 레이블 확인 (클라우드 환경에서는 자동 태깅됨)
kubectl get nodes --show-labels | grep topology.kubernetes.io/zone
```

클라우드 환경에서는 `topology.kubernetes.io/zone` 레이블이 자동으로 붙는다. 온프레미스라면 수동으로 추가해야 한다.

### 3. Pod Topology Spread Constraints

`topologySpreadConstraints`는 Kubernetes 1.19부터 GA된 기능으로, Pod를 특정 토폴로지(AZ, 노드 등)에 걸쳐 균등하게 분산시키는 선언적 방법이다. 기존의 `podAntiAffinity`보다 더 세밀한 제어가 가능하다.

### 4. 스토리지 고려사항

멀티 AZ 환경에서 가장 까다로운 부분 중 하나가 퍼시스턴트 스토리지다.

- **EBS, Azure Disk** 같은 블록 스토리지는 AZ 로컬이므로, Pod가 다른 AZ로 이동하면 볼륨을 마운트할 수 없다.
- **EFS, NFS, Ceph** 같은 공유 파일시스템은 멀티 AZ에서 접근 가능하지만 레이턴시가 높아질 수 있다.
- 스테이트풀 워크로드는 AZ 장애 시 자동 복구가 어렵다는 점을 항상 염두에 두어야 한다.

---

## 실전 예제

### 예제 1: 멀티 AZ 노드 그룹 구성 (AWS EKS + Terraform)

```hcl
# eks_nodegroup.tf
resource "aws_eks_node_group" "app_nodes" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "app-nodegroup"
  node_role_arn   = aws_iam_role.node.arn

  # 3개의 AZ에 걸쳐 서브넷 지정
  subnet_ids = [
    aws_subnet.private_az1.id,
    aws_subnet.private_az2.id,
    aws_subnet.private_az3.id,
  ]

  scaling_config {
    desired_size = 6  # AZ당 2개
    max_size     = 12
    min_size     = 3
  }

  instance_types = ["m5.xlarge"]

  labels = {
    role = "app"
  }

  tags = {
    "k8s.io/cluster-autoscaler/enabled"              = "true"
    "k8s.io/cluster-autoscaler/${var.cluster_name}"  = "owned"
  }
}
```

### 예제 2: Pod Topology Spread Constraints 적용

아래는 3개의 AZ에 걸쳐 Pod를 균등하게 분산시키는 Deployment 예제다.

```yaml
# deployment-ha.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-server
  namespace: production
spec:
  replicas: 6
  selector:
    matchLabels:
      app: api-server
  template:
    metadata:
      labels:
        app: api-server
    spec:
      # AZ 간 분산 설정
      topologySpreadConstraints:
        - maxSkew: 1                    # AZ 간 Pod 수 최대 편차
          topologyKey: topology.kubernetes.io/zone
          whenUnsatisfiable: DoNotSchedule  # 불가능하면 스케줄링 거부
          labelSelector:
            matchLabels:
              app: api-server
        # 노드 간 분산도 추가
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: ScheduleAnyway  # 불가능하면 최선을 다해 분산
          labelSelector:
            matchLabels:
              app: api-server

      # 동일 노드에 같은 Pod 배치 방지 (보조 장치)
      affinity:
        podAntiAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
            - weight: 100
              podAffinityTerm:
                labelSelector:
                  matchExpressions:
                    - key: app
                      operator: In
                      values:
                        - api-server
                topologyKey: kubernetes.io/hostname

      containers:
        - name: api-server
          image: my-registry/api-server:latest
          ports:
            - containerPort: 8080
          resources:
            requests:
              cpu: "500m"
              memory: "512Mi"
            limits:
              cpu: "1000m"
              memory: "1Gi"
          # 헬스체크 설정
          livenessProbe:
            httpGet:
              path: /actuator/health/liveness
              port: 8080
            initialDelaySeconds: 30
            periodSeconds: 10
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /actuator/health/readiness
              port: 8080
            initialDelaySeconds: 10
            periodSeconds: 5
            failureThreshold: 3
```

### 예제 3: PodDisruptionBudget으로 가용성 보장

노드 드레인이나 클러스터 업그레이드 시 최소 가용 Pod 수를 보장하려면 PDB(PodDisruptionBudget)가 필수다.

```yaml
# pdb.yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: api-server-pdb
  namespace: production
spec:
  # 최소 2/3 이상의 Pod가 항상 Running 상태여야 함
  minAvailable: "67%"
  selector:
    matchLabels:
      app: api-server
```

### 예제 4: Cluster Autoscaler 멀티 AZ 설정

노드 자동 확장 시 특정 AZ에 노드가 편중되지 않도록 설정한다.

```yaml
# cluster-autoscaler-configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: cluster-autoscaler-status
  namespace: kube-system
data:
  nodes.max-node-provision-time: "15m"
  # AZ 간 균형을 맞춰 스케일 아웃
  balance-similar-node-groups: "true"
  # 불필요한 노드 제거 전 대기 시간
  scale-down-delay-after-add: "10m"
  scale-down-unneeded-time: "10m"
```

```yaml
# cluster-autoscaler-deployment.yaml (핵심 args 발췌)
containers:
  - name: cluster-autoscaler
    image: registry.k8s.io/autoscaling/cluster-autoscaler:v1.28.0
    command:
      - ./cluster-autoscaler
      - --v=4
      - --stderrthreshold=info
      - --cloud-provider=aws
      - --skip-nodes-with-local-storage=false
      - --expander=least-waste
      - --balance-similar-node-groups=true    # ← 핵심: AZ 간 균형
      - --node-group-auto-discovery=asg:tag=k8s.io/cluster-autoscaler/enabled,k8s.io/cluster-autoscaler/my-cluster
```

### 예제 5: 스테이트풀 워크로드 – StatefulSet AZ 분산

데이터베이스처럼 상태를 가진 워크로드는 `volumeClaimTemplates`와 함께 AZ 분산을 신중하게 설계해야 한다.

```yaml
# statefulset-redis.yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: redis-cluster
spec:
  serviceName: redis
  replicas: 3
  selector:
    matchLabels:
      app: redis
  template:
    metadata:
      labels:
        app: redis
    spec:
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: topology.kubernetes.io/zone
          whenUnsatisfiable: DoNotSchedule
          labelSelector:
            matchLabels:
              app: redis
      containers:
        - name: redis
          image: redis:7.0
          ports:
            - containerPort: 6379
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: ["ReadWriteOnce"]
        # AZ-aware StorageClass 사용
        storageClassName: gp3-az-aware
        resources:
          requests:
            storage: 10Gi
```

---

## 주의사항 및 트레이드오프

### 1. AZ 간 네트워크 레이턴시와 비용

AZ 간 통신에는 추가 레이턴시(보통 수 ms 수준)와 데이터 전송 비용이 발생한다. 마이크로서비스가 많아질수록 이 비용이 누적되어 의미 있는 수준이 될 수 있다. **서비스 레이턴시에 민감한 컴포넌트는 같은 AZ 내에서 통신하도록 `Topology Aware Routing`을 검토**하는 것이 좋다.

```yaml
# Service에 Topology Aware Routing 활성화
apiVersion: v1
kind: Service
metadata:
  name: api-server-svc
  annotations:
    service.kubernetes.io/topology-mode: "Auto"
spec:
  selector:
    app: api-server
  ports:
    - port: 80
      targetPort: 8080
```

### 2. Cluster Autoscaler와 Spot/Preemptible 인스턴스 혼용 시 주의

Spot 인스턴스는 특정 AZ에서 갑자기 회수될 수 있다. 이 경우 해당 AZ의 노드가 급격히 줄어들면서 Pod가 다른 AZ로 이동해야 하는데, `topologySpreadConstraints`의 `DoNotSchedule` 정책이 걸려 있으면 오히려 스케줄링이 막힐 수 있다. 중요 워크로드는 **On-Demand 노드 그룹과 Spot 노드 그룹을 분리**하고, 각 그룹에 맞는 Taint/Toleration을 적용하는 것이 안전하다.

### 3. PDB와 롤링 업데이트 교착 상태

PDB를 너무 엄격하게 설정하면 노드 드레인이나 롤링 업데이트 중 교착 상태가 발생할 수 있다. 예를 들어 `minAvailable: 100%`로 설정하면 사실상 중단 없는 업데이트가 불가능해진다. 클러스터 운영자와 개발팀이 협의하여 서비스 특성에 맞는 값을 설정해야 한다.

### 4. etcd 성능 저하

자체 구축 클러스터에서 3개 AZ에 etcd를 분산할 경우, AZ 간 RTT(Round Trip Time)가 증가해 etcd 쓰기 성능이 저하될 수 있다. **etcd는 동일 리전 내 AZ에 배치**하고 지연 시간을 모니터링하며, 필요 시 etcd 전용 고성능 SSD 인스턴스를 사용하는 것을 권장한다.

### 5. 멀티 AZ의 한계: 리전 장애

멀티 AZ는 AZ 레벨 장애에는 효과적이지만, 리전 전체 장애에는 대응할 수 없다. 99.99% 이상의 SLA가 필요하다면 **멀티 리전(Multi-Region) 아키텍처**나 **Active-Active 글로벌 배포**를 별도로 고려해야 한다.

---

## 정리

멀티 AZ Kubernetes 클러스터 구성은 단순한 노드 분산 이상의 작업이다. 아래 체크리스트로 구성의 완성도를 점검해보자.

| 항목 | 권장 설정 |
|------|-----------|
| Control Plane | 3개 이상 AZ에 분산 (etcd 홀수 구성) |
| 워커 노드 | AZ별 균등 분산, 레이블 자동 태깅 확인 |
| Pod 스케줄링 | `topologySpreadConstraints` + `podAntiAffinity` 병행 |
| 가용성 보장 | `PodDisruptionBudget` 설정 |
| 자동 확장 | `balance-similar-node-groups: true` |
| 스토리지 | AZ-aware StorageClass or 공유 스토리지 사용 |
| 네트워크 비용 | Topology Aware Routing 적용 검토 |
| 모니터링 | AZ별 노드/Pod 분포 대시보드 구성 |

고가용성은 한 번에 완성되는 것이 아니라, 장애 상황을 시뮬레이션(Chaos Engineering)하고 지속적으로 개선하는 과정이다. Chaos Mesh나 Litmus 같은 도구로 AZ 장애를 주기적으로 테스트하고, 실제 장애 발생 시 서비스가 설계대로 동작하는지 검증하는 습관을 들이는 것이 장기적으로 가장 중요하다.
