# Java Thread Pool 튜닝과 커넥션 풀 사이징 전략

## 개요

고트래픽 환경에서 애플리케이션 성능을 결정짓는 핵심 요소 중 하나는 **Thread Pool**과 **Connection Pool**의 적절한 사이징이다. 잘못 설정된 풀은 과도한 컨텍스트 스위칭, 데이터베이스 연결 고갈, 요청 타임아웃 등의 문제를 야기하며, 이는 결국 서비스 장애로 이어진다.

많은 팀이 이 두 가지 설정을 "일단 크게 잡고 보자"는 식으로 접근하지만, 이는 오히려 리소스 낭비와 성능 저하를 초래할 수 있다. 이 글에서는 Thread Pool과 Connection Pool의 동작 원리를 이해하고, 실무에서 적용 가능한 튜닝 전략과 공식을 다룬다.

---

## 핵심 개념

### Thread Pool 동작 원리

Java의 `ThreadPoolExecutor`는 다음 순서로 동작한다:

1. `corePoolSize`까지 새 스레드 생성
2. 큐(BlockingQueue)에 작업 적재
3. 큐가 가득 차면 `maximumPoolSize`까지 스레드 추가 생성
4. `maximumPoolSize`도 초과하면 `RejectedExecutionHandler` 실행

이 순서를 이해하지 못하면 직관과 반대되는 동작을 마주하게 된다. **큐 사이즈를 무한정으로 설정(LinkedBlockingQueue 기본값)하면 `maximumPoolSize`는 사실상 의미가 없다.**

### Little's Law와 풀 사이징

풀 사이징의 이론적 기반은 **Little's Law**다:

```
L = λ × W
```

- `L`: 시스템 내 평균 요청 수 (= 필요한 스레드 수)
- `λ`: 초당 요청 처리량 (throughput)
- `W`: 요청당 평균 처리 시간 (latency)

예를 들어, 초당 500 RPS를 처리하고 평균 응답 시간이 200ms라면:

```
L = 500 × 0.2 = 100 스레드
```

이론적으로 100개의 스레드가 필요하다.

### CPU-Bound vs I/O-Bound

스레드 수 결정에 가장 중요한 기준은 작업 성격이다:

| 구분 | 특징 | 권장 스레드 수 |
|------|------|--------------|
| CPU-Bound | 계산 위주, 대기 시간 없음 | CPU 코어 수 + 1 |
| I/O-Bound | DB/네트워크 대기 시간 많음 | CPU 코어 수 × (1 + 대기시간/처리시간) |

**Brian Goetz의 공식:**

```
최적 스레드 수 = CPU 코어 수 × (1 + W/C)
```

- `W`: 대기 시간 (Wait time)
- `C`: 계산 시간 (Compute time)

DB 조회가 대부분인 I/O-Bound 서비스에서 W/C 비율이 9라면, 8코어 서버에서 `8 × (1 + 9) = 80` 스레드가 이론적 최적값이 된다.

---

## 실전 예제

### ThreadPoolExecutor 커스텀 설정

```java
@Configuration
public class ThreadPoolConfig {

    @Bean("applicationTaskExecutor")
    public ThreadPoolTaskExecutor applicationTaskExecutor() {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        
        int coreCount = Runtime.getRuntime().availableProcessors();
        
        // I/O-Bound 작업 기준 (W/C = 9 가정)
        executor.setCorePoolSize(coreCount * 10);
        executor.setMaxPoolSize(coreCount * 20);
        
        // 큐 사이즈: corePoolSize의 2배 정도로 제한
        // 무한 큐는 maximumPoolSize를 무력화함
        executor.setQueueCapacity(coreCount * 20);
        
        // 유휴 스레드 유지 시간
        executor.setKeepAliveSeconds(60);
        
        // 우아한 종료 설정
        executor.setWaitForTasksToCompleteOnShutdown(true);
        executor.setAwaitTerminationSeconds(30);
        
        // 거부 정책: Caller가 직접 실행 (백프레셔 효과)
        executor.setRejectedExecutionHandler(new ThreadPoolExecutor.CallerRunsPolicy());
        
        executor.setThreadNamePrefix("app-executor-");
        executor.initialize();
        
        return executor;
    }
    
    @Bean("externalApiExecutor")
    public ThreadPoolTaskExecutor externalApiExecutor() {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        
        // 외부 API 호출용 - 더 높은 W/C 비율 적용
        int coreCount = Runtime.getRuntime().availableProcessors();
        executor.setCorePoolSize(coreCount * 15);
        executor.setMaxPoolSize(coreCount * 30);
        executor.setQueueCapacity(500);
        executor.setThreadNamePrefix("external-api-");
        executor.initialize();
        
        return executor;
    }
}
```

### HikariCP 커넥션 풀 튜닝

HikariCP는 현재 Java 생태계에서 가장 널리 사용되는 JDBC 커넥션 풀이다. **HikariCP 공식 권장 공식**은 다음과 같다:

```
connections = ((core_count * 2) + effective_spindle_count)
```

SSD를 사용하는 경우 `effective_spindle_count = 1`로 간주한다.

```yaml
# application.yml
spring:
  datasource:
    hikari:
      # 최소 유지 커넥션 수
      minimum-idle: 10
      # 최대 커넥션 수 (핵심 설정)
      maximum-pool-size: 20
      # 커넥션 획득 대기 타임아웃 (30초)
      connection-timeout: 30000
      # 유휴 커넥션 유지 시간 (10분)
      idle-timeout: 600000
      # 커넥션 최대 수명 (30분)
      max-lifetime: 1800000
      # 커넥션 유효성 검사 쿼리
      connection-test-query: SELECT 1
      # 풀 이름 (모니터링 시 식별용)
      pool-name: MainHikariPool
      # 누수 감지 임계값 (2초)
      leak-detection-threshold: 2000
```

```java
@Configuration
public class DataSourceConfig {

    @Bean
    @Primary
    public DataSource primaryDataSource() {
        HikariConfig config = new HikariConfig();
        
        config.setJdbcUrl("jdbc:postgresql://localhost:5432/mydb");
        config.setUsername("user");
        config.setPassword("password");
        
        int cpuCores = Runtime.getRuntime().availableProcessors();
        
        // HikariCP 공식 적용
        int poolSize = (cpuCores * 2) + 1;
        config.setMinimumIdle(poolSize / 2);
        config.setMaximumPoolSize(poolSize);
        
        // 성능 최적화 설정
        config.addDataSourceProperty("cachePrepStmts", "true");
        config.addDataSourceProperty("prepStmtCacheSize", "250");
        config.addDataSourceProperty("prepStmtCacheSqlLimit", "2048");
        config.addDataSourceProperty("useServerPrepStmts", "true");
        
        config.setPoolName("PrimaryPool");
        config.setLeakDetectionThreshold(3000);
        
        return new HikariDataSource(config);
    }
    
    @Bean
    public DataSource readReplicaDataSource() {
        HikariConfig config = new HikariConfig();
        
        // Read Replica는 읽기 트래픽에 맞게 더 크게 설정 가능
        int cpuCores = Runtime.getRuntime().availableProcessors();
        config.setMaximumPoolSize((cpuCores * 2) + 1 + 5);
        config.setPoolName("ReadReplicaPool");
        
        // ... 나머지 설정
        
        return new HikariDataSource(config);
    }
}
```

### Thread Pool과 Connection Pool의 균형 설정

**Thread Pool과 Connection Pool 간의 관계가 핵심이다.** DB 커넥션을 사용하는 스레드 수가 커넥션 풀 사이즈를 초과하면 스레드들이 커넥션 대기로 블로킹된다.

```java
@Service
@RequiredArgsConstructor
public class PoolBalancingValidator {

    private final DataSource dataSource;
    
    @PostConstruct
    public void validatePoolBalance() {
        if (dataSource instanceof HikariDataSource hikariDataSource) {
            HikariPoolMXBean poolMXBean = hikariDataSource.getHikariPoolMXBean();
            HikariConfigMXBean configMXBean = hikariDataSource.getHikariConfigMXBean();
            
            int maxConnections = configMXBean.getMaximumPoolSize();
            
            // 스레드 풀 사이즈가 커넥션 풀보다 훨씬 크다면 경고
            // 일반적으로 Thread Pool <= Connection Pool * 1.5 권장
            log.info("Connection Pool Max Size: {}", maxConnections);
            log.info("권장 Thread Pool 최대 크기: {}", (int)(maxConnections * 1.5));
        }
    }
}
```

### Actuator를 활용한 모니터링

```java
@Component
@RequiredArgsConstructor
public class PoolMetricsCollector {

    private final MeterRegistry meterRegistry;
    private final ThreadPoolTaskExecutor applicationTaskExecutor;
    
    @Scheduled(fixedDelay = 5000)
    public void collectMetrics() {
        ThreadPoolExecutor pool = applicationTaskExecutor.getThreadPoolExecutor();
        
        // 활성 스레드 수
        meterRegistry.gauge("thread.pool.active", pool.getActiveCount());
        // 큐 대기 작업 수
        meterRegistry.gauge("thread.pool.queue.size", pool.getQueue().size());
        // 풀 사이즈
        meterRegistry.gauge("thread.pool.size", pool.getPoolSize());
        // 완료된 작업 수
        meterRegistry.gauge("thread.pool.completed", pool.getCompletedTaskCount());
        
        // 큐가 80% 이상 찼을 때 경고
        int queueSize = pool.getQueue().size();
        int queueCapacity = queueSize + pool.getQueue().remainingCapacity();
        
        if (queueSize > queueCapacity * 0.8) {
            log.warn("Thread Pool 큐 사용률 80% 초과! 현재: {}/{}", queueSize, queueCapacity);
        }
    }
}
```

---

## 주의사항 및 트레이드오프

### 1. 스레드 수가 많다고 항상 좋은 건 아니다

컨텍스트 스위칭 비용은 실제로 크다. 스레드가 지나치게 많으면 CPU가 실제 작업보다 컨텍스트 스위칭에 더 많은 시간을 쓰게 된다. **JVM 환경에서 스레드 수가 수천을 넘어가면 오히려 처리량이 급감하는 경우가 많다.**

Java 21의 **Virtual Thread(가상 스레드)** 는 이 문제를 해소하는 방향으로 설계되었다. 블로킹 I/O에서는 가상 스레드를 적극적으로 검토하자.

```java
// Java 21 Virtual Thread 적용
@Bean
public Executor virtualThreadExecutor() {
    return Executors.newVirtualThreadPerTaskExecutor();
}
```

### 2. 커넥션 풀 사이즈의 역설

많은 개발자가 커넥션 풀을 크게 잡을수록 성능이 좋아질 것이라 생각하지만, **DB 서버 관점에서는 오히려 반대다.** DB 서버 자체도 연결당 메모리를 소비하고, 과도한 연결은 DB 내부의 락 경합과 메모리 부족을 야기한다. PostgreSQL의 경우 연결당 약 5~10MB의 메모리를 사용한다.

### 3. CallerRunsPolicy의 양면성

`CallerRunsPolicy`는 백프레셔를 제공하지만, 요청을 받은 스레드(주로 HTTP 요청 스레드)가 작업을 직접 처리하게 되어 HTTP 응답 지연으로 이어질 수 있다. **사용자 응답이 중요한 경로에서는 신중하게 적용해야 한다.**

### 4. 풀 설정은 환경마다 다르다

개발, 스테이징, 운영 환경의 CPU 코어 수와 DB 서버 스펙이 다를 수 있다. **하드코딩된 풀 사이즈 대신 런타임 CPU 코어 수 기반의 동적 계산을 사용하거나, 환경 변수로 외부화하는 것을 권장한다.**

```yaml
hikari:
  maximum-pool-size: ${DB_POOL_SIZE:20}
```

### 5. 워밍업 전략

최소 유휴 커넥션(`minimum-idle`)을 최대값과 동일하게 설정하면 초기 트래픽 급증 시 커넥션 생성 지연 없이 대응할 수 있다. 운영 환경에서는 `minimumIdle = maximumPoolSize`로 고정 풀을 유지하는 것을 HikariCP 팀도 권장한다.

---

## 정리

Thread Pool과 Connection Pool 튜닝은 단순한 숫자 조정이 아니라, 애플리케이션의 작업 성격과 인프라 환경을 깊이 이해한 위에서 이루어져야 한다.

| 설정 포인트 | 핵심 원칙 |
|-------------|-----------|
| Thread Pool Core Size | CPU 코어 수 × (1 + W/C) 공식 기반 |
| Thread Pool Queue | 유한 큐 설정, 무한 큐는 maxPoolSize 무력화 |
| Connection Pool Size | `(CPU × 2) + 1` 을 기본값으로 시작 |
| Thread:Connection 비율 | Thread Pool ≤ Connection Pool × 1.5 권장 |
| 거부 정책 | 서비스 특성에 맞게 선택 (CallerRuns vs Abort) |

튜닝의 시작은 **측정**이다. Actuator + Micrometer + Grafana를 연동해 실시간 풀 메트릭을 시각화하고, 부하 테스트(k6, nGrinder 등)를 통해 병목을 확인한 뒤 설정을 조정하는 **데이터 기반 접근**이 가장 중요하다. 이론적 공식은 출발점일 뿐, 최종 답은 항상 실측 데이터에서 나온다.