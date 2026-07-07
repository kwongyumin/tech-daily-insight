import json
import os
import random
import re
from datetime import date
from pathlib import Path

import anthropic

HISTORY_FILE = Path(".topic-history.json")
POSTS_DIR = Path("posts")

TOPIC_POOL: dict[str, list[str]] = {
    "Java/Spring": [
        "Spring Boot 3.x의 새로운 기능 완벽 가이드",
        "Java 21 Virtual Threads (Project Loom) 실전 적용",
        "GraalVM Native Image로 Spring Boot 앱 경량화하기",
        "Spring WebFlux와 리액티브 프로그래밍 패턴",
        "Spring Security 6.x OAuth2/OIDC 설정 가이드",
        "JPA N+1 문제 해결 완전 정복",
        "Spring Batch 5.x 대용량 데이터 처리 패턴",
        "Spring Data JPA Specification으로 동적 쿼리 작성",
        "Spring AOT와 프록시 없는 경량 컨텍스트",
        "Java Records와 Sealed Classes 모던 자바 문법",
        "Java의 Structured Concurrency 동시성 혁신",
        "Java 패턴 매칭과 Switch 표현식 완벽 이해",
        "Kotlin + Spring Boot 마이그레이션 실전 가이드",
        "Spring Modulith 모듈형 모놀리스 아키텍처",
        "R2DBC 반응형 데이터베이스 접근 전략",
        "Spring Boot DevTools와 LiveReload 개발 생산성 극대화",
        "Spring Integration 엔터프라이즈 통합 패턴 실전",
        "OpenAPI 3.0과 Spring REST Docs 문서화 전략",
        "Spring Cloud Gateway API 게이트웨이 구성하기",
        "Spring Boot Actuator와 Micrometer 모니터링 설정",
        "GraphQL과 Spring Boot API 설계 가이드",
        "Spring Boot 3 Native Test 지원 활용법",
        "Testcontainers로 Spring Boot 통합 테스트 환경 구축",
        "Spring Boot 트랜잭션 전파 옵션 완전 정리",
        "Spring Bean 생명주기와 초기화 순서 트러블슈팅",
        "Spring Boot Configuration Properties 검증과 타입-세이프 설정",
        "Java Garbage Collector 종류별 튜닝 전략 (G1, ZGC, Shenandoah)",
        "Spring Retry와 회복탄력성 패턴 구현",
        "Spring Boot 멀티모듈 프로젝트 구조 설계",
        "Spring AOP 프록시 내부 동작과 Self-Invocation 함정",
        "Spring Boot 커스텀 Actuator Endpoint 만들기",
        "Java Thread Pool 튜닝과 커넥션 풀 사이징 전략",
        "Spring Boot Bean Validation 커스텀 Validator 설계",
    ],
    "서버/인프라": [
        "Kubernetes에서 Spring Boot 운영 베스트 프랙티스",
        "Docker 멀티스테이지 빌드로 이미지 사이즈 최적화",
        "Zero Downtime 배포 블루/그린 카나리 전략",
        "AWS ECS/Fargate Spring Boot 배포 전략",
        "서비스 메시 Istio와 Spring Boot 연동 가이드",
        "Nginx 리버스 프록시 고급 설정과 성능 튜닝",
        "Linux 서버 성능 분석과 병목 지점 찾기",
        "Helm Chart로 Kubernetes 애플리케이션 패키징",
        "Terraform으로 클라우드 인프라 코드화 (IaC)",
        "CI/CD 파이프라인 구축 GitHub Actions 완전 가이드",
        "HTTP/2와 HTTP/3 서버 설정 및 성능 비교",
        "gRPC와 Protocol Buffers 서버 간 통신 설계",
        "WebSocket과 STOMP 실시간 통신 서버 구현",
        "API Rate Limiting 전략과 구현 방법",
        "서버리스 아키텍처 AWS Lambda와 Spring Cloud Function",
        "Java Flight Recorder로 프로덕션 성능 분석하기",
        "ShedLock으로 분산 스케줄러 중복 실행 방지",
        "ELK 스택으로 Spring Boot 로그 중앙화",
        "Prometheus + Grafana Spring 앱 메트릭 시각화",
        "OpenTelemetry 분산 트레이싱 Spring Boot 적용",
        "Kubernetes HPA/VPA 오토스케일링 전략과 실전 튜닝",
        "컨테이너 리소스 Limit/Request 설정과 OOMKill 방지",
        "서비스 디스커버리 패턴 Consul vs Eureka 비교",
        "무중단 배포를 위한 Kubernetes Rolling Update 세부 튜닝",
        "Chaos Engineering 장애 주입 테스트 실전 가이드",
        "Envoy 데이터 플레인으로 서비스 메시 구성하기",
        "Kubernetes Operator 패턴으로 운영 자동화하기",
        "리눅스 cgroup과 컨테이너 격리 원리",
    ],
    "데이터베이스": [
        "PostgreSQL 쿼리 최적화와 인덱스 전략 완벽 가이드",
        "MySQL 파티셔닝과 샤딩 대규모 데이터 관리",
        "Redis를 활용한 분산 캐싱 전략과 패턴",
        "MongoDB Spring Data 반정형 데이터 처리 패턴",
        "Flyway로 데이터베이스 마이그레이션 자동화",
        "PostgreSQL MVCC와 트랜잭션 격리 수준 이해",
        "데이터베이스 커넥션 풀 HikariCP 최적화 가이드",
        "Apache Cassandra 분산 NoSQL 데이터 모델링",
        "Elasticsearch 풀텍스트 검색 엔진 구축하기",
        "TimescaleDB 시계열 데이터 저장과 분석",
        "데이터베이스 레플리케이션과 읽기 분산 전략",
        "Redis Pub/Sub과 Stream으로 실시간 메시징 구현",
        "CQRS 패턴 적용으로 읽기/쓰기 데이터베이스 분리",
        "DynamoDB 설계 원칙과 Single-Table Design",
        "ClickHouse 실시간 분석 쿼리 최적화 전략",
        "PostgreSQL Vacuum과 Bloat 관리 전략",
        "MySQL Replication Lag 원인 분석과 해결",
        "Redis Cluster 샤딩과 Failover 전략",
        "분산 락 Redisson vs ZooKeeper 비교",
        "무중단 데이터베이스 스키마 마이그레이션 전략",
        "OLTP와 OLAP 워크로드 분리 아키텍처",
    ],
    "네트워크": [
        "TCP/IP 핵심 개념과 백엔드 개발자가 알아야 할 것들",
        "HTTP 캐싱 전략 Cache-Control 완전 정복",
        "DNS 동작 원리와 서비스 배포 시 고려사항",
        "TLS/SSL 인증서 원리와 HTTPS 설정 가이드",
        "로드 밸런서 L4 vs L7 차이와 선택 기준",
        "CDN 동작 원리와 글로벌 서비스 최적화 전략",
        "WebRTC 실시간 P2P 통신 백엔드 시그널링 서버",
        "OAuth 2.0과 OpenID Connect 인증 흐름 완전 이해",
        "mTLS 상호 인증과 서비스 간 보안 통신",
        "네트워크 패킷 분석 Wireshark 실전 활용법",
        "API Gateway 패턴과 서비스 라우팅 전략",
        "Long Polling vs SSE vs WebSocket 실시간 통신 비교",
        "QUIC 프로토콜과 HTTP/3의 등장 배경과 특징",
        "네트워크 파티션과 CAP 정리 실전 적용",
        "서비스 메시 사이드카 패턴과 mTLS 자동화",
        "API Gateway Rate Limiting 알고리즘 비교 (Token Bucket vs Sliding Window)",
        "gRPC 스트리밍 패턴과 백프레셔 처리",
    ],
    "블록체인": [
        "블록체인 핵심 개념과 백엔드 개발자 관점에서의 이해",
        "스마트 컨트랙트 Solidity 개발 입문 가이드",
        "Web3.0과 탈중앙화 애플리케이션(dApp) 아키텍처",
        "NFT 기술 원리와 ERC-721 스마트 컨트랙트 구현",
        "DeFi(탈중앙화 금융) 핵심 프로토콜과 작동 원리",
        "이더리움 Layer 2 확장성 솔루션 비교 분석",
        "블록체인 지갑과 개인키 보안 관리 방법",
        "Hyperledger Fabric 기업용 블록체인 네트워크 구축",
        "IPFS 분산 파일 시스템과 블록체인 연동",
        "영지식 증명(ZKP) 개념과 프라이버시 보호 활용",
        "블록체인 오라클 문제와 Chainlink 솔루션",
        "DAO(탈중앙화 자율 조직) 거버넌스 설계 원리",
        "레이어2 롤업(Optimistic vs ZK) 기술 비교",
        "블록체인 트랜잭션 가스비 최적화 전략",
    ],
    "최신 IT 기술 동향": [
        "LLM API 통합 백엔드 서비스 설계 패턴",
        "RAG(Retrieval-Augmented Generation) 시스템 구축하기",
        "AI 시대 개발자의 역할 변화와 생산성 향상 전략",
        "WebAssembly(WASM) 서버사이드 실행 가능성과 미래",
        "엣지 컴퓨팅과 CDN 엣지 함수 활용 전략",
        "FinOps 클라우드 비용 최적화와 엔지니어링",
        "플랫폼 엔지니어링과 Internal Developer Platform 구축",
        "GitOps 원칙과 ArgoCD를 통한 배포 자동화",
        "Feature Flag 기반 점진적 배포와 A/B 테스트",
        "SRE(사이트 신뢰성 엔지니어링) 핵심 원칙과 실천",
        "Rust 언어가 시스템 프로그래밍에서 주목받는 이유",
        "데이터 메시(Data Mesh) 아키텍처와 분산 데이터 관리",
        "Apache Kafka와 스트림 처리 실시간 데이터 파이프라인",
        "마이크로서비스 vs 모놀리스 선택 기준과 트레이드오프",
        "분산 트랜잭션 Saga 패턴과 이벤트 소싱 적용",
        "DDD 도메인 주도 설계 Spring Boot 실전 적용",
        "Hexagonal Architecture (포트와 어댑터) 패턴 실전",
        "Circuit Breaker 패턴과 Resilience4j 실전 가이드",
        "RabbitMQ vs Kafka 메시지 브로커 선택 가이드",
        "eBPF 리눅스 커널 관찰 가능성과 네트워크 보안",
        "JWT 토큰 보안 설계와 갱신 전략 완전 가이드",
        "API 보안 OWASP Top 10 취약점과 방어 전략",
        "Zero Trust 보안 모델과 백엔드 서비스 적용",
        "LLM 프롬프트 캐싱과 비용 최적화 전략",
        "벡터 데이터베이스 선택 기준과 성능 비교",
        "AI 에이전트 오케스트레이션 패턴 설계",
        "MLOps 파이프라인 구축과 모델 배포 전략",
        "이벤트 드리븐 아키텍처 Outbox 패턴 구현",
        "카나리 분석 자동화와 배포 안전장치",
    ],
}

ANGLE_POOL: list[str] = [
    "아키텍처 설계와 트레이드오프",
    "성능 튜닝과 벤치마킹",
    "장애 대응과 트러블슈팅",
    "운영 및 모니터링 전략",
    "보안 및 규정 준수",
    "테스트 전략과 신뢰성 검증",
    "실전 마이그레이션 사례",
    "비용 최적화 관점",
]

ALL_TOPICS: list[tuple[str, str]] = [
    (category, topic)
    for category, topics in TOPIC_POOL.items()
    for topic in topics
]


def load_history() -> dict:
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    return {"topics": []}


def save_history(history: dict) -> None:
    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def pick_topic() -> tuple[str, str, str | None, list[str]]:
    """주제를 고른다. 처음 다루는 주제를 최우선으로 하고, 모든 주제를 한 번씩
    다룬 뒤에는 가장 오래 전에 다룬 주제를, 아직 쓰지 않은 관점(angle)과 함께
    재사용한다. 그래야 같은 주제라도 이전 글과 내용이 겹치지 않는다."""
    history = load_history()
    entries = history["topics"]

    angles_by_title: dict[str, list[str]] = {}
    last_index_by_title: dict[str, int] = {}
    for i, entry in enumerate(entries):
        title = entry["title"]
        angles_by_title.setdefault(title, [])
        if entry.get("angle"):
            angles_by_title[title].append(entry["angle"])
        last_index_by_title[title] = i

    never_used = [(cat, t) for cat, t in ALL_TOPICS if t not in angles_by_title]

    if never_used:
        category_counts: dict[str, int] = {cat: 0 for cat in TOPIC_POOL}
        for entry in entries:
            cat = entry.get("category", "")
            if cat in category_counts:
                category_counts[cat] += 1

        min_count = min(category_counts.values())
        least_used = {cat for cat, cnt in category_counts.items() if cnt == min_count}
        preferred = [(cat, t) for cat, t in never_used if cat in least_used]
        category, topic = random.choice(preferred if preferred else never_used)
        return category, topic, None, []

    # 모든 주제를 최소 한 번씩 다룬 상태: 아직 쓰지 않은 관점이 남은 주제 중
    # 가장 오래 전에 다룬 주제를 재사용한다 (LRU + 관점 로테이션).
    candidates: list[tuple[int, str, str, list[str]]] = []
    for cat, t in ALL_TOPICS:
        remaining_angles = [a for a in ANGLE_POOL if a not in angles_by_title[t]]
        if remaining_angles:
            candidates.append((last_index_by_title[t], cat, t, remaining_angles))

    if not candidates:
        # 모든 주제 x 모든 관점을 다 소진한 극단적인 경우: 전체를 다시 허용한다.
        candidates = [(last_index_by_title[t], cat, t, ANGLE_POOL) for cat, t in ALL_TOPICS]

    oldest_index = min(c[0] for c in candidates)
    oldest_group = [c for c in candidates if c[0] == oldest_index]
    _, category, topic, remaining_angles = random.choice(oldest_group)
    angle = random.choice(remaining_angles)
    return category, topic, angle, angles_by_title[topic]


def slugify(title: str) -> str:
    title = title.lower()
    title = re.sub(r"[^\w\s-]", "", title)
    title = re.sub(r"[\s_]+", "-", title)
    return title.strip("-")[:60]


def generate_post(topic: str, category: str, angle: str | None, prior_angles: list[str]) -> str:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    focus_instruction = ""
    if angle:
        prior_note = (
            f"이 주제는 이전 글에서 다음 관점으로 이미 다뤘습니다: {', '.join(prior_angles)}. "
            f"해당 글과 내용·코드 예제가 겹치지 않도록 하세요.\n"
            if prior_angles
            else ""
        )
        focus_instruction = f"""
이 주제는 과거에 다른 글에서 이미 다룬 적이 있습니다. 이번 글은 반드시 **"{angle}"** 관점에 집중해서 작성하세요.
{prior_note}기본 설치/설정 튜토리얼을 반복하지 말고, "{angle}" 관점에서만 다룰 수 있는 심화 내용·실전 사례·구체적인 수치나 트레이드오프를 중심으로 작성하세요.
"""

    prompt = f"""당신은 Java/Spring, 서버, 네트워크, 데이터베이스, 블록체인, 최신 IT 기술 동향에 정통한 시니어 백엔드 개발자입니다.
아래 주제로 개발자 블로그 포스팅을 한국어로 작성해주세요.

카테고리: {category}
주제: {topic}
{focus_instruction}
요구사항:
- 분량: 1200~2000 단어
- 형식: 마크다운 (제목, 소제목, 코드 블록 포함)
- 독자: 실무 경험이 있는 중급~시니어 개발자
- 실무에서 바로 쓸 수 있는 예제 코드 포함 (해당 카테고리에 맞는 언어/도구 사용)
- 구성: 개요 → 핵심 개념 → 실전 예제 → 주의사항 및 트레이드오프 → 정리

마크다운 형식으로만 응답하세요. 별도의 설명 없이 포스팅 본문만 작성하세요.
첫 줄은 반드시 `# {topic}` 형태의 H1 제목으로 시작하세요."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def main() -> None:
    today = date.today().isoformat()
    existing = list(POSTS_DIR.glob(f"{today}-*.md")) if POSTS_DIR.exists() else []
    if existing:
        print(f"[스킵] 오늘 포스팅이 이미 존재합니다: {existing[0]}")
        return

    category, topic, angle, prior_angles = pick_topic()
    print(f"[카테고리] {category}")
    print(f"[주제] {topic}")
    if angle:
        print(f"[관점] {angle} (이전 관점: {prior_angles or '없음'})")

    content = generate_post(topic=topic, category=category, angle=angle, prior_angles=prior_angles)

    today = date.today().isoformat()
    slug = slugify(topic)
    filename = f"{today}-{slug}.md"

    POSTS_DIR.mkdir(exist_ok=True)
    post_file = POSTS_DIR / filename
    post_file.write_text(content, encoding="utf-8")
    print(f"[저장] {post_file}")

    history = load_history()
    history["topics"].append({
        "date": today,
        "title": topic,
        "slug": slug,
        "category": category,
        "angle": angle,
    })
    save_history(history)
    print(f"[완료] 주제 이력 업데이트")


if __name__ == "__main__":
    main()
