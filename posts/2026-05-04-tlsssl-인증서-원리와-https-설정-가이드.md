# TLS/SSL 인증서 원리와 HTTPS 설정 가이드

## 개요

웹 서비스를 운영하다 보면 HTTPS 설정은 선택이 아닌 필수가 된 지 오래다. 브라우저는 HTTP 사이트에 "안전하지 않음" 경고를 띄우고, 구글은 HTTPS를 SEO 랭킹 요소로 반영한다. 하지만 많은 개발자들이 Let's Encrypt로 인증서를 발급받아 붙이는 것 이상의 동작 원리를 모른 채 사용하는 경우가 많다.

이 글에서는 TLS/SSL의 동작 원리부터 인증서 체계, 그리고 Nginx와 Spring Boot에서의 실전 HTTPS 설정까지 다룬다. 단순한 설정 가이드가 아니라 **왜** 이렇게 동작하는지를 이해하는 것이 목표다.

---

## 핵심 개념

### TLS와 SSL의 차이

SSL(Secure Sockets Layer)은 Netscape가 1990년대 개발한 프로토콜이다. SSL 3.0의 취약점(POODLE 등)이 발견된 이후 IETF가 표준화한 것이 TLS(Transport Layer Security)다. 현재 SSL 2.0/3.0은 모두 deprecated 상태이며, **실제로는 TLS를 사용하지만 관용적으로 SSL이라 부르는 경우가 많다.**

현재 권장 버전은 **TLS 1.2 이상**이며, TLS 1.3이 2018년 RFC 8446으로 표준화되어 성능과 보안 모두 개선되었다.

### TLS Handshake 동작 원리

TLS 연결은 실제 데이터 전송 전에 Handshake 과정을 거친다. TLS 1.2와 1.3의 Handshake 과정은 다르며, 1.3에서는 RTT가 1로 줄었다.

**TLS 1.2 Handshake (2-RTT)**
```
Client                          Server
  |  ---- ClientHello -------->  |  (지원하는 Cipher Suite, TLS 버전 전송)
  |  <--- ServerHello ---------  |  (선택된 Cipher Suite, 인증서 전송)
  |  <--- Certificate ---------  |
  |  <--- ServerHelloDone -----  |
  |  ---- ClientKeyExchange -->  |  (Pre-master Secret 전송)
  |  ---- ChangeCipherSpec --->  |
  |  ---- Finished ----------->  |
  |  <--- ChangeCipherSpec -----  |
  |  <--- Finished -------------  |
  |  === Encrypted Data ======>  |
```

**TLS 1.3 Handshake (1-RTT)**
```
Client                          Server
  |  ---- ClientHello -------->  |  (Key Share 포함)
  |  <--- ServerHello ---------  |  (Key Share + 인증서 포함)
  |  <--- {EncryptedExtensions}  |
  |  <--- {Certificate} -------  |
  |  <--- {Finished} ----------  |
  |  ---- {Finished} ----------> |
  |  === Encrypted Data ======>  |
```

TLS 1.3은 불필요한 Cipher Suite를 제거하고, 0-RTT(Early Data) 기능도 지원하여 재연결 시 레이턴시를 더욱 줄일 수 있다.

### 인증서 체계와 PKI

TLS 인증서는 **X.509** 표준을 따르며, PKI(Public Key Infrastructure) 체계 위에서 동작한다.

```
Root CA (최상위, 브라우저/OS에 내장)
  └── Intermediate CA (중간 CA)
        └── End-Entity Certificate (실제 서버 인증서)
```

이 계층 구조를 **인증서 체인(Certificate Chain)**이라 한다. 브라우저는 서버로부터 받은 인증서를 Root CA까지 추적하여 신뢰 여부를 결정한다.

인증서에는 다음 정보가 포함된다:
- **Subject**: 인증서 소유자 정보 (CN, O, C 등)
- **SAN(Subject Alternative Name)**: 인증서가 유효한 도메인 목록
- **공개키(Public Key)**: 암호화에 사용
- **유효기간**: Not Before / Not After
- **서명**: 상위 CA의 개인키로 서명된 값

```bash
# 인증서 내용 확인
openssl x509 -in certificate.crt -text -noout

# 인증서 체인 검증
openssl verify -CAfile ca-bundle.crt certificate.crt

# 원격 서버 인증서 확인
openssl s_client -connect example.com:443 -showcerts
```

---

## 실전 예제

### 1. Let's Encrypt + Certbot으로 인증서 발급

가장 널리 사용되는 무료 인증서 발급 방법이다.

```bash
# Certbot 설치 (Ubuntu/Debian 기준)
sudo apt update
sudo apt install certbot python3-certbot-nginx

# Nginx 플러그인으로 자동 발급 및 설정
sudo certbot --nginx -d example.com -d www.example.com

# Standalone 방식 (포트 80이 비어있을 때)
sudo certbot certonly --standalone -d example.com

# 자동 갱신 설정 확인 (systemd timer)
sudo systemctl status certbot.timer

# 수동 갱신 테스트
sudo certbot renew --dry-run
```

발급된 인증서는 `/etc/letsencrypt/live/example.com/` 에 저장된다:
- `fullchain.pem`: 서버 인증서 + 중간 CA 인증서
- `privkey.pem`: 개인키

### 2. Nginx HTTPS 설정

```nginx
# /etc/nginx/sites-available/example.com

# HTTP → HTTPS 리다이렉트
server {
    listen 80;
    listen [::]:80;
    server_name example.com www.example.com;
    
    # ACME Challenge (Let's Encrypt 갱신용)
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    
    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name example.com www.example.com;

    # 인증서 설정
    ssl_certificate     /etc/letsencrypt/live/example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;

    # TLS 버전 설정 (1.2, 1.3만 허용)
    ssl_protocols TLSv1.2 TLSv1.3;

    # 권장 Cipher Suite (Mozilla SSL Config Generator 기준)
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off; # TLS 1.3에서는 클라이언트 우선

    # OCSP Stapling (인증서 유효성 빠른 확인)
    ssl_stapling on;
    ssl_stapling_verify on;
    ssl_trusted_certificate /etc/letsencrypt/live/example.com/chain.pem;
    resolver 8.8.8.8 8.8.4.4 valid=300s;
    resolver_timeout 5s;

    # Session 설정
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off; # Forward Secrecy 보장

    # DH 파라미터 (DHE Cipher Suite 사용 시)
    ssl_dhparam /etc/nginx/dhparam.pem;

    # 보안 헤더
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;

    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
# DH 파라미터 생성 (최초 1회)
sudo openssl dhparam -out /etc/nginx/dhparam.pem 2048

# 설정 테스트 및 재로드
sudo nginx -t
sudo nginx -s reload
```

### 3. Spring Boot HTTPS 설정

Spring Boot 내장 서버(Tomcat)에 직접 TLS를 적용하는 경우다. 프로덕션에서는 Nginx/LB에서 TLS 터미네이션을 하는 경우가 많지만, 내부 서비스 간 mTLS(상호 인증)나 직접 HTTPS가 필요한 경우에 유용하다.

```bash
# PKCS12 형식으로 키스토어 생성
openssl pkcs12 -export \
  -in fullchain.pem \
  -inkey privkey.pem \
  -out keystore.p12 \
  -name tomcat \
  -password pass:your_password
```

```yaml
# application.yml
server:
  port: 8443
  ssl:
    enabled: true
    key-store: classpath:keystore.p12
    key-store-password: your_password
    key-store-type: PKCS12
    key-alias: tomcat
    protocol: TLS
    enabled-protocols: TLSv1.2,TLSv1.3
    ciphers: TLS_AES_128_GCM_SHA256,TLS_AES_256_GCM_SHA384,TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256
```

```java
// HTTP → HTTPS 리다이렉트 설정 (Spring Boot)
@Configuration
public class HttpsRedirectConfig {

    @Bean
    public ServletWebServerFactory servletContainer() {
        TomcatServletWebServerFactory tomcat = new TomcatServletWebServerFactory() {
            @Override
            protected void postProcessContext(Context context) {
                SecurityConstraint constraint = new SecurityConstraint();
                constraint.setUserConstraint("CONFIDENTIAL");
                SecurityCollection collection = new SecurityCollection();
                collection.addPattern("/*");
                constraint.addCollection(collection);
                context.addConstraint(constraint);
            }
        };
        tomcat.addAdditionalTomcatConnectors(httpConnector());
        return tomcat;
    }

    private Connector httpConnector() {
        Connector connector = new Connector(TomcatServletWebServerFactory.DEFAULT_PROTOCOL);
        connector.setScheme("http");
        connector.setPort(8080);
        connector.setSecure(false);
        connector.setRedirectPort(8443);
        return connector;
    }
}
```

### 4. SSL 설정 점수 확인

```bash
# testssl.sh로 로컬 점검
git clone --depth 1 https://github.com/drwetter/testssl.sh.git
cd testssl.sh
./testssl.sh example.com

# 주요 취약점 체크
./testssl.sh --heartbleed --ccs-injection --ticketbleed example.com
```

온라인 도구로는 [SSL Labs](https://www.ssllabs.com/ssltest/)를 사용하면 A+ 등급 기준을 쉽게 확인할 수 있다.

---

## 주의사항 및 트레이드오프

### 인증서 갱신 자동화의 함정

Let's Encrypt 인증서는 **90일** 유효기간을 갖는다. `certbot renew`는 만료 30일 전부터 갱신을 시도하며, cron 또는 systemd timer로 자동화해야 한다.

```bash
# cron 설정 예시 (매일 두 번 갱신 시도)
0 0,12 * * * root certbot renew --quiet --post-hook "nginx -s reload"
```

주의할 점은 **Rate Limit**이다. 동일 도메인에 대해 주당 5개의 인증서만 발급 가능하다. 테스트 시에는 반드시 `--staging` 플래그를 사용하자.

### HSTS와 preload의 위험성

```
Strict-Transport-Security: max-age=63072000; includeSubDomains; preload
```

`preload`를 설정하고 [hstspreload.org](https://hstspreload.org)에 등록하면 브라우저에 하드코딩되어 HTTP로는 절대 접근이 불가능해진다. **서브도메인에 HTTPS를 지원하지 않는 서비스가 있다면 `includeSubDomains`는 위험할 수 있다.** 제거하는 데 수개월이 걸릴 수 있으므로 신중하게 설정해야 한다.

### TLS 터미네이션 위치 결정

| 방식 | 장점 | 단점 |
|------|------|------|
| **LB에서 터미네이션** | 백엔드 부하 감소, 인증서 중앙 관리 | 내부 구간 평문 전송 |
| **백엔드까지 End-to-End** | 완전한 암호화, mTLS 가능 | 인증서 관리 복잡, CPU 부하 |
| **재암호화(Re-encryption)** | 보안 강화 | LB에서 복호화 후 재암호화 비용 |

내부 네트워크가 신뢰할 수 있다면 LB 터미네이션으로 충분하지만, 금융/의료 등 규제 산업에서는 End-to-End 암호화가 요구될 수 있다.

### 성능 고려사항

- **TLS 1.3**은 1.2 대비 Handshake RTT를 1 줄여주므로 레이턴시가 중요한 서비스에서 유리하다.
- **OCSP Stapling**을 활성화하면 클라이언트가 CA 서버에 직접 쿼리하지 않아 성능이 향상된다.
- **Session Resumption**: `ssl_session_tickets off` 설정이 Forward Secrecy 측면에서 안전하지만, 세션 재사용이 없어 매 연결마다 Full Handshake가 발생한다. `ssl_session_cache`를 사용하면 서버 측에서 세션을 관리하여 이 문제를 완화할 수 있다.

### 와일드카드 인증서 vs. SAN 인증서

```bash
# 와일드카드 인증서 발급 (DNS-01 Challenge 필요)
certbot certonly --manual --preferred-challenges dns \
  -d "*.example.com" -d "example.com"
```

와일드카드(`*.example.com`)는 서브도메인 관리가 편리하지만, **DNS-01 Challenge**가 필요하고 유출 시 모든 서브도메인이 위험해진다. 가능하면 최소 권한 원칙에 따라 필요한 도메인만 SAN으로 포함하는 것을 권장한다.

---

## 정리

TLS/SSL은 단순한 "자물쇠 아이콘"이 아니다. 공개키 암호화, 디지털 서명, 인증서 체인이 유기적으로 결합된 복잡한 시스템이다. 핵심 포인트를 정리하면 다음과 같다.

1. **TLS 1.2 이상**을 사용하고, 가능하면 TLS 1.3을 활성화하라.
2. **인증서 체인**을 올바르게 구성하라. `fullchain.pem`을 사용해야 중간 CA 누락 문제를 피할 수 있다.
3. **자동 갱신**을 설정하고, 갱신 후 서버 재로드 훅을 반드시 걸어두라.
4. **HSTS**는 신중하게 적용하고, `preload`는 더욱 신중히 결정하라.
5. **Mozilla SSL Config Generator**와 **SSL Labs**를 활용해 설정을 검증하라.
6. **OCSP Stapling**으로 인증서 검증 레이턴시를 줄여라.

보안 설정은 한 번 하고 끝나는 것이 아니다. 주기적으로 [SSL Labs](https://www.ssllabs.com/ssltest/)로 점수를 확인하고, CVE 동향을 모니터링하여 설정을 업데이트하는 습관이 중요하다.