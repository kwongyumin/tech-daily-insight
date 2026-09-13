# Kubernetes NetworkPolicy로 파드 간 트래픽 제어

## 개요

Kubernetes 클러스터는 기본적으로 모든 파드가 서로 자유롭게 통신할 수 있는 **플랫 네트워크(Flat Network)** 구조로 동작합니다. 개발 환경에서는 편리하지만, 프로덕션 환경에서는 심각한 보안 위협이 될 수 있습니다. 예를 들어 프론트엔드 파드가 데이터베이스 파드에 직접 접근하거나, 침해된 파드가 클러스터 내부 전체로 lateral movement를 수행하는 시나리오가 가능합니다.

**NetworkPolicy**는 이러한 문제를 해결하기 위해 Kubernetes가 제공하는 네이티브 오브젝트입니다. L3/L4 레벨에서 파드 간 트래픽을 세밀하게 제어할 수 있으며, Zero Trust 네트워크 모델을 클러스터 내부에 적용하는 데 핵심적인 역할을 합니다.

이 글에서는 NetworkPolicy의 핵심 개념을 정리하고, 실무에서 자주 마주치는 패턴을 예제 코드와 함께 살펴봅니다.

---

## 핵심 개념

### NetworkPolicy가 동작하는 방식

NetworkPolicy는 선언적으로 정의되지만, 실제 패킷 필터링은 **CNI(Container Network Interface) 플러그인**이 담당합니다. 즉, NetworkPolicy 오브젝트를 생성하더라도 사용 중인 CNI가 NetworkPolicy를 지원하지 않으면 아무런 효과가 없습니다.

NetworkPolicy를 지원하는 주요 CNI 플러그인:

| CNI 플러그인 | NetworkPolicy 지원 | 특징 |
|---|---|---|
| **Calico** | ✅ | GlobalNetworkPolicy 등 확장 기능 제공 |
| **Cilium** | ✅ | eBPF 기반, L7 정책 지원 |
| **Weave Net** | ✅ | 설정 간편 |
| **Flannel** | ❌ | NetworkPolicy 미지원 |
| **kindnet** | ❌ | kind 기본값, 미지원 |

> ⚠️ EKS의 기본 CNI(aws-node)는 NetworkPolicy를 지원하지 않습니다. EKS에서 NetworkPolicy를 사용하려면 Calico 또는 Cilium을 별도로 설치해야 합니다.

### 정책의 적용 범위와 셀렉터

NetworkPolicy는 세 가지 셀렉터를 조합하여 트래픽 규칙을 정의합니다.

- **podSelector**: 정책이 적용될 파드를 선택 (같은 네임스페이스 내)
- **namespaceSelector**: 트래픽을 허용할 소스/목적지 네임스페이스 선택
- **ipBlock**: CIDR 범위로 외부 IP를 허용/차단

### 기본 동작 원칙

NetworkPolicy는 **화이트리스트** 방식으로 동작합니다.

1. 파드에 NetworkPolicy가 **적용되지 않은 경우**: 모든 트래픽 허용
2. 파드에 하나 이상의 NetworkPolicy가 **적용된 경우**: 정책에 명시적으로 허용된 트래픽만 통과

이 원칙을 이해하지 못하면 의도치 않은 트래픽 차단이 발생할 수 있습니다.

---

## 실전 예제

### 예제 1: Default Deny - 네임스페이스 격리

모든 보안 정책의 출발점은 "기본적으로 모든 트래픽을 차단"하는 것입니다.

```yaml
# default-deny-all.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-all
  namespace: production
spec:
  podSelector: {}          # 네임스페이스의 모든 파드에 적용
  policyTypes:
    - Ingress
    - Egress
```

`podSelector: {}`는 해당 네임스페이스의 **모든 파드**를 선택합니다. 이 정책을 적용하면 `production` 네임스페이스의 모든 파드는 인바운드/아웃바운드 트래픽이 전부 차단됩니다.

### 예제 2: 특정 파드 간 통신 허용 (백엔드 → DB)

API 서버만 데이터베이스에 접근할 수 있도록 제한하는 패턴입니다.

```yaml
# allow-api-to-db.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-api-to-db
  namespace: production
spec:
  podSelector:
    matchLabels:
      app: postgres           # 이 정책이 적용될 파드 (DB)
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: api-server  # api-server 파드에서 오는 트래픽만 허용
      ports:
        - protocol: TCP
          port: 5432
```

이 정책으로 `app: postgres` 파드는 `app: api-server` 파드에서 오는 5432 포트 트래픽만 수신합니다.

### 예제 3: 네임스페이스 간 트래픽 제어

마이크로서비스 아키텍처에서 네임스페이스를 팀이나 도메인 단위로 분리하는 경우가 많습니다.

```yaml
# allow-monitoring-ingress.yaml
# monitoring 네임스페이스의 Prometheus가 production 파드를 스크래핑할 수 있도록 허용
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-prometheus-scraping
  namespace: production
spec:
  podSelector:
    matchLabels:
      metrics: "true"
  policyTypes:
    - Ingress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: monitoring  # 네임스페이스 셀렉터
          podSelector:
            matchLabels:
              app: prometheus  # AND 조건: 네임스페이스 + 파드 동시 만족
      ports:
        - protocol: TCP
          port: 8080
```

> 🔑 **중요한 문법 차이**: `from` 배열 내에서 `namespaceSelector`와 `podSelector`를 **같은 항목**에 작성하면 AND 조건, **다른 항목**으로 작성하면 OR 조건이 됩니다.

```yaml
# AND 조건 (monitoring 네임스페이스의 prometheus 파드만 허용)
ingress:
  - from:
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: monitoring
        podSelector:           # 같은 '-' 항목 안에 작성
          matchLabels:
            app: prometheus

# OR 조건 (monitoring 네임스페이스 전체 OR 어느 네임스페이스든 prometheus 파드 허용)
ingress:
  - from:
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: monitoring
      - podSelector:           # 별도 '-' 항목으로 작성
          matchLabels:
            app: prometheus
```

### 예제 4: Egress 제어 - DNS 허용 및 외부 API 접근

Egress 정책을 적용할 때 가장 빈번하게 발생하는 실수가 **DNS를 차단**하는 것입니다. DNS(UDP/TCP 53)를 허용하지 않으면 파드에서 도메인 이름 조회 자체가 불가능합니다.

```yaml
# api-server-egress.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: api-server-egress
  namespace: production
spec:
  podSelector:
    matchLabels:
      app: api-server
  policyTypes:
    - Egress
  egress:
    # 1. DNS 허용 (필수!)
    - ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53

    # 2. 같은 네임스페이스의 DB 파드로 아웃바운드 허용
    - to:
        - podSelector:
            matchLabels:
              app: postgres
      ports:
        - protocol: TCP
          port: 5432

    # 3. 외부 결제 API 서버 허용 (특정 IP 대역)
    - to:
        - ipBlock:
            cidr: 203.0.113.0/24  # 결제사 IP 대역
      ports:
        - protocol: TCP
          port: 443
```

### 예제 5: 완성된 3-Tier 아키텍처 정책

실무에서 자주 사용하는 Frontend → Backend → Database 구조의 전체 정책입니다.

```yaml
# 1. Frontend: 외부 인그레스 컨트롤러에서만 수신, 백엔드로만 송신
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: frontend-policy
  namespace: production
spec:
  podSelector:
    matchLabels:
      tier: frontend
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: ingress-nginx
  egress:
    - ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    - to:
        - podSelector:
            matchLabels:
              tier: backend
      ports:
        - protocol: TCP
          port: 8080

# 2. Backend: 프론트엔드에서만 수신, DB로만 송신
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: backend-policy
  namespace: production
spec:
  podSelector:
    matchLabels:
      tier: backend
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              tier: frontend
      ports:
        - protocol: TCP
          port: 8080
  egress:
    - ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    - to:
        - podSelector:
            matchLabels:
              tier: database
      ports:
        - protocol: TCP
          port: 5432

# 3. Database: 백엔드에서만 수신
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: database-policy
  namespace: production
spec:
  podSelector:
    matchLabels:
      tier: database
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              tier: backend
      ports:
        - protocol: TCP
          port: 5432
```

---

## 주의사항 및 트레이드오프

### 1. 디버깅의 어려움

NetworkPolicy는 패킷이 차단될 때 별도의 로그를 남기지 않습니다. 트러블슈팅 시에는 다음 방법을 사용합니다.

```bash
# 임시 디버그 파드로 연결 테스트
kubectl run debug-pod --image=nicolaka/netshoot -it --rm -n production -- /bin/bash

# 파드 내부에서 연결 확인
curl -v http://postgres-service:5432
nc -zv postgres-service 5432

# 현재 파드에 적용된 NetworkPolicy 확인
kubectl get networkpolicy -n production
kubectl describe networkpolicy allow-api-to-db -n production
```

Cilium을 사용하는 경우 `cilium monitor`로 드롭된 패킷을 실시간으로 확인할 수 있습니다.

### 2. 상태 유지 연결(Stateful) 처리

NetworkPolicy는 **Stateful**하게 동작합니다. Ingress 규칙으로 특정 포트를 허용하면 해당 연결의 응답 패킷은 별도의 Egress 규칙 없이 자동으로 허용됩니다. 이는 iptables의 conntrack 메커니즘 덕분입니다.

### 3. 성능 오버헤드

NetworkPolicy 규칙이 많아질수록 iptables 규칙 수가 폭발적으로 증가할 수 있습니다. 수천 개의 파드와 복잡한 정책이 공존하는 대규모 클러스터에서는 **Cilium(eBPF 기반)**이 iptables 기반 CNI보다 성능 면에서 유리합니다.

### 4. NetworkPolicy의 한계

- **L7(애플리케이션 레이어) 제어 불가**: HTTP 메서드, 경로, 헤더 기반 필터링은 불가능합니다. 이를 위해선 Istio, Linkerd 같은 서비스 메시나 Cilium의 L7 Policy를 사용해야 합니다.
- **노드 레벨 트래픽 미제어**: NetworkPolicy는 파드 간 트래픽에만 적용됩니다. 노드 레벨 네트워크 정책은 별도로 관리해야 합니다.
- **정책 복잡성 증가**: 마이크로서비스 수가 늘어날수록 정책 수도 비례하여 증가합니다. **Kyverno**나 **OPA Gatekeeper**를 활용한 정책 자동화를 고려하세요.

### 5. 정책 적용 순서

여러 NetworkPolicy가 하나의 파드에 적용될 경우, 정책들은 **합집합(Union)**으로 평가됩니다. 즉, 어느 하나의 정책에서라도 허용하면 트래픽이 통과됩니다. 정책 간 우선순위나 Deny 규칙은 기본 NetworkPolicy에서는 지원되지 않으며, 이를 위해선 Calico의 GlobalNetworkPolicy나 Cilium의 CiliumNetworkPolicy를 활용해야 합니다.

---

## 정리

Kubernetes NetworkPolicy는 클러스터 내부 보안의 핵심 도구이지만, 올바르게 사용하기 위해 이해해야 할 사항이 적지 않습니다.

| 핵심 포인트 | 내용 |
|---|---|
| **CNI 의존성** | NetworkPolicy 지원 CNI(Calico, Cilium 등) 필수 |
| **화이트리스트 방식** | 정책 적용 시 명시적 허용 외 전부 차단 |
| **Default Deny 우선** | 네임스페이스 단위로 기본 차단 후 필요한 통신만 개방 |
| **DNS 허용 필수** | Egress 정책 적용 시 UDP/TCP 53 포트 반드시 포함 |
| **AND/OR 셀렉터 주의** | 같은 항목 내 작성 시 AND, 별도 항목 작성 시 OR |
| **L7 제어 한계** | 서비스 메시 또는 확장 CNI 정책으로 보완 |

NetworkPolicy는 완벽한 솔루션이 아닙니다. 서비스 메시, OPA/Kyverno를 통한 정책 거버넌스, 그리고 정기적인 정책 감사와 함께 운영할 때 비로소 강력한 클러스터 보안 체계를 구축할 수 있습니다. 처음에는 복잡하게 느껴지더라도, Default Deny부터 시작하여 필요한 통신 경로를 하나씩 열어가는 방식으로 접근하면 관리 가능한 수준에서 보안을 강화할 수 있습니다.
