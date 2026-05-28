# Long Polling vs SSE vs WebSocket 실시간 통신 비교

## 개요

실시간 통신은 현대 웹 애플리케이션에서 필수 요소가 되었다. 채팅, 알림, 라이브 대시보드, 협업 도구 등 다양한 기능에서 서버와 클라이언트 간의 실시간 데이터 교환이 요구된다. 이를 구현하는 대표적인 방법으로 **Long Polling**, **SSE(Server-Sent Events)**, **WebSocket** 세 가지가 있다.

각 기술은 서로 다른 트레이드오프를 가지며, 상황에 따라 최적의 선택이 달라진다. 이 글에서는 각 기술의 동작 원리와 Spring Boot 기반 구현 예제, 그리고 실무에서 선택 기준을 다룬다.

---

## 핵심 개념

### Long Polling

HTTP 기반의 가장 단순한 실시간 통신 방식이다. 클라이언트가 요청을 보내면 서버는 새로운 데이터가 생길 때까지 응답을 보류(hold)한다. 데이터가 준비되거나 타임아웃이 발생하면 응답을 반환하고, 클라이언트는 즉시 다음 요청을 재전송한다.

```
Client → [HTTP Request] → Server (대기)
Server → [HTTP Response + Data] → Client
Client → [HTTP Request] → Server (대기) ...반복
```

**특징:**
- 기존 HTTP 인프라와 완벽 호환
- 연결마다 새로운 요청/응답 사이클 발생
- 서버 리소스 점유 (쓰레드 또는 커넥션)
- 네트워크 지연과 헤더 오버헤드 존재

---

### SSE (Server-Sent Events)

HTTP 위에서 서버에서 클라이언트로의 단방향 스트리밍을 제공한다. 단일 HTTP 연결을 유지하면서 서버가 지속적으로 이벤트를 push할 수 있다. `text/event-stream` 콘텐츠 타입을 사용하며, 브라우저 내장 `EventSource` API로 쉽게 소비할 수 있다.

```
Client → [HTTP Request] → Server
Server → [Event Stream...] → Client (연결 유지)
          data: {...}
          data: {...}
          data: {...}
```

**특징:**
- HTTP/1.1과 HTTP/2 모두 지원
- 단방향 통신 (서버 → 클라이언트)
- 자동 재연결 및 이벤트 ID 지원 (브라우저 네이티브)
- 텍스트 기반 프로토콜

---

### WebSocket

HTTP 핸드셰이크 이후 TCP 기반의 전이중(full-duplex) 연결을 유지한다. 클라이언트와 서버 모두 언제든지 데이터를 송수신할 수 있으며, 프레이밍 프로토콜로 낮은 오버헤드를 제공한다.

```
Client → [HTTP Upgrade Request] → Server
Server → [101 Switching Protocols] → Client
Client ←→ [Persistent TCP Connection] ←→ Server
```

**특징:**
- 양방향 실시간 통신
- 낮은 레이턴시와 오버헤드
- 바이너리/텍스트 데이터 모두 지원
- 별도 프록시 및 방화벽 설정 필요할 수 있음

---

## 실전 예제 (Spring Boot)

### Long Polling 구현

Spring의 `DeferredResult`를 활용해 비동기 Long Polling을 구현한다.

```java
@RestController
@RequestMapping("/api/polling")
public class LongPollingController {

    private final ConcurrentLinkedQueue<DeferredResult<ResponseEntity<String>>> waitingClients
            = new ConcurrentLinkedQueue<>();

    @GetMapping("/messages")
    public DeferredResult<ResponseEntity<String>> pollMessages() {
        // 30초 타임아웃 설정
        DeferredResult<ResponseEntity<String>> deferredResult =
                new DeferredResult<>(30_000L, ResponseEntity.noContent().build());

        waitingClients.add(deferredResult);

        deferredResult.onCompletion(() -> waitingClients.remove(deferredResult));
        deferredResult.onTimeout(() -> waitingClients.remove(deferredResult));

        return deferredResult;
    }

    // 메시지 발행 (예: 다른 클라이언트가 메시지 전송 시 호출)
    @PostMapping("/publish")
    public ResponseEntity<Void> publishMessage(@RequestBody String message) {
        waitingClients.forEach(client ->
                client.setResult(ResponseEntity.ok(message)));
        waitingClients.clear();
        return ResponseEntity.ok().build();
    }
}
```

클라이언트 측 JavaScript:

```javascript
async function longPoll() {
    try {
        const response = await fetch('/api/polling/messages');
        if (response.status === 200) {
            const data = await response.text();
            console.log('Received:', data);
        }
    } catch (e) {
        console.error('Poll error:', e);
        await new Promise(resolve => setTimeout(resolve, 1000)); // 재시도 딜레이
    } finally {
        longPoll(); // 즉시 재요청
    }
}
longPoll();
```

---

### SSE 구현

Spring의 `SseEmitter`를 사용한다.

```java
@RestController
@RequestMapping("/api/sse")
public class SseController {

    private final Map<String, SseEmitter> emitters = new ConcurrentHashMap<>();

    @GetMapping(value = "/subscribe/{userId}", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter subscribe(@PathVariable String userId) {
        // 0L = 타임아웃 없음 (Nginx 등 프록시 설정에 따라 조정 필요)
        SseEmitter emitter = new SseEmitter(0L);

        emitters.put(userId, emitter);

        emitter.onCompletion(() -> emitters.remove(userId));
        emitter.onTimeout(() -> emitters.remove(userId));
        emitter.onError(e -> emitters.remove(userId));

        // 초기 연결 확인 이벤트 전송
        try {
            emitter.send(SseEmitter.event()
                    .name("connect")
                    .data("connected"));
        } catch (IOException e) {
            emitter.completeWithError(e);
        }

        return emitter;
    }

    @PostMapping("/notify/{userId}")
    public ResponseEntity<Void> notifyUser(
            @PathVariable String userId,
            @RequestBody NotificationDto notification) {

        SseEmitter emitter = emitters.get(userId);
        if (emitter != null) {
            try {
                emitter.send(SseEmitter.event()
                        .id(String.valueOf(System.currentTimeMillis()))
                        .name("notification")
                        .data(notification));
            } catch (IOException e) {
                emitters.remove(userId);
                emitter.completeWithError(e);
            }
        }
        return ResponseEntity.ok().build();
    }
}
```

클라이언트 측 JavaScript:

```javascript
const eventSource = new EventSource('/api/sse/subscribe/user123');

eventSource.addEventListener('notification', (event) => {
    const data = JSON.parse(event.data);
    console.log('Notification:', data);
});

eventSource.addEventListener('connect', (event) => {
    console.log('SSE Connected:', event.data);
});

eventSource.onerror = (error) => {
    console.error('SSE Error:', error);
    // EventSource는 자동으로 재연결 시도
};
```

---

### WebSocket 구현 (STOMP over WebSocket)

Spring WebSocket + STOMP를 활용한 채팅 예제:

```java
@Configuration
@EnableWebSocketMessageBroker
public class WebSocketConfig implements WebSocketMessageBrokerConfigurer {

    @Override
    public void configureMessageBroker(MessageBrokerRegistry config) {
        config.enableSimpleBroker("/topic", "/queue");
        config.setApplicationDestinationPrefixes("/app");
    }

    @Override
    public void registerStompEndpoints(StompEndpointRegistry registry) {
        registry.addEndpoint("/ws")
                .setAllowedOriginPatterns("*")
                .withSockJS(); // SockJS fallback 지원
    }
}

@Controller
public class ChatController {

    @MessageMapping("/chat.send")
    @SendTo("/topic/public")
    public ChatMessage sendMessage(ChatMessage message) {
        message.setTimestamp(LocalDateTime.now());
        return message;
    }

    @MessageMapping("/chat.private")
    @SendToUser("/queue/messages")
    public ChatMessage sendPrivateMessage(ChatMessage message, Principal principal) {
        message.setSender(principal.getName());
        return message;
    }
}
```

클라이언트 측 JavaScript (STOMP.js):

```javascript
import { Client } from '@stomp/stompjs';

const client = new Client({
    brokerURL: 'ws://localhost:8080/ws',
    onConnect: () => {
        // 공개 채널 구독
        client.subscribe('/topic/public', (message) => {
            const chatMessage = JSON.parse(message.body);
            console.log('Public:', chatMessage);
        });

        // 메시지 전송
        client.publish({
            destination: '/app/chat.send',
            body: JSON.stringify({ content: 'Hello!', sender: 'user123' })
        });
    },
    onDisconnect: () => console.log('Disconnected'),
    reconnectDelay: 5000,
});

client.activate();
```

---

## 주의사항 및 트레이드오프

### 스케일 아웃 문제

가장 중요한 실무 이슈다. **Sticky Session 없이 수평 확장할 경우** 연결 유지 방식 모두 문제가 발생한다.

- **Long Polling**: 상태를 서버 메모리에 저장하면 인스턴스 간 공유 불가. Redis Pub/Sub 또는 메시지 큐로 해결.
- **SSE**: 각 인스턴스가 독립적으로 `SseEmitter`를 관리. Redis Pub/Sub으로 이벤트를 모든 인스턴스에 브로드캐스트해야 함.
- **WebSocket**: 세션이 특정 인스턴스에 바인딩됨. STOMP + 외부 메시지 브로커(RabbitMQ, Kafka) 사용을 권장.

```yaml
# Spring WebSocket 외부 브로커 설정 예시 (RabbitMQ)
spring:
  rabbitmq:
    host: localhost
    port: 5672
```

```java
@Override
public void configureMessageBroker(MessageBrokerRegistry config) {
    // 인메모리 대신 외부 브로커 사용
    config.enableStompBrokerRelay("/topic", "/queue")
            .setRelayHost("localhost")
            .setRelayPort(61613);
}
```

---

### 연결 수 제한

| 항목 | Long Polling | SSE | WebSocket |
|------|-------------|-----|-----------|
| HTTP/1.1 브라우저 연결 제한 | 6개/도메인 | 6개/도메인 | 제한 없음 |
| HTTP/2 | 다중화로 해소 | 다중화로 해소 | 별도 연결 |
| 서버 리소스 | 요청당 쓰레드 | 연결당 쓰레드/이벤트 | 연결당 핸들러 |

SSE는 HTTP/2 환경에서 단일 TCP 커넥션에 다중화되어 연결 수 제한 문제가 크게 해소된다. 반면 HTTP/1.1 환경에서는 도메인당 6개 연결 제한으로 SSE 탭을 여러 개 열면 금세 한계에 도달한다.

---

### 프록시 및 방화벽

- **Long Polling**: 표준 HTTP라 프록시 설정 불필요
- **SSE**: 일부 프록시(Nginx)에서 버퍼링 문제 발생. `proxy_buffering off` 설정 필요
- **WebSocket**: `Upgrade` 헤더 지원 확인 필요. 일부 기업 방화벽에서 차단

```nginx
# SSE Nginx 설정
location /api/sse/ {
    proxy_pass http://backend;
    proxy_buffering off;
    proxy_cache off;
    proxy_set_header Connection '';
    proxy_http_version 1.1;
    chunked_transfer_encoding on;
}

# WebSocket Nginx 설정
location /ws {
    proxy_pass http://backend;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 86400;
}
```

---

### 어떤 기술을 선택할까?

| 상황 | 권장 기술 |
|------|----------|
| 단순 알림, 피드 업데이트 (단방향) | **SSE** |
| 채팅, 게임, 협업 도구 (양방향) | **WebSocket** |
| 레거시 인프라, 단순 폴링 | **Long Polling** |
| 이미 HTTP/2 인프라 보유 + 단방향 | **SSE** |
| 초저지연 바이너리 통신 | **WebSocket** |

---

## 정리

세 가지 기술은 각기 다른 목적에 최적화되어 있다.

- **Long Polling**은 가장 단순하고 호환성이 높지만, 반복되는 HTTP 오버헤드와 서버 리소스 낭비가 단점이다. 기존 REST 인프라를 그대로 활용해야 하거나, 이벤트 빈도가 낮고 레거시 환경일 때 고려할 수 있다.

- **SSE**는 단방향 실시간 스트리밍에 가장 적합하다. 브라우저 네이티브 지원, 자동 재연결, HTTP/2와의 시너지가 강점이다. 서버 → 클라이언트 푸시만 필요한 대부분의 알림 시나리오에서 WebSocket보다 심플하고 효율적인 선택이다.

- **WebSocket**은 양방향 저지연 통신이 필요할 때 선택한다. STOMP와 외부 메시지 브로커를 결합하면 엔터프라이즈 수준의 확장성도 확보할 수 있다. 단, 스케일 아웃 전략과 프록시 설정에 신경 써야 한다.

실무에서는 "일단 WebSocket"이 아니라, 통신 방향과 인프라 환경을 먼저 분석하고 가장 단순한 솔루션부터 고려하는 접근이 중요하다. 많은 경우 SSE만으로도 요구사항을 충분히 만족시킬 수 있다.