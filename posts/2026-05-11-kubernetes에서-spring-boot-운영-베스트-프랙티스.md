# Kubernetes에서 Spring Boot 운영 베스트 프랙티스

## 개요

Spring Boot 애플리케이션을 Kubernetes(이하 K8s) 위에서 운영하는 것은 이제 많은 팀의 표준이 되었습니다. 하지만 단순히 컨테이너 이미지를 빌드해서 Pod으로 띄우는 것과, **프로덕션에서 안정적으로 운영**하는 것은 전혀 다른 이야기입니다.

이 포스팅에서는 실무에서 자주 마주치는 문제들을 중심으로, K8s 환경에서 Spring Boot를 운영할 때 반드시 알아야 할 베스트 프랙티스를 정리합니다. 단순한 개념 나열이 아닌, 실전에서 바로 적용 가능한 예제 코드와 함께 설명합니다.

---

## 핵심 개념

### 1. Graceful Shutdown

K8s는 Pod을 종료할 때 `SIGTERM` 시그널을 보내고, `terminationGracePeriodSeconds` 이후 `SIGKILL`을 보냅니다. Spring Boot 2.3+부터는 graceful shutdown이 내장 지원됩니다.

**핵심 포인트:**
- 진행 중인 요청을 완료할 때까지 기다림
- 새로운 요청은 더 이상 받지 않음
- K8s의 `terminationGracePeriodSeconds`와 Spring의 timeout을 맞춰야 함

### 2. Health Check (Liveness & Readiness)

K8s는 두 가지 헬스체크 프로브를 제공합니다.

| 프로브 | 실패 시 동작 | 목적 |
|--------|-------------|------|
| Liveness | Pod 재시작 | 애플리케이션 데드락, 무한루프 감지 |
| Readiness | 트래픽 차단 | 준비 안 된 Pod에 요청 전달 방지 |
| Startup | Liveness 대기 | 느린 기동 애플리케이션 지원 |

Spring Boot Actuator는 이 세 가지를 모두 지원합니다.

### 3. 리소스 요청 및 제한

`requests`와 `limits`를 명확히 설정하지 않으면 노드 과부하, OOMKilled 등의 문제가 발생합니다. JVM은 기본적으로 컨테이너 메모리 제한을 인식하지 못할 수 있으므로 JVM 옵션도 함께 설정해야 합니다.

---

## 실전 예제

### Step 1: Spring Boot 설정

**application.yml**

```yaml
server:
  shutdown: graceful  # graceful shutdown 활성화

spring:
  lifecycle:
    timeout-per-shutdown-phase: 30s  # 최대 30초 대기

management:
  endpoints:
    web:
      exposure:
        include: health, info, prometheus, metrics
  endpoint:
    health:
      probes:
        enabled: true          # liveness/readiness 엔드포인트 활성화
      show-details: always
      group:
        readiness:
          include: readinessState, db, redis  # 의존성 포함
  health:
    livenessstate:
      enabled: true
    readinessstate:
      enabled: true
```

**커스텀 Readiness 인디케이터 예제:**

```java
@Component
public class ExternalServiceHealthIndicator implements HealthIndicator {

    private final ExternalServiceClient client;

    public ExternalServiceHealthIndicator(ExternalServiceClient client) {
        this.client = client;
    }

    @Override
    public Health health() {
        try {
            boolean isAvailable = client.ping();
            if (isAvailable) {
                return Health.up()
                        .withDetail("service", "external-api")
                        .withDetail("status", "reachable")
                        .build();
            }
            return Health.down()
                    .withDetail("service", "external-api")
                    .withDetail("status", "unreachable")
                    .build();
        } catch (Exception e) {
            return Health.down(e).build();
        }
    }
}
```

---

### Step 2: Kubernetes Deployment 설정

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: spring-app
  labels:
    app: spring-app
spec:
  replicas: 3
  selector:
    matchLabels:
      app: spring-app
  template:
    metadata:
      labels:
        app: spring-app
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8080"
        prometheus.io/path: "/actuator/prometheus"
    spec:
      terminationGracePeriodSeconds: 60  # Spring의 30s + 여유 30s
      containers:
        - name: spring-app
          image: myregistry/spring-app:1.0.0
          ports:
            - containerPort: 8080
          
          # JVM 메모리 설정 - 컨테이너 제한 인식
          env:
            - name: JAVA_OPTS
              value: >-
                -XX:+UseContainerSupport
                -XX:MaxRAMPercentage=75.0
                -XX:InitialRAMPercentage=50.0
                -XX:+ExitOnOutOfMemoryError
                -Dspring.profiles.active=prod
            - name: POD_NAME
              valueFrom:
                fieldRef:
                  fieldPath: metadata.name
            - name: POD_NAMESPACE
              valueFrom:
                fieldRef:
                  fieldPath: metadata.namespace
          
          # 리소스 설정
          resources:
            requests:
              cpu: "500m"
              memory: "512Mi"
            limits:
              cpu: "1000m"
              memory: "1Gi"
          
          # Startup Probe: 기동 완료까지 최대 3분 대기
          startupProbe:
            httpGet:
              path: /actuator/health/liveness
              port: 8080
            initialDelaySeconds: 10
            periodSeconds: 10
            failureThreshold: 18  # 10s * 18 = 180s
          
          # Liveness Probe
          livenessProbe:
            httpGet:
              path: /actuator/health/liveness
              port: 8080
            initialDelaySeconds: 0
            periodSeconds: 10
            failureThreshold: 3
            timeoutSeconds: 5
          
          # Readiness Probe
          readinessProbe:
            httpGet:
              path: /actuator/health/readiness
              port: 8080
            initialDelaySeconds: 0
            periodSeconds: 5
            failureThreshold: 3
            timeoutSeconds: 3
          
          # 볼륨 마운트 (ConfigMap/Secret)
          volumeMounts:
            - name: app-config
              mountPath: /app/config
              readOnly: true
      
      volumes:
        - name: app-config
          configMap:
            name: spring-app-config
```

---

### Step 3: ConfigMap과 Secret 분리

환경 설정은 이미지에 포함시키지 않고 외부로 분리합니다.

```yaml
# ConfigMap - 비민감 설정
apiVersion: v1
kind: ConfigMap
metadata:
  name: spring-app-config
data:
  application-prod.yml: |
    spring:
      datasource:
        url: jdbc:postgresql://postgres-service:5432/mydb
        hikari:
          maximum-pool-size: 10
          minimum-idle: 5
          connection-timeout: 30000
      redis:
        host: redis-service
        port: 6379
    logging:
      level:
        root: INFO
        com.mycompany: DEBUG
---
# Secret - 민감 정보
apiVersion: v1
kind: Secret
metadata:
  name: spring-app-secret
type: Opaque
stringData:
  DB_PASSWORD: "your-db-password"
  REDIS_PASSWORD: "your-redis-password"
  JWT_SECRET: "your-jwt-secret"
```

Secret을 환경변수로 주입:

```yaml
# Deployment spec.containers 하위에 추가
envFrom:
  - secretRef:
      name: spring-app-secret
```

---

### Step 4: HPA (Horizontal Pod Autoscaler)

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: spring-app-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: spring-app
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 80
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 60
      policies:
        - type: Pods
          value: 2
          periodSeconds: 60
    scaleDown:
      stabilizationWindowSeconds: 300  # 5분 안정화 후 스케일다운
      policies:
        - type: Pods
          value: 1
          periodSeconds: 120
```

---

### Step 5: PodDisruptionBudget

노드 유지보수나 업그레이드 시 가용성을 보장합니다.

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: spring-app-pdb
spec:
  minAvailable: 2  # 최소 2개 Pod는 항상 Running 상태 유지
  selector:
    matchLabels:
      app: spring-app
```

---

## 주의사항 및 트레이드오프

### ⚠️ JVM Heap 설정

`-XX:MaxRAMPercentage`를 너무 높게 설정하면 Metaspace, Thread Stack, Direct Buffer 등 Non-Heap 영역이 부족해져 OOMKilled가 발생할 수 있습니다. 일반적으로 **75% 이하**를 권장하며, G1GC의 GC 오버헤드도 고려해야 합니다.

```bash
# 컨테이너 내부에서 JVM이 인식하는 메모리 확인
kubectl exec -it <pod-name> -- java -XX:+PrintFlagsFinal -version 2>&1 | grep -i heapsize
```

### ⚠️ Liveness Probe의 잘못된 사용

Liveness Probe에 DB 연결, 외부 API 상태를 포함시키면 **외부 장애 시 멀쩡한 Pod이 재시작**되는 Cascading Failure를 유발합니다. Liveness는 애플리케이션 자체의 생존 여부만 확인해야 하며, 외부 의존성은 Readiness에만 포함시키세요.

### ⚠️ 세션 상태 관리

K8s 환경에서 Pod은 언제든 재시작되거나 교체될 수 있습니다. 로컬 세션 데이터는 소실됩니다. **Spring Session + Redis**로 세션을 외부화하거나, 완전한 Stateless 아키텍처(JWT)를 채택하세요.

### ⚠️ Rolling Update 중 요청 유실

`preStop` 훅을 추가해 K8s가 엔드포인트에서 Pod을 제거하고 트래픽을 차단하는 시간을 확보합니다.

```yaml
lifecycle:
  preStop:
    exec:
      command: ["/bin/sh", "-c", "sleep 10"]
```

K8s 엔드포인트 업데이트와 실제 트래픽 차단 사이에는 수 초의 지연이 있기 때문에, 이 `sleep`이 없으면 SIGTERM 직후에도 요청이 들어올 수 있습니다.

### ⚠️ 트레이드오프 정리

| 항목 | 장점 | 단점/주의 |
|------|------|-----------|
| Graceful Shutdown | 안전한 종료 | 지연 시간 증가 |
| HPA | 자동 스케일링 | 메트릭 지연으로 과부하 일시 발생 |
| Resource Limits | 노드 안정성 | 너무 낮으면 Throttling |
| PDB | 고가용성 보장 | 클러스터 업그레이드 지연 가능 |

---

## 정리

Kubernetes에서 Spring Boot를 안정적으로 운영하기 위한 핵심을 요약하면 다음과 같습니다.

1. **Graceful Shutdown** + `preStop` 훅으로 안전한 종료를 보장하라
2. **Liveness/Readiness/Startup Probe**를 목적에 맞게 분리하라
3. **JVM 컨테이너 지원 옵션**(`-XX:+UseContainerSupport`, `-XX:MaxRAMPercentage`)을 반드시 설정하라
4. **설정은 ConfigMap/Secret으로 외부화**하고 이미지에 포함시키지 마라
5. **HPA + PDB 조합**으로 자동 스케일링과 고가용성을 동시에 확보하라
6. **Stateless 설계**를 기본으로 하고, 세션이 필요하다면 Redis로 외부화하라

위 항목들은 각각 독립적으로도 가치 있지만, **함께 적용했을 때 시너지**가 납니다. 프로덕션에서 한 번 장애를 겪고 나서 하나씩 추가하는 것보다, 처음부터 체계적으로 적용하는 것이 훨씬 비용이 적게 듭니다.

K8s 환경은 빠르게 변화하고 있으므로, 정기적으로 공식 문서와 Spring Boot의 릴리스 노트를 확인하며 최신 베스트 프랙티스를 유지하는 습관을 기르길 권장합니다.