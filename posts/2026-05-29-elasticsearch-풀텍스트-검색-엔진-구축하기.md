# Elasticsearch 풀텍스트 검색 엔진 구축하기

## 개요

현대 서비스에서 검색 기능은 사용자 경험의 핵심입니다. 단순한 SQL `LIKE` 쿼리로는 감당할 수 없는 수준의 검색 품질과 성능이 요구되는 시점이 오면, 자연스럽게 **Elasticsearch(이하 ES)**를 도입하게 됩니다.

Elasticsearch는 Apache Lucene 기반의 분산 검색 엔진으로, 풀텍스트 검색, 로그 분석, 추천 시스템 등 다양한 분야에서 활용됩니다. 이 글에서는 실무에서 바로 적용 가능한 수준으로 ES 풀텍스트 검색 엔진을 구축하는 방법을 다룹니다. 인덱스 설계, 한국어 형태소 분석기 적용, Spring Boot 연동, 그리고 운영 시 고려해야 할 트레이드오프까지 실전 중심으로 설명합니다.

---

## 핵심 개념

### Inverted Index (역색인)

ES가 빠른 풀텍스트 검색을 제공할 수 있는 근본적인 이유는 **역색인** 구조 덕분입니다. 일반 DB가 "문서 → 단어" 방향으로 저장한다면, 역색인은 "단어 → 문서 목록" 방향으로 색인을 구성합니다.

```
[일반 저장]
문서1: "스프링 부트로 API 서버 구축"
문서2: "스프링 시큐리티 인증 처리"

[역색인]
"스프링" → [문서1, 문서2]
"부트"   → [문서1]
"API"    → [문서1]
"시큐리티" → [문서2]
```

### 분석기(Analyzer) 파이프라인

텍스트가 색인될 때 `Analyzer → Tokenizer → Token Filter` 파이프라인을 거칩니다. 한국어 서비스라면 기본 Standard Analyzer 대신 **Nori** (한국어 형태소 분석기)를 반드시 적용해야 합니다.

### 주요 쿼리 타입

| 쿼리 타입 | 용도 |
|---|---|
| `match` | 단일 필드 풀텍스트 검색 |
| `multi_match` | 복수 필드 풀텍스트 검색 |
| `bool` | must / should / must_not 조합 |
| `term` | 정확한 값 매칭 (keyword 타입) |
| `range` | 숫자/날짜 범위 검색 |
| `function_score` | 스코어 커스터마이징 |

---

## 실전 예제

### 1. 인덱스 매핑 설계

상품 검색 시나리오를 가정합니다. 핵심은 **분석이 필요한 필드**와 **정확한 매칭이 필요한 필드**를 명확히 구분하는 것입니다.

```json
PUT /products
{
  "settings": {
    "number_of_shards": 3,
    "number_of_replicas": 1,
    "analysis": {
      "analyzer": {
        "korean_analyzer": {
          "type": "custom",
          "tokenizer": "nori_tokenizer",
          "filter": ["nori_part_of_speech", "lowercase", "stop"]
        },
        "korean_search_analyzer": {
          "type": "custom",
          "tokenizer": "nori_tokenizer",
          "filter": ["nori_part_of_speech", "lowercase"]
        }
      }
    }
  },
  "mappings": {
    "properties": {
      "productId": {
        "type": "keyword"
      },
      "name": {
        "type": "text",
        "analyzer": "korean_analyzer",
        "search_analyzer": "korean_search_analyzer",
        "fields": {
          "keyword": {
            "type": "keyword"
          }
        }
      },
      "description": {
        "type": "text",
        "analyzer": "korean_analyzer"
      },
      "category": {
        "type": "keyword"
      },
      "price": {
        "type": "integer"
      },
      "stock": {
        "type": "integer"
      },
      "tags": {
        "type": "keyword"
      },
      "createdAt": {
        "type": "date",
        "format": "yyyy-MM-dd'T'HH:mm:ss"
      }
    }
  }
}
```

> **실무 팁:** `name` 필드에 `fields.keyword`를 추가한 이유는 풀텍스트 검색과 정렬/집계를 동시에 지원하기 위해서입니다. 분석된 `text` 타입은 정렬에 사용할 수 없습니다.

### 2. Spring Boot + Elasticsearch 연동

**의존성 추가 (build.gradle)**

```groovy
implementation 'co.elastic.clients:elasticsearch-java:8.11.0'
implementation 'com.fasterxml.jackson.core:jackson-databind:2.15.2'
```

**ElasticsearchConfig**

```java
@Configuration
public class ElasticsearchConfig {

    @Value("${elasticsearch.host}")
    private String host;

    @Value("${elasticsearch.port}")
    private int port;

    @Bean
    public ElasticsearchClient elasticsearchClient() {
        RestClient restClient = RestClient.builder(
            new HttpHost(host, port, "http")
        ).build();

        ElasticsearchTransport transport = new RestClientTransport(
            restClient, new JacksonJsonpMapper()
        );

        return new ElasticsearchClient(transport);
    }
}
```

**Product Document 모델**

```java
@Getter
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class ProductDocument {
    private String productId;
    private String name;
    private String description;
    private String category;
    private Integer price;
    private Integer stock;
    private List<String> tags;
    private String createdAt;
}
```

**ProductSearchService**

```java
@Service
@RequiredArgsConstructor
@Slf4j
public class ProductSearchService {

    private static final String INDEX = "products";
    private final ElasticsearchClient esClient;

    /**
     * 복합 풀텍스트 검색
     * - name, description에 대한 multi_match
     * - 카테고리 필터, 가격 범위 필터 지원
     * - 재고 있는 상품 우선 노출 (function_score)
     */
    public SearchResponse<ProductDocument> search(ProductSearchRequest request) throws IOException {

        // Bool 쿼리 빌드
        BoolQuery.Builder boolQuery = new BoolQuery.Builder();

        // 1. 검색어 처리 (must)
        if (StringUtils.hasText(request.getKeyword())) {
            boolQuery.must(m -> m
                .multiMatch(mm -> mm
                    .query(request.getKeyword())
                    .fields("name^3", "description^1", "tags^2") // boost 가중치
                    .type(TextQueryType.BestFields)
                    .operator(Operator.And)
                )
            );
        }

        // 2. 카테고리 필터 (filter - 스코어에 영향 없음)
        if (StringUtils.hasText(request.getCategory())) {
            boolQuery.filter(f -> f
                .term(t -> t
                    .field("category")
                    .value(request.getCategory())
                )
            );
        }

        // 3. 가격 범위 필터
        if (request.getMinPrice() != null || request.getMaxPrice() != null) {
            boolQuery.filter(f -> f
                .range(r -> r
                    .field("price")
                    .gte(request.getMinPrice() != null
                        ? JsonData.of(request.getMinPrice()) : null)
                    .lte(request.getMaxPrice() != null
                        ? JsonData.of(request.getMaxPrice()) : null)
                )
            );
        }

        // 4. function_score - 재고 있는 상품 부스팅
        FunctionScoreQuery functionScoreQuery = FunctionScoreQuery.of(fs -> fs
            .query(q -> q.bool(boolQuery.build()))
            .functions(fn -> fn
                .filter(f -> f.range(r -> r.field("stock").gt(JsonData.of(0))))
                .weight(1.5)
            )
            .boostMode(FunctionBoostMode.Multiply)
        );

        return esClient.search(s -> s
            .index(INDEX)
            .query(q -> q.functionScore(functionScoreQuery))
            .from(request.getPage() * request.getSize())
            .size(request.getSize())
            .sort(so -> so
                .score(sc -> sc.order(SortOrder.Desc))
            )
            .highlight(h -> h
                .fields("name", hf -> hf.numberOfFragments(0))
                .fields("description", hf -> hf.numberOfFragments(2).fragmentSize(150))
                .preTags("<em>")
                .postTags("</em>")
            ),
            ProductDocument.class
        );
    }

    /**
     * 문서 색인 (단건)
     */
    public void index(ProductDocument product) throws IOException {
        esClient.index(i -> i
            .index(INDEX)
            .id(product.getProductId())
            .document(product)
        );
    }

    /**
     * 벌크 색인
     */
    public void bulkIndex(List<ProductDocument> products) throws IOException {
        List<BulkOperation> operations = products.stream()
            .map(p -> BulkOperation.of(op -> op
                .index(idx -> idx
                    .index(INDEX)
                    .id(p.getProductId())
                    .document(p)
                )
            ))
            .collect(Collectors.toList());

        BulkResponse response = esClient.bulk(b -> b.operations(operations));

        if (response.errors()) {
            response.items().stream()
                .filter(item -> item.error() != null)
                .forEach(item -> log.error("Bulk index error: {}", item.error().reason()));
        }
    }
}
```

### 3. 동기화 전략 — DB와 ES 일관성 유지

실무에서 가장 많이 쓰는 패턴은 **Transactional Outbox + CDC(Change Data Capture)** 조합입니다.

```
[DB 변경] → [Outbox 테이블에 이벤트 저장] → [Debezium/Kafka Connect]
         → [Kafka Topic] → [ES Indexer Consumer] → [Elasticsearch]
```

간단한 서비스라면 `@TransactionalEventListener`로 처리할 수 있습니다:

```java
@Service
@RequiredArgsConstructor
public class ProductEventHandler {

    private final ProductSearchService searchService;

    @Async
    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
    public void handleProductSaved(ProductSavedEvent event) {
        try {
            searchService.index(event.toDocument());
        } catch (IOException e) {
            // 실패 시 재시도 큐에 적재하거나 알림 발송
            log.error("ES indexing failed for productId: {}", event.getProductId(), e);
        }
    }
}
```

---

## 주의사항 및 트레이드오프

### ⚠️ 샤드 설계는 처음부터 신중하게

ES에서 샤드 수는 **인덱스 생성 후 변경이 불가**합니다 (Reindex 필요). 일반적인 가이드라인은 샤드당 10~50GB를 유지하는 것이지만, 초기 데이터 규모와 증가 속도를 고려해야 합니다. 무분별한 샤드 수 증가는 오히려 성능 저하를 유발합니다.

### ⚠️ ES는 주 데이터 저장소가 아닙니다

ES는 검색에 최적화된 보조 저장소입니다. **Single Source of Truth는 반드시 RDB 또는 다른 주 저장소**에 두어야 합니다. 장애 상황에서 ES 데이터는 재색인으로 복구할 수 있어야 합니다.

### ⚠️ Near Real-Time(NRT) 특성 이해

ES는 색인 후 기본적으로 **1초 후**에 검색 가능합니다(`refresh_interval` 기본값). 실시간성이 중요한 경우 `refresh=true` 옵션을 사용할 수 있지만, 성능 비용이 따릅니다. 대량 색인 시에는 오히려 `refresh_interval`을 `-1`로 설정해 성능을 높이고, 색인 완료 후 수동 refresh 하는 것이 유리합니다.

### ⚠️ 쿼리 캐싱과 힙 메모리 관리

ES는 JVM 힙 메모리의 50%를 OS 파일 시스템 캐시로 남겨두는 것을 권장합니다. 힙은 전체 물리 메모리의 50%, 최대 32GB를 넘지 않도록 설정합니다 (32GB 초과 시 CompressedOops 비활성화).

```yaml
# jvm.options
-Xms16g
-Xmx16g
```

### ⚠️ 스코어링의 함정

`function_score`, `script_score` 남용은 검색 성능 저하의 주범입니다. 특히 script 계산은 매 문서마다 실행되므로, 가능하면 **사전 계산된 값을 필드에 저장**하고 검색 시 참조하는 방식을 권장합니다.

---

## 정리

| 항목 | 권장사항 |
|---|---|
| 한국어 분석 | Nori Analyzer 적용 필수 |
| 매핑 설계 | text + keyword 멀티필드 구성 |
| 동기화 전략 | Outbox + CDC 또는 이벤트 기반 |
| 샤드 설계 | 샤드당 10~50GB 기준으로 초기 설계 |
| 메모리 | 힙 최대 32GB, 나머지는 OS 캐시 |
| 운영 | Kibana로 슬로우 쿼리 로그 모니터링 |

Elasticsearch는 강력하지만, 잘못 설계하면 오히려 복잡도와 운영 비용만 늘어날 수 있습니다. **인덱스 매핑과 분석기를 처음부터 올바르게 설계**하고, DB와의 동기화 전략을 명확히 정의하는 것이 성공적인 도입의 핵심입니다. 풀텍스트 검색 품질을 높이고 싶다면 이후 단계로 **동의어 사전(Synonym)**, **자동완성(Completion Suggester)**, **Learning to Rank(LTR)** 적용을 고려해보시기 바랍니다.