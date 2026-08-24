"""매일 기술 블로그 포스팅을 한 편 생성해 `posts/`에 저장한다.

주제 선택은 picker.py, 이력은 history.py, 파일명·제목 규칙은 slugs.py가 맡는다.
이 모듈은 그 조각들을 엮고 Claude API를 호출하는 역할만 한다.
"""
import os
import sys
from datetime import date
from pathlib import Path

import anthropic

from history import Entry, load_entries, save_entries
from picker import Pick, pick_topic
from slugs import build_slug, build_slug_index, build_title
from topics import ALL_TOPICS, ANGLE_POOL

HISTORY_FILE = Path(".topic-history.json")
POSTS_DIR = Path("posts")
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8192


def build_prompt(pick: Pick) -> str:
    """주제와 관점에 맞는 생성 프롬프트를 만든다."""
    display_title = build_title(pick.topic, pick.angle)

    focus_instruction = ""
    if pick.angle:
        prior_note = (
            f"이 주제는 이전 글에서 다음 관점으로 이미 다뤘습니다: {', '.join(pick.prior_angles)}. "
            f"해당 글과 내용·코드 예제가 겹치지 않도록 하세요.\n"
            if pick.prior_angles
            else ""
        )
        focus_instruction = f"""
이 주제는 과거에 다른 글에서 이미 다룬 적이 있습니다. 이번 글은 반드시 **"{pick.angle}"** 관점에 집중해서 작성하세요.
{prior_note}기본 설치/설정 튜토리얼을 반복하지 말고, "{pick.angle}" 관점에서만 다룰 수 있는 심화 내용·실전 사례·구체적인 수치나 트레이드오프를 중심으로 작성하세요.
"""

    return f"""당신은 Java/Spring, 서버, 네트워크, 데이터베이스, 블록체인, 최신 IT 기술 동향에 정통한 시니어 백엔드 개발자입니다.
아래 주제로 개발자 블로그 포스팅을 한국어로 작성해주세요.

카테고리: {pick.category}
주제: {pick.topic}
{focus_instruction}
요구사항:
- 분량: 1200~2000 단어
- 형식: 마크다운 (제목, 소제목, 코드 블록 포함)
- 독자: 실무 경험이 있는 중급~시니어 개발자
- 실무에서 바로 쓸 수 있는 예제 코드 포함 (해당 카테고리에 맞는 언어/도구 사용)
- 구성: 개요 → 핵심 개념 → 실전 예제 → 주의사항 및 트레이드오프 → 정리

마크다운 형식으로만 응답하세요. 별도의 설명 없이 포스팅 본문만 작성하세요.
첫 줄은 반드시 `# {display_title}` 형태의 H1 제목으로 시작하세요."""


def generate_post(pick: Pick) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("환경변수 ANTHROPIC_API_KEY가 설정되지 않았습니다.")

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": build_prompt(pick)}],
    )
    content = message.content[0].text.strip()
    if not content:
        raise RuntimeError("모델이 빈 응답을 반환했습니다.")
    return content + "\n"


def main() -> int:
    today = date.today().isoformat()
    if POSTS_DIR.exists() and list(POSTS_DIR.glob(f"{today}-*.md")):
        print(f"[스킵] 오늘 포스팅이 이미 존재합니다: {today}")
        return 0

    slug_index = build_slug_index(ALL_TOPICS, ANGLE_POOL)
    entries = load_entries(POSTS_DIR, HISTORY_FILE, slug_index)
    print(f"[이력] posts/ 기준 {len(entries)}편 확인")

    pick = pick_topic(entries, ALL_TOPICS, ANGLE_POOL)
    slug = build_slug(pick.topic, pick.angle)
    print(f"[카테고리] {pick.category}")
    print(f"[주제] {build_title(pick.topic, pick.angle)}")
    if pick.angle:
        print(f"[관점] {pick.angle} (이전 관점: {pick.prior_angles or '없음'})")

    if any(entry.slug == slug for entry in entries):
        print(f"[중단] 이미 발행된 슬러그를 다시 고르려 했습니다: {slug}", file=sys.stderr)
        return 1

    content = generate_post(pick)

    POSTS_DIR.mkdir(exist_ok=True)
    post_file = POSTS_DIR / f"{today}-{slug}.md"
    post_file.write_text(content, encoding="utf-8")
    print(f"[저장] {post_file}")

    published = Entry(
        date=today,
        title=pick.topic,
        slug=slug,
        category=pick.category,
        angle=pick.angle,
    )
    save_entries(HISTORY_FILE, [*entries, published])
    print(f"[완료] 주제 이력 {len(entries) + 1}편으로 갱신")
    return 0


if __name__ == "__main__":
    sys.exit(main())
