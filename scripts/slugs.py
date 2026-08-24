"""포스팅의 제목·슬러그(파일명) 생성 규칙.

같은 주제를 다른 관점(angle)으로 다시 쓸 때 제목과 슬러그가 반드시 달라져야
독자와 검색엔진이 두 글을 별개의 글로 인식한다. 관점이 슬러그에 포함되면
`posts/` 디렉토리 자체가 "어떤 주제를 어떤 관점으로 썼는지"에 대한 완전한
기록이 되므로, 이력 파일이 손상돼도 posts/에서 전부 복원할 수 있다.
"""
import re

TOPIC_SLUG_LIMIT = 60
TITLE_SEPARATOR = " — "


def slugify(title: str, limit: int = TOPIC_SLUG_LIMIT) -> str:
    """제목을 URL/파일명에 쓸 수 있는 슬러그로 변환한다."""
    slug = title.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug.strip("-")[:limit].strip("-")


def build_slug(topic: str, angle: str | None = None) -> str:
    """주제와 관점을 합쳐 슬러그를 만든다.

    주제 부분만 길이를 제한하고 관점 부분은 항상 온전히 붙인다. 관점까지
    한꺼번에 자르면 긴 주제에서 서로 다른 관점이 같은 슬러그로 잘려 충돌한다.
    """
    base = slugify(topic)
    if not angle:
        return base
    return f"{base}-{slugify(angle, limit=len(angle) * 4)}"


def build_title(topic: str, angle: str | None = None) -> str:
    """포스팅 H1에 사용할 표시용 제목."""
    if not angle:
        return topic
    return f"{topic}{TITLE_SEPARATOR}{angle}"


def build_slug_index(
    all_topics: list[tuple[str, str]],
    angle_pool: list[str],
) -> dict[str, tuple[str, str, str | None]]:
    """슬러그 -> (카테고리, 주제, 관점) 역인덱스.

    `posts/` 파일명만 보고 그 글이 어떤 주제·관점이었는지 되짚기 위해 쓴다.
    """
    index: dict[str, tuple[str, str, str | None]] = {}
    for category, topic in all_topics:
        for angle in (None, *angle_pool):
            index[build_slug(topic, angle)] = (category, topic, angle)
    return index
