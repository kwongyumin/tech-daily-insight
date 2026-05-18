# Spring Boot DevTools와 LiveReload 개발 생산성 극대화

## 개요

Spring Boot 애플리케이션을 개발하다 보면, 코드를 수정할 때마다 서버를 재시작하는 반복 작업이 개발 흐름을 방해하는 주요 원인 중 하나가 된다. 특히 대규모 프로젝트에서 Cold Start 시간이 30초를 넘어가면 생산성은 급격히 떨어진다.

**Spring Boot DevTools**는 이런 문제를 해결하기 위해 설계된 개발 전용 모듈로, 클래스패스 변경을 감지해 애플리케이션을 빠르게 재시작하고, 브라우저 LiveReload까지 지원한다. 단순한 편의 기능처럼 보이지만, 내부 동작 원리를 이해하고 올바르게 설정하면 개발 사이클을 획기적으로 단축할 수 있다.

이 포스팅에서는 DevTools의 핵심 메커니즘을 분석하고, 실무에서 바로 활용할 수 있는 설정과 예제를 공유한다.

---

## 핵심 개념

### 1. 클래스로더 분리 전략 (Dual ClassLoader)

DevTools의 빠른 재시작 비밀은 **두 개의 클래스로더**를 운용하는 데 있다.

- **Base ClassLoader**: 서드파티 라이브러리 등 변경이 거의 없는 클래스를 로드. 재시작 시 재사용된다.
- **Restart ClassLoader**: 개발자가 작성한 클래스(주로 `src/main/java`)를 로드. 변경 감지 시 이 로더만 버리고 새로 생성한다.

이 덕분에 일반적인 Full Restart보다 훨씬 빠른 재시작이 가능하다. 단, 완전한 Hot Swap은 아니기 때문에 JVM 메모리 모델에 따른 일부 제약이 존재한다.

### 2. LiveReload

DevTools는 내장 **LiveReload 서버(기본 포트 35729)**를 실행한다. 브라우저에 LiveReload 확장 프로그램을 설치하거나, Thymeleaf/FreeMarker 같은 템플릿 엔진 페이지에서 자동으로 연동되면, 서버 재시작 완료 후 브라우저가 자동으로 새로고침된다.

### 3. Property 기본값 오버라이드

DevTools는 개발 환경에 적합한 프로퍼티 기본값을 자동으로 설정한다.

| 프로퍼티 | DevTools 기본값 | 일반 기본값 |
|---|---|---|
| `spring.thymeleaf.cache` | `false` | `true` |
| `spring.freemarker.cache` | `false` | `true` |
| `spring.web.resources.cache.period` | `0` | 캐시 활성화 |
| `logging.level.web` | `DEBUG` | `INFO` |

이 자동 오버라이드 덕분에 템플릿 수정 후 캐시 때문에 변경이 반영 안 되는 문제를 별도 설정 없이 해결할 수 있다.

---

## 실전 예제

### 의존성 추가

```xml
<!-- Maven -->
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-devtools</artifactId>
    <optional>true</optional>
</dependency>
```

```groovy
// Gradle
dependencies {
    developmentOnly 'org.springframework.boot:spring-boot-devtools'
}
```

`optional` 또는 `developmentOnly` 스코프를 반드시 사용해야 한다. 그래야 패키징 시 JAR에 포함되지 않아 운영 환경에 DevTools가 올라가는 사고를 예방할 수 있다.

---

### application.yml 세밀한 튜닝

```yaml
spring:
  devtools:
    restart:
      enabled: true
      # 재시작 감지에서 제외할 경로 (기본값에 추가)
      exclude: "static/**,public/**,templates/**,META-INF/maven/**"
      # 추가로 감시할 경로 (기본 클래스패스 외)
      additional-paths: "src/main/resources"
      # 파일 변경 후 재시작까지의 대기 시간 (ms)
      # 여러 파일이 동시에 수정될 때 불필요한 재시작 방지
      poll-interval: 2s
      quiet-period: 400ms
    livereload:
      enabled: true
      port: 35729
```

`quiet-period`는 실무에서 매우 중요한 설정이다. IDE가 여러 파일을 저장할 때 파일마다 이벤트가 발생하므로, 이 기간 동안 추가 변경이 없을 때만 재시작을 트리거하도록 해 불필요한 재시작을 줄인다.

---

### 글로벌 DevTools 설정 파일

프로젝트 단위가 아닌 **개발 머신 전체**에 적용할 설정은 홈 디렉터리에 파일을 만들어 관리할 수 있다.

```
~/.config/spring-boot/spring-boot-devtools.yml
# 또는 (레거시)
~/.spring-boot-devtools.properties
```

```yaml
# ~/.config/spring-boot/spring-boot-devtools.yml
spring:
  devtools:
    restart:
      quiet-period: 500ms
      poll-interval: 3s
```

팀마다 IDE 저장 속도가 달라 글로벌 설정으로 개인 최적화를 할 때 유용하다.

---

### 특정 조건에서 재시작 트리거 제어

개발 중 특정 파일 변경만 재시작을 유발하도록 커스텀 `TriggerFile`을 활용할 수 있다.

```yaml
spring:
  devtools:
    restart:
      trigger-file: ".reloadtrigger"
```

이제 `.reloadtrigger` 파일을 수정(touch)할 때만 재시작이 발생한다. 대규모 리팩터링 중에 매번 저장마다 재시작이 일어나는 것을 막고 싶을 때 유용한 패턴이다.

```bash
# 재시작이 필요한 시점에만 실행
touch .reloadtrigger
```

---

### RemoteSpringApplication — 원격 개발 환경 연동

Docker 컨테이너나 원격 서버에서 실행 중인 애플리케이션에 로컬 변경 사항을 즉시 반영할 수 있는 기능이다.

**원격 서버 설정 (application.yml)**:

```yaml
spring:
  devtools:
    remote:
      secret: "my-secure-secret-key"  # 반드시 복잡한 값으로 설정
```

**로컬 IDE Run Configuration 추가**:

```
Main Class: org.springframework.boot.devtools.RemoteSpringApplication
Program Arguments: https://your-remote-server.com
```

원격 서버로 변경된 클래스를 HTTP로 업로드하고 재시작을 트리거한다. 단, 이 기능은 **운영 환경에 절대 사용해서는 안 된다**.

---

### IntelliJ IDEA와 완벽하게 연동하기

IntelliJ IDEA는 기본적으로 **Build Project를 수동으로 실행해야** 클래스 파일이 변경된다. 자동 재시작을 활용하려면 두 가지 방법이 있다.

**방법 1: 자동 빌드 활성화 (권장)**

```
Settings → Build, Execution, Deployment → Compiler
→ ✅ Build project automatically
```

이후 Registry 설정 (IntelliJ 2021.2 이전):
```
Help → Find Action → Registry
→ compiler.automake.allow.when.app.running = true
```

IntelliJ 2021.2 이후:
```
Settings → Advanced Settings
→ ✅ Allow auto-make to start even if developed application is currently running
```

**방법 2: 저장 시 액션 실행**

```
Settings → Tools → Actions on Save
→ ✅ Build project
```

---

### 커스텀 재시작 제외 패턴 실전 예시

대용량 정적 파일이나 자주 변경되지 않는 설정을 제외해 재시작 속도를 높인다.

```java
// RestartApplication 설정 커스터마이징
@Configuration
public class DevToolsConfig {

    // DevTools가 특정 클래스 변경을 무시하도록
    // 재시작 제외 클래스로더 설정 예시
    @Bean
    public RestartConfiguration restartConfiguration() {
        return new RestartConfiguration();
    }
}
```

```yaml
spring:
  devtools:
    restart:
      exclude: >
        static/**,
        public/**,
        templates/**,
        **/*.png,
        **/*.jpg,
        **/*.svg,
        **/*.css,
        **/*.js,
        META-INF/**
      additional-paths:
        - src/main/java
        - src/main/resources/messages
```

CSS, JS, 이미지 파일은 LiveReload만으로 충분하므로 재시작 트리거에서 제외하는 것이 핵심이다.

---

## 주의사항 및 트레이드오프

### ⚠️ 운영 환경 배포 시 DevTools 활성화 위험

DevTools는 `spring.devtools.restart.enabled=false`로 명시적으로 비활성화하거나, JAR 패키징 시 제외해야 한다. DevTools가 운영에 올라가면 다음 문제가 발생한다.

- 불필요한 클래스로더 오버헤드
- 원격 재시작 기능 노출 시 보안 취약점
- 캐시 비활성화로 인한 성능 저하

Spring Boot는 완전한 JAR로 패키징된 애플리케이션에서는 DevTools를 자동으로 비활성화하지만, **war 배포나 IDE에서 직접 원격 실행하는 경우**는 주의가 필요하다.

### ⚠️ Dual ClassLoader로 인한 `ClassCastException`

두 개의 클래스로더가 동작하므로, 같은 클래스처럼 보여도 ClassLoader가 다르면 `ClassCastException`이 발생할 수 있다. 특히 **캐시, 싱글턴 패턴, 직렬화** 관련 코드에서 재시작 후 이상 동작이 보고된다.

```java
// 문제 발생 가능 패턴
// 재시작 전 캐시에 저장된 객체를 재시작 후 꺼낼 때
// Base ClassLoader와 Restart ClassLoader 간 타입 불일치
Object cached = cache.get("key");
MyService service = (MyService) cached; // ClassCastException 가능
```

이런 경우 캐시를 인터페이스 기반으로 설계하거나, 재시작 시 캐시를 초기화하도록 처리해야 한다.

### ⚠️ JPA/Hibernate 세션과 재시작

Hibernate `SessionFactory`나 JPA `EntityManagerFactory`는 재시작 시 새로 초기화된다. 개발 중 H2 인메모리 DB를 사용하는 경우 **데이터가 초기화**된다는 점을 인지해야 한다.

```yaml
# 개발 환경 H2 파일 모드로 전환하면 재시작 후에도 데이터 유지
spring:
  datasource:
    url: jdbc:h2:file:./devdb;AUTO_SERVER=TRUE
```

### ⚠️ LiveReload vs HMR 비교

Spring Boot의 LiveReload는 React/Vue의 **Hot Module Replacement(HMR)**과 다르다. 전체 페이지를 새로고침하므로 폼 입력값, 스크롤 위치 등 브라우저 상태가 초기화된다. 복잡한 프론트엔드 상태가 필요한 개발이라면 Vite나 Webpack Dev Server의 HMR을 병행 운용하는 것을 권장한다.

### 성능 트레이드오프 정리

| 항목 | DevTools 재시작 | Full Restart | JRebel/DCEVM |
|---|---|---|---|
| 속도 | 빠름 (2~10초) | 느림 (10~60초+) | 매우 빠름 (< 1초) |
| 비용 | 무료 | 무료 | 유료/설정 복잡 |
| 완전성 | 80~90% | 100% | 95%+ |
| 운영 사용 | 불가 | 가능 | 가능 |

---

## 정리

Spring Boot DevTools는 단순한 자동 재시작 도구가 아니라, Dual ClassLoader 전략, LiveReload 통합, 프로퍼티 자동 오버라이드 등 개발 경험 전반을 고려해 설계된 모듈이다.

실무 적용 시 핵심 포인트를 다시 정리하면:

1. **의존성 스코프**를 반드시 `optional` 또는 `developmentOnly`로 설정해 운영 배포를 차단한다.
2. **`quiet-period`와 `exclude`** 설정으로 불필요한 재시작을 줄여 오히려 더 빠른 개발 사이클을 만든다.
3. **IntelliJ IDEA**와 연동 시 자동 빌드 설정을 반드시 활성화한다.
4. **Dual ClassLoader** 특성으로 인한 `ClassCastException` 등 엣지 케이스를 인지하고 코드를 설계한다.
5. 팀 협업 시 프로젝트 설정과 개인 글로벌 설정을 분리해 관리한다.

DevTools를 올바르게 설정한 개발 환경에서는 코드 수정부터 브라우저 확인까지 걸리는 시간을 기존 대비 **70~80% 단축**할 수 있다. 개발 생산성의 차이는 결국 이런 작은 사이클 타임의 합산이다.