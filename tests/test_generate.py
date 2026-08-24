"""프롬프트 조립 검증 (API 호출 없음).

관점이 붙은 글은 H1 지시문에도 관점이 반영되어야 한다. 그러지 않으면
본문만 다르고 제목은 똑같은 글이 만들어진다 — 2026-08 사고의 형태다.
"""
import pytest

from generate import build_prompt
from picker import Pick

TOPIC = "HTTP 캐싱 전략 Cache-Control 완전 정복"
ANGLE = "성능 튜닝과 벤치마킹"


def test_관점이_없으면_H1은_주제_그대로다():
    prompt = build_prompt(Pick("네트워크", TOPIC, None, []))
    assert f"# {TOPIC}" in prompt


def test_관점이_있으면_H1에_관점이_포함된다():
    prompt = build_prompt(Pick("네트워크", TOPIC, ANGLE, []))
    assert f"# {TOPIC} — {ANGLE}" in prompt


def test_관점이_없으면_재작성_지시가_들어가지_않는다():
    prompt = build_prompt(Pick("네트워크", TOPIC, None, []))
    assert "이미 다룬 적이 있습니다" not in prompt


def test_관점이_있으면_해당_관점에_집중하라고_지시한다():
    prompt = build_prompt(Pick("네트워크", TOPIC, ANGLE, []))
    assert ANGLE in prompt
    assert "이미 다룬 적이 있습니다" in prompt


def test_이전_관점들을_프롬프트에_알려준다():
    prior = ["보안 및 규정 준수", "테스트 전략과 신뢰성 검증"]
    prompt = build_prompt(Pick("네트워크", TOPIC, ANGLE, prior))
    for angle in prior:
        assert angle in prompt


def test_이전_관점이_없으면_겹침_경고를_넣지_않는다():
    prompt = build_prompt(Pick("네트워크", TOPIC, ANGLE, []))
    assert "이전 글에서 다음 관점으로" not in prompt


@pytest.mark.parametrize("field", ["카테고리", "주제", "요구사항"])
def test_프롬프트에_필수_항목이_들어간다(field):
    prompt = build_prompt(Pick("네트워크", TOPIC, None, []))
    assert field in prompt


# --- main() 오케스트레이션 (API는 목으로 대체) ---

import generate
from history import Entry, load_entries, save_entries
from slugs import build_slug, build_slug_index
from topics import ALL_TOPICS, ANGLE_POOL

INDEX = build_slug_index(ALL_TOPICS, ANGLE_POOL)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """임시 저장소로 격리하고 API 호출을 가짜 본문으로 대체한다."""
    posts = tmp_path / "posts"
    posts.mkdir()
    history = tmp_path / ".topic-history.json"
    monkeypatch.setattr(generate, "POSTS_DIR", posts)
    monkeypatch.setattr(generate, "HISTORY_FILE", history)
    monkeypatch.setattr(generate, "generate_post", lambda pick: "# 가짜 본문\n")
    return posts, history


def test_새_글을_저장하고_이력을_갱신한다(repo):
    posts, history = repo
    assert generate.main() == 0
    assert len(list(posts.glob("*.md"))) == 1
    assert len(load_entries(posts, history, INDEX)) == 1


def test_오늘_글이_이미_있으면_건너뛴다(repo):
    posts, _ = repo
    generate.main()
    before = {p.name for p in posts.glob("*.md")}
    assert generate.main() == 0
    assert {p.name for p in posts.glob("*.md")} == before


def test_이력_파일이_없어도_posts를_보고_동작한다(repo):
    posts, history = repo
    generate.main()
    history.unlink()
    entries = load_entries(posts, history, INDEX)
    assert len(entries) == 1


def test_이미_발행된_슬러그를_고르면_중단한다(repo, monkeypatch):
    posts, history = repo
    category, topic = ALL_TOPICS[0]
    slug = build_slug(topic)
    save_entries(history, [Entry("2026-01-01", topic, slug, category, None)])
    (posts / f"2026-01-01-{slug}.md").write_text("# 기존", encoding="utf-8")
    monkeypatch.setattr(generate, "pick_topic", lambda *a, **k: Pick(category, topic, None, []))
    assert generate.main() == 1


def test_API_키가_없으면_명확한_오류를_낸다(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        generate.generate_post(Pick("네트워크", TOPIC, None, []))
