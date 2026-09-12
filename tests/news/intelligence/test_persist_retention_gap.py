"""보존기간 삭제로 사라진 기사가 임포트 배치를 통째로 죽이던 결함 (2026-09-12).

crawler.cleanup_old_articles(7d) 가 크롤마다 news_articles 를 지운다. export ->
호스트 CLI 분석 -> import 사이에 경계를 넘긴 기사가 나오면 FK 위반이 나고, 저장
루프가 단일 트랜잭션이라 청크 최대 20건이 통째로 롤백된 뒤 예외가 scheduled_import
까지 올라가 임포트 잡 전체가 중단된다. 2026-09-10 실측 4회.
"""
from __future__ import annotations

from unittest.mock import patch

from trading.news.intelligence import analyzer


def _result(n: int) -> dict:
    return {
        "summary_2line": f"요약{n}", "impact_score": 3, "keywords": ["k"],
        "sentiment": "neutral", "classification": "기타",
    }


class _Cur:
    def __init__(self, sink): self.sink = sink
    def execute(self, sql, params=None): self.sink.append((sql, params))
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Conn:
    def __init__(self, sink): self.sink = sink
    def cursor(self): return _Cur(self.sink)
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _run(aligned, article_map):
    sink: list = []
    with patch.object(analyzer, "connection", lambda *a, **k: _Conn(sink)):
        stored = analyzer._persist_cli_results(aligned, article_map)
    return stored, sink


def test_사라진_기사는_건너뛰고_나머지는_저장된다():
    """한 건이 없다고 청크 전체를 잃으면 안 된다."""
    aligned = [(1, _result(1)), (1358799, _result(2)), (3, _result(3))]
    article_map = {1: {"sector": ""}, 3: {"sector": ""}}   # 1358799 는 삭제됨

    stored, sink = _run(aligned, article_map)

    assert stored == 2
    inserted = [p[0] for sql, p in sink if p and "INSERT INTO news_analysis" in sql]
    assert 1358799 not in inserted
    assert inserted == [1, 3]


def test_전부_사라져도_예외가_아니다():
    stored, sink = _run([(99, _result(1))], {})
    assert stored == 0
    assert sink == []


def test_전부_살아있으면_기존_동작_그대로():
    aligned = [(1, _result(1)), (2, _result(2))]
    stored, _ = _run(aligned, {1: {"sector": ""}, 2: {"sector": ""}})
    assert stored == 2
