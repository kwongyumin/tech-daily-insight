# 플랫폼 엔지니어링과 Internal Developer Platform 구축

## 개요

최근 몇 년간 DevOps가 대중화되면서 아이러니한 문제가 생겨났다. "You build it, you run it"이라는 철학 아래 개발팀이 인프라, 배포 파이프라인, 모니터링, 보안까지 모두 책임지게 되었고, 결과적으로 **인지 부하(Cognitive Load)** 가 폭발적으로 증가했다.

이 문제를 해결하기 위해 등장한 것이 **플랫폼 엔지니어링(Platform Engineering)** 이다. 핵심 아이디어는 간단하다. 인프라와 운영 관련 복잡성을 플랫폼 팀이 흡수하고, 개발자에게는 **골든 패스(Golden Path)** — 즉, 검증된 최선의 방법을 따르는 셀프 서비스 도구 모음 — 를 제공하는 것이다.

Gartner는 2026년까지 대형 소프트웨어 엔지니어링 조직의 80%가 플랫폼 엔지니어링 팀을 구성할 것으로 전망했다. 이 글에서는 플랫폼 엔지니어링의 핵심 개념부터 **IDP(Internal Developer Platform)** 를 실제로 구축하는 방법까지 실무 관점에서 다룬다.

---

## 핵심 개념

### 플랫폼 엔지니어링 vs DevOps

두 개념은 상호 보완적이지만 다르다.

| 구분 | DevOps | 플랫폼 엔지니어링 |
|------|--------|-----------------|
| 철학 | 개발-운영 협업 문화 | 개발자 셀프 서비스 |
| 책임 주체 | 각 팀이 자체 인프라 관리 | 플랫폼 팀이 공통 인프라 제공 |
| 인지 부하 | 개발자에게 높음 | 플랫폼 팀이 흡수 |
| 도구 | CI/CD 파이프라인 중심 | IDP + 개발자 포털 |

### IDP의 구성 요소

IDP는 단순한 도구 모음이 아니다. 다음 다섯 가지 레이어로 구성된 **제품**이다.

1. **인프라 오케스트레이션** — Terraform, Crossplane
2. **애플리케이션 설정 관리** — Helm, Kustomize
3. **배포 관리** — ArgoCD, Flux
4. **서비스 카탈로그** — Backstage
5. **개발자 포털** — Backstage UI, 커스텀 포털

### 골든 패스란?

골든 패스는 플랫폼 팀이 미리 정의한 **검증된 워크플로우**다. 예를 들어 Spring Boot 서비스를 새로 만들 때:

- 레포지터리 생성
- CI 파이프라인 자동 구성
- 스테이징/프로덕션 네임스페이스 생성
- 기본 모니터링 대시보드 생성
- RBAC 정책 적용

이 모든 것이 개발자가 버튼 하나 클릭하거나 CLI 명령어 하나로 완료되는 것이 목표다.

---

## 실전 예제

### 1. Backstage로 서비스 카탈로그 구축

Backstage는 Spotify가 개발하고 CNCF에 기증한 개발자 포털 프레임워크다. `catalog-info.yaml` 파일 하나로 서비스를 등록할 수 있다.

```yaml
# catalog-info.yaml
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: order-service
  description: 주문 처리 마이크로서비스
  annotations:
    github.com/project-slug: myorg/order-service
    backstage.io/techdocs-ref: dir:.
    prometheus.io/alert: "order-service-alerts"
  tags:
    - java
    - spring-boot
    - kafka
  links:
    - url: https://grafana.internal/d/order-service
      title: Grafana Dashboard
    - url: https://sentry.internal/order-service
      title: Sentry
spec:
  type: service
  lifecycle: production
  owner: team-commerce
  system: order-management
  dependsOn:
    - component:payment-service
    - resource:order-db
  providesApis:
    - order-api
```

### 2. Crossplane으로 인프라 셀프 서비스 구현

Crossplane은 Kubernetes CRD를 활용해 클라우드 리소스를 선언적으로 관리한다. 개발자가 직접 AWS RDS를 프로비저닝할 수 있는 Composition을 정의해보자.

```yaml
# xrd-postgres.yaml - 플랫폼 팀이 정의
apiVersion: apiextensions.crossplane.io/v1
kind: CompositeResourceDefinition
metadata:
  name: xpostgresinstances.platform.myorg.io
spec:
  group: platform.myorg.io
  names:
    kind: XPostgresInstance
    plural: xpostgresinstances
  claimNames:
    kind: PostgresInstance
    plural: postgresinstances
  versions:
    - name: v1alpha1
      served: true
      referenceable: true
      schema:
        openAPIV3Schema:
          type: object
          properties:
            spec:
              type: object
              properties:
                parameters:
                  type: object
                  properties:
                    storageGB:
                      type: integer
                      minimum: 20
                      maximum: 500
                    tier:
                      type: string
                      enum: ["dev", "staging", "production"]
                  required:
                    - storageGB
                    - tier
```

```yaml
# postgres-claim.yaml - 개발자가 사용
apiVersion: platform.myorg.io/v1alpha1
kind: PostgresInstance
metadata:
  name: order-service-db
  namespace: team-commerce
spec:
  parameters:
    storageGB: 100
    tier: production
  compositionSelector:
    matchLabels:
      provider: aws
      region: ap-northeast-2
  writeConnectionSecretToRef:
    name: order-service-db-credentials
```

이 YAML 파일 하나로 개발자는 AWS RDS 인스턴스, 서브넷 그룹, 보안 그룹, 파라미터 그룹을 자동으로 프로비저닝할 수 있다. 플랫폼 팀이 정의한 컴플라이언스 정책은 자동으로 적용된다.

### 3. GitHub Actions + ArgoCD 기반 골든 패스 파이프라인

```yaml
# .github/workflows/golden-path-deploy.yml
name: Golden Path Deploy

on:
  push:
    branches: [main]

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up JDK 21
        uses: actions/setup-java@v4
        with:
          java-version: '21'
          distribution: 'temurin'

      - name: Build with Gradle
        run: ./gradlew build -x test

      - name: Run Tests
        run: ./gradlew test

      - name: SonarQube Analysis
        uses: SonarSource/sonarcloud-github-action@master
        env:
          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}

      - name: Build & Push Docker Image
        uses: docker/build-push-action@v5
        with:
          push: true
          tags: |
            ${{ vars.REGISTRY }}/order-service:${{ github.sha }}
            ${{ vars.REGISTRY }}/order-service:latest

      # GitOps: 이미지 태그 업데이트
      - name: Update Helm Values
        run: |
          git clone https://github.com/myorg/gitops-config.git
          cd gitops-config
          yq e '.image.tag = "${{ github.sha }}"' \
            -i apps/order-service/values-production.yaml
          git config user.email "bot@myorg.io"
          git config user.name "Platform Bot"
          git commit -am "chore: update order-service to ${{ github.sha }}"
          git push

  notify:
    needs: build-and-push
    runs-on: ubuntu-latest
    steps:
      - name: Notify Backstage Deployment
        run: |
          curl -X POST ${{ vars.BACKSTAGE_URL }}/api/catalog/entities \
            -H "Authorization: Bearer ${{ secrets.BACKSTAGE_TOKEN }}" \
            -H "Content-Type: application/json" \
            -d '{
              "eventType": "deployment",
              "entityRef": "component:default/order-service",
              "sha": "${{ github.sha }}",
              "environment": "production"
            }'
```

### 4. Platform CLI 도구 — 개발자 경험 개선

개발자가 매일 사용하는 CLI를 Spring Shell로 구현하는 예제다.

```java
// PlatformCli.java
@SpringBootApplication
public class PlatformCli {
    public static void main(String[] args) {
        SpringApplication.run(PlatformCli.class, args);
    }
}

@ShellComponent
@RequiredArgsConstructor
public class ServiceCommands {

    private final BackstageClient backstageClient;
    private final GitOpsClient gitOpsClient;
    private final K8sClient k8sClient;

    @ShellMethod(value = "새 마이크로서비스 스캐폴딩", key = "create-service")
    public String createService(
            @ShellOption(help = "서비스 이름") String name,
            @ShellOption(help = "담당 팀") String team,
            @ShellOption(help = "서비스 타입", defaultValue = "spring-boot") String type
    ) {
        log.info("골든 패스로 {} 서비스 생성 중...", name);

        // 1. GitOps 레포에 서비스 디렉터리 생성
        gitOpsClient.scaffoldService(ServiceScaffoldRequest.builder()
                .name(name)
                .team(team)
                .type(type)
                .build());

        // 2. Kubernetes 네임스페이스 및 RBAC 설정
        k8sClient.createNamespace(NamespaceRequest.builder()
                .name(name)
                .team(team)
                .labels(Map.of(
                        "app.kubernetes.io/managed-by", "platform-cli",
                        "team", team
                ))
                .build());

        // 3. Backstage 카탈로그 등록
        backstageClient.registerComponent(ComponentRequest.builder()
                .name(name)
                .owner("team:" + team)
                .type(type)
                .lifecycle("development")
                .build());

        return String.format("""
                ✅ 서비스 생성 완료!
                   - 레포지터리: https://github.com/myorg/%s
                   - 네임스페이스: %s
                   - 카탈로그: https://backstage.internal/catalog/default/component/%s
                """, name, name, name);
    }

    @ShellMethod(value = "서비스 상태 확인", key = "service-status")
    public String serviceStatus(@ShellOption String name) {
        var status = k8sClient.getDeploymentStatus(name);
        return String.format("""
                📊 %s 상태
                   - Replicas: %d/%d
                   - 마지막 배포: %s
                   - 이미지: %s
                """,
                name,
                status.getReadyReplicas(),
                status.getTotalReplicas(),
                status.getLastDeployedAt(),
                status.getImage()
        );
    }
}
```

---

## 주의사항 및 트레이드오프

### ⚠️ 플랫폼은 제품이다 — 개발자 경험을 최우선으로

가장 흔한 실수는 플랫폼 팀이 **기술 중심으로 사고**하는 것이다. Crossplane, Backstage, ArgoCD는 모두 수단이다. 목적은 개발자가 **빠르고 안전하게** 서비스를 배포할 수 있는 경험을 제공하는 것이다.

- 개발자 인터뷰를 주기적으로 진행하라
- DORA 메트릭(배포 빈도, 리드 타임, MTTR, 변경 실패율)으로 성과를 측정하라
- 플랫폼 팀도 OKR을 갖고 로드맵을 공개하라

### ⚠️ 골든 패스는 강제가 아닌 권고여야 한다

골든 패스에서 벗어나야 하는 엣지 케이스는 반드시 존재한다. 플랫폼이 지나치게 경직되면 개발팀은 플랫폼을 우회하기 시작하고, 결국 **섀도 IT(Shadow IT)** 문제가 발생한다.

- 탈출구(escape hatch)를 명시적으로 제공하라
- 탈출구 사용 시 플랫폼 팀에 알림이 가도록 하고, 피드백 루프로 활용하라

### ⚠️ 추상화 레이어는 디버깅을 어렵게 만든다

Crossplane Composition이 실패했을 때, 개발자는 에러 메시지를 이해하기 어렵다. 플랫폼이 복잡해질수록 **관찰 가능성(Observability)** 에 투자해야 한다.

```bash
# Crossplane 리소스 상태 확인 명령어를 플랫폼 CLI에 포함
$ platform-cli debug postgres order-service-db

🔍 order-service-db 진단 중...
  ├─ PostgresInstance: Synced ✅
  ├─ XPostgresInstance: Synced ✅  
  ├─ RDSInstance: Creating ⏳ (예상 완료: 약 8분)
  └─ SubnetGroup: Ready ✅
```

### ⚠️ 팀 구조와 플랫폼 성숙도를 맞춰라

Conway's Law를 기억하라. 5명짜리 스타트업에서 Backstage + Crossplane + ArgoCD 풀스택 IDP를 구축하는 것은 오버엔지니어링이다. 팀 규모와 성숙도에 따라 단계적으로 접근하라.

| 단계 | 팀 규모 | 권장 접근 |
|------|---------|---------|
| 1단계 | ~20명 | 표준 Dockerfile + GitHub Actions 템플릿 |
| 2단계 | 20~100명 | Helm 차트 표준화 + ArgoCD 도입 |
| 3단계 | 100명 이상 | Backstage + Crossplane 기반 완전한 IDP |

---

## 정리

플랫폼 엔지니어링은 단순히 새로운 도구를 도입하는 것이 아니다. **개발자를 고객으로 바라보고, 그들의 인지 부하를 줄이는 내부 제품을 만드는 일**이다.

핵심을 정리하면:

- **IDP는 제품이다** — 로드맵, 사용자 피드백, 버전 관리가 필요하다
- **골든 패스는 강제가 아닌 권고** — 유연성을 남겨두어야 한다
- **Backstage + Crossplane + ArgoCD**는 현재 가장 검증된 IDP 스택이다
- **DORA 메트릭**으로 플랫폼의 비즈니스 가치를 증명하라
- **팀 규모에 맞게 단계적으로** 구축하라 — Big Bang 도입은 실패 확률이 높다

플랫폼 엔지니어링의 궁극적인 목표는 개발자가 인프라 걱정 없이 **비즈니스 가치 창출에 집중**할 수 있는 환경을 만드는 것이다. 이것이 잘 실현된다면, 배포는 지루한 일이 되고 — 그것이 바로 우리가 원하는 상태다.

---

*참고 자료*
- [CNCF Platforms White Paper](https://tag-app-delivery.cncf.io/whitepapers/platforms/)
- [Backstage Documentation](https://backstage.io/docs)
- [Crossplane Documentation](https://docs.crossplane.io)
- [Team Topologies — Matthew Skelton, Manuel Pais](https://teamtopologies.com)