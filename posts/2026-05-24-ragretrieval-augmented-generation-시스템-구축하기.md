# RAG(Retrieval-Augmented Generation) 시스템 구축하기

## 개요

LLM(Large Language Model)은 강력하지만 본질적인 한계를 가지고 있다. 학습 데이터의 컷오프 이후 정보를 모르고, 내부 기업 문서나 도메인 특화 지식은 전혀 없으며, 때로는 그럴싸하지만 틀린 답변(Hallucination)을 생성한다.

**RAG(Retrieval-Augmented Generation)**는 이 문제를 해결하는 가장 실용적인 아키텍처다. 외부 지식 베이스에서 관련 문서를 검색(Retrieve)하여 프롬프트에 컨텍스트로 주입한 뒤 LLM이 답변을 생성(Generate)하도록 한다. Fine-tuning 없이도 최신 정보와 도메인 지식을 LLM에 결합할 수 있어, 현재 엔터프라이즈 AI 도입의 핵심 패턴으로 자리 잡았다.

이 글에서는 RAG 시스템의 핵심 개념부터 Spring Boot + LangChain4j를 활용한 실전 구현, 그리고 프로덕션 적용 시 주의해야 할 트레이드오프까지 다룬다.

---

## 핵심 개념

### RAG 파이프라인의 두 단계

RAG는 크게 **인덱싱(Indexing)**과 **쿼리(Query)** 두 단계로 나뉜다.

```
[인덱싱 파이프라인]
문서 로드 → 청킹(Chunking) → 임베딩(Embedding) → 벡터 DB 저장

[쿼리 파이프라인]
사용자 질문 → 질문 임베딩 → 유사도 검색 → 컨텍스트 주입 → LLM 응답 생성
```

### 핵심 컴포넌트

| 컴포넌트 | 역할 | 대표 도구 |
|---|---|---|
| Document Loader | 다양한 소스에서 문서 로드 | Tika, PDFBox, Custom |
| Text Splitter | 문서를 청크 단위로 분할 | RecursiveCharacterSplitter |
| Embedding Model | 텍스트를 벡터로 변환 | OpenAI, HuggingFace, Cohere |
| Vector Store | 벡터 저장 및 유사도 검색 | Pinecone, Weaviate, pgvector |
| LLM | 최종 답변 생성 | GPT-4, Claude, Llama |

### 청킹 전략이 품질을 결정한다

청킹은 RAG 성능에 직접적인 영향을 미치는 핵심 요소다. 청크가 너무 작으면 컨텍스트가 부족하고, 너무 크면 노이즈가 증가하며 토큰 비용이 올라간다.

- **Fixed Size Chunking**: 단순하지만 문장이 잘릴 수 있음
- **Recursive Character Splitter**: 문단 → 문장 → 단어 순으로 재귀적으로 분할 (권장)
- **Semantic Chunking**: 의미론적 유사도를 기반으로 분할, 품질 높지만 비용 증가
- **Sliding Window**: 청크 간 오버랩을 두어 경계 손실 방지

---

## 실전 예제

### 프로젝트 구성

Spring Boot 3.x + LangChain4j + pgvector 기반으로 사내 기술 문서 Q&A 시스템을 구축한다.

```xml
<!-- pom.xml 의존성 -->
<dependencies>
    <dependency>
        <groupId>dev.langchain4j</groupId>
        <artifactId>langchain4j-spring-boot-starter</artifactId>
        <version>0.31.0</version>
    </dependency>
    <dependency>
        <groupId>dev.langchain4j</groupId>
        <artifactId>langchain4j-pgvector</artifactId>
        <version>0.31.0</version>
    </dependency>
    <dependency>
        <groupId>dev.langchain4j</groupId>
        <artifactId>langchain4j-open-ai</artifactId>
        <version>0.31.0</version>
    </dependency>
</dependencies>
```

### 1단계: 문서 인덱싱 서비스

```java
@Service
@RequiredArgsConstructor
@Slf4j
public class DocumentIndexingService {

    private final EmbeddingModel embeddingModel;
    private final EmbeddingStore<TextSegment> embeddingStore;

    /**
     * 텍스트 문서를 청킹하여 벡터 DB에 인덱싱
     */
    public void indexDocument(String documentId, String content, Map<String, String> metadata) {
        // 재귀적 텍스트 분할 (오버랩 50자)
        DocumentSplitter splitter = DocumentSplitters.recursive(
            512,   // 청크 최대 토큰 수
            50,    // 오버랩 크기
            new OpenAiTokenizer("text-embedding-ada-002")
        );

        Document document = Document.from(content, Metadata.from(metadata));
        List<TextSegment> segments = splitter.split(document);

        log.info("문서 '{}' → {}개 청크로 분할", documentId, segments.size());

        // 배치 임베딩 처리 (API 호출 최소화)
        List<Embedding> embeddings = embeddingModel.embedAll(segments).content();
        embeddingStore.addAll(embeddings, segments);

        log.info("벡터 DB 인덱싱 완료: documentId={}", documentId);
    }

    /**
     * PDF 파일 인덱싱
     */
    public void indexPdfFile(Path pdfPath) throws IOException {
        // Apache Tika를 활용한 PDF 텍스트 추출
        Document document = FileSystemDocumentLoader.loadDocument(
            pdfPath,
            new ApacheTikaDocumentParser()
        );

        Map<String, String> metadata = Map.of(
            "source", pdfPath.getFileName().toString(),
            "indexed_at", Instant.now().toString()
        );

        indexDocument(pdfPath.getFileName().toString(), document.text(), metadata);
    }
}
```

### 2단계: RAG 기반 Q&A 서비스

```java
@Service
@RequiredArgsConstructor
public class RagQnaService {

    private final EmbeddingModel embeddingModel;
    private final EmbeddingStore<TextSegment> embeddingStore;
    private final ChatLanguageModel chatModel;

    private static final String SYSTEM_PROMPT = """
        당신은 기술 문서 전문 어시스턴트입니다.
        제공된 컨텍스트를 기반으로만 답변하세요.
        컨텍스트에 없는 정보는 "제공된 문서에서 해당 정보를 찾을 수 없습니다"라고 답변하세요.
        답변 시 참조한 문서 출처를 반드시 명시하세요.
        """;

    public RagResponse query(String userQuestion) {
        // 1. 질문을 벡터로 변환
        Embedding questionEmbedding = embeddingModel.embed(userQuestion).content();

        // 2. 유사도 기반 관련 청크 검색 (상위 5개, 최소 유사도 0.75)
        EmbeddingSearchRequest searchRequest = EmbeddingSearchRequest.builder()
            .queryEmbedding(questionEmbedding)
            .maxResults(5)
            .minScore(0.75)
            .build();

        List<EmbeddingMatch<TextSegment>> matches =
            embeddingStore.search(searchRequest).matches();

        if (matches.isEmpty()) {
            return RagResponse.noContext("관련 문서를 찾을 수 없습니다.");
        }

        // 3. 검색된 청크를 컨텍스트로 조합
        String context = matches.stream()
            .map(match -> {
                String source = match.embedded().metadata().getString("source");
                String text = match.embedded().text();
                double score = match.score();
                return String.format("[출처: %s, 유사도: %.2f]\n%s", source, score, text);
            })
            .collect(Collectors.joining("\n\n---\n\n"));

        // 4. 프롬프트 구성 및 LLM 호출
        String userMessage = String.format("""
            [참고 문서]
            %s
            
            [질문]
            %s
            """, context, userQuestion);

        ChatResponse chatResponse = chatModel.chat(
            SystemMessage.from(SYSTEM_PROMPT),
            UserMessage.from(userMessage)
        );

        String answer = chatResponse.aiMessage().text();

        // 5. 소스 메타데이터 수집
        List<String> sources = matches.stream()
            .map(m -> m.embedded().metadata().getString("source"))
            .distinct()
            .toList();

        return RagResponse.of(answer, sources, matches.size());
    }
}
```

### 3단계: REST API 노출

```java
@RestController
@RequestMapping("/api/v1/rag")
@RequiredArgsConstructor
public class RagController {

    private final RagQnaService ragQnaService;
    private final DocumentIndexingService indexingService;

    @PostMapping("/query")
    public ResponseEntity<RagResponse> query(@RequestBody @Valid QueryRequest request) {
        RagResponse response = ragQnaService.query(request.question());
        return ResponseEntity.ok(response);
    }

    @PostMapping("/index")
    public ResponseEntity<Void> indexDocument(
        @RequestParam("file") MultipartFile file
    ) throws IOException {
        Path tempPath = Files.createTempFile("upload-", file.getOriginalFilename());
        file.transferTo(tempPath);
        indexingService.indexPdfFile(tempPath);
        Files.deleteIfExists(tempPath);
        return ResponseEntity.accepted().build();
    }
}

// DTO 정의
public record QueryRequest(@NotBlank String question) {}

public record RagResponse(
    String answer,
    List<String> sources,
    int retrievedChunks,
    boolean hasContext
) {
    public static RagResponse of(String answer, List<String> sources, int chunks) {
        return new RagResponse(answer, sources, chunks, true);
    }
    public static RagResponse noContext(String answer) {
        return new RagResponse(answer, List.of(), 0, false);
    }
}
```

### application.yml 설정

```yaml
langchain4j:
  open-ai:
    api-key: ${OPENAI_API_KEY}
    chat-model:
      model-name: gpt-4o-mini
      temperature: 0.1      # RAG에서는 낮은 temperature 권장
      max-tokens: 1000
    embedding-model:
      model-name: text-embedding-3-small

spring:
  datasource:
    url: jdbc:postgresql://localhost:5432/ragdb
    username: ${DB_USER}
    password: ${DB_PASSWORD}

# pgvector 설정
embedding-store:
  dimension: 1536           # text-embedding-3-small 차원
  table-name: document_embeddings
```

---

## 주의사항 및 트레이드오프

### 1. 청크 크기와 검색 품질의 균형

청크를 작게 쪼갤수록 검색 정밀도는 올라가지만, 개별 청크의 의미 완결성이 떨어진다. 실무에서는 **Parent-Child Chunking** 전략이 효과적이다. 작은 청크로 검색하되, 실제 컨텍스트는 해당 청크가 속한 부모 청크(더 큰 단위)를 LLM에 전달한다.

### 2. Hallucination은 줄지만 사라지지 않는다

RAG는 Hallucination을 크게 줄이지만 완전히 제거하지 못한다. LLM이 컨텍스트를 무시하거나 잘못 해석할 수 있다. **프롬프트에 "컨텍스트에 없으면 모른다고 답하라"는 명시적 지시**와 함께, 응답에 소스 인용을 강제하는 방식으로 신뢰성을 높여야 한다.

### 3. 벡터 검색의 한계 — Hybrid Search 고려

의미론적 유사도 검색만으로는 고유명사, 코드 스니펫, 버전 번호 같은 키워드 매칭에 취약하다. **Hybrid Search** (벡터 검색 + BM25/Full-text Search)를 결합하면 검색 재현율을 크게 높일 수 있다. pgvector + PostgreSQL의 `tsvector`를 함께 사용하거나, Elasticsearch의 `knn` + `match` 조합이 실용적이다.

### 4. 비용과 레이턴시 관리

| 전략 | 효과 |
|---|---|
| 임베딩 캐싱 (Redis) | 동일 질문 재임베딩 방지 |
| Reranker 모델 도입 | 검색 정밀도 향상, 단 추가 레이턴시 발생 |
| 청크 수 제한 | Top-K를 줄여 토큰 비용 절감 |
| 로컬 임베딩 모델 | OpenAI API 비용 제거, 온프레미스 보안 |

### 5. 인덱스 최신성 유지

문서가 업데이트되면 기존 벡터도 갱신해야 한다. 문서 ID 기반 버전 관리와 **증분 인덱싱(Incremental Indexing)** 전략을 설계 초기부터 반영해야 한다. 단순히 재인덱싱하면 중복 청크가 쌓인다.

### 6. 보안 — 컨텍스트 격리

멀티테넌트 환경에서는 사용자별 접근 권한에 맞는 문서만 검색해야 한다. 벡터 DB의 메타데이터 필터링 기능(`tenant_id`, `department` 등)을 검색 시 반드시 적용하라. 잘못 구성하면 다른 테넌트의 데이터가 컨텍스트로 노출될 수 있다.

---

## 정리

RAG는 LLM의 한계를 실용적으로 보완하는 현재 가장 검증된 아키텍처다. 핵심을 정리하면 다음과 같다.

- **청킹 전략**이 RAG 성능의 60%를 결정한다. 도메인에 맞는 전략 선택이 필수다.
- **Hybrid Search**로 키워드와 의미론적 검색을 결합하면 재현율이 크게 향상된다.
- **Temperature를 낮게** 설정하고, 시스템 프롬프트에 컨텍스트 외 답변을 금지하라.
- 프로덕션 적용 시 **비용, 레이턴시, 보안(컨텍스트 격리)**를 동시에 고려해야 한다.
- RAG는 시작점이다. 이후 **Reranker, Query Rewriting, Self-RAG** 등으로 고도화할 수 있다.

Spring Boot + LangChain4j 조합은 Java 생태계에서 RAG를 가장 빠르게 프로토타이핑할 수 있는 스택이다. 오늘 소개한 코드를 베이스로 사내 문서 검색, 고객 지원 자동화, 기술 명세 Q&A 등 다양한 유스케이스에 바로 적용해보길 권한다.