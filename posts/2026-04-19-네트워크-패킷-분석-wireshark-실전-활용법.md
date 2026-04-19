# 네트워크 패킷 분석 Wireshark 실전 활용법

## 개요

백엔드 개발자로 일하다 보면 "분명히 코드는 맞는데 왜 응답이 안 오지?", "타임아웃이 왜 이 시점에 발생하지?" 같은 상황을 맞닥뜨리게 된다. 로그만으로는 원인을 파악하기 어렵고, 애플리케이션 레이어 위에서만 들여다보면 놓치는 것들이 반드시 존재한다. 이럴 때 네트워크 패킷을 직접 들여다보는 능력은 디버깅 역량을 한 단계 끌어올려준다.

Wireshark는 세계에서 가장 널리 사용되는 네트워크 패킷 분석 도구다. GUI 기반으로 직관적이며, 수천 가지 프로토콜을 디코딩할 수 있고, 강력한 필터 언어를 제공한다. 이 포스팅에서는 단순한 사용법 소개를 넘어, 실무에서 실제로 마주치는 문제들을 Wireshark로 어떻게 진단하는지 다룬다.

---

## 핵심 개념

### 캡처 필터 vs 디스플레이 필터

Wireshark를 쓰면서 가장 먼저 혼동하는 개념이 바로 이 두 가지다.

- **캡처 필터(Capture Filter)**: 캡처 시작 전에 설정하며, 해당 조건에 맞는 패킷만 **수집**한다. BPF(Berkeley Packet Filter) 문법을 사용한다. 성능에 민감한 환경에서 디스크/메모리 용량을 절약할 때 유용하다.
- **디스플레이 필터(Display Filter)**: 이미 캡처된 패킷 중에서 **표시**할 것만 걸러낸다. Wireshark 고유의 필터 문법을 사용하며 훨씬 풍부한 표현이 가능하다.

```
# 캡처 필터 예시 (BPF 문법)
host 192.168.1.100 and port 8080
tcp and not port 22
src net 10.0.0.0/8

# 디스플레이 필터 예시 (Wireshark 문법)
http.request.method == "POST"
tcp.flags.syn == 1 && tcp.flags.ack == 0
ip.addr == 192.168.1.100 && tcp.port == 8080
```

### TCP 스트림과 Follow Stream

패킷 하나하나를 보는 것보다 특정 TCP 연결의 전체 흐름을 보는 것이 훨씬 유용할 때가 많다. 패킷을 우클릭하고 `Follow → TCP Stream`을 선택하면 해당 커넥션의 전체 페이로드를 재조합해서 보여준다. HTTP 요청/응답 페이로드, 헤더 내용 등을 그대로 확인할 수 있다.

### Statistics 메뉴 활용

- **Statistics → Conversations**: IP/TCP/UDP 단위로 연결별 트래픽량과 패킷 수를 요약해서 보여준다. 어떤 호스트가 가장 많은 트래픽을 일으키는지 빠르게 파악할 수 있다.
- **Statistics → I/O Graphs**: 시간에 따른 트래픽 추이를 그래프로 시각화한다. 특정 시점에 트래픽 스파이크가 발생했는지 확인할 때 유용하다.
- **Statistics → TCP Stream Graphs → Time-Sequence (Stevens)**: TCP 흐름 제어, 혼잡 제어, 재전송 패턴을 시각적으로 확인할 수 있다.

---

## 실전 예제

### 예제 1: HTTP API 타임아웃 원인 분석

서비스 간 HTTP 통신에서 간헐적으로 타임아웃이 발생한다고 가정하자. 애플리케이션 로그에는 "Connection timed out" 에러만 찍히고 원인을 알 수 없는 상황이다.

**1단계: 캡처 시작**

```bash
# CLI 환경이라면 tcpdump로 캡처 후 Wireshark로 분석
tcpdump -i eth0 -w /tmp/capture.pcap host 10.0.0.50 and port 8080

# 또는 tshark (Wireshark CLI 버전)
tshark -i eth0 -f "host 10.0.0.50 and port 8080" -w /tmp/capture.pcap
```

**2단계: 타임아웃 패턴 식별**

디스플레이 필터로 재전송 패킷을 필터링한다.

```
# TCP 재전송 확인
tcp.analysis.retransmission

# 타임아웃 관련 TCP 리셋 확인
tcp.flags.reset == 1

# ACK를 받지 못한 패킷 확인
tcp.analysis.lost_segment
```

**3단계: 연결 상태 분석**

SYN → SYN-ACK → ACK 핸드셰이크가 정상적으로 이루어지는지 확인한다.

```
# 3-way handshake 필터링
tcp.flags.syn == 1

# 특정 서버로의 연결 시도 후 응답 없음 확인
ip.dst == 10.0.0.50 && tcp.flags.syn == 1 && !tcp.flags.ack
```

SYN 패킷을 보냈는데 일정 시간 내에 SYN-ACK가 오지 않는다면 서버 측 문제(백로그 큐 포화, 방화벽 드롭 등)를 의심할 수 있다.

---

### 예제 2: SSL/TLS 핸드셰이크 실패 분석

HTTPS 통신에서 인증서 오류나 프로토콜 불일치가 발생할 때 Wireshark로 TLS 레이어를 분석하면 정확한 원인을 찾을 수 있다.

```
# TLS 관련 패킷 필터링
tls

# TLS Alert 메시지만 확인 (핸드셰이크 실패 포함)
tls.alert_message

# ClientHello 확인 (클라이언트가 제안하는 cipher suite 확인)
tls.handshake.type == 1

# ServerHello 확인
tls.handshake.type == 2
```

ClientHello와 ServerHello를 비교해서 지원하는 TLS 버전과 Cipher Suite가 교집합이 있는지 확인한다. Alert 메시지의 Description 필드를 보면 `handshake_failure`, `certificate_unknown`, `protocol_version` 등의 구체적인 실패 원인을 알 수 있다.

**TLS 복호화 설정 (개발/스테이징 환경)**

```bash
# Java 애플리케이션에서 SSLKEYLOGFILE 설정
# JVM 옵션에 추가하거나 환경변수로 설정
export SSLKEYLOGFILE=/tmp/tls_keys.log

# Spring Boot 실행 시
java -Djavax.net.debug=ssl:handshake -jar app.jar
```

Wireshark에서 `Edit → Preferences → Protocols → TLS`에서 Pre-Master Secret log 파일 경로를 지정하면 암호화된 TLS 트래픽도 복호화해서 볼 수 있다. **단, 이는 반드시 개발/스테이징 환경에서만 사용해야 한다.**

---

### 예제 3: 데이터베이스 슬로우 쿼리 네트워크 관점 분석

애플리케이션에서 특정 DB 쿼리가 느리다고 보고될 때, 네트워크 레이턴시 때문인지 실제 DB 처리 시간 때문인지 구분하는 것이 중요하다.

```
# MySQL 트래픽 필터링
mysql

# PostgreSQL 트래픽 필터링
pgsql

# 특정 포트의 요청-응답 왕복 시간 분석을 위한 필터
ip.addr == 10.0.0.20 && tcp.port == 5432
```

패킷의 Time 컬럼을 보면 클라이언트가 쿼리를 보낸 시점과 서버가 응답을 돌려보낸 시점의 차이를 확인할 수 있다. 네트워크 레이턴시(RTT)는 SYN-SYN/ACK 간격으로 측정할 수 있고, 이 값을 제외한 나머지가 실제 DB 처리 시간이다.

```bash
# tshark로 응답 시간 통계 추출
tshark -r capture.pcap -Y "mysql" \
  -T fields \
  -e frame.number \
  -e frame.time_relative \
  -e mysql.query \
  -e mysql.response_time
```

---

### 예제 4: 커맨드라인 자동화 스크립트

운영 환경에서 특정 조건의 패킷만 실시간으로 모니터링하거나, 주기적으로 캡처해서 분석하는 자동화 스크립트다.

```bash
#!/bin/bash
# 특정 호스트로의 비정상 연결 시도 모니터링

TARGET_HOST="10.0.0.100"
TARGET_PORT="8080"
CAPTURE_DURATION=60  # 초
OUTPUT_DIR="/var/log/packet_captures"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p "$OUTPUT_DIR"

echo "패킷 캡처 시작: ${TIMESTAMP}"

tshark -i eth0 \
  -f "host ${TARGET_HOST} and port ${TARGET_PORT}" \
  -a duration:${CAPTURE_DURATION} \
  -w "${OUTPUT_DIR}/capture_${TIMESTAMP}.pcap" \
  2>/dev/null

# 캡처 완료 후 TCP 재전송 비율 분석
echo "=== TCP 재전송 분석 ==="
tshark -r "${OUTPUT_DIR}/capture_${TIMESTAMP}.pcap" \
  -Y "tcp.analysis.retransmission" \
  -T fields \
  -e frame.time \
  -e ip.src \
  -e ip.dst \
  -e tcp.srcport \
  -e tcp.dstport \
  | wc -l | xargs -I{} echo "재전송 패킷 수: {}"

# RST 패킷 확인
echo "=== TCP RST 분석 ==="
tshark -r "${OUTPUT_DIR}/capture_${TIMESTAMP}.pcap" \
  -Y "tcp.flags.reset == 1" \
  -T fields \
  -e frame.time \
  -e ip.src \
  -e ip.dst

echo "캡처 완료: ${OUTPUT_DIR}/capture_${TIMESTAMP}.pcap"
```

---

## 주의사항 및 트레이드오프

### 보안 및 법적 고려사항

Wireshark는 강력한 도구인 만큼 책임감 있는 사용이 필수다.

- **본인이 관리하는 네트워크에서만 사용**해야 한다. 타인의 네트워크에서 무단으로 패킷을 캡처하는 것은 대부분의 국가에서 불법이다.
- 프로덕션 환경에서 패킷을 캡처할 경우, **개인정보나 민감한 데이터가 포함될 수 있다**. 캡처 파일의 보관 및 삭제 정책을 반드시 사전에 정의해야 한다.
- TLS 복호화는 **개발/스테이징 환경에서만** 사용하고, 프로덕션에서는 절대 사용하지 않는다.

### 성능 영향

- 캡처 자체는 커널 수준에서 동작하므로 영향이 크지 않지만, **트래픽이 많은 환경에서 GUI로 실시간 패킷을 분석하면 Wireshark 자체가 CPU를 많이 사용**할 수 있다.
- 이 경우 `tcpdump`나 `tshark`로 먼저 pcap 파일로 저장한 뒤, 별도 환경에서 Wireshark GUI로 분석하는 것을 권장한다.
- 캡처 파일 크기가 커지면 분석이 느려진다. `--snapshot-length` 옵션으로 패킷 헤더만 캡처하거나, `-C` 옵션으로 파일 크기 제한을 설정하는 것이 좋다.

### 트레이드오프: 어디서 캡처할 것인가

| 캡처 위치 | 장점 | 단점 |
|---|---|---|
| 클라이언트 측 | 애플리케이션 관점 확인 용이 | 네트워크 중간 구간 파악 어려움 |
| 서버 측 | 서버 수신 실제 패킷 확인 | NIC 오프로딩으로 캡처 내용이 실제와 다를 수 있음 |
| 네트워크 장비 미러링 | 전체 트래픽 관찰 | 장비 접근 권한 필요, 설정 복잡 |

**NIC 오프로딩 주의**: 서버에서 캡처할 때 체크섬 오류가 많이 보인다면, 이는 NIC 오프로딩(TCP Checksum Offloading) 때문일 가능성이 높다. 실제 오류가 아니므로 당황하지 않아도 된다. 필요하다면 `ethtool -K eth0 tx off rx off`로 오프로딩을 비활성화할 수 있다.

---

## 정리

Wireshark는 "로그로는 보이지 않는" 네트워크 레이어의 문제를 정확히 짚어낼 수 있는 강력한 도구다. 타임아웃, TLS 핸드셰이크 실패, 비정상적인 커넥션 종료, 네트워크 레이턴시 vs 서버 처리 시간 구분 등 실무에서 자주 마주치는 문제들을 훨씬 빠르게 진단할 수 있다.

핵심은 **캡처 필터와 디스플레이 필터를 능숙하게 조합**하는 것이다. 처음에는 디스플레이 필터 문법이 낯설더라도, 자주 쓰이는 패턴(tcp.analysis.retransmission, tls.alert_message, http.response.code 등)을 익혀두면 빠르게 원하는 패킷을 골라낼 수 있다.

프로덕션 환경에서는 직접 Wireshark GUI를 올리기 어려우므로, `tcpdump` 또는 `tshark`로 pcap을 저장하고 분석하는 워크플로우를 습관으로 만들어 두길 권장한다. 자동화 스크립트와 결합하면 이상 감지 시 자동으로 패킷을 덤프해두는 시스템을 구축할 수도 있다.

네트워크 문제 앞에서 막막했던 경험이 있다면, 이제 Wireshark를 통해 패킷 레벨에서 직접 확인하는 습관을 들여보자. 디버깅 시간이 획기적으로 줄어들 것이다.