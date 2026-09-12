"""뉴스 처리량 설정 (2026-09-12).

실측: 크롤 유입 약 1,900건/일 vs 용량 100 x 6슬롯 = 600건/일 → 유입의 74%가
분석 없이 보존기간(7일)에 삭제됐다(분석률 26%, 2,795/10,853).

상한은 시간이 아니라 CLI 호출 횟수다. 호스트는 청크당 약 80초이고 슬롯 간격이
3시간이라 시간 여유는 충분하다.
"""
from __future__ import annotations

from trading.news.intelligence import analyzer


def test_슬롯당_처리량이_유입의_절반은_덮는다():
    """유입 약 1,900건/일, 슬롯 6회 → 슬롯당 160건이면 절반."""
    assert analyzer.MAX_ARTICLES_PER_RUN * 6 >= 950


def test_청크_크기는_20을_유지한다():
    """2026-07-08 인시던트: 94~98개 단일배치가 거의 100% 스크램블됐다.
    처리량은 청크 '수' 로 늘리고 청크 '크기' 는 건드리지 않는다."""
    assert analyzer.HOST_CHUNK_SIZE == 20


def test_슬롯당_청크가_import_여유_안에_들어간다():
    """호스트 청크당 약 80초. import 는 분석 시작 +20분(:10 -> :30)이므로
    15청크(20분)까지 감당한다."""
    chunks = analyzer.MAX_ARTICLES_PER_RUN / analyzer.HOST_CHUNK_SIZE
    assert chunks <= 15, f"{chunks:.0f}청크는 import 여유 20분을 넘긴다"


def test_env_로_되돌릴_수_있다(monkeypatch):
    """쿼터 문제가 생기면 재배포 없이 내릴 수 있어야 한다."""
    import importlib

    monkeypatch.setenv("NEWS_MAX_ARTICLES_PER_RUN", "100")
    importlib.reload(analyzer)
    try:
        assert analyzer.MAX_ARTICLES_PER_RUN == 100
    finally:
        monkeypatch.delenv("NEWS_MAX_ARTICLES_PER_RUN")
        importlib.reload(analyzer)
