"""주제 풀 구성 검증.

2026-09-14 개편: 블록체인·최신 IT 기술 동향은 새 글 선택에서 빼고, 예전 글을
알아보기 위한 레거시 풀로만 남긴다. 최신 IT 기술 동향 자리는 토스 기술 블로그를
참고한 서비스 아키텍처/실무 사례 카테고리가 대신한다.
"""
from collections import Counter

from topics import (
    ALL_TOPICS,
    ARCHITECTURE_CATEGORY,
    CATEGORY_ALIASES,
    KNOWN_TOPICS,
    LEGACY_TOPIC_POOL,
    LEGACY_TOPICS,
    PRIORITY_TOPICS,
)

SELECTABLE_CATEGORIES = {"Java/Spring", "서버/인프라", "데이터베이스", "네트워크", ARCHITECTURE_CATEGORY}


def test_새_글은_다섯_개_카테고리에서만_고른다():
    assert {category for category, _ in ALL_TOPICS} == SELECTABLE_CATEGORIES


def test_블록체인과_최신_IT_동향은_레거시로만_남는다():
    assert set(LEGACY_TOPIC_POOL) == {"블록체인", "최신 IT 기술 동향"}
    selectable = {topic for _, topic in ALL_TOPICS}
    assert [topic for _, topic in LEGACY_TOPICS if topic in selectable] == []


def test_식별용_전체_풀은_선택_대상과_레거시의_합이다():
    assert KNOWN_TOPICS == [*ALL_TOPICS, *LEGACY_TOPICS]


def test_주제_이름은_레거시까지_포함해_중복되지_않는다():
    names = [topic for _, topic in KNOWN_TOPICS]
    assert len(names) == len(set(names))


def test_최신_IT_동향_글은_아키텍처_카테고리_발행_수로_합산한다():
    """발행 수 0인 새 카테고리가 몇 주씩 연달아 선택되는 것을 막는다."""
    assert CATEGORY_ALIASES == {"최신 IT 기술 동향": ARCHITECTURE_CATEGORY}


def test_토스_기반_우선_주제는_52개이고_모두_선택_대상이다():
    assert len(PRIORITY_TOPICS) == 52
    assert set(PRIORITY_TOPICS) <= set(ALL_TOPICS)


def test_우선_주제의_카테고리별_분포():
    assert Counter(category for category, _ in PRIORITY_TOPICS) == {
        "Java/Spring": 8,
        "서버/인프라": 7,
        "데이터베이스": 7,
        "네트워크": 5,
        ARCHITECTURE_CATEGORY: 25,
    }


def test_카테고리별_선택_가능한_주제_수():
    counts = {c: sum(1 for category, _ in ALL_TOPICS if category == c) for c in SELECTABLE_CATEGORIES}
    assert counts == {
        "Java/Spring": 54,
        "서버/인프라": 46,
        "데이터베이스": 38,
        "네트워크": 31,
        ARCHITECTURE_CATEGORY: 25,
    }
