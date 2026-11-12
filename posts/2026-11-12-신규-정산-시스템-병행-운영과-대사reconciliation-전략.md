# 신규 정산 시스템 병행 운영과 대사(Reconciliation) 전략

## 개요

정산 시스템은 금융, 커머스, 결제 플랫폼 등 거의 모든 서비스에서 핵심 도메인에 해당한다. 그런데 이 시스템을 교체하거나 개선할 때는 단순한 마이그레이션과는 차원이 다른 복잡성이 따른다. 돈이 오가는 계산 로직이기 때문에, 한 번의 오류가 실제 금전적 손실이나 과오납, 미납 사태로 이어질 수 있다.

이 글에서는 **구 시스템을 운영하면서 신규 정산 시스템을 병행(Parallel Run)으로 도입하고, 두 시스템의 결과를 대사(Reconciliation)하여 신뢰도를 검증한 뒤 전환하는 전략**을 다룬다. 특정 기업의 내부 사례가 아닌, 이런 유형의 시스템 전환에서 공통적으로 나타나는 설계 문제와 선택 기준을 중심으로 설명한다.

---

## 핵심 개념

### 병행 운영(Parallel Run)이란?

병행 운영은 구 시스템과 신 시스템이 **동일한 입력에 대해 동시에 처리를 수행**하고, 그 결과를 비교하는 방식이다. 신 시스템의 결과를 실제 서비스에는 반영하지 않고(Shadow Mode), 오직 비교와 검증 목적으로만 사용한다.

```
실제 트랜잭션
     │
     ├──▶ [구 시스템] ──▶ 실제 정산 반영 (Output A)
     │
     └──▶ [신 시스템] ──▶ 비교용 결과만 저장 (Output B)
                                  │
                          [대사(Reconciliation)]
                          A vs B 차이 감지 및 리포트
```

이 방식은 신 시스템이 "틀렸을 때"의 리스크를 최소화하면서 실제 운영 데이터로 충분한 검증을 할 수 있다는 장점이 있다.

### 대사(Reconciliation)란?

대사는 금융 도메인에서 **두 개 이상의 데이터 소스를 비교하여 불일치를 발견하고 조정하는 프로세스**다. 정산 시스템 전환에서는 구 시스템과 신 시스템의 계산 결과가 일치하는지 확인하는 데 사용된다.

대사 과정에서 주로 확인하는 항목은 다음과 같다.

- **금액 일치 여부**: 정산 금액, 수수료, 부가세 등의 계산값
- **건수 일치 여부**: 처리된 트랜잭션 수
- **상태 일치 여부**: 정산 완료/보류/실패 상태
- **집계 단위별 합산**: 파트너사별, 상품별, 날짜별 집계

---

## 실전 예제

### 1. 이벤트 기반 Shadow 처리 구조 (Spring + Kafka)

병행 운영을 구현하는 가장 현실적인 방법은 **이벤트 기반으로 신 시스템에 동일한 이벤트를 흘려보내는 것**이다. 구 시스템의 처리 흐름을 건드리지 않으면서, Kafka 같은 메시징 시스템을 통해 신 시스템이 동일한 데이터를 소비하도록 한다.

```java
// 구 시스템: 정산 처리 후 이벤트 발행
@Service
@RequiredArgsConstructor
public class LegacySettlementService {

    private final LegacySettlementRepository repository;
    private final ApplicationEventPublisher eventPublisher;

    @Transactional
    public SettlementResult settle(SettlementRequest request) {
        SettlementResult result = calculateSettlement(request);
        repository.save(result);

        // 신 시스템 검증을 위한 이벤트 발행 (Kafka)
        eventPublisher.publishEvent(new SettlementCompletedEvent(
            request.getTransactionId(),
            request,
            result
        ));

        return result;
    }
}
```

```java
// 신 시스템: Shadow 처리 후 대사용 결과 저장
@Component
@RequiredArgsConstructor
@KafkaListener(topics = "settlement.completed", groupId = "new-settlement-shadow")
public class NewSettlementShadowConsumer {

    private final NewSettlementEngine newEngine;
    private final ReconciliationRepository reconciliationRepository;

    public void consume(SettlementCompletedEvent event) {
        try {
            // 신 시스템으로 동일 입력 처리
            SettlementResult newResult = newEngine.calculate(event.getRequest());

            // 대사 레코드 저장 (실제 반영 X)
            reconciliationRepository.save(ReconciliationRecord.builder()
                .transactionId(event.getTransactionId())
                .legacyResult(event.getResult())
                .newResult(newResult)
                .processedAt(LocalDateTime.now())
                .build());

        } catch (Exception e) {
            log.error("Shadow processing failed for txId={}", event.getTransactionId(), e);
            // 신 시스템 실패는 구 시스템에 영향 없음
        }
    }
}
```

### 2. 대사 비교 로직

대사의 핵심은 단순한 equals 비교가 아니라, **허용 오차(tolerance)와 불일치 분류**를 명확히 정의하는 것이다.

```java
@Service
@RequiredArgsConstructor
public class ReconciliationService {

    private static final BigDecimal AMOUNT_TOLERANCE = new BigDecimal("0.01"); // 1원 이하 허용

    public ReconciliationReport compare(ReconciliationRecord record) {
        SettlementResult legacy = record.getLegacyResult();
        SettlementResult newResult = record.getNewResult();

        List<Discrepancy> discrepancies = new ArrayList<>();

        // 1. 정산 금액 비교
        BigDecimal amountDiff = legacy.getSettlementAmount()
            .subtract(newResult.getSettlementAmount()).abs();

        if (amountDiff.compareTo(AMOUNT_TOLERANCE) > 0) {
            discrepancies.add(Discrepancy.of(
                DiscrepancyType.AMOUNT_MISMATCH,
                "settlement_amount",
                legacy.getSettlementAmount(),
                newResult.getSettlementAmount()
            ));
        }

        // 2. 수수료 비교
        BigDecimal feeDiff = legacy.getFeeAmount()
            .subtract(newResult.getFeeAmount()).abs();

        if (feeDiff.compareTo(AMOUNT_TOLERANCE) > 0) {
            discrepancies.add(Discrepancy.of(
                DiscrepancyType.FEE_MISMATCH,
                "fee_amount",
                legacy.getFeeAmount(),
                newResult.getFeeAmount()
            ));
        }

        // 3. 정산 상태 비교
        if (!legacy.getStatus().equals(newResult.getStatus())) {
            discrepancies.add(Discrepancy.of(
                DiscrepancyType.STATUS_MISMATCH,
                "status",
                legacy.getStatus(),
                newResult.getStatus()
            ));
        }

        return ReconciliationReport.builder()
            .transactionId(record.getTransactionId())
            .matched(discrepancies.isEmpty())
            .discrepancies(discrepancies)
            .build();
    }
}
```

### 3. 배치 기반 집계 대사

개별 트랜잭션 단위 대사 외에, **일별·파트너사별 집계 합산이 일치하는지 확인하는 배치 대사**도 반드시 필요하다.

```sql
-- 구 시스템 일별 정산 집계
SELECT
    settlement_date,
    partner_id,
    COUNT(*) AS tx_count,
    SUM(settlement_amount) AS total_amount,
    SUM(fee_amount) AS total_fee
FROM legacy_settlement
WHERE settlement_date = :targetDate
GROUP BY settlement_date, partner_id;

-- 신 시스템 일별 정산 집계 (shadow 테이블)
SELECT
    settlement_date,
    partner_id,
    COUNT(*) AS tx_count,
    SUM(settlement_amount) AS total_amount,
    SUM(fee_amount) AS total_fee
FROM new_settlement_shadow
WHERE settlement_date = :targetDate
GROUP BY settlement_date, partner_id;
```

```java
@Component
@RequiredArgsConstructor
public class DailyReconciliationBatch {

    private final LegacyAggregationRepository legacyRepo;
    private final NewSystemAggregationRepository newRepo;
    private final AlertService alertService;

    @Scheduled(cron = "0 0 2 * * *") // 매일 새벽 2시
    public void runDailyReconciliation() {
        LocalDate targetDate = LocalDate.now().minusDays(1);

        List<AggregationResult> legacyAggs = legacyRepo.aggregateByDate(targetDate);
        List<AggregationResult> newAggs = newRepo.aggregateByDate(targetDate);

        Map<String, AggregationResult> newAggMap = newAggs.stream()
            .collect(Collectors.toMap(AggregationResult::getPartnerId, Function.identity()));

        for (AggregationResult legacy : legacyAggs) {
            AggregationResult newAgg = newAggMap.get(legacy.getPartnerId());

            if (newAgg == null) {
                alertService.sendAlert("대사 누락: partnerId=" + legacy.getPartnerId());
                continue;
            }

            if (!legacy.getTotalAmount().equals(newAgg.getTotalAmount())) {
                alertService.sendAlert(String.format(
                    "집계 불일치: partnerId=%s, legacy=%s, new=%s",
                    legacy.getPartnerId(),
                    legacy.getTotalAmount(),
                    newAgg.getTotalAmount()
                ));
            }
        }
    }
}
```

### 4. 전환(Cutover) 판단 기준 지표

대사 결과를 대시보드로 시각화하고, **전환 기준 지표(Gate Criteria)**를 사전에 정의해두는 것이 중요하다.

```
전환 판단 체크리스트:
─────────────────────────────────────────────
 항목                         기준
─────────────────────────────────────────────
 트랜잭션 단위 대사 일치율    99.99% 이상
 집계 금액 불일치 건수        연속 7일 0건
 Shadow 처리 실패율           0.1% 이하
 불일치 유형 분류 완료        100%
 롤백 절차 검토 완료          ✅
─────────────────────────────────────────────
```

---

## 주의사항 및 트레이드오프

### ① 시간적 비결정성(Timing Non-determinism) 문제

구 시스템과 신 시스템이 동일한 입력을 처리하더라도, **처리 시점의 환율, 수수료율, 정책 테이블이 달라질 수 있다**. 특히 Shadow 소비자가 Kafka lag으로 인해 수 초~수 분 뒤에 처리될 경우, 그 사이에 정책이 변경된다면 "당연히 불일치"인 케이스가 발생한다.

이를 방지하려면 **이벤트 페이로드에 처리 시점의 정책 스냅샷을 포함**시키거나, 신 시스템이 구 시스템과 동일한 정책 버전을 참조할 수 있도록 설계해야 한다.

### ② 대사 결과의 노이즈 관리

초기에는 의도된 로직 차이(예: 버그 수정, 반올림 정책 변경)로 인해 대사 불일치가 대량 발생한다. 이를 모두 알럿으로 처리하면 **알럿 피로(Alert Fatigue)**가 생겨 실제 중요한 불일치를 놓칠 수 있다.

불일치를 다음과 같이 분류하여 관리하는 것을 권장한다.

- **Known Diff**: 의도된 로직 차이로 등록된 케이스 (화이트리스트)
- **Unknown Diff**: 원인 불명의 불일치 → 즉시 조사 대상
- **Tolerable Diff**: 허용 오차 이내의 부동소수점 차이

### ③ Shadow 처리 성능 부하

병행 운영은 사실상 **모든 트랜잭션을 두 번 처리**하는 것이다. 신 시스템의 Shadow 처리가 느리거나 DB 부하를 유발한다면, Kafka consumer lag이 쌓이고 대사 결과의 실시간성이 떨어진다. Shadow 처리는 별도 클러스터나 읽기 전용 복제본을 활용하고, 처리량을 모니터링해야 한다.

### ④ 전환 후 롤백 전략

전환(Cutover) 이후에도 단기간은 구 시스템과의 대사를 유지하는 것이 안전하다. 신 시스템을 실제 반영으로 전환한 뒤에도, **구 시스템을 Read-only 상태로 일정 기간 유지**하면서 역방향 대사(신 → 구)를 통해 이상 감지를 계속한다. 문제가 발견되면 구 시스템으로 즉시 롤백할 수 있는 Feature Flag 기반의 전환 스위치를 준비해두자.

```java
@Service
public class SettlementRouter {

    @Value("${settlement.use-new-system:false}")
    private boolean useNewSystem;

    public SettlementResult route(SettlementRequest request) {
        if (useNewSystem) {
            return newSettlementService.settle(request);
        }
        return legacySettlementService.settle(request);
    }
}
```

---

## 정리

신규 정산 시스템의 도입은 단순한 코드 교체가 아니라, **신뢰를 쌓아가는 점진적인 전환 프로세스**다. 핵심 원칙을 정리하면 다음과 같다.

| 단계 | 목표 | 핵심 활동 |
|------|------|-----------|
| **병행 운영 준비** | Shadow 환경 구성 | Kafka 토픽 분기, Shadow DB 구성 |
| **대사 설계** | 비교 기준 정의 | 허용 오차, 불일치 분류 체계 수립 |
| **병행 운영 실행** | 실 데이터 검증 | 트랜잭션/집계 대사 반복 수행 |
| **노이즈 제거** | Known Diff 관리 | 화이트리스트 등록 및 Unknown Diff 집중 조사 |
| **전환 판단** | Gate Criteria 충족 | 대사 일치율, 실패율 기준 달성 확인 |
| **전환 후 모니터링** | 조기 이상 감지 | 역방향 대사, 롤백 스위치 유지 |

정산 시스템 전환은 완벽한 코드보다 **충분한 검증 프로세스**가 더 중요하다. 대사 체계를 탄탄하게 설계하고, 불일치의 원인을 하나씩 해소해 나가는 과정 자체가 신 시스템의 신뢰도를 증명하는 과정이다. 급하게 전환을 서두르기보다는, 대사 결과가 명확히 "안전하다"고 말해줄 때까지 병행 운영을 유지하는 것이 현명한 선택이다.
