# 📚 Tech Daily Insight

> AI가 매일 커밋합니다. Java/Spring · DB · 네트워크 · 블록체인 · 최신 IT 동향
> Claude AI × GitHub Actions로 자동화된 백엔드 개발자 기술 아카이브

<br/>

## 📝 Changelog

### 2026-07-07
- **버그**: 주제 풀 소진 시 이력을 초기화하고 처음부터 다시 순환하는 방식이라, 2026-07-06부터 같은 주제가 내용까지 완전히 동일하게 중복 발행됨
- **수정**: `pick_topic()`을 LRU + 관점(angle) 로테이션 방식으로 변경. 주제 소진 후에는 가장 오래전에 다룬 주제를 아직 쓰지 않은 관점(아키텍처, 성능 튜닝, 장애 대응 등 8종)으로 재작성하도록 개선하고, 주제 풀을 59개 → 142개로 확장 (`scripts/generate.py`)

<br/>

## 🤖 어떻게 동작하나요?

매일 오전 9시, 아무도 키보드를 두드리지 않아도 새로운 기술 포스팅이 올라옵니다.

```
매일 09:00 (KST)
      │
      ▼
GitHub Actions 트리거
      │
      ▼
Claude AI가 주제 선택 & 포스팅 생성
      │
      ▼
posts/ 디렉토리에 마크다운 파일 저장
      │
      ▼
자동 커밋 & Push 완료 ✅
```

<br/>

## 🛠️ 기술 스택

| 역할 | 기술 |
|------|------|
| AI 콘텐츠 생성 | [Claude AI](https://anthropic.com) (claude-sonnet-4-6) |
| 자동화 파이프라인 | GitHub Actions |
| 언어 | Python 3.11 |
| 주제 중복 방지 | `.topic-history.json` (카테고리별 균형 선택) |

<br/>

## 📂 구조

```
tech_daily_insight/
├── .github/
│   └── workflows/
│       └── daily-post.yml       # GitHub Actions 워크플로우
├── posts/                       # 생성된 기술 포스팅 모음
│   └── YYYY-MM-DD-title.md
├── scripts/
│   └── generate.py              # Claude AI 포스팅 생성 스크립트
└── .topic-history.json          # 주제 중복 방지 이력
```

<br/>

## 📋 다루는 주제

| 카테고리 | 예시 주제 |
|---------|---------|
| ☕ Java/Spring | Virtual Threads, Spring Boot 3.x, GraalVM, WebFlux |
| 🖥️ 서버/인프라 | Kubernetes, Docker, CI/CD, AWS, 분산 시스템 |
| 🗄️ 데이터베이스 | PostgreSQL 최적화, Redis, MongoDB, Elasticsearch |
| 🌐 네트워크 | TCP/IP, HTTP/3, TLS, OAuth 2.0, Load Balancer |
| ⛓️ 블록체인 | 스마트 컨트랙트, DeFi, Web3.0, ZKP |
| 🚀 최신 IT 동향 | LLM 통합, RAG, GitOps, eBPF, 플랫폼 엔지니어링 |

총 **142개** 주제를 카테고리별 균형 있게 순환하며, 중복 없이 포스팅됩니다.

<br/>

## ⚙️ 자동화 구현 방식

### 1. 주제 선택 로직
- 6개 카테고리에서 가장 적게 사용된 카테고리 우선 선택
- `.topic-history.json`으로 사용된 주제와 관점(angle)을 함께 추적 → 완전 중복 방지
- 모든 주제를 한 번씩 다룬 뒤에는, 가장 오래전에 다룬 주제를 아직 쓰지 않은 관점과 함께 재사용 (LRU + 관점 로테이션)

### 2. Claude AI 포스팅 생성
- 카테고리와 주제를 프롬프트에 포함하여 고품질 포스팅 생성
- 1,200 ~ 2,000 단어 분량의 실전 예제 포함 마크다운
- 구성: 개요 → 핵심 개념 → 실전 예제 → 주의사항 → 정리

### 3. GitHub Actions 파이프라인
- `cron: '0 0 * * *'` → 매일 UTC 00:00 (KST 09:00) 실행
- `concurrency` 설정으로 중복 실행 방지
- push 실패 시 자동 재시도 (최대 3회)
- `ANTHROPIC_API_KEY` 등 민감 정보는 GitHub Secrets로 안전하게 관리

<br/>

## 📈 Stats

![GitHub commit activity](https://img.shields.io/github/commit-activity/m/kwongyumin/tech_daily_insight)
![GitHub last commit](https://img.shields.io/github/last-commit/kwongyumin/tech_daily_insight)

---

> 포스팅은 Claude AI가 생성하며, 학습 및 참고 목적으로 활용하시기 바랍니다.
