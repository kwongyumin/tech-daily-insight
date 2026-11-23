# Kubernetes CSI 스토리지 프로비저닝 이해하기

## 개요

Kubernetes 클러스터에서 애플리케이션을 운영하다 보면 반드시 마주치는 문제 중 하나가 **퍼시스턴트 스토리지 관리**입니다. StatefulSet으로 데이터베이스를 운영하든, 파일 업로드 서버를 구성하든, 로그 수집 파이프라인을 구축하든 스토리지는 필수 요소입니다.

Kubernetes 초기에는 스토리지 드라이버가 쿠버네티스 코어 소스에 직접 포함되어 있었습니다. NFS, iSCSI, GCEPersistentDisk 같은 볼륨 플러그인이 `in-tree` 방식으로 관리되었는데, 이 방식은 치명적인 단점이 있었습니다. 새로운 스토리지 벤더가 Kubernetes를 지원하려면 Kubernetes 릴리즈 사이클에 맞춰 코어 코드베이스에 직접 기여해야 했고, 버그 수정조차 Kubernetes 릴리즈를 기다려야 했습니다.

이 문제를 해결하기 위해 등장한 것이 **CSI(Container Storage Interface)**입니다. CSI는 컨테이너 오케스트레이터와 스토리지 시스템 사이의 표준 인터페이스를 정의합니다. 2019년 Kubernetes 1.13에서 GA(Generally Available)로 승격된 이후, 현재 거의 모든 클라우드 스토리지 솔루션이 CSI 드라이버를 제공합니다.

이 포스팅에서는 CSI의 아키텍처와 프로비저닝 흐름을 깊이 있게 살펴보고, 실무에서 바로 활용할 수 있는 설정 예제를 함께 다룹니다.

---

## 핵심 개념

### CSI 아키텍처 구성 요소

CSI는 크게 두 가지 컴포넌트로 구성됩니다.

**1. Controller Plugin (ControllerServer)**

클러스터 레벨에서 볼륨의 생성/삭제/스냅샷 등을 관리합니다. 주로 `Deployment` 또는 `StatefulSet`으로 배포되며, 외부 프로비저너(external-provisioner), 외부 어태처(external-attacher) 같은 사이드카 컨테이너와 함께 동작합니다.

**2. Node Plugin (NodeServer)**

각 노드에서 볼륨 마운트/언마운트를 담당합니다. `DaemonSet`으로 배포되어 모든 노드에 존재해야 합니다. Node Driver Registrar 사이드카가 kubelet에 CSI 드라이버를 등록합니다.

### 볼륨 라이프사이클

CSI 프로비저닝은 다음 단계로 이루어집니다.

```
PVC 생성 → Provision (볼륨 생성) → Attach (노드에 연결) → Mount (파드 내 마운트)
파드 종료 → Unmount → Detach → (필요 시) Delete (볼륨 삭제)
```

각 단계는 CSI gRPC 인터페이스의 메서드와 직접 대응됩니다.

| 단계 | CSI 메서드 | 담당 |
|------|-----------|------|
| Provision | CreateVolume | Controller Plugin |
| Attach | ControllerPublishVolume | Controller Plugin |
| Mount | NodeStageVolume / NodePublishVolume | Node Plugin |
| Unmount | NodeUnpublishVolume / NodeUnstageVolume | Node Plugin |
| Detach | ControllerUnpublishVolume | Controller Plugin |
| Delete | DeleteVolume | Controller Plugin |

### StorageClass와 Dynamic Provisioning

**Static Provisioning**은 관리자가 PV를 미리 생성하는 방식이고, **Dynamic Provisioning**은 PVC가 생성될 때 자동으로 PV와 실제 볼륨이 생성되는 방식입니다. 실무에서는 Dynamic Provisioning이 훨씬 일반적으로 사용됩니다.

Dynamic Provisioning을 위해서는 `StorageClass`가 필요합니다. StorageClass의 `provisioner` 필드가 사용할 CSI 드라이버를 지정합니다.

---

## 실전 예제

### 1. EBS CSI 드라이버를 이용한 StorageClass 구성 (AWS 환경)

AWS EKS 환경에서 EBS CSI 드라이버를 설치한 후, StorageClass를 정의합니다.

```yaml
# storageclass-ebs-gp3.yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: ebs-gp3
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: ebs.csi.aws.com
volumeBindingMode: WaitForFirstConsumer  # 파드가 스케줄될 노드의 AZ에 맞춰 볼륨 생성
reclaimPolicy: Retain                    # PVC 삭제 시 볼륨 보존
parameters:
  type: gp3
  iops: "3000"
  throughput: "125"
  encrypted: "true"
allowVolumeExpansion: true               # 볼륨 크기 확장 허용
```

```bash
kubectl apply -f storageclass-ebs-gp3.yaml
kubectl get storageclass
```

### 2. PVC와 파드 연결

```yaml
# pvc-example.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: app-data-pvc
  namespace: production
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: ebs-gp3
  resources:
    requests:
      storage: 20Gi
```

```yaml
# deployment-with-pvc.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app-server
  namespace: production
spec:
  replicas: 1
  selector:
    matchLabels:
      app: app-server
  template:
    metadata:
      labels:
        app: app-server
    spec:
      containers:
        - name: app
          image: my-app:latest
          volumeMounts:
            - name: app-data
              mountPath: /var/lib/app/data
      volumes:
        - name: app-data
          persistentVolumeClaim:
            claimName: app-data-pvc
```

```bash
kubectl apply -f pvc-example.yaml
kubectl apply -f deployment-with-pvc.yaml

# PVC 상태 확인 (Bound가 될 때까지 대기)
kubectl get pvc -n production -w
```

### 3. VolumeSnapshotClass와 스냅샷 활용

CSI의 강력한 기능 중 하나는 볼륨 스냅샷입니다. 운영 환경에서 데이터베이스 백업이나 블루/그린 배포 시 활용할 수 있습니다.

```yaml
# volumesnapshotclass.yaml
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshotClass
metadata:
  name: ebs-vsc
driver: ebs.csi.aws.com
deletionPolicy: Retain
parameters:
  tagSpecification_1: "Name={{.VolumeSnapshotNamespace}}/{{.VolumeSnapshotName}}"
```

```yaml
# volumesnapshot.yaml
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata:
  name: app-data-snapshot-20240101
  namespace: production
spec:
  volumeSnapshotClassName: ebs-vsc
  source:
    persistentVolumeClaimName: app-data-pvc
```

```bash
# 스냅샷 상태 확인
kubectl get volumesnapshot -n production
kubectl describe volumesnapshot app-data-snapshot-20240101 -n production
```

스냅샷으로부터 새 PVC를 복원할 수도 있습니다.

```yaml
# pvc-from-snapshot.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: app-data-restored
  namespace: production
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: ebs-gp3
  resources:
    requests:
      storage: 20Gi
  dataSource:
    name: app-data-snapshot-20240101
    kind: VolumeSnapshot
    apiGroup: snapshot.storage.k8s.io
```

### 4. 볼륨 용량 확장

`allowVolumeExpansion: true`로 설정된 StorageClass를 사용하면 PVC의 용량을 온라인 확장할 수 있습니다.

```bash
# 현재 PVC 용량 확인
kubectl get pvc app-data-pvc -n production

# PVC 스펙 편집 (storage 값을 20Gi → 50Gi로 수정)
kubectl edit pvc app-data-pvc -n production
```

```yaml
# 편집할 부분
spec:
  resources:
    requests:
      storage: 50Gi  # 20Gi에서 변경
```

```bash
# 확장 상태 확인
kubectl describe pvc app-data-pvc -n production
# Conditions 항목에서 FileSystemResizePending → 완료 여부 확인
```

---

## 주의사항 및 트레이드오프

### AccessMode 선택의 함정

CSI 드라이버마다 지원하는 AccessMode가 다릅니다. EBS처럼 블록 스토리지 기반의 드라이버는 `ReadWriteOnce(RWO)`만 지원하는 경우가 많습니다. 여러 파드에서 동시에 마운트해야 한다면 EFS(NFS 기반) 또는 `ReadWriteMany(RWX)`를 지원하는 드라이버를 사용해야 합니다.

```bash
# 드라이버가 지원하는 기능 확인
kubectl get csidriver ebs.csi.aws.com -o yaml
```

RWX가 필요한 경우 NFS 기반 솔루션은 성능 오버헤드가 있으므로, 실제로 다수의 파드가 동시에 쓰기 작업을 해야 하는지, 읽기 공유만으로 충분한지를 먼저 검토해야 합니다.

### volumeBindingMode 선택

| 모드 | 동작 | 적합한 상황 |
|------|------|------------|
| `Immediate` | PVC 생성 즉시 볼륨 프로비저닝 | 단일 AZ 환경, 특정 존 고정 필요 시 |
| `WaitForFirstConsumer` | 파드 스케줄 이후 프로비저닝 | 멀티 AZ 환경 (기본 권장) |

멀티 AZ 환경에서 `Immediate` 모드를 사용하면 볼륨이 생성된 AZ와 파드가 스케줄된 AZ가 다를 수 있어 파드가 영구적으로 Pending 상태에 빠질 수 있습니다. **멀티 AZ 클러스터에서는 반드시 `WaitForFirstConsumer`를 사용하세요.**

### reclaimPolicy와 데이터 보호

`reclaimPolicy`의 기본값은 `Delete`입니다. PVC가 삭제되면 실제 볼륨도 함께 삭제됩니다. 프로덕션 환경에서 중요한 데이터를 담는 PVC에는 반드시 `Retain`을 사용해야 합니다.

```bash
# 기존 PV의 reclaim policy를 변경하는 경우 (StorageClass 변경은 PV에 미적용)
kubectl patch pv <pv-name> -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'
```

단, `Retain` 정책은 PVC 삭제 후 PV가 `Released` 상태로 남으며, 자동 재사용되지 않습니다. 볼륨 재활용이 필요한 경우 별도 운영 프로세스가 필요합니다.

### CSI 드라이버 버전 호환성

Kubernetes 버전과 CSI 드라이버 버전 사이의 호환성을 반드시 확인해야 합니다. 특히 VolumeSnapshot 기능은 별도의 `snapshot-controller`와 CRD 설치가 필요하며, 버전 미스매치 시 스냅샷 작업이 조용히 실패하거나 오류를 발생시킬 수 있습니다.

```bash
# 설치된 CSI 드라이버 목록 확인
kubectl get csidrivers

# snapshot-controller 동작 여부 확인
kubectl get pods -n kube-system | grep snapshot-controller

# VolumeSnapshot CRD 설치 여부 확인
kubectl get crd | grep snapshot
```

### 스토리지 성능 모니터링

CSI 드라이버는 볼륨 성능 메트릭을 kubelet을 통해 노출합니다. Prometheus + Grafana 환경에서는 `kubelet_volume_stats_*` 메트릭으로 용량 사용량 및 I/O 현황을 모니터링할 수 있습니다.

```yaml
# PrometheusRule 예시 - 볼륨 용량 80% 초과 시 경보
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: pvc-capacity-alert
spec:
  groups:
    - name: storage
      rules:
        - alert: PVCUsageHigh
          expr: |
            (kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes) > 0.8
          for: 5m
          labels:
            severity: warning
          annotations:
            summary: "PVC {{ $labels.persistentvolumeclaim }} 용량 80% 초과"
```

---

## 정리

CSI는 Kubernetes 스토리지 생태계를 획기적으로 발전시킨 표준입니다. 핵심 내용을 정리하면 다음과 같습니다.

- **CSI = 표준 인터페이스**: 스토리지 벤더가 Kubernetes 코어와 독립적으로 드라이버를 개발·배포할 수 있게 합니다.
- **Controller + Node 이중 구조**: 클러스터 레벨 작업(프로비저닝)과 노드 레벨 작업(마운트)이 명확히 분리됩니다.
- **Dynamic Provisioning + StorageClass**: 운영 편의성을 극대화하며, `volumeBindingMode`와 `reclaimPolicy` 설정이 운영 안정성을 결정합니다.
- **스냅샷 활용**: CSI VolumeSnapshot은 데이터 보호와 복원 전략의 중요한 수단입니다.

스토리지는 한번 잘못 설계하면 데이터 손실이나 서비스 중단으로 이어지는 민감한 영역입니다. AccessMode, bindingMode, reclaimPolicy 세 가지만 제대로 이해하고 설정해도 대부분의 운영 사고를 예방할 수 있습니다. 클러스터 규모가 커질수록 스토리지 모니터링과 용량 계획도 함께 고려하는 것을 강력히 권장합니다.
