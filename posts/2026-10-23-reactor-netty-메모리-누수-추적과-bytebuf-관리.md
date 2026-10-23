# Reactor Netty 메모리 누수 추적과 ByteBuf 관리

## 개요

Spring WebFlux와 WebClient를 사용하는 서비스를 운영하다 보면 어느 순간 JVM 힙은 정상인데 OS 레벨에서 메모리가 지속적으로 증가하는 현상을 목격하게 된다. 쓰레드 덤프를 떠봐도 이상이 없고, GC 로그도 깔끔하다. 그런데 프로세스의 RSS(Resident Set Size)는 꾸준히 우상향한다.

이런 경우 대부분의 원인은 **Netty의 오프힙(off-heap) 메모리**, 즉 `ByteBuf`의 누수다. Reactor Netty는 Netty 위에 구축되어 있으며, 내부적으로 `PooledByteBufAllocator`를 통해 오프힙 다이렉트 메모리를 관리한다. JVM GC의 영향을 받지 않기 때문에 참조가 해제되지 않으면 그대로 메모리에 남는다.

이 포스팅에서는 `ByteBuf`의 수명 주기, 누수가 발생하는 전형적인 패턴, 그리고 실무에서 누수를 탐지하고 해결하는 방법을 다룬다.

---

## 핵심 개념

### ByteBuf의 참조 카운팅

Netty의 `ByteBuf`는 **참조 카운팅(Reference Counting)** 방식으로 메모리를 관리한다. 객체가 생성될 때 참조 카운트는 1이며, `retain()`을 호출하면 증가하고 `release()`를 호출하면 감소한다. 카운트가 0이 되면 메모리가 풀(Pool)로 반환된다.

```java
ByteBuf buf = Unpooled.buffer(256);
// refCnt = 1

buf.retain();
// refCnt = 2

buf.release();
// refCnt = 1

buf.release();
// refCnt = 0 → 메모리 반환
```

Reactor Netty는 파이프라인 내에서 `ByteBuf`의 생명주기를 자동으로 관리하지만, 개발자가 중간에 버퍼를 직접 다루는 순간 이 책임이 개발자에게 넘어온다.

### PooledByteBufAllocator와 오프힙 메모리

기본 설정에서 Reactor Netty는 `PooledByteBufAllocator.DEFAULT`를 사용한다. 이 할당자는 다이렉트 메모리(Direct Memory)를 풀링하여 재사용한다. 효율적이지만 `release()`가 누락되면 풀이 고갈되거나 OS 메모리가 지속적으로 증가하는 문제가 생긴다.

JVM 힙과는 별개로 `-XX:MaxDirectMemorySize`로 제한할 수 있지만, 이 값을 초과하면 `OutOfMemoryError: Direct buffer memory`가 발생한다.

### Reactor Netty의 자동 해제 규칙

Reactor Netty는 다음과 같은 규칙으로 `ByteBuf`를 자동 해제한다.

- **HTTP 응답 본문**: 구독자가 `DataBuffer`를 소비하면 자동 해제된다.
- **`flatMap` 내에서 변환**: 체인 중간에 버퍼를 변환하면, 원본 버퍼는 명시적으로 해제해야 한다.
- **`DataBufferUtils.retain()`**: 비동기 작업을 위해 버퍼를 보존할 때는 반드시 쌍을 이루는 해제가 필요하다.

---

## 실전 예제

### 누수 감지 활성화

가장 먼저 할 일은 Netty의 **ResourceLeakDetector**를 활성화하는 것이다. 기본값은 `SIMPLE`이지만, 개발/스테이징 환경에서는 `PARANOID`로 설정해야 누수 경로를 정확히 추적할 수 있다.

```java
// 애플리케이션 시작 시 설정
ResourceLeakDetector.setLevel(ResourceLeakDetector.Level.PARANOID);
```

또는 JVM 옵션으로 설정할 수 있다.

```bash
-Dio.netty.leakDetection.level=PARANOID
-Dio.netty.leakDetection.targetRecords=32
```

누수가 발생하면 로그에 다음과 같은 메시지가 출력된다.

```
LEAK: ByteBuf.release() was not called before it's garbage-collected.
Recent access records:
#1: Created at:
    io.netty.buffer.PooledByteBufAllocator.newDirectBuffer(PooledByteBufAllocator.java:...)
    ...
```

### 전형적인 누수 패턴과 수정

**패턴 1: WebClient 응답을 직접 변환할 때**

```java
// ❌ 잘못된 예 - DataBuffer를 소비하지 않고 변환만 함
public Mono<String> fetchData(String url) {
    return webClient.get()
        .uri(url)
        .retrieve()
        .bodyToFlux(DataBuffer.class)
        .map(dataBuffer -> {
            // dataBuffer를 String으로 변환 후 원본을 해제하지 않음
            byte[] bytes = new byte[dataBuffer.readableByteCount()];
            dataBuffer.read(bytes);
            return new String(bytes, StandardCharsets.UTF_8);
            // dataBuffer.release() 누락!
        })
        .reduce("", String::concat);
}

// ✅ 올바른 예 - try-finally로 반드시 해제
public Mono<String> fetchData(String url) {
    return webClient.get()
        .uri(url)
        .retrieve()
        .bodyToFlux(DataBuffer.class)
        .map(dataBuffer -> {
            try {
                byte[] bytes = new byte[dataBuffer.readableByteCount()];
                dataBuffer.read(bytes);
                return new String(bytes, StandardCharsets.UTF_8);
            } finally {
                DataBufferUtils.release(dataBuffer);
            }
        })
        .reduce("", String::concat);
}
```

**패턴 2: DataBufferUtils를 활용한 안전한 처리**

Spring의 `DataBufferUtils`는 `ByteBuf` 관리를 추상화하는 유틸리티를 제공한다. 직접 `ByteBuf`를 다루는 것보다 이 API를 사용하는 것이 훨씬 안전하다.

```java
public Mono<String> safeBodyRead(String url) {
    return webClient.get()
        .uri(url)
        .retrieve()
        .bodyToFlux(DataBuffer.class)
        .as(DataBufferUtils::join) // 여러 청크를 하나로 합침
        .map(dataBuffer -> {
            try {
                return dataBuffer.toString(StandardCharsets.UTF_8);
            } finally {
                DataBufferUtils.release(dataBuffer);
            }
        });
}
```

**패턴 3: 커스텀 ServerHttpResponseDecorator에서의 누수**

응답을 가로채거나 수정하는 필터를 작성할 때 특히 주의가 필요하다.

```java
public class LoggingFilter implements WebFilter {

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, WebFilterChain chain) {
        ServerHttpResponse originalResponse = exchange.getResponse();
        DataBufferFactory bufferFactory = originalResponse.bufferFactory();

        ServerHttpResponseDecorator decoratedResponse = new ServerHttpResponseDecorator(originalResponse) {
            @Override
            public Mono<Void> writeWith(Publisher<? extends DataBuffer> body) {
                Flux<DataBuffer> modifiedBody = Flux.from(body)
                    .map(dataBuffer -> {
                        // ✅ 원본 버퍼를 읽고 새 버퍼를 생성한 뒤 원본을 해제
                        byte[] content = new byte[dataBuffer.readableByteCount()];
                        dataBuffer.read(content);
                        DataBufferUtils.release(dataBuffer); // 원본 반드시 해제

                        // 로깅
                        log.debug("Response body: {}", new String(content, StandardCharsets.UTF_8));

                        // 새 버퍼 반환 (프레임워크가 이후 해제를 담당)
                        return bufferFactory.wrap(content);
                    });

                return super.writeWith(modifiedBody);
            }
        };

        return chain.filter(exchange.mutate().response(decoratedResponse).build());
    }
}
```

### 메모리 사용량 모니터링

Micrometer와 Actuator를 활용하면 Netty의 메모리 상태를 실시간으로 모니터링할 수 있다.

```java
@Component
public class NettyMemoryMetrics {

    @PostConstruct
    public void registerMetrics() {
        PooledByteBufAllocator allocator = PooledByteBufAllocator.DEFAULT;
        PooledByteBufAllocatorMetric metric = allocator.metric();

        Gauge.builder("netty.pool.used.direct.memory",
                metric, PooledByteBufAllocatorMetric::usedDirectMemory)
            .description("Netty direct memory in use")
            .baseUnit("bytes")
            .register(Metrics.globalRegistry);

        Gauge.builder("netty.pool.used.heap.memory",
                metric, PooledByteBufAllocatorMetric::usedHeapMemory)
            .description("Netty heap memory in use")
            .baseUnit("bytes")
            .register(Metrics.globalRegistry);

        Gauge.builder("netty.pool.num.direct.arenas",
                metric, m -> (double) m.numDirectArenas())
            .description("Number of direct arenas")
            .register(Metrics.globalRegistry);
    }
}
```

Prometheus + Grafana 환경이라면 `netty.pool.used.direct.memory`가 지속적으로 증가하는지 대시보드로 관찰하는 것이 효과적이다.

---

## 주의사항 및 트레이드오프

### PARANOID 모드의 성능 비용

`ResourceLeakDetector.Level.PARANOID`는 모든 버퍼 접근 시 스택 트레이스를 기록하기 때문에 상당한 성능 저하를 유발한다. **프로덕션 환경에서는 절대 사용하지 말 것**. 프로덕션에서는 기본값인 `SIMPLE`을 유지하고, 누수가 의심될 때만 스테이징 환경에서 `PARANOID`를 활성화하는 워크플로우를 권장한다.

| 레벨 | 감지율 | 성능 영향 | 권장 환경 |
|---|---|---|---|
| DISABLED | 0% | 없음 | 최고 성능이 필요한 특수 상황 |
| SIMPLE | ~1% 샘플링 | 낮음 | 프로덕션 기본값 |
| ADVANCED | ~1% 샘플링 + 접근 기록 | 중간 | 개발/스테이징 |
| PARANOID | 100% | 매우 높음 | 디버깅 전용 |

### bodyToMono vs bodyToFlux의 선택

`bodyToMono(String.class)`와 같이 Spring이 제공하는 고수준 API를 사용하면 `DataBuffer`의 생명주기 관리를 프레임워크에 위임할 수 있다. 가능하다면 직접 `DataBuffer`를 다루는 대신 이 API를 우선 사용해야 한다.

단, 응답 본문이 매우 크거나 스트리밍이 필요한 경우에는 어쩔 수 없이 `bodyToFlux(DataBuffer.class)`를 써야 하는데, 이때는 앞서 소개한 패턴을 철저히 따라야 한다.

### `DataBufferUtils.retain()`의 함정

비동기 작업에서 `DataBuffer`를 다른 스레드로 전달하거나 지연 처리할 때 `DataBufferUtils.retain()`으로 참조 카운트를 증가시킨다. 이 경우 **처리가 완료된 시점에 반드시 `DataBufferUtils.release()`를 호출**해야 한다. 예외가 발생하더라도 해제가 보장되도록 `doOnError`, `doFinally` 같은 리액티브 후크를 활용하라.

```java
Flux<DataBuffer> retained = flux.map(buf -> {
    DataBufferUtils.retain(buf);
    return buf;
}).doFinally(signal -> {
    // 정상 종료, 에러, 취소 모든 경우에 해제
    DataBufferUtils.release(buffer);
});
```

### Heap vs Direct Buffer 선택

`Unpooled.heapBuffer()` 대신 `Unpooled.directBuffer()`를 선택하면 네트워크 I/O 성능은 향상되지만 GC의 안전망을 잃는다. 서비스의 특성에 따라 적절히 선택하되, 누수 위험성을 충분히 인지하고 있어야 한다.

---

## 정리

Reactor Netty의 메모리 누수 문제는 JVM 표준 도구만으로는 발견하기 어렵기 때문에 초기에 체계적인 접근이 필요하다.

핵심을 요약하면 다음과 같다.

1. **`DataBuffer`를 직접 다룰 때는 항상 `release()`를 책임진다.** `try-finally` 또는 리액티브 `doFinally`로 예외 상황도 보장하라.
2. **가능하면 `bodyToMono`, `bodyToFlux(SomeType.class)` 같은 고수준 API를 우선 사용하라.** 프레임워크가 생명주기를 관리한다.
3. **개발/스테이징 환경에서는 `ADVANCED` 이상의 누수 탐지를 활성화하라.** 문제를 조기에 발견하는 것이 핵심이다.
4. **Micrometer로 `netty.pool.used.direct.memory`를 지속 모니터링하라.** 점진적인 증가는 누수의 신호다.
5. **`ServerHttpResponseDecorator`나 커스텀 필터에서는 특히 주의하라.** 응답 본문을 가로채는 코드는 버그가 숨기 쉬운 영역이다.

리액티브 스택에서 메모리 누수는 오랜 시간이 지난 후에야 증상이 드러나는 경우가 많다. 코드 리뷰 시 `DataBuffer`를 직접 다루는 코드를 발견했다면 반드시 해제 경로를 확인하는 습관을 팀 문화로 정착시키는 것이 장기적으로 가장 효과적인 예방책이다.
