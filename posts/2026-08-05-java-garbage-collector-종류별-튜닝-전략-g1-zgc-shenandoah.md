# Java Garbage Collector 종류별 튜닝 전략 (G1, ZGC, Shenandoah)

## 개요

JVM 성능 튜닝에서 Garbage Collector(GC) 선택과 설정은 애플리케이션의 응답 지연(Latency)과 처리량(Throughput)에 직접적인 영향을 미칩니다. Java 11 이후로 G1GC가 기본 GC로 자리잡았고, ZGC와 Shenandoah가 저지연(Low-latency) 요구사항에 대응하는 대안으로 주목받고 있습니다.

이 글에서는 각 GC의 동작 원리를 간략히 짚고, 실무에서 자주 마주치는 상황별 튜닝 파라미터와 전략을 구체적인 예제와 함께 소개합니다. 단순히 옵션 나열이 아닌, **왜 이 옵션을 써야 하는지**에 집중하겠습니다.

---

## 핵심 개념

### GC 선택 기준

GC를 선택할 때 가장 먼저 고민해야 할 두 가지 축은 다음과 같습니다.

- **Throughput 우선**: 단위 시간당 처리량이 중요한 배치 작업, 데이터 파이프라인
- **Latency 우선**: P99 응답 지연이 중요한 API 서버, 실시간 스트리밍, 금융 거래 시스템

| GC | STW(Stop-The-World) | 특징 | 적합한 환경 |
|---|---|---|---|
| G1GC | 수백ms 수준 | Region 기반, 예측 가능한 pause | 범용, 힙 4GB~16GB |
| ZGC | 1~10ms 이하 | Colored Pointer, Load Barrier | 대용량 힙, 저지연 서비스 |
| Shenandoah | 1~10ms 이하 | Brooks Pointer, 동시 Compaction | 중소 힙, OpenJDK 친화적 |

### G1GC의 Region 기반 설계

G1GC는 힙을 동일 크기의 Region으로 나누고, Garbage가 많은 Region(Garbage First)을 우선 수집합니다. `MaxGCPauseMillis`로 목표 pause 시간을 설정하면 G1GC가 그에 맞춰 수집할 Region 수를 자동으로 조절합니다.

### ZGC의 Concurrent 처리

ZGC는 Java 15부터 Production Ready 상태이며, Java 21에서 Generational ZGC가 기본으로 활성화되었습니다. 힙 포인터에 색상 메타데이터를 심어 GC 스레드가 애플리케이션 스레드와 동시에 작동하므로 힙 크기에 관계없이 STW 시간이 거의 일정합니다.

### Shenandoah의 동시 Compaction

Shenandoah는 Red Hat이 개발한 GC로 OpenJDK에 포함되어 있습니다. Brooks Pointer(간접 참조 포인터)를 활용해 Compaction 단계도 STW 없이 수행합니다. ZGC와 비슷한 목표를 가지지만 구현 방식이 다르며, 상대적으로 작은 힙에서도 효율적입니다.

---

## 실전 예제

### G1GC 튜닝

#### 기본 설정 및 힙 조정

```bash
# JVM 시작 옵션 (Spring Boot Fat Jar 기준)
java -Xms4g -Xmx8g \
  -XX:+UseG1GC \
  -XX:MaxGCPauseMillis=200 \
  -XX:G1HeapRegionSize=16m \
  -XX:G1NewSizePercent=30 \
  -XX:G1MaxNewSizePercent=40 \
  -XX:G1MixedGCCountTarget=8 \
  -XX:InitiatingHeapOccupancyPercent=45 \
  -XX:+G1UseAdaptiveIHOP \
  -Xlog:gc*:file=/var/log/app/gc.log:time,uptime,level,tags:filecount=5,filesize=20m \
  -jar app.jar
```

**주요 파라미터 설명**

- `MaxGCPauseMillis=200`: 목표 pause 시간. 너무 낮게 잡으면 GC가 충분히 회수하지 못해 Full GC 빈도가 올라갑니다.
- `G1HeapRegionSize`: 힙이 클수록 Region 크기를 키워야 Humongous Object(Region 크기의 50% 초과 객체) 할당 문제를 줄일 수 있습니다.
- `InitiatingHeapOccupancyPercent=45`: Concurrent Mark를 시작하는 힙 점유율 임계값. 기본값(45)보다 낮추면 더 자주 GC가 돌아 Allocation Failure를 예방합니다.
- `G1UseAdaptiveIHOP`: JVM이 할당 속도를 분석해 IHOP를 동적으로 조정합니다.

#### Humongous Object 문제 진단

```java
// 애플리케이션 코드에서 대형 객체 할당 패턴 확인
// Spring Boot Actuator + Micrometer로 GC 메트릭 노출

@Configuration
public class GcMetricsConfig {

    @Bean
    public MeterRegistryCustomizer<MeterRegistry> gcMetricsCustomizer() {
        return registry -> {
            // JVM GC 메트릭 자동 등록
            new JvmGcMetrics().bindTo(registry);
            new JvmMemoryMetrics().bindTo(registry);
        };
    }
}
```

```yaml
# application.yml - Actuator 설정
management:
  endpoints:
    web:
      exposure:
        include: health,metrics,prometheus
  metrics:
    tags:
      application: ${spring.application.name}
```

Prometheus + Grafana에서 다음 쿼리로 GC pause 시간을 모니터링할 수 있습니다.

```promql
# GC pause 평균 시간 (초)
rate(jvm_gc_pause_seconds_sum[5m]) / rate(jvm_gc_pause_seconds_count[5m])

# GC 발생 빈도
rate(jvm_gc_pause_seconds_count[1m])
```

---

### ZGC 튜닝

ZGC는 기본적으로 자동 튜닝이 잘 되어 있어 파라미터를 최소화하는 것이 원칙입니다.

```bash
# Java 21 기준 Generational ZGC 설정
java -Xms8g -Xmx16g \
  -XX:+UseZGC \
  -XX:+ZGenerational \
  -XX:ConcGCThreads=4 \
  -XX:ZAllocationSpikeTolerance=2.0 \
  -XX:+ZUncommit \
  -XX:ZUncommitDelay=300 \
  -Xlog:gc*:file=/var/log/app/gc.log:time,uptime:filecount=10,filesize=50m \
  -jar app.jar
```

**주요 파라미터 설명**

- `ZGenerational`: Java 21 기본값. Young/Old Generation 구분으로 단명 객체를 더 효율적으로 처리합니다.
- `ConcGCThreads=4`: GC 동시 처리 스레드 수. CPU 코어 수의 25~50% 정도가 적절합니다.
- `ZAllocationSpikeTolerance=2.0`: 할당 스파이크 허용 배수. 트래픽 급증 환경에서 높이면 Allocation Stall을 줄일 수 있습니다.
- `ZUncommit`: GC 후 OS에 메모리를 반환합니다. 컨테이너 환경에서 메모리 자원 효율에 유리합니다.

#### Allocation Stall 모니터링

ZGC에서 가장 주의해야 할 문제는 GC가 할당 속도를 따라가지 못할 때 발생하는 **Allocation Stall**입니다.

```bash
# GC 로그에서 Allocation Stall 확인
grep "Allocation Stall" /var/log/app/gc.log

# 예시 출력:
# [2024-01-15T10:23:45.123+0000][warning][gc] Allocation Stall (Thread "http-nio-8080-exec-5" 52ms)
```

Allocation Stall이 지속적으로 발생한다면 다음 전략을 적용합니다.

1. `ConcGCThreads` 증가
2. 힙 상한(`-Xmx`) 증가
3. `ZAllocationSpikeTolerance` 값 상향 조정

---

### Shenandoah 튜닝

```bash
# Shenandoah GC 설정 (OpenJDK 17+)
java -Xms4g -Xmx8g \
  -XX:+UseShenandoahGC \
  -XX:ShenandoahGCMode=iu \
  -XX:ShenandoahGCHeuristics=adaptive \
  -XX:ShenandoahInitFreeThreshold=70 \
  -XX:ShenandoahMinFreeThreshold=10 \
  -XX:+ShenandoahUncommit \
  -XX:ShenandoahUncommitDelay=1000 \
  -Xlog:gc*:file=/var/log/app/gc.log:time,uptime:filecount=5,filesize=20m \
  -jar app.jar
```

**주요 파라미터 설명**

- `ShenandoahGCMode=iu`: Incremental Update 모드. 기본 SATB 모드 대비 단명 객체 처리에 유리합니다.
- `ShenandoahGCHeuristics=adaptive`: GC 시작 시점을 적응형으로 결정합니다. `compact`(공격적 GC), `static`(고정 임계값) 모드도 있습니다.
- `ShenandoahInitFreeThreshold=70`: 첫 번째 GC를 시작하는 여유 공간 임계값(%).

#### Heuristics 모드 비교

```bash
# 트래픽이 예측 가능한 서비스 (배치 등)
-XX:ShenandoahGCHeuristics=static \
-XX:ShenandoahInitFreeThreshold=50

# 메모리 사용을 최소화해야 하는 환경
-XX:ShenandoahGCHeuristics=compact

# 대화형 서비스 (기본 권장)
-XX:ShenandoahGCHeuristics=adaptive
```

---

### GC 로그 분석 자동화

GC 튜닝의 핵심은 로그 기반의 반복적인 검증입니다. [GCEasy](https://gceasy.io)나 [JVM GC Analyzer](https://github.com/chewiebug/GCViewer) 같은 도구도 좋지만, 간단한 스크립트로 핵심 지표를 추출할 수 있습니다.

```bash
#!/bin/bash
# gc_summary.sh - GC 로그 요약 스크립트

LOG_FILE=${1:-"/var/log/app/gc.log"}

echo "=== GC Pause Summary ==="
grep -oP "(?<=Pause )\d+\.\d+" "$LOG_FILE" | \
  awk '{sum+=$1; count++; if($1>max) max=$1} 
       END {printf "Count: %d, Avg: %.2fms, Max: %.2fms\n", count, sum/count, max}'

echo ""
echo "=== Full GC Count ==="
grep -c "Pause Full" "$LOG_FILE" || echo "0"

echo ""
echo "=== GC Overhead (last 1000 pauses) ==="
grep -oP "(?<=Pause )\d+\.\d+" "$LOG_FILE" | tail -1000 | \
  awk '{sum+=$1} END {printf "Total pause time: %.2fs\n", sum/1000}'
```

---

## 주의사항 및 트레이드오프

### GC 선택 시 함정

**1. MaxGCPauseMillis는 보장이 아닌 목표입니다**

G1GC의 `MaxGCPauseMillis`를 50ms로 설정해도 Mixed GC나 Full GC 시 수백ms가 발생할 수 있습니다. SLA가 엄격하다면 ZGC나 Shenandoah를 고려해야 합니다.

**2. ZGC/Shenandoah는 CPU 비용이 있습니다**

동시 GC 스레드가 항상 실행되므로 CPU 사용률이 G1GC 대비 10~20% 높을 수 있습니다. CPU 제약이 있는 컨테이너 환경에서는 오히려 전체 처리량이 떨어질 수 있습니다.

**3. 힙이 작으면 ZGC 장점이 희석됩니다**

ZGC는 대용량 힙(16GB+)에서 진가를 발휘합니다. 힙이 4GB 이하라면 G1GC 대비 뚜렷한 이점을 체감하기 어렵습니다.

**4. JVM 벤더 호환성 확인 필수**

Shenandoah는 Oracle JDK에서 지원하지 않고 OpenJDK, Red Hat OpenJDK, Azul 등에서만 사용 가능합니다. 배포 환경의 JVM 벤더를 반드시 확인하세요.

### 컨테이너 환경 특이사항

```bash
# Kubernetes Pod에서 컨테이너 메모리 제한 반영
java -XX:+UseContainerSupport \
     -XX:MaxRAMPercentage=75.0 \
     -XX:InitialRAMPercentage=50.0 \
     -XX:+UseZGC \
     -XX:+ZGenerational \
     -jar app.jar
```

`UseContainerSupport`(Java 10+ 기본 활성화)로 cgroups 메모리 제한을 인식하게 하고, `MaxRAMPercentage`로 비율 기반 힙 설정을 사용하면 컨테이너 재배포 시 힙 옵션을 변경하지 않아도 됩니다.

---

## 정리

GC 튜닝에 정답은 없습니다. 워크로드 특성에 맞는 GC를 선택하고, 실제 환경에서 측정한 데이터를 기반으로 점진적으로 최적화하는 것이 핵심입니다.

| 상황 | 권장 GC | 핵심 옵션 |
|---|---|---|
| 범용 서비스, 힙 4~16GB | G1GC | `MaxGCPauseMillis`, `IHOP` |
| P99 지연 < 10ms, 대용량 힙 | ZGC (Generational) | `ConcGCThreads`, `ZAllocationSpikeTolerance` |
| 저지연 + OpenJDK 기반 | Shenandoah | `GCMode=iu`, `Heuristics=adaptive` |

튜닝 프로세스는 다음 순서를 권장합니다.

1. **베이스라인 측정**: GC 로그 + Micrometer 메트릭으로 현재 상태 기록
2. **단일 파라미터 변경**: 한 번에 하나씩 변경해 영향을 명확히 파악
3. **부하 테스트**: JMeter, k6 등으로 실제 트래픽 패턴과 유사한 부하 재현
4. **프로덕션 점진적 적용**: Canary 배포로 리스크 최소화

GC 튜닝은 한 번에 끝나지 않습니다. 서비스가 성장하면서 힙 사용 패턴이 바뀌므로, 주기적인 GC 로그 리뷰를 팀 문화로 정착시키는 것이 장기적으로 가장 효과적인 전략입니다.