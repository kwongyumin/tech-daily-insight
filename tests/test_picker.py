"""주제 선택 로직 검증.

핵심 계약: 어떤 상황에서도 이미 발행한 (주제 x 관점) 조합을 다시 고르지 않는다.
2026-08-12~23에 3월 포스트가 순서 그대로 재발행된 사고의 회귀 방지 테스트를 포함한다.
"""
import random

import pytest

from history import Entry
from picker import Pick, pick_topic
from slugs import build_slug
from topics import ALL_TOPICS, ANGLE_POOL

FAKE_TOPICS = [("네트워크", f"주제{i}") for i in range(6)] + [("데이터베이스", f"디비{i}") for i in range(4)]
FAKE_ANGLES = ["관점A", "관점B", "관점C"]


def _publish(entries: list[Entry], pick: Pick, day: int) -> list[Entry]:
    """선택 결과를 이력에 더한 새 리스트를 만든다 (원본 불변)."""
    entry = Entry(
        date=f"2026-01-{day:02d}",
        title=pick.topic,
        slug=build_slug(pick.topic, pick.angle),
        category=pick.category,
        angle=pick.angle,
    )
    return [*entries, entry]


def _simulate(days: int, all_topics, angle_pool, seed: int = 0) -> list[Entry]:
    rng = random.Random(seed)
    entries: list[Entry] = []
    for day in range(1, days + 1):
        entries = _publish(entries, pick_topic(entries, all_topics, angle_pool, rng), day)
    return entries


def test_이력이_비면_관점없이_새_주제를_고른다():
    pick = pick_topic([], ALL_TOPICS, ANGLE_POOL, random.Random(0))
    assert pick.angle is None
    assert (pick.category, pick.topic) in ALL_TOPICS


def test_아직_안_쓴_주제가_있으면_재사용하지_않는다():
    entries = _simulate(len(FAKE_TOPICS) - 1, FAKE_TOPICS, FAKE_ANGLES)
    used = {e.title for e in entries}
    pick = pick_topic(entries, FAKE_TOPICS, FAKE_ANGLES, random.Random(1))
    assert pick.topic not in used
    assert pick.angle is None


def test_주제가_모두_소진되면_관점을_붙여_재사용한다():
    entries = _simulate(len(FAKE_TOPICS), FAKE_TOPICS, FAKE_ANGLES)
    pick = pick_topic(entries, FAKE_TOPICS, FAKE_ANGLES, random.Random(2))
    assert pick.angle in FAKE_ANGLES


def test_같은_주제에_이미_쓴_관점은_다시_고르지_않는다():
    entries = _simulate(len(FAKE_TOPICS) + 3, FAKE_TOPICS, FAKE_ANGLES)
    pick = pick_topic(entries, FAKE_TOPICS, FAKE_ANGLES, random.Random(3))
    already = {e.angle for e in entries if e.title == pick.topic}
    assert pick.angle not in already


def test_직전에_다룬_주제를_바로_다시_고르지_않는다():
    entries = _simulate(len(FAKE_TOPICS) + 5, FAKE_TOPICS, FAKE_ANGLES)
    pick = pick_topic(entries, FAKE_TOPICS, FAKE_ANGLES, random.Random(4))
    assert pick.topic != entries[-1].title


def test_선택_결과는_직전_관점_목록을_함께_알려준다():
    entries = _simulate(len(FAKE_TOPICS) + 4, FAKE_TOPICS, FAKE_ANGLES)
    pick = pick_topic(entries, FAKE_TOPICS, FAKE_ANGLES, random.Random(5))
    expected = [e.angle for e in entries if e.title == pick.topic and e.angle]
    assert pick.prior_angles == expected


def test_pick_topic은_전달받은_이력을_변경하지_않는다():
    entries = _simulate(4, FAKE_TOPICS, FAKE_ANGLES)
    before = list(entries)
    pick_topic(entries, FAKE_TOPICS, FAKE_ANGLES, random.Random(6))
    assert entries == before


@pytest.mark.parametrize("seed", range(5))
def test_모든_조합을_소진할_때까지_슬러그가_한_번도_겹치지_않는다(seed):
    """이번 사고의 회귀 방지: 주제 재사용 시에도 파일명이 충돌하면 안 된다."""
    total = len(FAKE_TOPICS) * (len(FAKE_ANGLES) + 1)
    entries = _simulate(total, FAKE_TOPICS, FAKE_ANGLES, seed=seed)
    slugs = [e.slug for e in entries]
    assert len(set(slugs)) == total


def test_실제_주제_풀로도_전_조합이_고유하다():
    total = len(ALL_TOPICS) * (len(ANGLE_POOL) + 1)
    entries = _simulate(total, ALL_TOPICS, ANGLE_POOL, seed=7)
    assert len({e.slug for e in entries}) == total


def test_모든_조합이_소진된_뒤에도_예외없이_주제를_돌려준다():
    total = len(FAKE_TOPICS) * (len(FAKE_ANGLES) + 1)
    entries = _simulate(total, FAKE_TOPICS, FAKE_ANGLES)
    pick = pick_topic(entries, FAKE_TOPICS, FAKE_ANGLES, random.Random(8))
    assert (pick.category, pick.topic) in FAKE_TOPICS


def test_카테고리가_한쪽으로_쏠리지_않는다():
    entries = _simulate(len(FAKE_TOPICS), FAKE_TOPICS, FAKE_ANGLES)
    counts = {cat: sum(1 for e in entries if e.category == cat) for cat, _ in FAKE_TOPICS}
    expected = {cat: sum(1 for c, _ in FAKE_TOPICS if c == cat) for cat, _ in FAKE_TOPICS}
    assert counts == expected


def test_관점도_고르게_분배된다():
    total = len(FAKE_TOPICS) * (len(FAKE_ANGLES) + 1)
    entries = _simulate(total, FAKE_TOPICS, FAKE_ANGLES)
    counts = [sum(1 for e in entries if e.angle == a) for a in FAKE_ANGLES]
    assert max(counts) - min(counts) <= 1
