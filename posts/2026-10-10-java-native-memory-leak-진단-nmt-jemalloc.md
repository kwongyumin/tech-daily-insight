# Java Native Memory Leak 진단 (NMT, jemalloc)

## 개요

Java 애플리케이션을 운영하다 보면 JVM 힙 메모리는 안정적으로 보이는데 프로세스 전체 메모리(`RSS`, `RES`)가 지속적으로 증가하는 현상을 마주할 때가 있다. GC 로그를 봐도 이상이 없고, 힙 덤프를 분석해도 별다른 문제가 없다. 이런 경우 범인은 **Native Memory Leak**일 가능성이 높다.

Native Memory는 JVM 힙 외부에서 OS로부터 직접 할당받는 메모리 영역이다. 코드 캐시, 메타스페이스, 스레드 스택, 다이렉트 버퍼, JNI 등이 이 영역을 사용한다. 문제는 이 영역이 표준 Java 모니터링 도구로는 잘 보이지 않는다는 점이다.

이 포스팅에서는 JVM 내장 도구인 **NMT(Native Memory Tracking)**와 메모리 할당 프로파일러인 **jemalloc**을 활용해 Native Memory Leak을 체계적으로 진단하는 방법을 실무 관점에서 정리한다.

---

## 핵심 개념

### Native Memory란 무엇인가

JVM 프로세스가 사용하는 메모리는 크게 두 가지로 나뉜다.

- **Java Heap**: `new` 키워드로 생성되는 객체들이 올라가는 공간. GC의 관리 대상.
- **Native Memory**: JVM 런타임 자체와 JNI 라이브러리, OS 레벨 자원이 사용하는 공간.

Native Memory를 주로 소비하는 주요 영역은 다음과 같다.

| 영역 | 설명 |
|------|------|
| Metaspace | 클래스 메타데이터 저장 |
| Code Cache | JIT 컴파일된 코드 저장 |
| Thread Stack | 각 스레드의 스택 메모리 |
| Direct Buffer | `ByteBuffer.allocateDirect()` 등으로 할당 |
| GC 내부 구조 | GC 알고리즘이 사용하는 내부 데이터 |
| JNI / Native Library | C/C++ 라이브러리 직접 할당 |

### NMT (Native Memory Tracking)

NMT는 JDK 8u40부터 제공되는 JVM 내장 기능으로, JVM이 직접 관리하는 Native Memory 사용량을 추적한다. 단, JVM 외부 라이브러리(예: JNI로 호출한 C 라이브러리)가 `malloc`으로 직접 할당한 메모리는 추적하지 못한다.

NMT를 활성화하면 약 5~10%의 성능 오버헤드가 발생하므로, 운영 환경에서는 신중하게 적용해야 한다.

### jemalloc

jemalloc은 Facebook이 개발한 고성능 메모리 할당 라이브러리로, 메모리 프로파일링 기능을 내장하고 있다. `LD_PRELOAD`를 사용해 기존 `malloc` 구현을 교체하면, JVM 외부 라이브러리가 호출하는 `malloc`까지 추적할 수 있다. NMT로는 볼 수 없었던 영역까지 커버한다는 점에서 강력한 보완 도구다.

---

## 실전 예제

### Step 1: NMT로 JVM Native Memory 추적하기

#### JVM 옵션 설정

```bash
# summary 모드: 영역별 요약 (오버헤드 낮음)
-XX:NativeMemoryTracking=summary

# detail 모드: 할당 지점까지 추적 (오버헤드 높음)
-XX:NativeMemoryTracking=detail
```

운영 환경에서는 `summary` 모드를 권장한다.

#### jcmd로 스냅샷 확인

```bash
# PID 확인
jps -l

# 현재 Native Memory 사용량 요약
jcmd <PID> VM.native_memory summary

# 베이스라인 설정 (이후 증분 추적 가능)
jcmd <PID> VM.native_memory baseline

# 일정 시간 후 베이스라인과 비교
jcmd <PID> VM.native_memory summary.diff
```

#### 출력 예시 및 해석

```
Native Memory Tracking:

Total: reserved=4.5GB, committed=1.2GB
-                 Java Heap (reserved=2048MB, committed=512MB)
-                     Class (reserved=1056MB, committed=45MB)
-                    Thread (reserved=128MB, committed=128MB)
-                      Code (reserved=240MB, committed=32MB)
-                        GC (reserved=512MB, committed=256MB)
-                  Internal (reserved=12MB, committed=12MB)
-                    Symbol (reserved=22MB, committed=22MB)
-    Native Memory Tracking (reserved=4MB, committed=4MB)
```

`diff` 명령 결과에서 특정 영역의 `committed` 값이 지속적으로 증가하면 해당 영역을 집중적으로 살펴봐야 한다.

#### Metaspace 누수 의심 시나리오

동적 클래스 생성(리플렉션, 바이트코드 조작, Groovy 스크립트 등)이 반복되면 Metaspace가 계속 증가한다.

```java
// 잘못된 패턴: 매 요청마다 새 ClassLoader 생성
public class BadGroovyEvaluator {
    public Object evaluate(String script) throws Exception {
        GroovyClassLoader loader = new GroovyClassLoader(); // 매번 새로 생성
        Class<?> clazz = loader.parseClass(script);
        // loader를 닫지 않으면 해당 ClassLoader가 로드한 클래스들이 Metaspace에 잔류
        return clazz.getDeclaredConstructor().newInstance();
    }
}

// 개선된 패턴: ClassLoader 재사용 또는 명시적 해제
public class BetterGroovyEvaluator implements Closeable {
    private final GroovyClassLoader sharedLoader = new GroovyClassLoader();

    public Object evaluate(String script) throws Exception {
        Class<?> clazz = sharedLoader.parseClass(script);
        return clazz.getDeclaredConstructor().newInstance();
    }

    @Override
    public void close() throws IOException {
        sharedLoader.close(); // ClassLoader 명시적 해제
    }
}
```

### Step 2: jemalloc으로 JVM 외부 메모리 추적하기

NMT로도 원인을 찾지 못했다면, JNI 라이브러리나 JVM 내부 구현에서 `malloc`을 직접 호출하는 부분을 의심해야 한다.

#### jemalloc 설치 (Linux)

```bash
# Ubuntu/Debian
sudo apt-get install libjemalloc-dev

# CentOS/RHEL
sudo yum install jemalloc

# 설치 경로 확인
ldconfig -p | grep jemalloc
# /usr/lib/x86_64-linux-gnu/libjemalloc.so.2
```

#### 프로파일링 활성화

```bash
# 환경변수 설정 후 JVM 시작
export LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libjemalloc.so.2
export MALLOC_CONF="prof:true,prof_leak:true,lg_prof_interval:30,lg_prof_sample:17"

# lg_prof_interval: 2^30 bytes(약 1GB) 할당마다 자동 덤프
# lg_prof_sample: 2^17 bytes(128KB) 단위로 샘플링

java -jar your-application.jar
```

#### 힙 덤프 생성 및 분석

```bash
# 실행 중 수동으로 덤프 생성
kill -USR2 <PID>

# 생성된 .heap 파일을 jeprof로 분석
jeprof --pdf /path/to/java /tmp/jeprof.<PID>.*.heap > output.pdf

# 특정 시점 간 증분 비교
jeprof --pdf /path/to/java \
  --base=/tmp/jeprof.<PID>.001.heap \
  /tmp/jeprof.<PID>.010.heap > diff.pdf
```

#### 분석 결과 해석 포인트

jeprof가 생성하는 콜 그래프에서 다음을 주의깊게 살펴본다.

- 전체 할당량에서 비중이 높은 함수 경로
- 시간이 지남에 따라 비중이 지속적으로 늘어나는 경로
- JNI 브릿지(`Java_*` 형태 함수명) 이후로 이어지는 할당 체인

### Step 3: Direct ByteBuffer 누수 진단

`ByteBuffer.allocateDirect()`로 할당한 메모리는 GC 대상이 아니다. `Cleaner`가 연결되어 있어 해당 객체가 GC될 때 해제되지만, GC가 늦게 발생하거나 참조가 유지되면 누수처럼 보인다.

```java
// 진단용: Direct Buffer 현재 사용량 확인
import java.lang.management.ManagementFactory;
import com.sun.management.OperatingSystemMXBean;
import java.nio.BufferPoolMXBean;
import java.nio.ByteBuffer;

public class DirectBufferMonitor {
    public static void printDirectBufferStats() {
        ManagementFactory.getPlatformMXBeans(BufferPoolMXBean.class)
            .stream()
            .filter(pool -> pool.getName().equals("direct"))
            .findFirst()
            .ifPresent(pool -> {
                System.out.printf("Direct Buffer Count: %d%n", pool.getCount());
                System.out.printf("Direct Buffer Used: %d MB%n", pool.getMemoryUsed() / 1024 / 1024);
                System.out.printf("Direct Buffer Capacity: %d MB%n", pool.getTotalCapacity() / 1024 / 1024);
            });
    }
}
```

```bash
# JVM 옵션으로 Direct Buffer 한도 설정 (과도한 할당 방지)
-XX:MaxDirectMemorySize=512m
```

---

## 주의사항 및 트레이드오프

### NMT 사용 시 주의사항

**성능 오버헤드**: `detail` 모드는 최대 10% 이상의 CPU/메모리 오버헤드를 유발할 수 있다. 운영 환경에서는 `summary` 모드를 기본으로 하고, 문제 재현 시에만 `detail` 모드를 적용하는 것을 권장한다.

**추적 범위의 한계**: NMT는 JVM이 관리하는 영역만 추적한다. JNI 라이브러리 내부에서 발생하는 할당은 보이지 않는다. NMT에서 이상이 없다면 반드시 jemalloc 같은 도구로 2차 분석을 진행해야 한다.

### jemalloc 사용 시 주의사항

**샘플링 오버헤드**: `lg_prof_sample` 값을 낮출수록 정확도는 높아지지만 오버헤드도 커진다. `lg_prof_sample=17`(128KB 단위)은 일반적으로 적절한 균형점이다.

**덤프 파일 크기**: `lg_prof_interval`을 너무 낮게 설정하면 디스크가 빠르게 채워질 수 있다. 적절한 인터벌 설정과 덤프 파일 자동 정리 스크립트를 함께 운영하라.

**운영 환경 적용**: jemalloc 프로파일링은 JVM 기동 시 활성화해야 하므로, 운영 환경 적용은 롤링 배포나 별도 인스턴스를 통해 진행하는 것이 안전하다.

### glibc malloc과의 관계

Linux 기본 `malloc`(glibc)은 멀티스레드 환경에서 메모리 단편화로 인해 OS에 반환하지 않는 메모리가 쌓이는 현상이 발생한다. 이를 실제 누수로 오진하는 경우가 있다. `MALLOC_ARENA_MAX=2` 또는 `MALLOC_MMAP_THRESHOLD_` 튜닝으로 완화할 수 있으며, jemalloc 자체를 기본 할당자로 사용하는 것만으로도 이 문제를 줄일 수 있다.

```bash
# glibc 단편화 완화 옵션
export MALLOC_ARENA_MAX=2
export MALLOC_MMAP_THRESHOLD_=131072
```

---

## 정리

Java Native Memory Leak은 단일 도구로 해결되지 않는다. 문제를 체계적으로 접근하기 위한 진단 흐름을 다음과 같이 정리할 수 있다.

```
RSS 지속 증가 확인
        │
        ▼
  힙 사용량 정상?
   YES ──────► Native Memory 문제로 판단
        │
        ▼
NMT(summary) 로 JVM 영역별 추적
        │
   특정 영역 증가 확인?
   YES ──────► 해당 영역 집중 분석 (Metaspace, Thread, Code 등)
   NO  ──────► JVM 외부 가능성
        │
        ▼
jemalloc 프로파일링 적용
        │
        ▼
콜 그래프에서 증가 패턴 식별
        │
        ▼
JNI 라이브러리 / glibc 단편화 검토
```

핵심은 **힙 밖을 보는 습관**이다. 모니터링 대시보드에 `process.resident_memory_bytes` 같은 OS 레벨 메트릭을 반드시 포함시키고, 힙 지표와의 괴리가 발생하면 즉시 Native Memory 쪽을 의심해야 한다. NMT와 jemalloc을 조합하면 JVM이 관리하는 영역부터 JNI 라이브러리 내부까지 전 영역을 커버할 수 있다.
