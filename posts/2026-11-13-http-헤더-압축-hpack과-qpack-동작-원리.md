# HTTP 헤더 압축 HPACK과 QPACK 동작 원리

## 개요

HTTP/1.1 시대에는 헤더를 평문 텍스트로 전송했다. 요청마다 `User-Agent`, `Accept`, `Cookie`, `Authorization` 같은 헤더가 반복적으로 전송되었고, 이는 상당한 오버헤드를 초래했다. 특히 모바일 환경에서는 헤더 크기가 페이로드 크기를 초과하는 경우도 적지 않았다.

HTTP/2는 이 문제를 해결하기 위해 **HPACK**(RFC 7541)을 도입했고, HTTP/3(QUIC 기반)은 **QPACK**(RFC 9204)으로 이를 계승하면서 QUIC의 특성에 맞게 재설계했다. 두 알고리즘 모두 헤더 압축을 목적으로 하지만, 근본적인 설계 철학과 동작 방식에서 차이가 있다.

이 글에서는 HPACK과 QPACK의 내부 동작 원리를 깊이 있게 살펴보고, 실무에서 이를 어떻게 이해하고 활용할 수 있는지 정리한다.

---

## 핵심 개념

### HPACK의 구조

HPACK은 두 가지 핵심 테이블을 기반으로 동작한다.

#### 1. 정적 테이블 (Static Table)

RFC 7541에 사전 정의된 61개의 헤더 필드와 값 쌍이다. 예를 들어 인덱스 1은 `:authority`, 인덱스 2는 `:method: GET`, 인덱스 7은 `:scheme: https`다.

```
Index | Name                 | Value
------+----------------------+------------
1     | :authority           |
2     | :method              | GET
3     | :method              | POST
4     | :path                | /
5     | :path                | /index.html
...
61    | www-authenticate     |
```

정적 테이블에 있는 헤더는 단순히 인덱스 번호 하나로 표현할 수 있어 압축 효율이 매우 높다.

#### 2. 동적 테이블 (Dynamic Table)

정적 테이블에 없는 헤더나 커스텀 헤더는 동적 테이블에 추가된다. 동적 테이블은 FIFO(First In, First Out) 큐 방식으로 관리되며, 최대 크기(`SETTINGS_HEADER_TABLE_SIZE`)를 초과하면 오래된 항목부터 제거된다.

동적 테이블의 인덱스는 62번부터 시작한다.

```
Dynamic Table (최신 → 오래된 순)
Index | Name              | Value
------+-------------------+------------------
62    | authorization     | Bearer eyJhbGc...
63    | x-request-id      | abc-123
64    | content-type      | application/json
```

#### 3. Huffman 인코딩

HPACK은 테이블 인덱싱과 더불어 Huffman 인코딩을 선택적으로 사용할 수 있다. RFC 7541에 정의된 고정 Huffman 코드를 사용하며, 평균적으로 ASCII 문자열 대비 약 20~30% 압축 효율을 추가로 얻을 수 있다.

### HPACK의 세 가지 표현 방식

HPACK은 헤더 필드를 세 가지 방식으로 인코딩한다.

**1. 인덱스 헤더 필드 (Indexed Header Field)**
```
  0   1   2   3   4   5   6   7
+---+---+---+---+---+---+---+---+
| 1 |        Index (7+)         |
+---+---------------------------+
```
첫 비트가 1이면 인덱스 참조다. 7비트로 최대 127개의 인덱스를 직접 표현할 수 있고, 그 이상은 가변 길이 인코딩을 사용한다.

**2. 리터럴 + 동적 테이블 추가 (Literal with Indexing)**
```
  0   1   2   3   4   5   6   7
+---+---+---+---+---+---+---+---+
| 0 | 1 |      Index (6+)       |
+---+---+-----------------------+
```
헤더 이름은 인덱스로, 값은 리터럴로 전송하면서 동적 테이블에 추가한다.

**3. 리터럴 + 테이블 미추가 (Literal without Indexing)**
민감한 정보(예: `Set-Cookie`, `Authorization`)는 동적 테이블에 저장하지 않도록 명시적으로 지정할 수 있다. `Never Indexed` 플래그를 사용하면 중간 프록시도 테이블에 추가하지 않는다.

### HPACK의 HOL 블로킹 문제

HPACK의 치명적인 약점은 **동적 테이블이 TCP 스트림에 순서 의존적**이라는 것이다. HTTP/2는 TCP 위에서 동작하므로, 하나의 TCP 연결에서 패킷 손실이 발생하면 모든 스트림이 블로킹된다(Head-of-Line Blocking). 동적 테이블 업데이트 역시 순서가 보장되어야 하므로, HPACK은 HTTP/3의 QUIC 환경에 그대로 적용할 수 없었다.

---

### QPACK의 등장 배경

HTTP/3는 QUIC 위에서 동작하고, QUIC은 스트림 간 독립성을 보장한다. 즉 하나의 스트림에서 패킷 손실이 발생해도 다른 스트림은 영향을 받지 않는다. 그러나 HPACK의 동적 테이블은 테이블 업데이트 순서에 의존하므로, QUIC의 스트림 간 독립성과 충돌한다.

QPACK은 이 문제를 해결하기 위해 **헤더 블록과 동적 테이블 업데이트를 분리된 단방향 스트림으로 처리**한다.

### QPACK의 구조

QPACK은 세 가지 스트림을 사용한다.

```
클라이언트                          서버
    |                                |
    |-- 요청 스트림 (Stream N) ------>|  (헤더 블록 포함)
    |                                |
    |-- 인코더 스트림 (단방향) ------->|  (동적 테이블 업데이트)
    |<-- 디코더 스트림 (단방향) ------|  (ACK / 취소 신호)
```

**인코더 스트림**: 동적 테이블에 새 항목을 추가하는 명령을 전송  
**디코더 스트림**: 디코더가 특정 테이블 항목을 수신했음을 확인(ACK)하거나, 헤더 블록 처리 완료를 알림  
**요청/응답 스트림**: 실제 헤더 블록 전송

#### Required Insert Count (RIC)

QPACK 헤더 블록은 **Required Insert Count**라는 필드를 포함한다. 이는 헤더 블록을 디코딩하기 위해 동적 테이블에 최소 몇 개의 항목이 삽입되어 있어야 하는지를 나타낸다.

```
+------+-------+-----+--------+
| Req. | Sign  | ΔBase | Headers |
| Insert Count |      |         |
+------+-------+-----+--------+
```

디코더는 RIC를 확인하고, 아직 필요한 테이블 항목이 도착하지 않았다면 해당 헤더 블록 처리를 대기한다. 이를 통해 스트림 간 독립성을 유지하면서도 동적 테이블을 안전하게 사용할 수 있다.

---

## 실전 예제

### Java에서 HTTP/2 헤더 압축 동작 확인

```java
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

public class Http2HeaderCompressionDemo {

    public static void main(String[] args) throws Exception {
        HttpClient client = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_2)
                .connectTimeout(Duration.ofSeconds(10))
                .build();

        // 첫 번째 요청: 동적 테이블이 비어있으므로 헤더를 풀 사이즈로 전송
        HttpRequest firstRequest = HttpRequest.newBuilder()
                .uri(URI.create("https://example.com/api/users"))
                .header("Authorization", "Bearer eyJhbGciOiJIUzI1NiJ9...")
                .header("Content-Type", "application/json")
                .header("Accept-Encoding", "gzip, deflate, br")
                .header("X-Request-Id", "req-001")
                .GET()
                .build();

        HttpResponse<String> firstResponse = client.send(
                firstRequest, HttpResponse.BodyHandlers.ofString());

        System.out.println("HTTP Version: " + firstResponse.version());
        System.out.println("First request status: " + firstResponse.statusCode());

        // 두 번째 요청: 동적 테이블에 이전 헤더가 캐시되어 압축 효율 향상
        HttpRequest secondRequest = HttpRequest.newBuilder()
                .uri(URI.create("https://example.com/api/users/123"))
                .header("Authorization", "Bearer eyJhbGciOiJIUzI1NiJ9...")
                .header("Content-Type", "application/json")
                .header("Accept-Encoding", "gzip, deflate, br")
                .header("X-Request-Id", "req-002")
                .GET()
                .build();

        HttpResponse<String> secondResponse = client.send(
                secondRequest, HttpResponse.BodyHandlers.ofString());

        System.out.println("Second request status: " + secondResponse.statusCode());
        // 두 번째 요청부터 Authorization, Content-Type, Accept-Encoding은
        // 동적 테이블 인덱스로 대체되어 전송 크기가 대폭 감소
    }
}
```

### Wireshark / 패킷 분석으로 HPACK 확인

실무에서 HPACK 동작을 직접 확인하려면 Wireshark를 활용할 수 있다.

```bash
# TLS 키 로깅 활성화 (JVM 옵션)
-Djavax.net.debug=ssl:handshake

# 환경 변수로 TLS 세션 키 추출 (Chrome, curl 등)
export SSLKEYLOGFILE=~/ssl_keylog.txt

# curl로 HTTP/2 헤더 정보 확인
curl -v --http2 \
  -H "Authorization: Bearer token123" \
  -H "Content-Type: application/json" \
  -H "X-Custom-Header: value" \
  https://example.com/api/resource

# 두 번 연속 요청해서 압축 효과 비교
curl -v --http2 \
  -H "Authorization: Bearer token123" \
  -H "Content-Type: application/json" \
  -H "X-Custom-Header: value" \
  https://example.com/api/resource
```

### Spring Boot에서 민감 헤더 보호

HPACK/QPACK의 `Never Indexed` 옵션을 활용하려면, 애플리케이션 레벨에서 민감한 헤더를 적절히 처리해야 한다.

```java
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import jakarta.servlet.http.HttpServletResponse;

@RestController
public class SecureHeaderController {

    @GetMapping("/api/secure")
    public ResponseEntity<String> secureEndpoint(HttpServletResponse response) {
        // Set-Cookie는 HPACK에서 Never Indexed로 처리 권장
        // 프레임워크 레벨에서 자동 처리되는 경우가 많으나
        // 커스텀 민감 헤더는 직접 관리 필요

        return ResponseEntity.ok()
                // 민감 정보는 헤더보다 응답 바디에 담는 것이 더 안전
                .header("X-Session-Token", generateToken())
                .body("secure response");
    }

    // Netty (HTTP/2 서버) 설정에서 동적 테이블 크기 조정
    // application.yml:
    // server:
    //   http2:
    //     enabled: true
    // netty 설정은 Http2FrameCodecBuilder를 통해 커스터마이징
    private String generateToken() {
        return "generated-secure-token";
    }
}
```

```yaml
# application.yml - HTTP/2 활성화
server:
  http2:
    enabled: true
  ssl:
    enabled: true
    key-store: classpath:keystore.p12
    key-store-password: changeit
    key-store-type: PKCS12
```

---

## 주의사항 및 트레이드오프

### 1. 동적 테이블 크기 설정

동적 테이블 크기(`SETTINGS_HEADER_TABLE_SIZE`)는 메모리와 압축 효율 간의 트레이드오프다.

| 테이블 크기 | 압축 효율 | 메모리 사용 | 적합한 환경 |
|------------|----------|-----------|-----------|
| 작음 (4KB) | 낮음 | 낮음 | 메모리 제약 환경 |
| 중간 (64KB) | 높음 | 중간 | 일반 API 서버 |
| 큼 (256KB+) | 매우 높음 | 높음 | 헤더 종류가 많은 서비스 |

동시 연결이 수만 개인 환경에서는 연결당 테이블 메모리가 누적되므로, 무조건 큰 값이 좋지 않다.

### 2. CRIME/BREACH 공격 고려

HPACK/QPACK은 헤더 압축이므로, TLS 위에서 동작할 때 **CRIME 계열 공격**에 노출될 가능성이 있다. 공격자가 요청을 조작해 압축 결과 크기 변화를 관찰할 수 있다면, 비밀 헤더 값을 추론할 수 있다.

대응 방법:
- 민감한 헤더는 `Never Indexed` 플래그 사용
- `Authorization`, `Cookie` 헤더는 HPACK 사양에서 이미 주의를 권고
- 외부 공격자가 요청을 임의로 삽입하지 못하도록 CORS 및 인증 강화

### 3. QPACK의 블로킹 모드 선택

QPACK은 두 가지 모드를 지원한다.

**블로킹 모드**: 동적 테이블 항목이 도착할 때까지 헤더 디코딩을 대기. 압축 효율이 높지만 RTT 증가 가능성 있음.

**논블로킹 모드**: 동적 테이블을 사용하지 않고 정적 테이블과 리터럴만 사용. 압축 효율은 낮지만 HOL 블로킹 완전 차단.

```
# QPACK 설정 예시 (HTTP/3 서버 구성 - nginx 예시)
http {
    http3 on;
    
    # QPACK 동적 테이블 크기 (0으로 설정 시 논블로킹 모드)
    http3_hq_header_table_size 4096;
    
    # 최대 블로킹 스트림 수
    http3_max_header_size 16k;
}
```

레이턴시가 중요한 실시간 서비스에서는 논블로킹 모드를, 헤더가 많고 연결 재사용이 활발한 API 서버에서는 블로킹 모드를 고려한다.

### 4. 프록시/로드밸런서의 헤더 테이블 독립성

HPACK 동적 테이블은 **엔드포인트 간 독립적**이다. 클라이언트-프록시 구간과 프록시-서버 구간의 테이블은 완전히 별개다. 따라서 프록시가 헤더를 추가하거나 수정하면, 서버 측 동적 테이블 활용률이 떨어질 수 있다.

```
클라이언트 → (HPACK 테이블 A) → 프록시 → (HPACK 테이블 B) → 서버
```

L7 로드밸런서 뒤에 HTTP/2 백엔드를 운영할 때, 로드밸런서가 동적 테이블을 효율적으로 관리하고 있는지 모니터링이 필요하다.

---

## 정리

| 항목 | HPACK (HTTP/2) | QPACK (HTTP/3) |
|-----|---------------|---------------|
| 기반 프로토콜 | TCP | QUIC |
| 정적 테이블 | 61개 항목 | 99개 항목 |
| 동적 테이블 | 단일 스트림 의존 | 별도 인코더/디코더 스트림 |
| HOL 블로킹 | 존재 (TCP 의존) | 없음 (스트림 독립) |
| 블로킹 모드 | 항상 블로킹 | 선택 가능 |
| ACK 메커니즘 | 없음 | 디코더 스트림으로 ACK |

HPACK과 QPACK은 단순한 압축 알고리즘이 아니라, 각 프로토콜의 특성에 맞게 설계된 **상태 기계(State Machine)** 다. 동적 테이블의 상태가 클라이언트와 서버 양쪽에서 동기화되어야 하므로, 연결이 끊어지면 테이블도 초기화된다.

실무에서는 헤더 수와 크기를 최소화하고, 반복되는 헤더(특히 인증 토큰, 콘텐츠 타입)를 일관되게 유지하는 것만으로도 HPACK/QPACK의 압축 효율을 극대화할 수 있다. 또한 민감한 헤더에 대한 `Never Indexed` 처리와 동적 테이블 크기 튜닝은 보안과 성능 모두에 직접적인 영향을 미치므로, 프로덕션 환경에서 반드시 검토해야 한다.

HTTP/3와 QUIC의 도입이 가속화되는 지금, QPACK의 블로킹/논블로킹 모드 선택과 동적 테이블 전략은 서비스 레이턴시 최적화의 중요한 레버 중 하나가 될 것이다.
