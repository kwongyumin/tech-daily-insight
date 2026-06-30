# 네트워크 패킷 분석 Wireshark 실전 활용법

## 개요

백엔드 개발자로 일하다 보면 "분명히 코드는 맞는데 왜 응답이 안 오지?"라는 상황을 마주치게 된다. 로그를 아무리 뒤져봐도 애플리케이션 레벨에서는 아무 문제가 없다. 이럴 때 네트워크 패킷 레벨에서 실제로 무슨 일이 벌어지는지 들여다볼 수 있다면 문제 해결 속도가 극적으로 빨라진다.

Wireshark는 세계에서 가장 널리 쓰이는 오픈소스 패킷 분석기다. GUI 기반의 강력한 필터링과 프로토콜 해석 기능을 제공하며, TLS 복호화, HTTP/2 스트림 분석, TCP 재전송 탐지 등 실무에서 즉시 활용할 수 있는 기능들이 가득하다.

이 포스팅에서는 Wireshark의 기본 사용법을 넘어서, **실무에서 마주치는 네트워크 이슈를 Wireshark로 어떻게 진단하고 해결하는지** 집중적으로 다룬다.

---

## 핵심 개념

### 캡처 필터 vs 디스플레이 필터

Wireshark를 처음 쓸 때 많이 혼동하는 부분이다.

- **캡처 필터 (Capture Filter)**: 패킷을 수집하는 단계에서 적용. BPF(Berkeley Packet Filter) 문법 사용. 메모리와 디스크 사용량을 줄임.
- **디스플레이 필터 (Display Filter)**: 이미 캡처된 패킷을 화면에서 걸러내는 용도. Wireshark 고유 문법 사용. 훨씬 더 세밀한 조건 설정 가능.

```
# 캡처 필터 예시 (BPF 문법)
host 192.168.1.100 and port 8080
tcp port 443
not arp

# 디스플레이 필터 예시 (Wireshark 문법)
http.request.method == "POST"
tcp.flags.reset == 1
ip.addr == 10.0.0.1 && tcp.port == 3306
```

### 주요 TCP 플래그 이해

패킷 분석에서 TCP 플래그 해석은 필수다.

| 플래그 | 의미 | 실무 포인트 |
|--------|------|-------------|
| SYN | 연결 시작 | SYN만 있고 SYN-ACK가 없으면 방화벽/라우팅 문제 |
| RST | 강제 연결 종료 | 애플리케이션 오류나 포트 미오픈 시 발생 |
| FIN | 정상 연결 종료 | FIN-FIN-ACK 흐름이 정상 |
| PSH | 즉시 전달 요청 | 데이터 전송 시 함께 설정됨 |
| ACK | 수신 확인 | 연결 수립 이후 모든 패킷에 포함 |

---

## 실전 예제

### 예제 1: TCP 3-Way Handshake 분석

가장 기본적이면서도 중요한 케이스다. 특정 서버에 연결이 안 된다면 핸드셰이크가 완료되는지부터 확인한다.

```
# 디스플레이 필터: 특정 IP와의 TCP 연결 시작 패킷만 보기
ip.addr == 192.168.1.100 && tcp.flags.syn == 1
```

정상적인 흐름:
```
No.  Time     Source          Destination     Info
1    0.000    Client          Server          [SYN] Seq=0
2    0.001    Server          Client          [SYN, ACK] Seq=0 Ack=1
3    0.001    Client          Server          [ACK] Seq=1 Ack=1
```

SYN 이후 응답이 없다면 서버 측 방화벽 또는 보안 그룹 설정을 먼저 확인해야 한다.

---

### 예제 2: HTTP API 요청/응답 분석

REST API 개발 중 응답이 느리거나 예상과 다른 상태 코드가 반환될 때 유용하다.

```
# HTTP 요청만 필터링
http.request

# POST 요청만 보기
http.request.method == "POST"

# 특정 URI 패턴 포함
http.request.uri contains "/api/v1"

# HTTP 응답 코드 5xx만 보기
http.response.code >= 500
```

**실전 팁**: "Follow TCP Stream" (우클릭 메뉴)을 활용하면 특정 HTTP 요청과 응답 전체를 하나의 스트림으로 확인할 수 있다. 요청 헤더, 바디, 응답까지 한 번에 파악 가능하다.

---

### 예제 3: TLS 트래픽 복호화 (HTTPS 분석)

HTTPS 통신을 분석하려면 TLS 세션 키가 필요하다. Java 애플리케이션에서 세션 키를 파일로 출력하도록 설정한다.

**Java 애플리케이션 JVM 옵션 설정:**
```bash
# JVM 실행 옵션에 추가
-Djavax.net.debug=ssl:handshake
-DSSLKEYLOGFILE=/tmp/ssl_keys.log
```

**Spring Boot 애플리케이션에서 RestTemplate 디버깅:**
```java
@Configuration
public class HttpClientConfig {

    @Bean
    public RestTemplate restTemplate() {
        // SSL 디버깅을 위한 커스텀 설정
        SSLContext sslContext = SSLContexts.custom()
                .loadTrustMaterial(null, (chain, authType) -> true)
                .build();

        CloseableHttpClient httpClient = HttpClients.custom()
                .setSSLContext(sslContext)
                .build();

        HttpComponentsClientHttpRequestFactory factory =
                new HttpComponentsClientHttpRequestFactory(httpClient);

        return new RestTemplate(factory);
    }
}
```

**환경변수로 SSLKEYLOGFILE 설정 (Node.js와 크롬도 지원):**
```bash
export SSLKEYLOGFILE=/tmp/ssl_keys.log
```

**Wireshark에서 키 파일 적용:**
```
Edit → Preferences → Protocols → TLS
→ (Pre)-Master-Secret log filename에 /tmp/ssl_keys.log 입력
```

이제 Wireshark에서 HTTPS 트래픽이 평문으로 보인다.

---

### 예제 4: 데이터베이스 커넥션 풀 문제 진단

MySQL/PostgreSQL 연결이 간헐적으로 끊기는 문제를 분석할 때 활용한다.

```
# MySQL 포트 트래픽만 보기
tcp.port == 3306

# RST 패킷 탐지 (비정상 연결 종료)
tcp.flags.reset == 1 && tcp.port == 3306

# TCP 재전송 탐지
tcp.analysis.retransmission

# TCP Zero Window (수신 버퍼 가득 참)
tcp.analysis.zero_window
```

**HikariCP 설정과 연계 분석 포인트:**
```yaml
# application.yml
spring:
  datasource:
    hikari:
      connection-timeout: 30000
      idle-timeout: 600000
      max-lifetime: 1800000  # 이 값이 DB의 wait_timeout보다 작아야 함
      keepalive-time: 300000  # TCP keepalive 설정
      maximum-pool-size: 20
```

Wireshark에서 `tcp.analysis.keep_alive` 필터로 keepalive 패킷이 실제로 전송되는지 확인할 수 있다. keepalive가 DB 서버의 `wait_timeout`(기본 8시간)보다 짧게 설정되어 있는지 검증하는 데 직접 패킷을 보는 것이 가장 확실하다.

---

### 예제 5: tshark를 활용한 CLI 자동화

서버 환경이나 CI/CD 파이프라인에서는 GUI 없이 tshark(Wireshark의 CLI 버전)를 사용한다.

```bash
# 특정 인터페이스에서 30초간 캡처 후 파일 저장
tshark -i eth0 -a duration:30 -w /tmp/capture.pcap

# HTTP 요청만 실시간 출력
tshark -i eth0 -Y "http.request" -T fields \
  -e frame.time \
  -e ip.src \
  -e http.request.method \
  -e http.request.uri

# 캡처된 파일에서 TCP 재전송 횟수 집계
tshark -r /tmp/capture.pcap \
  -Y "tcp.analysis.retransmission" \
  -T fields -e ip.dst | sort | uniq -c | sort -rn

# 응답 시간 분포 분석 (HTTP)
tshark -r /tmp/capture.pcap \
  -Y "http.response" \
  -T fields \
  -e frame.time_relative \
  -e http.response.code \
  -e http.time \
  -E separator=,
```

**bash 스크립트로 이상 패킷 모니터링:**
```bash
#!/bin/bash
# rst_monitor.sh - RST 패킷 급증 감지 스크립트

INTERFACE="eth0"
THRESHOLD=10
INTERVAL=60

while true; do
    RST_COUNT=$(tshark -i "$INTERFACE" -a duration:"$INTERVAL" \
        -Y "tcp.flags.reset == 1" 2>/dev/null | wc -l)

    if [ "$RST_COUNT" -gt "$THRESHOLD" ]; then
        echo "[ALERT] $(date): RST packets = $RST_COUNT (threshold: $THRESHOLD)"
        # 슬랙 웹훅이나 PagerDuty 알림 연동 가능
        curl -s -X POST "$SLACK_WEBHOOK_URL" \
            -H 'Content-type: application/json' \
            --data "{\"text\":\"RST 패킷 급증 감지: ${RST_COUNT}건\"}"
    fi

    echo "$(date): RST count = $RST_COUNT"
done
```

---

## 주의사항 및 트레이드오프

### 보안 및 법적 이슈

**패킷 캡처는 권한 없이 타인의 트래픽을 수집하면 불법이다.** 회사 네트워크에서 사용할 때는 반드시 정보보안팀의 승인을 받아야 한다. 특히 클라우드 환경(AWS VPC, GCP VNet)에서는 미러링 설정 전에 컴플라이언스를 확인하라.

### 성능 영향

- 고트래픽 구간에서 전체 패킷 캡처 시 CPU와 디스크 I/O에 부하가 생긴다.
- 프로덕션 환경에서는 캡처 필터로 범위를 좁히거나, `ring buffer` 옵션으로 파일 크기를 제한한다.

```bash
# 링 버퍼 설정: 100MB 파일 5개를 순환하며 저장
tshark -i eth0 -b filesize:102400 -b files:5 -w /tmp/capture.pcap
```

### 암호화된 트래픽의 한계

TLS 1.3부터는 Perfect Forward Secrecy가 강화되어, 서버 개인키만으로는 사후 복호화가 불가능하다. 반드시 세션 생성 시점에 키 로그 파일을 추출해야 한다.

### 분산 환경에서의 한계

MSA 환경에서 여러 서비스 간 트래픽을 분석할 때는 Wireshark 단독으로는 부족하다. **Jaeger, Zipkin 같은 분산 트레이싱 도구**와 병행 사용하는 것이 효과적이다. Wireshark는 특정 구간의 로우 레벨 이슈 확인에 집중하고, 전체 흐름은 APM 도구에 맡기는 역할 분담이 바람직하다.

---

## 정리

Wireshark는 단순한 패킷 덤프 도구가 아니다. TCP 커넥션 수립부터 TLS 핸드셰이크, HTTP/2 스트림, 데이터베이스 프로토콜까지 네트워크 스택 전체를 들여다볼 수 있는 강력한 진단 도구다.

실무에서 활용 포인트를 정리하면 다음과 같다.

| 상황 | 활용법 |
|------|--------|
| API 응답 지연 | TCP RTT 분석, `tcp.analysis.ack_rtt` 필터 |
| 간헐적 연결 끊김 | RST 패킷 탐지, keepalive 검증 |
| HTTPS 디버깅 | SSLKEYLOGFILE + Wireshark TLS 복호화 |
| DB 커넥션 풀 이슈 | Zero Window, 재전송 패킷 모니터링 |
| 자동화/서버 환경 | tshark CLI + 스크립트 연동 |

개발자가 Wireshark를 능숙하게 다룰 수 있게 되면, "왜 되는지 모르지만 됨"이 아니라 "이래서 된다/안 된다"를 설명할 수 있는 수준의 네트워크 이해도를 갖추게 된다. 주기적으로 본인이 개발한 서비스의 실제 패킷을 캡처해 들여다보는 습관을 들이길 권장한다.