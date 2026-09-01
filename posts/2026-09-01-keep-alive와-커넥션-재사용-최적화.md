# Keep-Alive와 커넥션 재사용 최적화

## 개요

HTTP 통신에서 매 요청마다 새로운 TCP 커넥션을 맺는다면 어떤 일이 벌어질까? 3-way handshake에 소요되는 레이턴시, TLS라면 추가로 발생하는 핸드셰이크 오버헤드, 그리고 TCP의 Slow Start로 인한 초기 처리량 제한까지. 대규모 트래픽 환경에서는 이 오버헤드가 누적되어 성능 병목의 주요 원인이 된다.

**Keep-Alive**는 이 문제를 해결하기 위한 핵심 메커니즘이다. 하나의 TCP 커넥션을 여러 HTTP 요청/응답에 재사용함으로써 커넥션 수립 비용을 줄이고, 처리량을 높인다. 이 글에서는 Keep-Alive의 동작 원리부터 Spring Boot 환경에서의 실전 설정, 그리고 커넥션 풀 최적화까지 실무에서 바로 적용 가능한 내용을 다룬다.

---

## 핵심 개념

### TCP 커넥션 수립 비용

TCP 커넥션 하나를 열기 위해 클라이언트와 서버는 3-way handshake를 수행한다. 일반적인 왕복 지연(RTT)이 10ms라고 가정하면, 핸드셰이크만으로 최소 1.5 RTT(약 15ms)가 소모된다. HTTPS 환경에서는 TLS handshake가 추가되어 2~3 RTT가 더 필요하다.

```
Client          Server
  |--SYN--------->|
  |<------SYN-ACK-|
  |--ACK--------->|   ← 여기서 비로소 데이터 전송 가능
  |--TLS Hello--->|   ← HTTPS라면 추가 핸드셰이크
  |<--TLS Hello---|
  ...
```

초당 1,000건의 요청을 처리하는 서비스에서 매번 커넥션을 새로 맺는다면, 핸드셰이크 오버헤드만으로 상당한 자원이 낭비된다.

### HTTP Keep-Alive의 동작 방식

HTTP/1.0에서는 기본적으로 각 요청 후 커넥션을 닫았다. HTTP/1.1부터 **Persistent Connection**이 기본값으로 채택되었고, `Connection: keep-alive` 헤더를 통해 커넥션 유지를 명시한다.

```
GET /api/data HTTP/1.1
Host: example.com
Connection: keep-alive
Keep-Alive: timeout=60, max=1000
```

- `timeout`: 커넥션을 유지할 최대 유휴 시간(초)
- `max`: 해당 커넥션으로 처리할 최대 요청 수

서버는 응답 헤더에 동일한 정보를 포함하여 클라이언트에 알린다.

### HTTP/2와 멀티플렉싱

HTTP/2는 Keep-Alive를 넘어 **하나의 커넥션 위에서 여러 스트림을 동시에 처리**하는 멀티플렉싱을 지원한다. HTTP/1.1의 Head-of-Line Blocking 문제를 해결하며, 실질적으로 더 적은 커넥션으로 더 높은 처리량을 달성한다.

```
HTTP/1.1 (Keep-Alive)         HTTP/2 (Multiplexing)
─────────────────────         ─────────────────────
[Conn1] Req1 → Res1           [Conn1] Req1 ──→ Res1
[Conn1] Req2 → Res2                    Req2 ──→ Res2  (동시)
[Conn1] Req3 → Res3                    Req3 ──→ Res3  (동시)
```

---

## 실전 예제

### Spring Boot + Tomcat 설정

Spring Boot의 내장 Tomcat에서 Keep-Alive 관련 설정은 `application.yml`로 제어할 수 있다.

```yaml
server:
  tomcat:
    # 커넥션 타임아웃 (Keep-Alive 유휴 타임아웃)
    connection-timeout: 20000        # 20초
    # 최대 커넥션 수
    max-connections: 8192
    # 요청 처리 스레드 풀
    threads:
      max: 200
      min-spare: 10
    # Keep-Alive 요청 최대 수 (-1은 무제한)
    max-keep-alive-requests: 100
  # HTTP/2 활성화
  http2:
    enabled: true
```

Tomcat의 `KeepAliveTimeout`은 `connection-timeout`과 별도로 커스터마이징할 수 있다.

```java
@Configuration
public class TomcatConfig {

    @Bean
    public WebServerFactoryCustomizer<TomcatServletWebServerFactory> tomcatCustomizer() {
        return factory -> factory.addConnectorCustomizers(connector -> {
            connector.setProperty("keepAliveTimeout", "15000");   // 15초
            connector.setProperty("maxKeepAliveRequests", "200"); // 최대 200 요청
        });
    }
}
```

### RestTemplate vs WebClient 커넥션 풀 설정

외부 API를 호출하는 클라이언트 측에서도 커넥션 재사용이 중요하다. `RestTemplate`은 기본적으로 커넥션 풀을 사용하지 않기 때문에, **Apache HttpClient**를 명시적으로 연결해야 한다.

```java
@Configuration
public class HttpClientConfig {

    @Bean
    public PoolingHttpClientConnectionManager connectionManager() {
        PoolingHttpClientConnectionManager manager =
            new PoolingHttpClientConnectionManager();
        manager.setMaxTotal(200);            // 전체 최대 커넥션
        manager.setDefaultMaxPerRoute(50);   // 호스트당 최대 커넥션
        return manager;
    }

    @Bean
    public CloseableHttpClient httpClient(
            PoolingHttpClientConnectionManager connectionManager) {
        return HttpClients.custom()
            .setConnectionManager(connectionManager)
            .setKeepAliveStrategy((response, context) -> {
                // 서버 응답의 Keep-Alive timeout을 파싱하거나 기본값 사용
                HeaderElementIterator it = new BasicHeaderElementIterator(
                    response.headerIterator(HTTP.CONN_KEEP_ALIVE));
                while (it.hasNext()) {
                    HeaderElement he = it.nextElement();
                    if ("timeout".equalsIgnoreCase(he.getName())) {
                        return Long.parseLong(he.getValue()) * 1000;
                    }
                }
                return 30_000L; // 기본 30초
            })
            .evictExpiredConnections()
            .evictIdleConnections(Duration.ofSeconds(60))
            .build();
    }

    @Bean
    public RestTemplate restTemplate(CloseableHttpClient httpClient) {
        HttpComponentsClientHttpRequestFactory factory =
            new HttpComponentsClientHttpRequestFactory(httpClient);
        factory.setConnectTimeout(3000);
        factory.setReadTimeout(5000);
        return new RestTemplate(factory);
    }
}
```

### WebClient (Reactor Netty) 커넥션 풀 설정

WebFlux 환경에서는 Reactor Netty의 커넥션 풀을 활용한다.

```java
@Configuration
public class WebClientConfig {

    @Bean
    public WebClient webClient() {
        ConnectionProvider provider = ConnectionProvider.builder("custom-pool")
            .maxConnections(200)
            .maxIdleTime(Duration.ofSeconds(30))
            .maxLifeTime(Duration.ofSeconds(60))
            .pendingAcquireTimeout(Duration.ofSeconds(5))
            .evictInBackground(Duration.ofSeconds(120))
            .build();

        HttpClient httpClient = HttpClient.create(provider)
            .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, 3000)
            .responseTimeout(Duration.ofSeconds(5))
            .protocol(HttpProtocol.HTTP11, HttpProtocol.H2); // HTTP/2 지원

        return WebClient.builder()
            .clientConnector(new ReactorClientHttpConnector(httpClient))
            .build();
    }
}
```

### 커넥션 상태 모니터링

Micrometer와 Actuator를 통해 커넥션 풀 상태를 실시간으로 모니터링할 수 있다.

```java
@Component
@RequiredArgsConstructor
public class ConnectionPoolMetrics {

    private final PoolingHttpClientConnectionManager connectionManager;
    private final MeterRegistry meterRegistry;

    @PostConstruct
    public void bindMetrics() {
        Gauge.builder("http.client.pool.available",
                connectionManager, cm -> cm.getTotalStats().getAvailable())
            .description("사용 가능한 커넥션 수")
            .register(meterRegistry);

        Gauge.builder("http.client.pool.leased",
                connectionManager, cm -> cm.getTotalStats().getLeased())
            .description("현재 사용 중인 커넥션 수")
            .register(meterRegistry);

        Gauge.builder("http.client.pool.pending",
                connectionManager, cm -> cm.getTotalStats().getPending())
            .description("커넥션 획득 대기 요청 수")
            .register(meterRegistry);
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. Keep-Alive timeout 불일치 문제 (Half-Open Connection)

서버의 Keep-Alive timeout보다 클라이언트의 커넥션 재사용 시간이 길 경우, 서버가 먼저 커넥션을 닫은 상태에서 클라이언트가 요청을 보내는 **Half-Open Connection** 문제가 발생한다. 이는 `Connection reset by peer` 오류로 나타난다.

**해결 방법:**

```java
// 클라이언트의 Keep-Alive 시간을 서버보다 짧게 설정
manager.setKeepAliveStrategy((response, context) -> {
    // 서버 timeout이 60초라면, 클라이언트는 50초로 설정
    return 50_000L;
});

// 또는 유효성 검사 활성화
httpClient = HttpClients.custom()
    .setConnectionManager(connectionManager)
    .setConnectionManagerShared(false)
    // 커넥션 사용 전 유효성 검사
    .evictExpiredConnections()
    .build();
```

Nginx를 리버스 프록시로 사용 중이라면 nginx의 `keepalive_timeout`과 Spring Boot의 설정을 함께 조율해야 한다.

```nginx
upstream backend {
    server localhost:8080;
    keepalive 32;          # upstream 커넥션 풀 크기
    keepalive_timeout 30s; # upstream Keep-Alive 시간
}

server {
    keepalive_timeout 65s; # 클라이언트 ↔ Nginx Keep-Alive
    keepalive_requests 1000;
}
```

### 2. 커넥션 풀 고갈

`maxPerRoute` 설정이 낮거나 응답이 느린 외부 서비스에 연결할 때 커넥션 풀이 고갈되어 새로운 요청이 블로킹될 수 있다. Pending 수가 급증하면 즉시 알람을 설정하라.

| 지표 | 경고 임계값 | 위험 임계값 |
|------|-----------|-----------|
| Pool Utilization | 70% | 90% |
| Pending Requests | 10 | 50 |
| Avg Acquire Time | 100ms | 500ms |

### 3. 로드밸런서 환경에서의 주의점

L4/L7 로드밸런서가 중간에 있을 경우, 로드밸런서 자체의 idle timeout이 서버와 클라이언트 설정보다 짧을 수 있다. AWS ALB의 기본 idle timeout은 **60초**이므로, 서버의 Keep-Alive timeout은 이보다 짧게 설정해야 한다.

```
AWS ALB (60s timeout)
    ↕
Spring Boot (keepAliveTimeout: 55s)  ← ALB보다 짧게!
    ↕
DB Connection Pool (maxLifetime: 1800s)
```

### 4. 메모리와 커넥션 수의 트레이드오프

커넥션을 많이 열어두면 재사용 성능은 높아지지만, 각 커넥션이 소켓 버퍼(기본 수십 KB)를 점유한다. 1,000개의 idle 커넥션은 수십~수백 MB의 메모리를 소비할 수 있다. 실제 트래픽 패턴에 맞게 `maxIdleTime`과 `maxTotal`을 조정해야 한다.

---

## 정리

Keep-Alive와 커넥션 재사용 최적화는 단순히 설정 값 하나를 변경하는 것이 아니라, **클라이언트 ↔ 프록시 ↔ 서버 ↔ 업스트림** 전 구간의 timeout과 풀 크기를 일관성 있게 설계하는 작업이다.

핵심 원칙을 정리하면 다음과 같다:

1. **클라이언트의 커넥션 유지 시간 < 서버의 Keep-Alive timeout** — Half-Open 방지
2. **서버의 Keep-Alive timeout < 로드밸런서의 idle timeout** — 중간 장비에 의한 커넥션 단절 방지
3. **커넥션 풀 크기는 실측 기반으로 설정** — 이론적 최대치보다 모니터링 데이터를 신뢰
4. **유효하지 않은 커넥션은 주기적으로 정리** — `evictExpiredConnections`, `evictIdleConnections` 활용
5. **가능하면 HTTP/2 도입** — 멀티플렉싱으로 커넥션 수 자체를 줄이는 것이 궁극적인 해법

처음에는 기본값으로 시작하고, Micrometer 지표를 통해 풀 고갈이나 대기 시간이 발생하는 구간을 찾아 점진적으로 튜닝하는 접근을 권장한다. 성급한 최적화보다 데이터 기반의 점진적 개선이 안정적인 서비스를 만드는 지름길이다.
