# GitOps 원칙과 ArgoCD를 통한 배포 자동화

## 개요

현대 클라우드 네이티브 환경에서 인프라와 애플리케이션 배포의 복잡도는 계속 증가하고 있습니다. 수십 개의 마이크로서비스, 멀티 클러스터 환경, 잦은 릴리스 사이클 속에서 **일관성 있고 추적 가능한 배포 파이프라인**은 선택이 아닌 필수가 되었습니다.

GitOps는 이 문제를 해결하기 위해 Git을 **단일 진실의 원천(Single Source of Truth)** 으로 삼는 운영 방식입니다. 그리고 ArgoCD는 GitOps 원칙을 Kubernetes 환경에서 실현해주는 대표적인 CD(Continuous Delivery) 도구입니다.

이 포스팅에서는 GitOps의 핵심 원칙을 정리하고, ArgoCD를 실제로 구성하여 배포 자동화를 구현하는 방법을 실무 예제와 함께 살펴보겠습니다.

---

## GitOps 핵심 개념

### GitOps란 무엇인가

GitOps는 Weaveworks가 2017년에 제안한 개념으로, 다음 네 가지 원칙을 핵심으로 합니다.

1. **선언적(Declarative)**: 시스템의 상태는 선언적으로 기술되어야 한다.
2. **버전 관리(Versioned & Immutable)**: 원하는 상태는 Git에 버전으로 관리되며 불변성을 유지한다.
3. **자동 Pull(Pulled automatically)**: 승인된 상태 변경은 자동으로 시스템에 적용된다.
4. **지속적 검증(Continuously reconciled)**: 소프트웨어 에이전트가 실제 상태와 원하는 상태의 차이를 감지하고 자동으로 조정한다.

### 기존 Push 방식 vs GitOps Pull 방식

| 구분 | Push 방식 (기존 CI/CD) | Pull 방식 (GitOps) |
|------|----------------------|-------------------|
| 배포 트리거 | CI 파이프라인이 클러스터에 직접 push | 에이전트가 Git 변경을 감지하여 pull |
| 클러스터 접근 | 외부 시스템이 kubeconfig 보유 | 클러스터 내부 에이전트만 접근 |
| 감사 추적 | 파이프라인 로그에 분산 | Git 커밋 히스토리로 일원화 |
| 드리프트 감지 | 수동 또는 별도 도구 필요 | 자동으로 감지 및 조정 |
| 롤백 | 파이프라인 재실행 필요 | `git revert` 또는 `git checkout` |

### ArgoCD의 아키텍처

ArgoCD는 크게 세 가지 컴포넌트로 구성됩니다.

- **API Server**: gRPC/REST 인터페이스를 제공하며 Web UI, CLI, CI/CD 시스템과 통신
- **Repository Server**: Git 저장소를 클론하고 Kubernetes 매니페스트를 생성
- **Application Controller**: 실제 클러스터 상태를 모니터링하고 Git의 원하는 상태와 동기화

---

## 실전 예제: ArgoCD 설치 및 배포 자동화 구성

### 1. ArgoCD 설치

```bash
# ArgoCD 네임스페이스 생성 및 설치
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# 초기 admin 비밀번호 확인
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath="{.data.password}" | base64 -d && echo

# 포트 포워딩으로 UI 접근
kubectl port-forward svc/argocd-server -n argocd 8080:443
```

### 2. Git 저장소 구조 설계

GitOps를 효과적으로 운영하기 위해 **App of Apps 패턴** 또는 **환경별 디렉토리 분리** 구조를 권장합니다.

```
gitops-repo/
├── apps/                    # App of Apps 루트
│   ├── dev/
│   │   └── applications.yaml
│   ├── staging/
│   │   └── applications.yaml
│   └── production/
│       └── applications.yaml
├── services/                # 개별 서비스 매니페스트
│   ├── order-service/
│   │   ├── base/
│   │   │   ├── deployment.yaml
│   │   │   ├── service.yaml
│   │   │   └── kustomization.yaml
│   │   └── overlays/
│   │       ├── dev/
│   │       │   └── kustomization.yaml
│   │       ├── staging/
│   │       │   └── kustomization.yaml
│   │       └── production/
│   │           └── kustomization.yaml
└── infrastructure/          # 공통 인프라 (Ingress, Cert-Manager 등)
    └── ...
```

### 3. Kustomize를 활용한 환경별 매니페스트 관리

**base/deployment.yaml**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: order-service
  labels:
    app: order-service
spec:
  replicas: 1
  selector:
    matchLabels:
      app: order-service
  template:
    metadata:
      labels:
        app: order-service
    spec:
      containers:
        - name: order-service
          image: myregistry/order-service:latest
          ports:
            - containerPort: 8080
          env:
            - name: SPRING_PROFILES_ACTIVE
              value: "default"
          resources:
            requests:
              memory: "256Mi"
              cpu: "250m"
            limits:
              memory: "512Mi"
              cpu: "500m"
```

**overlays/production/kustomization.yaml**
```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: production

resources:
  - ../../base

images:
  - name: myregistry/order-service
    newTag: "v1.5.2"  # 이미지 태그는 CI에서 자동 업데이트

patches:
  - patch: |-
      - op: replace
        path: /spec/replicas
        value: 3
      - op: replace
        path: /spec/template/spec/containers/0/env/0/value
        value: "production"
    target:
      kind: Deployment
      name: order-service

  - patch: |-
      - op: replace
        path: /spec/template/spec/containers/0/resources/requests/memory
        value: "512Mi"
      - op: replace
        path: /spec/template/spec/containers/0/resources/limits/memory
        value: "1Gi"
    target:
      kind: Deployment
      name: order-service
```

### 4. ArgoCD Application 정의

**App of Apps 패턴을 활용한 프로덕션 Application 설정**

```yaml
# apps/production/applications.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: production-apps
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: production
  source:
    repoURL: https://github.com/myorg/gitops-repo.git
    targetRevision: main
    path: apps/production
  destination:
    server: https://kubernetes.default.svc
    namespace: argocd
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
---
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: order-service-production
  namespace: argocd
spec:
  project: production
  source:
    repoURL: https://github.com/myorg/gitops-repo.git
    targetRevision: main
    path: services/order-service/overlays/production
  destination:
    server: https://kubernetes.default.svc
    namespace: production
  syncPolicy:
    automated:
      prune: true
      selfHeal: true    # 수동 변경 감지 시 자동 복원
    retry:
      limit: 5
      backoff:
        duration: 5s
        factor: 2
        maxDuration: 3m
  revisionHistoryLimit: 10
```

### 5. CI 파이프라인과 GitOps 연동

CI 파이프라인은 **이미지 빌드 및 태그 업데이트까지만** 담당하고, 실제 배포는 ArgoCD에 위임합니다.

**GitHub Actions 예제**

```yaml
# .github/workflows/ci-gitops.yaml
name: CI - Build and Update GitOps Repo

on:
  push:
    branches: [main]
    paths:
      - 'src/**'

jobs:
  build-and-update:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout source
        uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Login to Registry
        uses: docker/login-action@v3
        with:
          registry: myregistry
          username: ${{ secrets.REGISTRY_USER }}
          password: ${{ secrets.REGISTRY_PASSWORD }}

      - name: Build and Push Image
        id: docker_build
        uses: docker/build-push-action@v5
        with:
          push: true
          tags: myregistry/order-service:${{ github.sha }}

      - name: Update GitOps Repository
        env:
          GIT_TOKEN: ${{ secrets.GITOPS_TOKEN }}
          IMAGE_TAG: ${{ github.sha }}
        run: |
          git clone https://x-access-token:${GIT_TOKEN}@github.com/myorg/gitops-repo.git
          cd gitops-repo

          # Kustomize로 이미지 태그 업데이트
          cd services/order-service/overlays/staging
          kustomize edit set image myregistry/order-service:${IMAGE_TAG}

          git config user.email "ci-bot@myorg.com"
          git config user.name "CI Bot"
          git add .
          git commit -m "chore: update order-service image to ${IMAGE_TAG}"
          git push origin main
```

---

## 주의사항 및 트레이드오프

### 1. Secret 관리 전략

Git에 민감 정보를 직접 커밋하는 것은 절대 금지입니다. 다음 세 가지 방식 중 환경에 맞게 선택해야 합니다.

- **Sealed Secrets**: Bitnami의 `kubeseal`을 사용해 암호화된 시크릿을 Git에 저장
- **External Secrets Operator**: AWS Secrets Manager, Vault 등 외부 저장소와 연동
- **SOPS (Secrets OPerationS)**: Mozilla SOPS로 파일 자체를 암호화

```bash
# Sealed Secrets 예시
kubeseal --format=yaml \
  --cert=https://sealed-secrets.myorg.com/v1/cert.pem \
  < secret.yaml > sealed-secret.yaml
# sealed-secret.yaml은 Git에 안전하게 커밋 가능
```

### 2. selfHeal과 수동 개입의 균형

`selfHeal: true` 설정은 드리프트를 자동으로 복원하지만, **긴급 패치나 디버깅 시 수동 변경이 즉시 롤백**되는 문제가 있습니다. 운영 환경에서는 다음을 고려하세요.

- 긴급 상황 시 해당 Application의 자동 동기화를 일시적으로 비활성화
- ArgoCD의 **Sync Window** 기능으로 특정 시간대에만 자동 동기화 허용
- 프로덕션 환경은 `automated` 대신 수동 승인 후 동기화하는 방식 검토

### 3. 멀티 클러스터 환경에서의 복잡도

ArgoCD 하나로 멀티 클러스터를 관리할 수 있지만, 클러스터 수가 늘어날수록 **ArgoCD 자체의 고가용성**과 **ApplicationSet**을 활용한 템플릿화가 중요해집니다.

```yaml
# ApplicationSet으로 멀티 클러스터 배포 자동화
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: order-service-multicluster
spec:
  generators:
    - clusters:
        selector:
          matchLabels:
            environment: production
  template:
    metadata:
      name: 'order-service-{{name}}'
    spec:
      source:
        repoURL: https://github.com/myorg/gitops-repo.git
        path: services/order-service/overlays/production
        targetRevision: main
      destination:
        server: '{{server}}'
        namespace: production
```

### 4. 모노레포 vs 폴리레포

| 방식 | 장점 | 단점 |
|------|------|------|
| 모노레포 | 변경 추적 용이, 단순한 권한 관리 | 저장소 규모 증가, 불필요한 ArgoCD 동기화 |
| 폴리레포 | 서비스별 독립적 배포, 명확한 소유권 | 저장소 간 버전 관리 복잡 |

실무에서는 인프라 레포와 서비스 레포를 분리하되, 서비스는 팀 단위로 레포를 운영하는 **하이브리드** 방식이 많이 사용됩니다.

---

## 정리

GitOps와 ArgoCD의 조합은 단순한 배포 자동화를 넘어 **운영의 패러다임 전환**을 의미합니다.

| 핵심 포인트 | 내용 |
|------------|------|
| 단일 진실의 원천 | Git 커밋 히스토리가 곧 배포 히스토리 |
| 보안 강화 | 외부 시스템의 클러스터 접근 불필요 |
| 빠른 롤백 | `git revert` 한 줄로 이전 상태 복원 |
| 드리프트 자동 감지 | 수동 변경도 즉시 감지하여 일관성 유지 |

도입 시 처음부터 완벽한 구조를 갖추려 하기보다는 **단일 클러스터, 단일 서비스**부터 시작하여 팀의 GitOps 성숙도를 점진적으로 높여나가는 것을 권장합니다. Secret 관리 전략을 먼저 확립하고, RBAC 및 ArgoCD Project를 통한 권한 분리를 초기에 설계하는 것이 장기적으로 운영 부담을 줄이는 핵심입니다.

GitOps는 도구의 문제가 아닌 **팀의 협업 방식과 문화의 문제**입니다. ArgoCD는 훌륭한 도구이지만, Git을 통한 리뷰 프로세스와 팀 간의 명확한 책임 경계가 함께 갖춰질 때 진정한 가치를 발휘합니다.