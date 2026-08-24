# Helm Chart로 Kubernetes 애플리케이션 패키징 — 장애 대응과 트러블슈팅

## 장애 대응과 트러블슈팅: Helm이 망가졌을 때 살아남기

---

## 개요

Helm을 도입한 팀이라면 초기에는 편리함에 감탄하지만, 운영 단계에 접어들면서 "Helm이 왜 이러는 거지?"라는 상황을 반드시 마주하게 됩니다. Release가 `pending-upgrade` 상태에서 꼼짝도 안 하거나, `helm rollback`이 오히려 상황을 악화시키거나, 동일한 Chart로 배포했는데 환경마다 결과가 다른 경우가 대표적입니다.

이 글은 Helm Chart 기본 사용법을 반복하지 않습니다. **실제 프로덕션 환경에서 발생하는 Helm 관련 장애 패턴**과 그 대응 전략, 그리고 트러블슈팅 시 사용하는 구체적인 명령어와 판단 기준을 다룹니다.

---

## 핵심 개념: Helm Release State Machine 이해

트러블슈팅의 출발점은 Helm의 상태 머신을 정확히 이해하는 것입니다. Helm Release는 다음 상태를 순환합니다.

```
deployed → pending-upgrade → upgrading → deployed (성공)
                                       ↘ failed (실패)
```

문제는 **`pending-upgrade` 상태에서 프로세스가 비정상 종료**될 때 발생합니다. Helm은 상태를 Secret에 저장하는데, 이 Secret이 `pending-upgrade`로 남으면 이후 어떤 `helm upgrade` 명령도 실행되지 않습니다.

```bash
# Release 상태 확인 (가장 먼저 실행해야 할 명령)
helm list -n <namespace> -a
helm history <release-name> -n <namespace>
```

```
REVISION  STATUS           DESCRIPTION
1         superseded       Initial install
2         pending-upgrade  Preparing upgrade  ← 여기서 멈춘 상태
```

이 상태를 이해하지 못하면 `helm upgrade --install`을 반복 실행해도 `Error: UPGRADE FAILED: another operation (install/upgrade/rollback) is in progress` 에러만 반복됩니다.

---

## 실전 예제

### Case 1: Pending State 강제 복구

`pending-upgrade`에서 멈춘 Release를 복구하는 방법입니다. **주의: 프로덕션에서는 반드시 원인 파악 후 실행하세요.**

```bash
# 1단계: 현재 Helm Secret 확인
kubectl get secrets -n <namespace> -l owner=helm,name=<release-name> \
  --sort-by='.metadata.creationTimestamp'

# 2단계: 문제 Secret 상태 직접 수정 (임시 해결책)
kubectl get secret sh.helm.release.v1.<release>.v2 -n <namespace> -o json \
  | python3 -c "
import sys, json, base64, gzip

data = json.load(sys.stdin)
release_b64 = data['data']['release']
release_gz = base64.b64decode(release_b64)
release_json = json.loads(gzip.decompress(release_gz))

release_json['info']['status'] = 'failed'  # pending-upgrade → failed로 변경
print(release_json['info']['status'])
" 

# 3단계: rollback으로 마지막 정상 상태로 복귀
helm rollback <release-name> <last-good-revision> -n <namespace> --wait --timeout 5m
```

실무 팁: 위 Python 스크립트로 직접 Secret을 수정하는 것보다, `helm rollback`이 안 된다면 해당 revision의 Secret을 삭제하고 재배포하는 것이 더 안전합니다.

```bash
# 최후 수단: 특정 revision Secret 삭제
kubectl delete secret sh.helm.release.v1.<release>.v<revision> -n <namespace>
```

---

### Case 2: Helm Diff로 배포 전 위험 감지

장애는 배포 이후가 아니라 **배포 전에 감지**해야 합니다. `helm-diff` 플러그인은 실제 Kubernetes 리소스와 Chart 간의 차이를 보여주는 필수 도구입니다.

```bash
# helm-diff 설치
helm plugin install https://github.com/databus23/helm-diff

# 배포 전 변경사항 확인 (dry-run보다 훨씬 실용적)
helm diff upgrade <release-name> ./my-chart \
  -n <namespace> \
  -f values-prod.yaml \
  --context 5 \
  --suppress-secrets
```

다음은 Deployment의 `replicas` 변경이 의도치 않게 포함된 경우를 감지하는 예시입니다.

```diff
# Source: my-chart/templates/deployment.yaml
  spec:
-   replicas: 3
+   replicas: 1        ← values.yaml의 기본값이 적용된 위험한 상황
    selector:
      matchLabels:
        app: my-app
```

이런 diff를 **배포 전에 CI/CD 파이프라인에서 자동으로 감지**하는 구성을 GitLab CI 기준으로 구성할 수 있습니다.

```yaml
# .gitlab-ci.yml
helm-diff-check:
  stage: validate
  script:
    - helm diff upgrade ${RELEASE_NAME} ./chart
        -f values/${ENV}.yaml
        -n ${NAMESPACE}
        --suppress-secrets
        --output json > diff-output.json
    # replica 감소 또는 resource limit 제거 감지
    - |
      python3 - <<'EOF'
      import json, sys
      
      with open('diff-output.json') as f:
          diffs = json.load(f)
      
      dangerous_patterns = ['replicas', 'memory', 'cpu', 'storageClassName']
      
      for diff in diffs:
          for pattern in dangerous_patterns:
              if pattern in str(diff.get('patch', '')):
                  print(f"⚠️  위험한 변경 감지: {pattern} in {diff.get('name')}")
                  sys.exit(1)
      EOF
  allow_failure: false
```

---

### Case 3: Rollback이 실패하는 상황과 대안

`helm rollback`은 만능이 아닙니다. 다음 상황에서는 rollback 자체가 실패하거나 역효과를 냅니다.

**CRD(Custom Resource Definition) 포함된 Chart:** Helm은 rollback 시 CRD를 이전 버전으로 되돌리지 않습니다. CRD 스키마가 변경된 후 rollback하면 기존 CR(Custom Resource)이 유효하지 않아 컨트롤러가 오작동합니다.

```bash
# CRD 변경 여부 확인
helm get manifest <release> --revision <old-revision> -n <namespace> \
  | grep -A 5 "kind: CustomResourceDefinition"

# CRD는 수동으로 별도 관리 (chart에서 분리 권장)
kubectl apply -f crds/ --server-side
```

**PersistentVolumeClaim 포함 시:** rollback해도 PVC는 그대로 남습니다. 만약 새 버전이 PVC 이름이나 스펙을 변경했다면 rollback 후에도 마운트 오류가 발생합니다.

```yaml
# values.yaml에서 PVC 이름을 변경 불가능하게 고정
persistence:
  existingClaim: "my-app-data-pvc"  # 절대 Chart 내부에서 생성하지 말 것
```

**실제 안전한 롤백 전략:**

```bash
# 1. 롤백 시뮬레이션 (--dry-run)
helm rollback <release> <revision> -n <namespace> --dry-run

# 2. 롤백과 함께 이전 values 확인
helm get values <release> --revision <revision> -n <namespace>

# 3. 실제 롤백 (timeout 명시 필수)
helm rollback <release> <revision> -n <namespace> \
  --wait \
  --timeout 10m \
  --cleanup-on-fail  # 실패 시 새로 생성된 리소스 제거
```

---

### Case 4: Hook 타임아웃으로 인한 배포 교착

`helm.sh/hook` 어노테이션을 사용한 Job(DB 마이그레이션 등)이 타임아웃되면 전체 배포가 블로킹됩니다.

```yaml
# templates/migrations-job.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ include "myapp.fullname" . }}-migrations
  annotations:
    "helm.sh/hook": pre-upgrade
    "helm.sh/hook-weight": "-5"
    "helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded
spec:
  activeDeadlineSeconds: 300  # 5분 타임아웃 필수
  backoffLimit: 2
  template:
    spec:
      restartPolicy: Never
      containers:
      - name: migrations
        image: {{ .Values.image.repository }}:{{ .Values.image.tag }}
        command: ["python", "manage.py", "migrate", "--noinput"]
        resources:
          requests:
            memory: "256Mi"
            cpu: "100m"
          limits:
            memory: "512Mi"
            cpu: "500m"
```

Hook이 교착된 경우 진단:

```bash
# Hook Job 상태 확인
kubectl get jobs -n <namespace> -l "helm.sh/chart"

# Hook Job 로그 확인
kubectl logs job/<release-name>-migrations -n <namespace> --tail=100

# 강제 삭제 후 재배포 (DB 상태 확인 후 실행)
kubectl delete job <release-name>-migrations -n <namespace>
helm upgrade <release-name> ./chart -n <namespace> -f values-prod.yaml
```

---

## 주의사항 및 트레이드오프

### `--atomic` vs `--wait` 선택 기준

| 옵션 | 동작 | 적합한 상황 | 위험 |
|------|------|------------|------|
| `--atomic` | 실패 시 자동 rollback | 단순 스테이트리스 앱 | Hook 실패 시 의도치 않은 rollback |
| `--wait` | 준비될 때까지 대기 후 성공/실패 반환 | 수동 rollback 정책이 있을 때 | 타임아웃 시 pending 상태 가능 |
| 없음 | 즉시 반환 | CI에서 비동기 배포 | 실패 감지 불가 |

**프로덕션 권장:** `--wait --timeout 10m`을 기본으로 사용하고, `--atomic`은 스테이지 환경에서만 사용합니다.

### Release History 관리

Helm은 기본적으로 Release 히스토리를 Secret으로 무한히 저장합니다. Secret이 쌓이면 etcd 부하가 증가하고 `helm history` 명령 자체가 느려집니다.

```bash
# 보관할 최대 히스토리 수 설정 (배포 시 적용)
helm upgrade <release> ./chart -n <namespace> --history-max 10

# 기존 Secret 정리 (주의: 롤백 포인트가 사라짐)
helm plugin install https://github.com/helm/helm-mapkubeapis
```

실측값: 히스토리 100개 이상 누적 시 `helm list` 응답 시간이 200ms → 2초 이상으로 증가한 사례가 있습니다. `--history-max 20`을 기본값으로 설정하는 것을 권장합니다.

### values.yaml 민감 정보 관리

`helm get values`는 기본적으로 plaintext로 값을 출력합니다. 비밀번호나 API 키를 values.yaml에 직접 넣으면 Helm Secret에 base64 인코딩(암호화 아님)으로 저장됩니다.

```bash
# 잘못된 방법: values에 시크릿 직접 포함
helm upgrade myapp ./chart --set database.password=supersecret

# 권장 방법: External Secrets Operator 또는 Vault 연동
# Chart에서는 SecretRef만 참조
```

```yaml
# templates/deployment.yaml (권장 패턴)
env:
- name: DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.database.existingSecret }}
      key: password
```

---

## 정리

Helm 장애 대응의 핵심은 세 가지입니다.

1. **상태 먼저 확인**: `helm list -a`와 `helm history`로 Release 상태를 파악하는 것이 모든 트러블슈팅의 출발점입니다.

2. **배포 전 방어**: `helm diff`를 CI 파이프라인에 통합해 의도하지 않은 변경을 배포 전에 잡으세요. 장애는 배포 후 대응보다 배포 전 차단이 비용이 훨씬 적습니다.

3. **rollback의 한계 인지**: Helm rollback은 CRD, PVC, 외부 상태(DB 스키마 등)를 되돌리지 못합니다. 이 범위를 명확히 팀에 공유하고, 이런 리소스는 Chart 외부에서 별도로 관리하는 전략을 수립하세요.

Helm은 강력하지만 상태를 가진 도구입니다. 그 상태가 불일치할 때 어떻게 진단하고 복구할지를 미리 런북(Runbook)으로 정리해두는 것이 실질적인 장애 대응 시간을 크게 단축합니다.