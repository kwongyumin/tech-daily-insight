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


def _least_used_categories(entries, all_topics) -> set[str]:
    counts = {category: 0 for category, _ in all_topics}
    for entry in entries:
        if entry.category in counts:
            counts[entry.category] += 1
    fewest = min(counts.values())
    return {category for category, count in counts.items() if count == fewest}


def _pick_unused(entries, all_topics, unused, rng) -> Pick:
    preferred_categories = _least_used_categories(entries, all_topics)
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


def pick_topic(entries, all_topics, angle_pool, rng=None) -> Pick:
    """다음 글의 카테고리·주제·관점을 고른다. `entries`는 변경하지 않는다."""
    rng = rng or random.Random()
    angles_by_topic = _angles_by_topic(entries)
    last_seen = _last_seen_by_topic(entries)

    unused = [(cat, topic) for cat, topic in all_topics if topic not in angles_by_topic]
    if unused:
        return _pick_unused(entries, all_topics, unused, rng)

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
    preferred_categories = _least_used_categories(entries, all_topics)
    preferred = [item for item in stale if item[1] in preferred_categories]
    _, category, topic, remaining_angles = rng.choice(preferred or stale)

    return Pick(
        category=category,
        topic=topic,
        angle=_pick_angle(entries, remaining_angles, rng),
        prior_angles=list(angles_by_topic[topic]),
    )
