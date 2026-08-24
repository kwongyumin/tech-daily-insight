"""실제 저장소 상태 정합성 검증.

posts/ 디렉토리와 주제 풀이 어긋나면 주제 선택이 이력을 잘못 읽는다.
2026-07-07 사고가 정확히 그 상태였다.
"""
from pathlib import Path

import pytest

from history import load_entries, parse_post_filename
from slugs import build_slug, build_slug_index, build_title
from topics import ALL_TOPICS, ANGLE_POOL, TOPIC_POOL

REPO = Path(__file__).resolve().parents[1]
POSTS_DIR = REPO / "posts"
HISTORY_FILE = REPO / ".topic-history.json"
INDEX = build_slug_index(ALL_TOPICS, ANGLE_POOL)


@pytest.fixture(scope="module")
def entries():
    return load_entries(POSTS_DIR, HISTORY_FILE, INDEX)


def test_주제_풀에_중복된_주제가_없다():
    all_topics = [topic for topics in TOPIC_POOL.values() for topic in topics]
    assert len(all_topics) == len(set(all_topics))


def test_모든_포스트_파일명이_규칙을_따른다():
    invalid = [p.name for p in POSTS_DIR.glob("*.md") if parse_post_filename(p.name) is None]
    assert invalid == []


def test_이력_수가_실제_포스트_수와_일치한다(entries):
    assert len(entries) == len(list(POSTS_DIR.glob("*.md")))


def test_모든_포스트가_주제_풀에서_식별된다(entries):
    """식별 불가한 글이 있으면 주제 선택이 그 글을 '안 쓴 주제'로 착각한다."""
    unknown = [e.slug for e in entries if not e.category]
    assert unknown == []


def test_관점이_적용된_글은_원본_글과_슬러그가_다르다(entries):
    for entry in entries:
        if entry.angle:
            assert entry.slug != build_slug(entry.title)


def test_관점이_적용된_글의_H1에_관점이_포함된다(entries):
    for entry in entries:
        if not entry.angle:
            continue
        path = POSTS_DIR / f"{entry.date}-{entry.slug}.md"
        first_line = path.read_text(encoding="utf-8").split("\n", 1)[0]
        assert first_line == f"# {build_title(entry.title, entry.angle)}"


def test_같은_주제에_같은_관점을_두_번_쓰지_않았다(entries):
    seen = set()
    for entry in entries:
        if entry.angle:
            assert (entry.title, entry.angle) not in seen
            seen.add((entry.title, entry.angle))
