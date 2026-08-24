"""슬러그·제목 생성 규칙 검증.

핵심 계약: 같은 주제라도 관점(angle)이 다르면 슬러그와 제목이 반드시 달라야 한다.
2026-08 중복 발행 사고의 직접 원인이 이 계약의 부재였다.
"""
import pytest

from slugs import build_slug, build_slug_index, build_title, slugify
from topics import ALL_TOPICS, ANGLE_POOL


def test_slugify_한글과_영문_혼용_제목을_처리한다():
    assert slugify("HTTP 캐싱 전략 Cache-Control 완전 정복") == "http-캐싱-전략-cache-control-완전-정복"


def test_slugify_특수문자를_제거한다():
    assert slugify("Web3.0과 탈중앙화 애플리케이션(dApp) 아키텍처") == "web30과-탈중앙화-애플리케이션dapp-아키텍처"


def test_angle_없는_슬러그는_기존_규칙을_유지한다():
    topic = "HTTP 캐싱 전략 Cache-Control 완전 정복"
    assert build_slug(topic) == slugify(topic)


def test_angle이_붙으면_슬러그가_달라진다():
    topic = "HTTP 캐싱 전략 Cache-Control 완전 정복"
    assert build_slug(topic, "테스트 전략과 신뢰성 검증") != build_slug(topic)


def test_서로_다른_angle은_서로_다른_슬러그를_만든다():
    topic = "HTTP 캐싱 전략 Cache-Control 완전 정복"
    slugs = {build_slug(topic, a) for a in ANGLE_POOL}
    assert len(slugs) == len(ANGLE_POOL)


def test_모든_주제x관점_조합의_슬러그가_고유하다():
    """긴 주제명이 60자에서 잘려도 관점 부분은 보존되어야 충돌하지 않는다."""
    index = build_slug_index(ALL_TOPICS, ANGLE_POOL)
    expected = len(ALL_TOPICS) * (len(ANGLE_POOL) + 1)
    assert len(index) == expected


def test_angle이_없으면_제목은_주제_그대로다():
    assert build_title("QUIC 프로토콜과 HTTP/3의 등장 배경과 특징") is not None
    assert build_title("주제") == "주제"


def test_angle이_있으면_제목에_관점이_드러난다():
    title = build_title("HTTP 캐싱 전략", "성능 튜닝과 벤치마킹")
    assert "HTTP 캐싱 전략" in title
    assert "성능 튜닝과 벤치마킹" in title


@pytest.mark.parametrize("angle", [None, *ANGLE_POOL])
def test_슬러그는_파일명으로_안전한_길이를_넘지_않는다(angle):
    for _, topic in ALL_TOPICS:
        assert len(build_slug(topic, angle).encode("utf-8")) < 200
