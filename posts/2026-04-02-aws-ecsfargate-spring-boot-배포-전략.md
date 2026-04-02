# AWS ECS/Fargate Spring Boot 배포 전략

## 개요

컨테이너 기반 배포가 사실상 표준이 된 지금, AWS ECS(Elastic Container Service)와 Fargate는 많은 팀이 선택하는 관리형 컨테이너 오케스트레이션 솔루션이다. Kubernetes에 비해 운영 복잡도가 낮고, AWS 생태계와 깊이 통합되어 있어 인프라 관리 부담을 크게 줄일 수 있다.

이 글에서는 Spring Boot 애플리케이션을 ECS Fargate에 배포하는 전략을 실전 중심으로 다룬다. 단순한 배포를 넘어 **Blue/Green 배포**, **롤링 업데이트**, **Auto Scaling**, **Secret 관리**까지 프로덕션 수준에서 고려해야 할 요소들을 살펴본다.

---

## 핵심 개념

### ECS 아키텍처 구성 요소

ECS를 처음 접하는 분들도 있으므로 핵심 개념을 간략히 정리한다.

| 개념 | 설명 |
|------|------|
| **Cluster** | ECS 서비스/태스크가 실행되는 논리적 그룹 |
| **Task Definition** | 컨테이너 이미지, 리소스, 환경변수 등을 정의하는 템플릿 |
| **Service** | 태스크를 지정한 수만큼 유지·관리하는 컨트롤러 |
| **Task** | 실제 실행 중인 컨테이너 인스턴스 |
| **Fargate** | 서버 프로비저닝 없이 컨테이너를 실행하는 서버리스 컴퓨팅 엔진 |

### Fargate vs EC2 Launch Type

Fargate는 EC2 인스턴스를 직접 관리하지 않아도 된다는 큰 장점이 있지만, 비용 측면에서는 EC2 타입 대비 단가가 높다. 트래픽이 예측 가능하고 대규모라면 EC2 타입을, 운영 효율성과 빠른 스케일링이 중요하다면 Fargate를 선택하는 것이 일반적이다.

---

## 실전 예제

### 1. Spring Boot 애플리케이션 컨테이너화

프로덕션 환경에 최적화된 멀티 스테이지 Dockerfile을 작성한다.

```dockerfile
# Stage 1: Build
FROM eclipse-temurin:21-jdk-alpine AS builder
WORKDIR /app

COPY gradlew .
COPY gradle gradle
COPY build.gradle settings.gradle ./
COPY src src

RUN chmod +x ./gradlew
RUN ./gradlew bootJar -x test --no-daemon

# Stage 2: Runtime
FROM eclipse-temurin:21-jre-alpine AS runtime
WORKDIR /app

# 보안: non-root 사용자 실행
RUN addgroup -S appgroup && adduser -S appuser -G appgroup
USER appuser

COPY --from=builder /app/build/libs/*.jar app.jar

# JVM 튜닝 (컨테이너 메모리 인식)
ENV JAVA_OPTS="-XX:+UseContainerSupport \
               -XX:MaxRAMPercentage=75.0 \
               -XX:+UseG1GC \
               -Djava.security.egd=file:/dev/./urandom"

EXPOSE 8080

ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar app.jar"]
```

> **핵심 포인트**: `-XX:+UseContainerSupport`는 JVM이 호스트 전체 메모리가 아닌 컨테이너에 할당된 메모리를 기준으로 동작하게 한다. Java 10+ 기본 활성화지만 명시적으로 선언하는 것이 좋다.

### 2. Task Definition 정의 (JSON)

```json
{
  "family": "spring-boot-app",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "1024",
  "memory": "2048",
  "executionRoleArn": "arn:aws:iam::ACCOUNT_ID:role/ecsTaskExecutionRole",
  "taskRoleArn": "arn:aws:iam::ACCOUNT_ID:role/ecsTaskRole",
  "containerDefinitions": [
    {
      "name": "spring-boot-app",
      "image": "ACCOUNT_ID.dkr.ecr.ap-northeast-2.amazonaws.com/spring-boot-app:latest",
      "portMappings": [
        {
          "containerPort": 8080,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {
          "name": "SPRING_PROFILES_ACTIVE",
          "value": "prod"
        }
      ],
      "secrets": [
        {
          "name": "DB_PASSWORD",
          "valueFrom": "arn:aws:secretsmanager:ap-northeast-2:ACCOUNT_ID:secret:prod/db-password"
        },
        {
          "name": "JWT_SECRET",
          "valueFrom": "arn:aws:ssm:ap-northeast-2:ACCOUNT_ID:parameter/prod/jwt-secret"
        }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/spring-boot-app",
          "awslogs-region": "ap-northeast-2",
          "awslogs-stream-prefix": "ecs"
        }
      },
      "healthCheck": {
        "command": [
          "CMD-SHELL",
          "curl -f http://localhost:8080/actuator/health || exit 1"
        ],
        "interval": 30,
        "timeout": 5,
        "retries": 3,
        "startPeriod": 60
      }
    }
  ]
}
```

### 3. Spring Boot Actuator 헬스체크 설정

ECS 헬스체크와 연동하기 위해 Actuator를 올바르게 설정한다.

```yaml
# application-prod.yml
management:
  endpoints:
    web:
      base-path: /actuator
      exposure:
        include: health, info, metrics, prometheus
  endpoint:
    health:
      show-details: when-authorized
      probes:
        enabled: true  # liveness, readiness probe 활성화
  health:
    livenessstate:
      enabled: true
    readinessstate:
      enabled: true

server:
  port: 8080
  shutdown: graceful  # Graceful Shutdown 필수!

spring:
  lifecycle:
    timeout-per-shutdown-phase: 30s
```

### 4. CI/CD 파이프라인 (GitHub Actions)

```yaml
# .github/workflows/deploy.yml
name: Deploy to ECS Fargate

on:
  push:
    branches: [main]

env:
  AWS_REGION: ap-northeast-2
  ECR_REPOSITORY: spring-boot-app
  ECS_CLUSTER: production-cluster
  ECS_SERVICE: spring-boot-service
  CONTAINER_NAME: spring-boot-app

jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      id-token: write
      contents: read

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Configure AWS credentials (OIDC)
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::${{ secrets.AWS_ACCOUNT_ID }}:role/github-actions-role
          aws-region: ${{ env.AWS_REGION }}

      - name: Login to Amazon ECR
        id: login-ecr
        uses: aws-actions/amazon-ecr-login@v2

      - name: Build, tag, and push image to ECR
        id: build-image
        env:
          ECR_REGISTRY: ${{ steps.login-ecr.outputs.registry }}
          IMAGE_TAG: ${{ github.sha }}
        run: |
          docker build -t $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG .
          docker push $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG
          echo "image=$ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG" >> $GITHUB_OUTPUT

      - name: Download task definition
        run: |
          aws ecs describe-task-definition \
            --task-definition ${{ env.ECS_SERVICE }} \
            --query taskDefinition > task-definition.json

      - name: Update ECS task definition with new image
        id: task-def
        uses: aws-actions/amazon-ecs-render-task-definition@v1
        with:
          task-definition: task-definition.json
          container-name: ${{ env.CONTAINER_NAME }}
          image: ${{ steps.build-image.outputs.image }}

      - name: Deploy to ECS
        uses: aws-actions/amazon-ecs-deploy-task-definition@v1
        with:
          task-definition: ${{ steps.task-def.outputs.task-definition }}
          service: ${{ env.ECS_SERVICE }}
          cluster: ${{ env.ECS_CLUSTER }}
          wait-for-service-stability: true
          # CodeDeploy Blue/Green 배포 사용 시 아래 설정 활성화
          # codedeploy-appspec: appspec.yml
          # codedeploy-application: spring-boot-app
          # codedeploy-deployment-group: spring-boot-dg
```

### 5. Auto Scaling 설정 (Terraform)

```hcl
# auto_scaling.tf
resource "aws_appautoscaling_target" "ecs_target" {
  max_capacity       = 10
  min_capacity       = 2
  resource_id        = "service/${var.cluster_name}/${var.service_name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

# CPU 기반 스케일링
resource "aws_appautoscaling_policy" "cpu_scaling" {
  name               = "cpu-target-tracking"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.ecs_target.resource_id
  scalable_dimension = aws_appautoscaling_target.ecs_target.scalable_dimension
  service_namespace  = aws_appautoscaling_target.ecs_target.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value       = 60.0
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

# 메모리 기반 스케일링
resource "aws_appautoscaling_policy" "memory_scaling" {
  name               = "memory-target-tracking"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.ecs_target.resource_id
  scalable_dimension = aws_appautoscaling_target.ecs_target.scalable_dimension
  service_namespace  = aws_appautoscaling_target.ecs_target.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageMemoryUtilization"
    }
    target_value       = 70.0
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}
```

---

## 주의사항 및 트레이드오프

### ⚠️ Graceful Shutdown은 선택이 아닌 필수

ECS가 태스크를 교체할 때 `SIGTERM` 시그널을 보낸 후 `stopTimeout`(기본 30초) 이내에 종료되지 않으면 `SIGKILL`로 강제 종료된다. Spring Boot의 `server.shutdown=graceful` 설정과 ECS Task Definition의 `stopTimeout`을 일치시켜야 진행 중인 요청이 유실되지 않는다.

```json
// Task Definition에서 stopTimeout 설정
"stopTimeout": 60
```

### ⚠️ Secret 관리 전략

환경변수로 시크릿을 직접 넣는 것은 절대 금물이다. AWS Secrets Manager 또는 SSM Parameter Store를 사용하고, Task Execution Role에 최소 권한만 부여해야 한다. Secrets Manager는 자동 로테이션을 지원하지만, **ECS는 태스크 시작 시점에만 시크릿을 주입**하므로 로테이션 후 새 값 반영을 위해 태스크 재시작이 필요하다.

### ⚠️ VPC 네트워크 설계

`awsvpc` 네트워크 모드를 사용하면 각 태스크가 독립적인 ENI를 가진다. 대규모 서비스에서 태스크 수가 많아지면 **서브넷의 IP 고갈** 문제가 생길 수 있다. 충분한 CIDR 블록을 가진 서브넷 설계가 사전에 이루어져야 한다.

### ⚠️ Blue/Green vs 롤링 업데이트 선택

| 전략 | 장점 | 단점 |
|------|------|------|
| **Rolling Update** | 추가 비용 없음, 설정 간단 | 배포 중 구버전·신버전 공존 |
| **Blue/Green** | 즉시 롤백, 검증 후 전환 | CodeDeploy 연동 필요, 비용 증가 |

데이터베이스 스키마 변경이 수반되는 배포라면 **Expand-Contract 패턴**과 함께 Blue/Green 배포를 강력히 권장한다.

### ⚠️ 콜드 스타트와 Spring Boot 최적화

Fargate에서 새 태스크 시작 시 Spring Boot의 초기화 시간이 길면 헬스체크 실패로 배포가 실패할 수 있다. `startPeriod`를 적절히 설정하고, Spring Boot 3.x의 **AOT(Ahead-of-Time) 컴파일**이나 **GraalVM Native Image**를 고려하면 시작 시간을 수 초 이내로 줄일 수 있다.

---

## 정리

AWS ECS/Fargate에 Spring Boot를 배포할 때 핵심 체크리스트를 정리하면 다음과 같다.

- **컨테이너 최적화**: 멀티 스테이지 빌드, non-root 사용자, `UseContainerSupport` JVM 옵션
- **헬스체크**: Actuator 헬스 엔드포인트 + `startPeriod` 여유 있게 설정
- **Graceful Shutdown**: `server.shutdown=graceful` + ECS `stopTimeout` 동기화
- **Secret 관리**: Secrets Manager / SSM Parameter Store 연동, 평문 환경변수 금지
- **배포 전략**: 무중단을 위한 Blue/Green 또는 최소 롤링 업데이트
- **Auto Scaling**: CPU/메모리 기반 타겟 트래킹, Scale-in 쿨다운 충분히 확보
- **네트워크**: 서브넷 IP 대역 여유 확보, 보안 그룹 최소 권한 원칙

ECS Fargate는 Kubernetes 대비 진입 장벽이 낮지만, 프로덕션 환경에서 안정적으로 운영하려면 위의 세부 설정들을 꼼꼼히 챙겨야 한다. 특히 Graceful Shutdown과 Secret 관리는 실수가 잦은 부분이므로 팀 내 배포 가이드라인으로 문서화해두는 것을 강력히 권장한다.