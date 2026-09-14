"""다음에 쓸 주제와 관점을 고르는 로직.

두 단계로 동작한다.

1. 아직 한 번도 다루지 않은 주제가 남아 있으면 그중에서 고른다 (카테고리 균형 우선).
2. 모든 주제를 한 번씩 다뤘으면, 오래전에 다룬 주제를 아직 쓰지 않은 관점과
   함께 재사용한다.

2단계에서 "가장 오래된 것 하나"만 고르면 이력 순서를 그대로 되감아 재생하게 된다
(2026-08 사고: 3월 발행 순서가 8월에 그대로 반복됨). 그래서 오래된 후보 여러 개를
묶어 그 안에서 고른다.
"""
import random
from dataclasses import dataclass

STALE_WINDOW_RATIO = 0.1
MIN_STALE_WINDOW = 5
# 같은 카테고리를 연달아 발행할 수 있는 최대 일수 (select_topic)
MAX_CATEGORY_RUN = 2


@dataclass(frozen=True)
class Pick:
    """이번 회차에 쓸 글의 선택 결과. 불변 값 객체."""

    category: str
    topic: str
    angle: str | None
    prior_angles: list[str]


def _angles_by_topic(entries) -> dict[str, list[str]]:
    angles: dict[str, list[str]] = {}
    for entry in entries:
        angles.setdefault(entry.title, [])
        if entry.angle:
            angles[entry.title].append(entry.angle)
    return angles


def _last_seen_by_topic(entries) -> dict[str, int]:
    return {entry.title: order for order, entry in enumerate(entries)}


def _least_used_categories(entries, all_topics, category_aliases) -> set[str]:
    """발행 수가 가장 적은 카테고리. 선택 대상이 아닌 레거시 카테고리 글은 세지 않되,
    별칭이 있으면 대응하는 새 카테고리 발행 수로 합산한다."""
    counts = {category: 0 for category, _ in all_topics}
    for entry in entries:
        category = category_aliases.get(entry.category, entry.category)
        if category in counts:
            counts[category] += 1
    fewest = min(counts.values())
    return {category for category, count in counts.items() if count == fewest}


def _pick_unused(entries, all_topics, unused, rng, category_aliases) -> Pick:
    preferred_categories = _least_used_categories(entries, all_topics, category_aliases)
    preferred = [item for item in unused if item[0] in preferred_categories]
    category, topic = rng.choice(preferred or unused)
    return Pick(category=category, topic=topic, angle=None, prior_angles=[])


def _pick_angle(entries, remaining_angles, rng) -> str:
    """전체 이력에서 가장 적게 쓴 관점을 우선한다."""
    usage = {angle: 0 for angle in remaining_angles}
    for entry in entries:
        if entry.angle in usage:
            usage[entry.angle] += 1
    fewest = min(usage.values())
    return rng.choice([angle for angle, count in usage.items() if count == fewest])


def _stale_candidates(candidates: list[tuple[int, str, str, list[str]]]) -> list:
    """가장 오래 묵은 후보 묶음. 하나만 고르면 과거 발행 순서를 그대로 반복한다."""
    window = max(MIN_STALE_WINDOW, int(len(candidates) * STALE_WINDOW_RATIO))
    return sorted(candidates, key=lambda item: item[0])[:window]


def pick_topic(entries, all_topics, angle_pool, rng=None, category_aliases=None) -> Pick:
    """다음 글의 카테고리·주제·관점을 고른다. `entries`는 변경하지 않는다.

    `all_topics`에 없는 주제(레거시)는 이력에 있어도 다시 고르지 않는다.
    """
    rng = rng or random.Random()
    category_aliases = category_aliases or {}
    angles_by_topic = _angles_by_topic(entries)
    last_seen = _last_seen_by_topic(entries)

    unused = [(cat, topic) for cat, topic in all_topics if topic not in angles_by_topic]
    if unused:
        return _pick_unused(entries, all_topics, unused, rng, category_aliases)

    candidates = []
    for category, topic in all_topics:
        remaining = [a for a in angle_pool if a not in angles_by_topic[topic]]
        if remaining:
            candidates.append((last_seen[topic], category, topic, remaining))

    if not candidates:
        # 모든 주제 x 모든 관점을 소진한 극단적 상황. 전체를 다시 허용한다.
        candidates = [
            (last_seen[topic], category, topic, list(angle_pool))
            for category, topic in all_topics
        ]

    stale = _stale_candidates(candidates)
    preferred_categories = _least_used_categories(entries, all_topics, category_aliases)
    preferred = [item for item in stale if item[1] in preferred_categories]
    _, category, topic, remaining_angles = rng.choice(preferred or stale)

    return Pick(
        category=category,
        topic=topic,
        angle=_pick_angle(entries, remaining_angles, rng),
        prior_angles=list(angles_by_topic[topic]),
    )


def _blocked_category(entries, category_aliases, max_run) -> str | None:
    """최근 max_run편이 모두 같은 카테고리면 그 카테고리를 돌려준다."""
    if max_run <= 0 or len(entries) < max_run:
        return None
    recent = {category_aliases.get(e.category, e.category) for e in entries[-max_run:]}
    return next(iter(recent)) if len(recent) == 1 else None


def _has_unpublished(entries, topics, angle_pool) -> bool:
    """topics 안에 아직 발행하지 않은 (주제, 관점) 조합이 남아 있는가."""
    published = {(e.title, e.angle) for e in entries}
    return any((topic, angle) not in published for _, topic in topics for angle in (None, *angle_pool))


def select_topic(
    entries,
    all_topics,
    angle_pool,
    priority_topics=(),
    rng=None,
    category_aliases=None,
    max_category_run=MAX_CATEGORY_RUN,
) -> Pick:
    """카테고리 연속 제한과 우선 주제를 적용해 후보를 좁힌 뒤 pick_topic에 맡긴다.

    1. 최근 `max_category_run`편이 같은 카테고리면 그 카테고리를 뺀다. 단, 남은 카테고리에
       새로 쓸 조합이 없으면 제한을 푼다. 고집하면 pick_topic이 이미 쓴 조합을 다시 골라
       발행이 중단된다.
    2. 남은 후보 중 아직 안 쓴 우선 주제가 있으면 우선 주제만 남긴다.
    """
    category_aliases = category_aliases or {}
    blocked = _blocked_category(entries, category_aliases, max_category_run)
    allowed = [item for item in all_topics if item[0] != blocked]
    if not _has_unpublished(entries, allowed, angle_pool):
        allowed = list(all_topics)

    used = {e.title for e in entries}
    priority_names = {topic for _, topic in priority_topics}
    priority_allowed = [item for item in allowed if item[1] in priority_names]
    if any(topic not in used for _, topic in priority_allowed):
        allowed = priority_allowed

    return pick_topic(entries, allowed, angle_pool, rng, category_aliases)
