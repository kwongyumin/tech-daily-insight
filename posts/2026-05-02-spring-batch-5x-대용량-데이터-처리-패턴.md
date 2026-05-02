# Spring Batch 5.x 대용량 데이터 처리 패턴

## 개요

Spring Batch 5.x는 Spring Boot 3.x와 함께 Jakarta EE 9+ 기반으로 전환되며 많은 변화를 가져왔다. 단순한 네임스페이스 변경(javax → jakarta)을 넘어, `@EnableBatchProcessing` 자동 구성 방식 변경, `JobRepository` 설정 간소화, 그리고 다양한 성능 최적화 옵션이 추가되었다.

대용량 데이터 처리는 배치의 핵심 존재 이유다. 수천만 건의 데이터를 안정적으로 처리하려면 단순히 "돌아가는 코드"를 넘어, 청크 전략 설계, 파티셔닝, 병렬 처리, 그리고 재시도/스킵 전략까지 종합적인 고려가 필요하다. 이 글에서는 Spring Batch 5.x 환경에서 실무에 바로 적용 가능한 대용량 처리 패턴을 다룬다.

---

## 핵심 개념

### 1. Chunk-Oriented Processing 재검토

Spring Batch의 청크 처리는 `ItemReader → ItemProcessor → ItemWriter` 파이프라인으로 동작한다. 5.x에서는 `ChunkOrientedTasklet`의 내부 동작이 더욱 명확하게 분리되었으며, `ChunkListener`를 통한 세밀한 제어가 가능해졌다.

청크 크기(chunk size)는 성능의 핵심 변수다. 너무 작으면 트랜잭션 오버헤드가 증가하고, 너무 크면 메모리 압박과 롤백 비용이 커진다. 일반적으로 **500~5000** 사이에서 DB 쿼리 패턴, 네트워크 I/O, GC 압박을 종합적으로 고려하여 결정한다.

### 2. Partitioning vs. Multi-threaded Step

대용량 처리에서 가장 많이 선택하는 두 가지 전략이다.

| 구분 | Partitioning | Multi-threaded Step |
|------|-------------|---------------------|
| 데이터 분할 방식 | 파티션 키 기반 명시적 분할 | 단일 Reader 공유 (스레드 경쟁) |
| 상태 관리 | 각 파티션 독립적 `StepExecution` | 단일 `StepExecution` |
| 재시작 안정성 | 높음 (파티션 단위 재시작) | 낮음 |
| 적합한 상황 | 범위 분할 가능한 데이터 | 순서 무관한 큐/파일 처리 |

### 3. 5.x에서의 주요 변경 사항

```java
// 4.x 방식 (Deprecated)
@Configuration
@EnableBatchProcessing
public class BatchConfig extends DefaultBatchConfigurer { ... }

// 5.x 방식 - @EnableBatchProcessing 없이 자동 구성
// application.yml에서 제어
// spring.batch.job.enabled=false (자동 실행 방지)
```

5.x에서는 `@EnableBatchProcessing`을 선언하면 오히려 자동 구성이 **비활성화**된다. Spring Boot의 자동 구성에 맡기는 것이 권장 방식이다.

---

## 실전 예제

### 예제 1: 범위 기반 파티셔닝으로 1억 건 처리

주문 데이터 1억 건을 날짜 범위로 파티셔닝하는 예제다.

```java
// Partitioner 구현
@Component
public class OrderDatePartitioner implements Partitioner {

    private final OrderRepository orderRepository;

    @Override
    public Map<String, ExecutionContext> partition(int gridSize) {
        LocalDate minDate = orderRepository.findMinOrderDate();
        LocalDate maxDate = orderRepository.findMaxOrderDate();

        long totalDays = ChronoUnit.DAYS.between(minDate, maxDate);
        long daysPerPartition = Math.max(1, totalDays / gridSize);

        Map<String, ExecutionContext> partitions = new LinkedHashMap<>();
        LocalDate partitionStart = minDate;

        for (int i = 0; i < gridSize; i++) {
            LocalDate partitionEnd = (i == gridSize - 1)
                ? maxDate
                : partitionStart.plusDays(daysPerPartition - 1);

            ExecutionContext context = new ExecutionContext();
            context.putString("startDate", partitionStart.toString());
            context.putString("endDate", partitionEnd.toString());
            context.putInt("partitionIndex", i);

            partitions.put("partition_" + i, context);
            partitionStart = partitionEnd.plusDays(1);
        }
        return partitions;
    }
}
```

```java
// Job & Step 설정
@Configuration
public class OrderBatchConfig {

    @Bean
    public Job orderProcessingJob(JobRepository jobRepository,
                                   Step managerStep) {
        return new JobBuilder("orderProcessingJob", jobRepository)
            .start(managerStep)
            .build();
    }

    @Bean
    public Step managerStep(JobRepository jobRepository,
                             Step workerStep,
                             OrderDatePartitioner partitioner,
                             TaskExecutor batchTaskExecutor) {
        return new StepBuilder("managerStep", jobRepository)
            .partitioner("workerStep", partitioner)
            .step(workerStep)
            .gridSize(10)
            .taskExecutor(batchTaskExecutor)
            .build();
    }

    @Bean
    public Step workerStep(JobRepository jobRepository,
                            PlatformTransactionManager transactionManager,
                            ItemReader<Order> orderItemReader,
                            ItemProcessor<Order, ProcessedOrder> orderProcessor,
                            ItemWriter<ProcessedOrder> orderWriter) {
        return new StepBuilder("workerStep", jobRepository)
            .<Order, ProcessedOrder>chunk(1000, transactionManager)
            .reader(orderItemReader)
            .processor(orderProcessor)
            .writer(orderWriter)
            .faultTolerant()
            .skipLimit(100)
            .skip(DataAccessException.class)
            .retryLimit(3)
            .retry(DeadlockLoserDataAccessException.class)
            .build();
    }

    @Bean
    public TaskExecutor batchTaskExecutor() {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(10);
        executor.setMaxPoolSize(10); // 파티션 수와 맞춤
        executor.setQueueCapacity(0); // 즉시 거부, 파티션 수 초과 방지
        executor.setThreadNamePrefix("batch-worker-");
        executor.initialize();
        return executor;
    }
}
```

### 예제 2: JdbcPagingItemReader 최적화

대용량 DB 읽기에서 가장 흔한 실수는 `JdbcCursorItemReader`와 `JdbcPagingItemReader`를 잘못 선택하는 것이다.

```java
@Bean
@StepScope
public JdbcPagingItemReader<Order> orderItemReader(
    DataSource dataSource,
    @Value("#{stepExecutionContext['startDate']}") String startDate,
    @Value("#{stepExecutionContext['endDate']}") String endDate) {

    Map<String, Object> parameterValues = new HashMap<>();
    parameterValues.put("startDate", LocalDate.parse(startDate));
    parameterValues.put("endDate", LocalDate.parse(endDate));

    return new JdbcPagingItemReaderBuilder<Order>()
        .name("orderItemReader")
        .dataSource(dataSource)
        .selectClause("SELECT order_id, customer_id, amount, order_date, status")
        .fromClause("FROM orders")
        .whereClause("WHERE order_date BETWEEN :startDate AND :endDate")
        .sortKeys(Map.of("order_id", Order.ASCENDING)) // 정렬 키는 인덱스 컬럼으로!
        .parameterValues(parameterValues)
        .rowMapper(new BeanPropertyRowMapper<>(Order.class))
        .pageSize(1000)
        .build();
}
```

> **주의**: `sortKeys`에 지정된 컬럼은 반드시 인덱스가 있어야 한다. 페이징 쿼리는 내부적으로 `ORDER BY` + `OFFSET/FETCH` 또는 keyset pagination으로 변환되므로, 인덱스 없이 사용하면 오히려 성능이 급격히 저하된다.

### 예제 3: 고성능 JdbcBatchItemWriter

```java
@Bean
public JdbcBatchItemWriter<ProcessedOrder> orderWriter(DataSource dataSource) {
    return new JdbcBatchItemWriterBuilder<ProcessedOrder>()
        .dataSource(dataSource)
        .sql("""
            INSERT INTO processed_orders (order_id, customer_id, final_amount, processed_at)
            VALUES (:orderId, :customerId, :finalAmount, :processedAt)
            ON DUPLICATE KEY UPDATE
                final_amount = VALUES(final_amount),
                processed_at = VALUES(processed_at)
            """)
        .beanMapped()
        .assertUpdates(false) // UPSERT 시 false 필수
        .build();
}
```

### 예제 4: 메모리 효율적인 ItemProcessor 체이닝

```java
@Bean
public CompositeItemProcessor<Order, ProcessedOrder> compositeProcessor() {
    return new CompositeItemProcessorBuilder<Order, ProcessedOrder>()
        .delegates(
            validationProcessor(),    // 유효성 검사
            enrichmentProcessor(),    // 데이터 보강
            calculationProcessor()    // 금액 계산
        )
        .build();
}

// null 반환으로 필터링 (skip과 다름 - 카운트에서 제외)
@Bean
@StepScope
public ItemProcessor<Order, Order> validationProcessor() {
    return order -> {
        if (order.getAmount().compareTo(BigDecimal.ZERO) <= 0) {
            log.warn("Invalid order filtered: {}", order.getOrderId());
            return null; // Writer로 전달되지 않음
        }
        return order;
    };
}
```

---

## 주의사항 및 트레이드오프

### 1. @StepScope는 항상 확인하라

파티셔닝 시 `ItemReader`, `ItemProcessor`, `ItemWriter`에 `@StepScope`가 없으면 모든 파티션이 동일한 빈 인스턴스를 공유한다. 파티션별 `ExecutionContext` 값을 SpEL로 주입받는 경우 반드시 `@StepScope`를 붙여야 한다.

```java
// 잘못된 예 - 모든 파티션이 같은 startDate를 바라봄
@Bean
public ItemReader<Order> orderReader(@Value("#{stepExecutionContext['startDate']}") ...) { }

// 올바른 예
@Bean
@StepScope  // 필수!
public ItemReader<Order> orderReader(@Value("#{stepExecutionContext['startDate']}") ...) { }
```

### 2. JobRepository 테이블 인덱스 관리

대용량 배치를 오랫동안 운영하면 `BATCH_JOB_EXECUTION`, `BATCH_STEP_EXECUTION` 테이블이 수천만 건으로 불어난다. 주기적인 정리 작업과 함께, `JOB_INSTANCE_ID`, `JOB_EXECUTION_ID` 컬럼 인덱스 상태를 점검해야 한다.

```sql
-- 오래된 배치 이력 정리 예시 (MySQL)
DELETE FROM BATCH_STEP_EXECUTION_CONTEXT
WHERE STEP_EXECUTION_ID IN (
    SELECT STEP_EXECUTION_ID FROM BATCH_STEP_EXECUTION
    WHERE START_TIME < DATE_SUB(NOW(), INTERVAL 90 DAY)
);
-- 순서: CONTEXT → STEP_EXECUTION → JOB_EXECUTION_CONTEXT → JOB_EXECUTION → JOB_INSTANCE
```

### 3. 트랜잭션 격리 수준과 데드락

멀티 스레드/파티션 환경에서 같은 테이블에 쓰기 작업이 집중되면 데드락이 발생할 수 있다. `RETRY` 설정만으로 해결이 안 될 경우 다음을 검토한다.

- **INSERT 순서 정렬**: 파티션별 INSERT 순서를 `order_id` 기준으로 정렬
- **격리 수준 조정**: `READ_COMMITTED`로 낮추고 낙관적 잠금 병행
- **파티션 키와 테이블 파티셔닝 일치**: DB 테이블을 날짜 파티셔닝하고 배치 파티션 키를 동일하게 맞추면 잠금 경합이 최소화된다.

### 4. 청크 크기와 JVM 힙 메모리 관계

청크 크기 1000에 객체 하나가 평균 1KB라면 하나의 청크는 약 1MB를 차지한다. 10개 파티션이 동시에 실행되면 청크 버퍼만 10MB다. 하지만 `ItemProcessor`에서 데이터 보강으로 객체가 10배 커진다면 100MB가 된다. GC 로그를 모니터링하면서 청크 크기를 조정하는 것이 실무적 접근이다.

### 5. 멱등성(Idempotency) 설계

배치는 실패 후 재실행을 전제로 설계해야 한다. Writer에서 `ON DUPLICATE KEY UPDATE` 또는 `MERGE` 구문을 사용하거나, 처리 상태 컬럼(`processed_yn`, `batch_job_id`)을 두어 중복 처리를 방지한다. Step의 `allowStartIfComplete(true)` 옵션은 완료된 Step도 재실행이 필요한 경우에만 신중하게 사용한다.

---

## 정리

Spring Batch 5.x에서 대용량 데이터를 안정적으로 처리하기 위한 핵심 포인트를 정리하면 다음과 같다.

| 관심사 | 권장 전략 |
|--------|----------|
| 데이터 분할 | 범위 기반 Partitioner + 인덱스 컬럼 활용 |
| 읽기 성능 | JdbcPagingItemReader + 적절한 pageSize |
| 쓰기 성능 | JdbcBatchItemWriter + UPSERT |
| 오류 처리 | faultTolerant + skip/retry 전략 |
| 병렬성 | ThreadPoolTaskExecutor (파티션 수 == 스레드 수) |
| 재시작 안전성 | @StepScope + 멱등성 설계 |

배치 시스템은 "만들기"보다 "운영하기"가 훨씬 어렵다. 처음부터 모니터링, 알람, 이력 관리를 고려한 설계를 해두면 장애 상황에서 훨씬 빠르게 대응할 수 있다. Spring Batch의 `JobExplorer`, `JobOperator` API와 Micrometer 메트릭 연동을 추가로 검토해볼 것을 권장한다.