# CI/CD 파이프라인 구축 GitHub Actions 완전 가이드

## 개요

현대 소프트웨어 개발에서 CI/CD(Continuous Integration/Continuous Deployment)는 선택이 아닌 필수가 되었다. 코드 품질을 자동으로 검증하고, 반복적인 배포 작업을 자동화함으로써 팀의 생산성을 극적으로 향상시킬 수 있다.

GitHub Actions는 GitHub 저장소와 네이티브하게 통합된 CI/CD 플랫폼으로, 별도의 인프라 구성 없이 강력한 자동화 파이프라인을 구축할 수 있다. Jenkins, CircleCI, GitLab CI 등 기존 도구 대비 **초기 진입 장벽이 낮고**, GitHub 생태계와의 통합성이 뛰어나다는 장점이 있다.

이 글에서는 Spring Boot 기반 백엔드 서비스를 대상으로 실무에서 바로 적용 가능한 GitHub Actions 파이프라인을 단계별로 구성해본다.

---

## 핵심 개념

### Workflow, Job, Step의 관계

GitHub Actions는 세 가지 계층 구조로 이루어진다.

- **Workflow**: `.github/workflows/*.yml` 파일로 정의되는 최상위 자동화 단위. 하나 이상의 Job으로 구성된다.
- **Job**: Runner 위에서 실행되는 작업 단위. 기본적으로 병렬 실행되며, `needs` 키워드로 의존성을 설정해 순차 실행도 가능하다.
- **Step**: Job 내부에서 순서대로 실행되는 개별 명령 단위. `run` 또는 `uses`로 정의한다.

### Trigger 이벤트

워크플로우를 실행시키는 이벤트를 `on` 키워드로 정의한다.

```yaml
on:
  push:
    branches: [ "main", "develop" ]
  pull_request:
    branches: [ "main" ]
  schedule:
    - cron: '0 2 * * *'  # 매일 새벽 2시 정기 실행
  workflow_dispatch:       # 수동 실행 허용
```

### Runner

Job이 실행되는 환경이다. GitHub에서 관리하는 **GitHub-hosted Runner**와 자체 서버에 설치하는 **Self-hosted Runner**로 나뉜다. Self-hosted Runner는 사내 네트워크 접근이 필요하거나, 특수한 하드웨어가 필요한 경우에 유용하다.

### Secrets와 환경 변수

민감한 정보는 반드시 GitHub Repository Settings > Secrets and variables에 등록하고, `${{ secrets.SECRET_NAME }}` 형태로 참조해야 한다.

---

## 실전 예제

### 1단계: 기본 CI 파이프라인 구성

Spring Boot 프로젝트의 빌드와 테스트를 자동화하는 기본 워크플로우다.

```yaml
# .github/workflows/ci.yml
name: CI Pipeline

on:
  push:
    branches: [ "main", "develop" ]
  pull_request:
    branches: [ "main" ]

jobs:
  build-and-test:
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_DB: testdb
          POSTGRES_USER: testuser
          POSTGRES_PASSWORD: testpass
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
      - name: Checkout source code
        uses: actions/checkout@v4

      - name: Set up JDK 17
        uses: actions/setup-java@v4
        with:
          java-version: '17'
          distribution: 'temurin'
          cache: 'gradle'

      - name: Grant execute permission for gradlew
        run: chmod +x gradlew

      - name: Run tests
        run: ./gradlew test
        env:
          SPRING_DATASOURCE_URL: jdbc:postgresql://localhost:5432/testdb
          SPRING_DATASOURCE_USERNAME: testuser
          SPRING_DATASOURCE_PASSWORD: testpass

      - name: Build without tests
        run: ./gradlew build -x test

      - name: Upload test results
        uses: actions/upload-artifact@v4
        if: always()  # 테스트 실패 시에도 리포트 업로드
        with:
          name: test-results
          path: build/reports/tests/
```

`services` 블록을 활용하면 테스트용 DB를 사이드카 컨테이너로 간단히 실행할 수 있다. `options`의 `health-cmd`로 컨테이너가 준비될 때까지 기다리는 것이 핵심이다.

---

### 2단계: Docker 빌드 및 ECR 푸시

빌드된 애플리케이션을 Docker 이미지로 패키징하고 AWS ECR에 푸시하는 Job이다.

```yaml
  docker-build-push:
    runs-on: ubuntu-latest
    needs: build-and-test  # CI 통과 후에만 실행
    if: github.ref == 'refs/heads/main'  # main 브랜치에서만 실행

    steps:
      - name: Checkout source code
        uses: actions/checkout@v4

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: ap-northeast-2

      - name: Login to Amazon ECR
        id: login-ecr
        uses: aws-actions/amazon-ecr-login@v2

      - name: Extract metadata for Docker
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ steps.login-ecr.outputs.registry }}/my-app
          tags: |
            type=sha,prefix=,suffix=,format=short
            type=raw,value=latest,enable={{is_default_branch}}

      - name: Build and push Docker image
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

`docker/build-push-action`의 `cache-from/cache-to` 설정으로 GitHub Actions 캐시를 활용해 빌드 시간을 크게 단축할 수 있다. 레이어 캐시가 잘 구성된 경우 빌드 시간이 70% 이상 줄어드는 것을 실무에서 확인했다.

---

### 3단계: ECS 배포 자동화

ECR에 푸시된 이미지를 AWS ECS에 배포하는 Job이다.

```yaml
  deploy-to-ecs:
    runs-on: ubuntu-latest
    needs: docker-build-push
    environment: production  # GitHub Environments 보호 규칙 적용

    steps:
      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: ap-northeast-2

      - name: Download task definition
        run: |
          aws ecs describe-task-definition \
            --task-definition my-app-task \
            --query taskDefinition > task-definition.json

      - name: Update ECS task definition with new image
        id: task-def
        uses: aws-actions/amazon-ecs-render-task-definition@v1
        with:
          task-definition: task-definition.json
          container-name: my-app
          image: ${{ needs.docker-build-push.outputs.image }}

      - name: Deploy to ECS
        uses: aws-actions/amazon-ecs-deploy-task-definition@v1
        with:
          task-definition: ${{ steps.task-def.outputs.task-definition }}
          service: my-app-service
          cluster: my-cluster
          wait-for-service-stability: true
```

`environment: production` 설정과 GitHub Environments를 함께 활용하면 배포 전 Reviewer 승인을 강제할 수 있어 실수로 인한 프로덕션 배포를 방지할 수 있다.

---

### 재사용 가능한 Composite Action 만들기

공통 로직은 Composite Action으로 추출해 재사용성을 높인다.

```yaml
# .github/actions/setup-java-gradle/action.yml
name: 'Setup Java & Gradle'
description: 'Java 환경 설정 및 Gradle 캐시 구성'

inputs:
  java-version:
    description: 'Java version'
    required: false
    default: '17'

runs:
  using: "composite"
  steps:
    - name: Set up JDK
      uses: actions/setup-java@v4
      with:
        java-version: ${{ inputs.java-version }}
        distribution: 'temurin'
        cache: 'gradle'

    - name: Grant execute permission for gradlew
      run: chmod +x gradlew
      shell: bash
```

이후 워크플로우에서 다음과 같이 재사용한다.

```yaml
    - name: Setup Java and Gradle
      uses: ./.github/actions/setup-java-gradle
      with:
        java-version: '21'
```

---

## 주의사항 및 트레이드오프

### 보안 관리

**Secrets 노출 위험**: `run` 블록에서 `echo ${{ secrets.API_KEY }}`처럼 직접 출력하면 로그에 노출될 수 있다. GitHub는 등록된 Secret 값을 자동으로 마스킹하지만, Base64 인코딩 등의 변환 후에는 마스킹이 되지 않으므로 주의가 필요하다.

**Fork PR 보안**: 외부 Contributor의 Fork에서 생성된 PR은 기본적으로 Secrets에 접근할 수 없다. `pull_request_target` 이벤트를 사용할 경우 코드 실행 컨텍스트가 달라지므로 보안 취약점이 발생할 수 있어 신중하게 사용해야 한다.

### 비용 고려

GitHub-hosted Runner는 Public 저장소에서는 무료지만, Private 저장소에서는 분 단위로 과금된다. 복잡한 파이프라인에서는 **Job 병렬화**와 **캐시 전략**을 통해 실행 시간을 최소화하는 것이 중요하다.

```yaml
# 매트릭스 전략으로 다중 환경 병렬 테스트
strategy:
  matrix:
    java-version: [17, 21]
    os: [ubuntu-latest, windows-latest]
  fail-fast: false  # 하나 실패해도 나머지 계속 실행
```

### Self-hosted Runner의 함정

Self-hosted Runner는 비용 절감과 사내 네트워크 접근에는 유리하지만, **보안 관리 부담**이 증가한다. 특히 Public 저장소에서는 외부 코드가 사내 서버에서 실행될 수 있으므로, Public 저장소에는 Self-hosted Runner 사용을 권장하지 않는다.

### 워크플로우 파일 관리

워크플로우가 복잡해질수록 `Reusable Workflow`를 적극 활용해야 한다. 단일 파일에 모든 로직을 넣으면 유지보수가 극도로 어려워진다.

```yaml
# Reusable Workflow 호출 예시
jobs:
  call-ci:
    uses: ./.github/workflows/reusable-ci.yml
    with:
      java-version: '17'
    secrets: inherit  # 상위 워크플로우의 Secrets 전달
```

---

## 정리

GitHub Actions를 활용한 CI/CD 파이프라인 구축을 정리하면 다음과 같다.

| 단계 | 핵심 포인트 |
|------|-------------|
| CI | 테스트 DB를 `services`로 구성, `always()`로 실패 시에도 리포트 수집 |
| Docker Build | GHA 캐시 활용으로 빌드 시간 단축 |
| CD | `environment`로 프로덕션 배포 승인 프로세스 강제 |
| 재사용성 | Composite Action, Reusable Workflow로 DRY 원칙 적용 |
| 보안 | Secrets 노출 방지, Fork PR 권한 관리 철저히 |

GitHub Actions는 낮은 진입 장벽으로 빠르게 시작할 수 있지만, 파이프라인이 복잡해질수록 **보안**, **비용**, **유지보수성**을 함께 고려해야 한다. 처음부터 완벽한 파이프라인을 구성하기보다는 기본 CI부터 시작해 점진적으로 고도화하는 접근을 추천한다.

실무에서는 이 글의 예제를 베이스로 SonarQube 코드 품질 분석, Slack 알림 연동, 성능 테스트 자동화 등을 단계적으로 추가해나가면 팀에 최적화된 DevOps 파이프라인을 완성할 수 있다.