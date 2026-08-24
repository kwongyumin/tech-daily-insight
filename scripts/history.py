"""발행 이력 관리.

`posts/` 디렉토리를 진실의 원천으로 삼는다. 이력 JSON은 사람이 읽기 위한
기록이자 주제 풀에서 사라진 옛 주제를 위한 보조 자료일 뿐이며, 이력 JSON이
비거나 손상돼도 posts/ 파일명만으로 전체 이력을 다시 세울 수 있다.
"""
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path

POST_FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)\.md$")


@dataclass(frozen=True)
class Entry:
    """발행된 글 한 편의 기록. 불변 값 객체."""

    date: str
    title: str  # 표시 제목이 아니라 주제 풀의 원본 주제명
    slug: str
    category: str
    angle: str | None

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "title": self.title,
            "slug": self.slug,
            "category": self.category,
            "angle": self.angle,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Entry":
        return cls(
            date=str(raw.get("date", "")),
            title=str(raw.get("title", "")),
            slug=str(raw.get("slug", "")),
            category=str(raw.get("category", "")),
            angle=raw.get("angle") or None,
        )


def parse_post_filename(name: str) -> tuple[str, str] | None:
    """`YYYY-MM-DD-슬러그.md` 파일명을 (날짜, 슬러그)로 분리한다."""
    match = POST_FILENAME_RE.match(name)
    return (match.group(1), match.group(2)) if match else None


def load_recorded(history_file: Path) -> dict[str, Entry]:
    """이력 JSON을 슬러그별로 읽는다. 없거나 깨져 있으면 빈 결과."""
    if not history_file.exists():
        return {}
    try:
        payload = json.loads(history_file.read_text(encoding="utf-8"))
        raw_entries = payload["topics"]
    except (json.JSONDecodeError, OSError, TypeError, KeyError):
        return {}
    if not isinstance(raw_entries, list):
        return {}
    recorded: dict[str, Entry] = {}
    for raw in raw_entries:
        if isinstance(raw, dict) and raw.get("slug"):
            entry = Entry.from_dict(raw)
            recorded[entry.slug] = entry
    return recorded


def entries_from_posts(
    posts_dir: Path,
    slug_index: dict[str, tuple[str, str, str | None]],
    recorded: dict[str, Entry],
) -> list[Entry]:
    """posts/ 디렉토리를 스캔해 발행 이력을 재구성한다."""
    if not posts_dir.exists():
        return []

    entries: list[Entry] = []
    for path in posts_dir.glob("*.md"):
        parsed = parse_post_filename(path.name)
        if parsed is None:
            continue
        date, slug = parsed
        known = slug_index.get(slug)
        if known is not None:
            category, topic, angle = known
            entries.append(Entry(date, topic, slug, category, angle))
        elif slug in recorded:
            entries.append(replace(recorded[slug], date=date))
        else:
            # 주제 풀에서 사라졌고 기록에도 없는 글. 주제 선택에는 영향을 주지
            # 않지만 이력에는 남긴다.
            entries.append(Entry(date, slug, slug, "", None))

    return sorted(entries, key=lambda e: (e.date, e.slug))


def load_entries(
    posts_dir: Path,
    history_file: Path,
    slug_index: dict[str, tuple[str, str, str | None]],
) -> list[Entry]:
    """주제 선택에 사용할 실제 발행 이력을 얻는다."""
    return entries_from_posts(posts_dir, slug_index, load_recorded(history_file))


def save_entries(history_file: Path, entries: list[Entry]) -> None:
    payload = {"topics": [entry.to_dict() for entry in entries]}
    history_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
