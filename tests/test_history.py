"""발행 이력 계층 검증.

핵심 계약: `posts/` 디렉토리가 진실의 원천이다. 이력 JSON이 사라지거나
손상돼도 posts/ 파일명만으로 전체 이력을 복원할 수 있어야 한다.
2026-07-07 사고는 이력 JSON만 신뢰한 탓에 발생했다.
"""
import json

from history import Entry, entries_from_posts, load_entries, parse_post_filename, save_entries
from slugs import build_slug, build_slug_index
from topics import ALL_TOPICS, ANGLE_POOL

INDEX = build_slug_index(ALL_TOPICS, ANGLE_POOL)
TOPIC = "HTTP 캐싱 전략 Cache-Control 완전 정복"
ANGLE = "테스트 전략과 신뢰성 검증"


def _write_post(posts_dir, date, slug):
    posts_dir.mkdir(exist_ok=True)
    (posts_dir / f"{date}-{slug}.md").write_text("# 본문", encoding="utf-8")


def test_포스트_파일명에서_날짜와_슬러그를_분리한다():
    assert parse_post_filename("2026-08-19-http-캐싱-전략.md") == ("2026-08-19", "http-캐싱-전략")


def test_형식에_맞지_않는_파일명은_무시한다():
    assert parse_post_filename("README.md") is None
    assert parse_post_filename("2026-8-1-잘못된-날짜.md") is None


def test_posts에서_주제와_카테고리를_복원한다(tmp_path):
    posts = tmp_path / "posts"
    _write_post(posts, "2026-03-20", build_slug(TOPIC))
    entries = entries_from_posts(posts, INDEX, {})
    assert [(e.date, e.title, e.angle) for e in entries] == [("2026-03-20", TOPIC, None)]


def test_관점이_포함된_슬러그에서_관점까지_복원한다(tmp_path):
    posts = tmp_path / "posts"
    _write_post(posts, "2026-08-19", build_slug(TOPIC, ANGLE))
    (entry,) = entries_from_posts(posts, INDEX, {})
    assert entry.title == TOPIC
    assert entry.angle == ANGLE


def test_이력_파일이_없어도_posts에서_전체를_복원한다(tmp_path):
    posts = tmp_path / "posts"
    _write_post(posts, "2026-03-20", build_slug(TOPIC))
    _write_post(posts, "2026-08-19", build_slug(TOPIC, ANGLE))
    entries = load_entries(posts, tmp_path / "없는파일.json", INDEX)
    assert len(entries) == 2


def test_이력_파일이_손상돼도_예외없이_복원한다(tmp_path):
    posts = tmp_path / "posts"
    _write_post(posts, "2026-03-20", build_slug(TOPIC))
    broken = tmp_path / "broken.json"
    broken.write_text("{망가진 JSON", encoding="utf-8")
    assert len(load_entries(posts, broken, INDEX)) == 1


def test_주제_풀에_없는_슬러그는_기록_파일로_폴백한다(tmp_path):
    posts = tmp_path / "posts"
    _write_post(posts, "2026-01-01", "사라진-주제")
    recorded = {"사라진-주제": Entry("2026-01-01", "사라진 주제", "사라진-주제", "네트워크", None)}
    (entry,) = entries_from_posts(posts, INDEX, recorded)
    assert entry.title == "사라진 주제"
    assert entry.category == "네트워크"


def test_이력은_날짜순으로_정렬된다(tmp_path):
    posts = tmp_path / "posts"
    _write_post(posts, "2026-08-19", build_slug(TOPIC, ANGLE))
    _write_post(posts, "2026-03-20", build_slug(TOPIC))
    entries = entries_from_posts(posts, INDEX, {})
    assert [e.date for e in entries] == ["2026-03-20", "2026-08-19"]


def test_저장한_이력을_그대로_다시_읽는다(tmp_path):
    posts = tmp_path / "posts"
    _write_post(posts, "2026-08-19", build_slug(TOPIC, ANGLE))
    history_file = tmp_path / "history.json"
    original = entries_from_posts(posts, INDEX, {})
    save_entries(history_file, original)
    assert load_entries(posts, history_file, INDEX) == original


def test_저장된_JSON은_사람이_읽을_수_있는_형태다(tmp_path):
    history_file = tmp_path / "history.json"
    save_entries(history_file, [Entry("2026-08-19", TOPIC, build_slug(TOPIC, ANGLE), "네트워크", ANGLE)])
    payload = json.loads(history_file.read_text(encoding="utf-8"))
    assert payload["topics"][0]["title"] == TOPIC
    assert "캐싱" in history_file.read_text(encoding="utf-8")
