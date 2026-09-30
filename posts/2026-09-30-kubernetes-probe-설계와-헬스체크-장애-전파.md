# Kubernetes Probe 설계와 헬스체크 장애 전파

## 개요

Kubernetes를 운영하다 보면 "Pod는 Running인데 트래픽이 안 된다", "배포 중에 갑자기 서비스가 중단됐다", "롤링 업데이트 후 에러율이 급증했다"는 상황을 경험하게 된다. 이런 사고의 상당수는 Probe 설계가 잘못됐거나 아예 없는 경우에 발생한다.

Kubernetes의 Probe는 단순한 헬스체크가 아니다. **컨테이너의 생명주기를 제어하고, 트래픽 라우팅 결정에 직접 관여하며, 잘못 설계하면 오히려 장애를 증폭시키는 장치**다. 이 글에서는 세 가지 Probe의 역할과 차이를 명확히 정리하고, 실무에서 자주 보이는 잘못된 패턴과 올바른 설계 기준을 다룬다.

---

## 핵심 개념

### Probe 세 가지의 역할 구분

| Probe | 실패 시 동작 | 목적 |
|---|---|---|
| **Liveness** | 컨테이너 재시작 | 데드락, 무한루프 등 회복 불가 상태 감지 |
| **Readiness** | 엔드포인트 제거 (트래픽 차단) | 일시적 과부하, 초기화 미완료 상태 감지 |
| **Startup** | 컨테이너 재시작 (Liveness 유예) | 느린 기동 애플리케이션 보호 |

이 세 가지의 역할이 겹쳐 보이지만, 실패했을 때의 **결과가 완전히 다르다**. Liveness 실패는 컨테이너를 죽이고 다시 시작한다. Readiness 실패는 컨테이너는 살아있지만 서비스 엔드포인트에서 제거된다. 이 차이를 이해하지 못하면 Probe를 잘못 설계하게 된다.

### 헬스체크 장애 전파 메커니즘

Readiness Probe가 실패하면 kubelet이 해당 Pod를 `Endpoints` 오브젝트에서 제거한다. kube-proxy는 이 변경을 감지해 iptables/IPVS 룰을 업데이트하고, 이후 들어오는 트래픽은 해당 Pod로 전달되지 않는다.

문제는 이 과정이 **즉각적이지 않다**는 것이다. kubelet → API 서버 → Endpoints 컨트롤러 → kube-proxy로 이어지는 전파 지연이 존재한다. 그래서 Pod가 Terminating 상태에 들어가도 잠시간 트래픽이 유입될 수 있고, `preStop` 훅 없이 종료하면 일부 요청이 끊기게 된다.

```
Pod Terminating
    │
    ├─ SIGTERM 수신
    │
    ├─ (동시에) Endpoint 제거 전파 시작
    │       └─ API Server → Endpoints → kube-proxy (수백ms ~ 수초)
    │
    └─ preStop 훅 실행 후 프로세스 종료
```

이 비동기 전파 구조를 이해하지 못하면 graceful shutdown을 구현해도 여전히 커넥션 오류가 발생한다.

---

## 실전 예제

### Spring Boot 애플리케이션 기준 Probe 설계

Spring Boot Actuator를 사용하는 경우, `/actuator/health`를 그대로 사용하는 것보다 **Liveness와 Readiness를 분리**하는 것이 좋다.

```yaml
# application.yml
management:
  endpoint:
    health:
      probes:
        enabled: true
  health:
    livenessState:
      enabled: true
    readinessState:
      enabled: true
```

위 설정을 하면 아래 두 엔드포인트가 활성화된다.
- `/actuator/health/liveness` → `LivenessState`
- `/actuator/health/readiness` → `ReadinessState`

```yaml
# Kubernetes Deployment
spec:
  containers:
  - name: app
    image: myapp:latest
    
    startupProbe:
      httpGet:
        path: /actuator/health/liveness
        port: 8080
      failureThreshold: 30
      periodSeconds: 5
      # 최대 150초(30 * 5)까지 기동 시간 허용

    livenessProbe:
      httpGet:
        path: /actuator/health/liveness
        port: 8080
      initialDelaySeconds: 0  # startupProbe가 통과한 이후 시작
      periodSeconds: 10
      failureThreshold: 3
      timeoutSeconds: 3

    readinessProbe:
      httpGet:
        path: /actuator/health/readiness
        port: 8080
      initialDelaySeconds: 0
      periodSeconds: 5
      failureThreshold: 3
      successThreshold: 1
      timeoutSeconds: 3
    
    lifecycle:
      preStop:
        exec:
          command: ["/bin/sh", "-c", "sleep 5"]
```

`preStop`에 `sleep 5`를 넣는 이유는 앞서 언급한 Endpoint 전파 지연을 흡수하기 위해서다. Pod가 Terminating이 된 직후에도 수초간 트래픽이 들어올 수 있으므로, 실제 프로세스 종료를 잠시 늦추는 것이다.

### Readiness Probe에 외부 의존성 포함 여부

실무에서 가장 자주 논쟁이 되는 부분이다. DB 연결 여부나 외부 API 응답 여부를 Readiness에 포함해야 할까?

```java
// ❌ 잘못된 패턴: 외부 DB 상태를 Liveness에 연결
@Component
public class DatabaseLivenessIndicator implements LivenessStateHealthIndicator {
    
    @Autowired
    private DataSource dataSource;
    
    @Override
    public Health health() {
        try (Connection conn = dataSource.getConnection()) {
            conn.isValid(1);
            return Health.up().build();
        } catch (SQLException e) {
            // DB가 잠깐 끊겨도 컨테이너를 재시작하게 됨 → 장애 증폭
            return Health.down().build();
        }
    }
}
```

```java
// ✅ 올바른 패턴: 외부 의존성은 Readiness에만 포함
@Component
public class DatabaseReadinessIndicator implements ReadinessStateHealthIndicator {
    
    @Autowired
    private DataSource dataSource;
    
    @Override
    public Health health() {
        try (Connection conn = dataSource.getConnection()) {
            conn.isValid(1);
            return Health.up().build();
        } catch (SQLException e) {
            // 트래픽만 차단, 컨테이너는 유지
            return Health.down()
                         .withDetail("reason", "DB connection failed")
                         .build();
        }
    }
}
```

DB가 잠깐 끊겼을 때 Liveness 실패로 처리하면, 모든 Pod가 동시에 재시작을 시도하면서 오히려 복구가 더 느려진다. Readiness에 넣으면 트래픽만 차단하고 Pod는 살아있으므로, DB가 복구되는 즉시 자동으로 트래픽이 재개된다.

### TCP 소켓 방식과 gRPC Probe

HTTP 엔드포인트가 없는 서비스라면 TCP 또는 gRPC Probe를 사용한다.

```yaml
# TCP 방식
livenessProbe:
  tcpSocket:
    port: 9090
  periodSeconds: 10
  failureThreshold: 3

# gRPC 방식 (Kubernetes 1.24+)
livenessProbe:
  grpc:
    port: 9090
    service: "liveness"  # gRPC Health Checking Protocol
  periodSeconds: 10
  failureThreshold: 3
```

TCP Probe는 포트가 열려있는지만 확인하므로 **애플리케이션 로직 수준의 헬스는 확인하지 못한다**. 가능하면 HTTP 또는 gRPC 방식을 사용하는 것이 권장된다.

---

## 주의사항 및 트레이드오프

### 1. Liveness Probe는 보수적으로 설계하라

Liveness 실패는 컨테이너 재시작을 유발한다. **재시작이 실제로 문제를 해결하는 경우에만** Liveness에 포함해야 한다. 데드락이나 무한루프처럼 재시작으로 해결되는 상황은 Liveness가 맞다. 하지만 외부 의존성 장애나 일시적인 부하 급증은 재시작으로 해결되지 않는다.

Liveness 임계값도 여유 있게 설정해야 한다. `failureThreshold: 3`, `periodSeconds: 10`이면 30초 만에 재시작이 트리거된다. GC pause나 일시적 응답 지연이 있는 JVM 기반 서비스에서는 이 값이 너무 공격적일 수 있다.

```yaml
# JVM 서비스에 더 적합한 설정 예
livenessProbe:
  httpGet:
    path: /actuator/health/liveness
    port: 8080
  periodSeconds: 15
  failureThreshold: 4
  timeoutSeconds: 5
  # 실패 확정까지 최대 60초(15 * 4) 허용
```

### 2. CrashLoopBackOff와 Probe 설계의 관계

Probe 설계가 잘못되면 `CrashLoopBackOff`가 발생한다. 가장 흔한 원인은 Startup Probe 없이 느린 기동 애플리케이션에 Liveness를 설정하는 경우다.

```
기동 중 (60초 소요)
  └─ Liveness Probe 실패 (initialDelaySeconds 초과)
       └─ 컨테이너 재시작
            └─ 기동 중 (60초 소요)
                 └─ Liveness Probe 실패
                      └─ ... (무한 반복)
```

이 상황을 해결하는 방법은 두 가지다.

- `initialDelaySeconds`를 충분히 크게 설정 (단점: 기동 시간이 일정하지 않으면 여전히 실패할 수 있음)
- **Startup Probe를 별도로 설정** (권장: Startup 통과 이후 Liveness가 시작됨)

### 3. Readiness를 너무 민감하게 설정하면 장애가 증폭된다

트래픽이 몰리는 상황에서 일부 Pod의 응답이 느려져 Readiness가 실패하면, 해당 Pod로 가던 트래픽이 나머지 Pod로 집중된다. 이것이 연쇄적으로 Readiness 실패를 유발하면 전체 서비스 다운으로 이어진다.

이를 방지하려면:
- `failureThreshold`와 `periodSeconds`를 충분히 여유 있게 설정
- HPA(Horizontal Pod Autoscaler)와 함께 설계해 부하 급증 시 Pod 수를 늘리도록
- PodDisruptionBudget(PDB)으로 동시에 Unavailable 상태가 되는 Pod 수를 제한

```yaml
# PodDisruptionBudget 예시
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: myapp-pdb
spec:
  minAvailable: 2  # 항상 최소 2개의 Pod는 Available 상태 유지
  selector:
    matchLabels:
      app: myapp
```

### 4. Probe 엔드포인트 자체가 부하를 유발하지 않도록

Probe가 너무 무거운 작업을 수행하면 오히려 서비스에 부하를 준다. 헬스체크 엔드포인트는 **경량으로 유지**해야 한다.

```java
// ❌ 무거운 헬스체크
@GetMapping("/health")
public ResponseEntity<String> health() {
    // 매 체크마다 DB 쿼리 실행, 외부 API 호출 등
    dbService.runTestQuery();
    externalApi.ping();
    return ResponseEntity.ok("OK");
}

// ✅ 가벼운 헬스체크 + 별도 딥 체크 분리
@GetMapping("/health/live")
public ResponseEntity<String> liveness() {
    // 단순 상태 플래그만 확인
    return applicationState.isAlive() 
        ? ResponseEntity.ok("OK") 
        : ResponseEntity.status(503).build();
}
```

---

## 정리

Kubernetes Probe는 단순히 "살아있냐"를 확인하는 것이 아니라, **컨테이너의 생명주기와 트래픽 라우팅을 제어하는 핵심 메커니즘**이다. 세 가지 Probe의 역할을 명확히 구분하고, 각각의 실패가 어떤 결과를 낳는지 이해하는 것이 설계의 출발점이다.

실무에서 반드시 기억해야 할 원칙을 정리하면 다음과 같다.

- **Liveness는 재시작으로 해결되는 상태만 감지**한다. 외부 의존성 장애를 Liveness에 넣으면 장애가 증폭된다.
- **Readiness는 일시적 불능 상태를 감지**한다. 트래픽만 차단하고 컨테이너는 유지된다.
- **Startup Probe는 느린 기동 서비스의 필수 안전장치**다. CrashLoopBackOff의 많은 원인이 여기서 비롯된다.
- **preStop 훅과 terminationGracePeriodSeconds**로 Endpoint 전파 지연을 흡수해야 진정한 graceful shutdown이 가능하다.
- **Probe 임계값은 서비스 특성에 맞게 조정**해야 한다. 특히 JVM 기반 서비스는 GC pause를 고려해 여유 있게 설정한다.

Probe를 제대로 설계하면 배포 중 다운타임을 없애고, 장애 상황에서의 자동 복구 능력을 크게 높일 수 있다. 반대로 잘못 설계된 Probe는 아무것도 안 하는 것보다 더 나쁜 결과를 낳을 수 있다. 기존 서비스의 Probe 설정을 한 번 점검해보는 것을 권장한다.
