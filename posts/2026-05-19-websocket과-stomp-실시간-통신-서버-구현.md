# WebSocket과 STOMP 실시간 통신 서버 구현

## 개요

실시간 통신은 현대 웹 애플리케이션에서 빠질 수 없는 요소가 되었다. 채팅, 주식 시세, 알림 시스템, 라이브 협업 도구 등 수많은 서비스가 서버-클라이언트 간 양방향 통신을 요구한다. HTTP 폴링(Polling)이나 롱 폴링(Long Polling) 방식은 오버헤드가 크고 지연이 발생하기 때문에, 현업에서는 WebSocket을 기반으로 한 실시간 통신이 사실상 표준으로 자리잡았다.

그런데 WebSocket 자체는 저수준(low-level) 프로토콜이다. 메시지 형식, 라우팅, 구독 관리 등을 직접 구현해야 하므로 복잡도가 올라간다. 이를 해결하기 위해 **STOMP(Simple Text Oriented Messaging Protocol)** 를 WebSocket 위에 얹어 사용하는 방식이 널리 쓰인다. Spring은 `spring-websocket` 모듈을 통해 STOMP 기반 실시간 통신을 우아하게 지원한다.

이 글에서는 Spring Boot + STOMP + SockJS 조합으로 실무에서 바로 활용 가능한 실시간 통신 서버를 구현하는 방법을 다룬다. 단순한 채팅 예제를 넘어, 인증 처리, 메시지 브로커 연동, 에러 핸들링까지 실전 수준의 내용을 다룰 것이다.

---

## 핵심 개념

### WebSocket이란?

WebSocket은 HTTP 업그레이드 핸드셰이크를 통해 하나의 TCP 커넥션을 유지하면서 양방향 통신을 가능하게 하는 프로토콜이다. 최초 연결 이후에는 HTTP 오버헤드 없이 프레임 단위로 데이터를 주고받는다.

```
Client → Server: HTTP Upgrade Request
Server → Client: 101 Switching Protocols
↕ 이후 WebSocket 프레임으로 양방향 통신
```

### STOMP란?

STOMP는 WebSocket 위에서 동작하는 메시징 프로토콜로, 다음과 같은 기능을 제공한다.

- **CONNECT / DISCONNECT**: 세션 관리
- **SEND**: 특정 destination으로 메시지 전송
- **SUBSCRIBE / UNSUBSCRIBE**: 특정 topic 구독 관리
- **MESSAGE**: 서버가 구독자에게 메시지 전달

STOMP 덕분에 pub/sub 구조를 쉽게 구현할 수 있고, 메시지 브로커(RabbitMQ, ActiveMQ)와도 자연스럽게 연동된다.

### SockJS란?

WebSocket을 지원하지 않는 환경(일부 구형 브라우저, 프록시 설정)을 위한 폴백(fallback) 라이브러리다. 클라이언트는 SockJS를 통해 WebSocket이 가능하면 WebSocket을, 그렇지 않으면 HTTP 스트리밍이나 롱 폴링을 자동으로 선택한다.

---

## 실전 예제

### 의존성 설정

```xml
<!-- pom.xml -->
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-websocket</artifactId>
</dependency>
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-security</artifactId>
</dependency>
<!-- 외부 메시지 브로커 사용 시 -->
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-amqp</artifactId>
</dependency>
```

### WebSocket 설정

```java
@Configuration
@EnableWebSocketMessageBroker
public class WebSocketConfig implements WebSocketMessageBrokerConfigurer {

    @Override
    public void configureMessageBroker(MessageBrokerRegistry registry) {
        // 내장 브로커 사용 (Simple Broker)
        // 구독자에게 메시지를 전달할 prefix
        registry.enableSimpleBroker("/topic", "/queue");
        
        // 클라이언트가 서버로 메시지를 보낼 때 사용하는 prefix
        registry.setApplicationDestinationPrefixes("/app");
        
        // 특정 사용자에게 메시지를 보낼 때 사용하는 prefix
        registry.setUserDestinationPrefix("/user");
    }

    @Override
    public void registerStompEndpoints(StompEndpointRegistry registry) {
        registry.addEndpoint("/ws")
                .setAllowedOriginPatterns("*")  // 운영환경에서는 도메인 명시
                .withSockJS();                   // SockJS 폴백 활성화
    }
    
    @Override
    public void configureWebSocketTransport(WebSocketTransportRegistration registration) {
        registration.setMessageSizeLimit(128 * 1024);   // 128KB
        registration.setSendBufferSizeLimit(512 * 1024); // 512KB
        registration.setSendTimeLimit(20_000);           // 20초
    }
}
```

### 채팅 메시지 모델

```java
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class ChatMessage {
    
    public enum MessageType {
        ENTER, TALK, LEAVE
    }
    
    private MessageType type;
    private String roomId;
    private String sender;
    private String content;
    private LocalDateTime timestamp;
}
```

### 메시지 핸들러 컨트롤러

```java
@Controller
@RequiredArgsConstructor
@Slf4j
public class ChatController {

    private final SimpMessagingTemplate messagingTemplate;
    private final ChatRoomService chatRoomService;

    /**
     * /app/chat.send 로 들어온 메시지를 처리하여
     * /topic/room/{roomId} 구독자들에게 브로드캐스트
     */
    @MessageMapping("/chat.send/{roomId}")
    public void sendMessage(
            @DestinationVariable String roomId,
            @Payload ChatMessage message,
            SimpMessageHeaderAccessor headerAccessor) {
        
        String sender = (String) headerAccessor.getSessionAttributes().get("username");
        
        message.setSender(sender);
        message.setTimestamp(LocalDateTime.now());
        message.setType(ChatMessage.MessageType.TALK);
        
        log.info("Message received - room: {}, sender: {}", roomId, sender);
        
        // /topic/room/{roomId} 구독자 전체에게 전송
        messagingTemplate.convertAndSend("/topic/room/" + roomId, message);
    }

    /**
     * 특정 사용자에게만 메시지 전송 (1:1 DM)
     */
    @MessageMapping("/chat.dm")
    public void sendDirectMessage(
            @Payload ChatMessage message,
            Principal principal) {
        
        message.setSender(principal.getName());
        message.setTimestamp(LocalDateTime.now());
        
        // /user/{targetUser}/queue/dm 으로 특정 사용자에게만 전송
        messagingTemplate.convertAndSendToUser(
            message.getRoomId(), // 여기서는 수신자 username으로 활용
            "/queue/dm",
            message
        );
    }

    /**
     * 입장/퇴장 이벤트 처리
     */
    @MessageMapping("/chat.enter/{roomId}")
    public void enterRoom(
            @DestinationVariable String roomId,
            @Payload ChatMessage message,
            SimpMessageHeaderAccessor headerAccessor) {
        
        headerAccessor.getSessionAttributes().put("roomId", roomId);
        headerAccessor.getSessionAttributes().put("username", message.getSender());
        
        message.setContent(message.getSender() + "님이 입장하셨습니다.");
        message.setType(ChatMessage.MessageType.ENTER);
        message.setTimestamp(LocalDateTime.now());
        
        messagingTemplate.convertAndSend("/topic/room/" + roomId, message);
    }
}
```

### WebSocket 이벤트 리스너

연결/해제 이벤트를 캐치하여 퇴장 메시지를 자동 발송하는 패턴은 실무에서 매우 유용하다.

```java
@Component
@RequiredArgsConstructor
@Slf4j
public class WebSocketEventListener {

    private final SimpMessagingTemplate messagingTemplate;

    @EventListener
    public void handleWebSocketConnectListener(SessionConnectedEvent event) {
        StompHeaderAccessor accessor = StompHeaderAccessor.wrap(event.getMessage());
        log.info("WebSocket connected - sessionId: {}", accessor.getSessionId());
    }

    @EventListener
    public void handleWebSocketDisconnectListener(SessionDisconnectEvent event) {
        StompHeaderAccessor accessor = StompHeaderAccessor.wrap(event.getMessage());
        
        String username = (String) accessor.getSessionAttributes().get("username");
        String roomId = (String) accessor.getSessionAttributes().get("roomId");
        
        if (username != null && roomId != null) {
            log.info("User disconnected: {} from room: {}", username, roomId);
            
            ChatMessage leaveMessage = ChatMessage.builder()
                    .type(ChatMessage.MessageType.LEAVE)
                    .sender(username)
                    .roomId(roomId)
                    .content(username + "님이 퇴장하셨습니다.")
                    .timestamp(LocalDateTime.now())
                    .build();
            
            messagingTemplate.convertAndSend("/topic/room/" + roomId, leaveMessage);
        }
    }
}
```

### JWT 기반 인증 처리

WebSocket 연결 시 JWT 토큰을 검증하는 인터셉터를 추가한다.

```java
@Component
@RequiredArgsConstructor
public class JwtChannelInterceptor implements ChannelInterceptor {

    private final JwtTokenProvider jwtTokenProvider;

    @Override
    public Message<?> preSend(Message<?> message, MessageChannel channel) {
        StompHeaderAccessor accessor = MessageHeaderAccessor.getAccessor(
            message, StompHeaderAccessor.class
        );

        if (StompCommand.CONNECT.equals(accessor.getCommand())) {
            String token = accessor.getFirstNativeHeader("Authorization");
            
            if (token != null && token.startsWith("Bearer ")) {
                token = token.substring(7);
                
                if (jwtTokenProvider.validateToken(token)) {
                    Authentication auth = jwtTokenProvider.getAuthentication(token);
                    accessor.setUser(auth);
                    
                    // 세션에 사용자 정보 저장
                    accessor.getSessionAttributes()
                            .put("username", auth.getName());
                } else {
                    throw new IllegalArgumentException("Invalid JWT token");
                }
            }
        }
        return message;
    }
}

// WebSocketConfig에 인터셉터 등록
@Override
public void configureClientInboundChannel(ChannelRegistration registration) {
    registration.interceptors(jwtChannelInterceptor);
}
```

---

## 주의사항 및 트레이드오프

### 1. 내장 브로커 vs 외부 메시지 브로커

`enableSimpleBroker`는 단일 서버 환경에서만 동작한다. **다중 인스턴스(수평 확장)** 환경에서는 각 서버의 구독 정보가 공유되지 않아 메시지 유실이 발생한다.

이 경우 RabbitMQ나 ActiveMQ 같은 외부 브로커를 사용해야 한다.

```java
@Override
public void configureMessageBroker(MessageBrokerRegistry registry) {
    // 외부 브로커 사용 (STOMP relay)
    registry.enableStompBrokerRelay("/topic", "/queue")
            .setRelayHost("rabbitmq-host")
            .setRelayPort(61613)  // STOMP port
            .setClientLogin("guest")
            .setClientPasscode("guest")
            .setSystemLogin("guest")
            .setSystemPasscode("guest");
    
    registry.setApplicationDestinationPrefixes("/app");
}
```

### 2. 대규모 동시 접속 처리

WebSocket은 커넥션을 유지하므로 파일 디스크립터(fd) 제한에 걸릴 수 있다. 운영 환경에서는 반드시 OS 레벨 설정을 확인해야 한다.

```bash
# 현재 파일 디스크립터 제한 확인
ulimit -n

# /etc/security/limits.conf 수정
* soft nofile 65535
* hard nofile 65535
```

Spring WebSocket은 내부적으로 스레드 풀을 사용하므로, 설정을 적절히 튜닝해야 한다.

```java
@Override
public void configureClientInboundChannel(ChannelRegistration registration) {
    registration.taskExecutor()
                .corePoolSize(4)
                .maxPoolSize(10)
                .queueCapacity(100);
}
```

### 3. 연결 끊김 재연결 처리

클라이언트 측에서 반드시 재연결 로직을 구현해야 한다. SockJS는 일부 폴백을 제공하지만, 네트워크 불안정 시 재연결 전략이 필요하다.

### 4. 메시지 순서 보장

STOMP + Simple Broker는 메시지 순서를 완전히 보장하지 않는다. 금융, 게임 등 순서가 중요한 도메인에서는 Kafka나 RabbitMQ의 순서 보장 기능을 활용하거나, 메시지에 시퀀스 번호를 부여하는 방식을 고려해야 한다.

### 5. 보안 고려사항

- `setAllowedOriginPatterns("*")`는 개발 환경에서만 사용하고, 운영에서는 허용 도메인을 명시적으로 지정한다.
- STOMP CONNECT 헤더를 통한 인증뿐 아니라, HTTP 핸드셰이크 단계에서도 인증을 검증하는 이중 보안을 적용한다.
- 메시지 크기 제한(`setMessageSizeLimit`)을 반드시 설정해 DoS 공격을 방어한다.

---

## 정리

| 구분 | 단일 서버 | 다중 서버(스케일 아웃) |
|---|---|---|
| 메시지 브로커 | Simple Broker | RabbitMQ / ActiveMQ |
| 구독 관리 | 메모리 | 외부 브로커 |
| 구현 복잡도 | 낮음 | 높음 |
| 가용성 | 단일 장애점 | 고가용성 |

Spring WebSocket + STOMP 조합은 실시간 통신 기능을 빠르게 구현할 수 있는 강력한 방법이다. 내장 브로커만으로도 소규모 서비스는 충분히 운영 가능하지만, 서비스가 성장할수록 외부 메시지 브로커 도입과 수평 확장 전략을 미리 설계해 두는 것이 중요하다.

실무에서는 단순 구현 이상으로 **인증 처리, 에러 핸들링, 재연결 전략, 모니터링** 을 함께 고민해야 한다. 특히 WebSocket 연결 수, 메시지 처리량, 브로커 큐 상태를 Prometheus + Grafana로 모니터링하는 체계를 갖추면 장애 대응 속도를 크게 높일 수 있다.