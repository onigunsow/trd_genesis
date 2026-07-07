"""SPEC-TRADING-061 REQ-061-2/3 — ID 기반 결과-기사 정렬 매핑.

핵심 재현: LLM/파서가 기사 전송 순서와 다른 순서로 결과를 반환해도(REORDER),
echo된 1-based idx 로만 정렬해야 하며 리스트 위치(enumerate)로 매핑해서는
안 된다. idx 집합이 기대 {1..N} 과 정확히 일치하지 않으면(missing/extra/
duplicate/out-of-range) 전체를 거부하고 저장하지 않는다(fail-closed).

이 테스트는 순수 함수 ``_align_results_to_articles`` 만 검증한다(DB 접근 없음).
RED 시점(수정 전)에는 이 함수 자체가 존재하지 않아 ImportError 로 실패한다 —
이는 곧 "현재 코드가 위치 기반으로만 매핑하며 ID 기반 정렬 안전망이 없다"는
사실 그 자체를 증명한다(RC1/RC2).
"""

from __future__ import annotations

from trading.news.intelligence.analyzer import _align_results_to_articles


def _result(idx: int, tag: str) -> dict:
    """idx 를 echo 하는 최소 result dict (tag 로 어느 결과인지 식별)."""
    return {
        "idx": idx,
        "summary_2line": f"summary-{tag}",
        "impact_score": 3,
        "keywords": [tag],
        "sentiment": "neutral",
        "classification": "company_specific",
        "sector": "",
    }


class TestReorderedResultsAlignByIdNotPosition:
    """REQ-061-2: 위치 매핑 철폐 — echo된 idx 로만 정렬."""

    def test_reordered_results_map_to_correct_article_ids(self):
        # 기사 3건: article_ids 순서 = [101, 102, 103] (idx 1,2,3 에 대응)
        article_ids = [101, 102, 103]

        # LLM 이 순서를 뒤섞어 반환(REORDER) — idx=2 결과가 먼저 옴
        results = [
            _result(idx=2, tag="B"),
            _result(idx=3, tag="C"),
            _result(idx=1, tag="A"),
        ]

        aligned = _align_results_to_articles(results, article_ids)

        assert aligned is not None
        aligned_map = dict(aligned)
        # idx=1 -> article_ids[0]=101 -> tag A. 리스트 위치가 아니라 idx 로 매핑돼야 한다.
        assert aligned_map[101]["keywords"] == ["A"]
        assert aligned_map[102]["keywords"] == ["B"]
        assert aligned_map[103]["keywords"] == ["C"]

    def test_correctly_ordered_results_still_map_correctly(self):
        """회귀: 정상 순서 응답도 여전히 올바르게 저장돼야 한다."""
        article_ids = [201, 202, 203]
        results = [
            _result(idx=1, tag="A"),
            _result(idx=2, tag="B"),
            _result(idx=3, tag="C"),
        ]

        aligned = _align_results_to_articles(results, article_ids)

        assert aligned is not None
        aligned_map = dict(aligned)
        assert aligned_map[201]["keywords"] == ["A"]
        assert aligned_map[202]["keywords"] == ["B"]
        assert aligned_map[203]["keywords"] == ["C"]


class TestFailClosedOnIdMismatch:
    """REQ-061-3: idx 집합 불일치 → 전체 거부(None), 저장 없음."""

    def test_missing_idx_in_set_rejects_all(self):
        article_ids = [1, 2, 3]
        results = [_result(idx=1, tag="A"), _result(idx=2, tag="B")]  # idx=3 없음
        assert _align_results_to_articles(results, article_ids) is None

    def test_extra_out_of_range_idx_rejects_all(self):
        article_ids = [1, 2]
        results = [_result(idx=1, tag="A"), _result(idx=99, tag="X")]  # 99 는 범위 밖
        assert _align_results_to_articles(results, article_ids) is None

    def test_duplicate_idx_rejects_all(self):
        article_ids = [1, 2]
        results = [_result(idx=1, tag="A"), _result(idx=1, tag="A-dup")]
        assert _align_results_to_articles(results, article_ids) is None

    def test_no_idx_field_rejects_all(self):
        """구(舊) 프롬프트 유입(REQ-061-6): idx 미echo → fail-closed, 위치 폴백 없음."""
        article_ids = [1, 2]
        results = [
            {"summary_2line": "x", "impact_score": 1, "keywords": [],
             "sentiment": "neutral", "classification": "noise", "sector": ""},
            {"summary_2line": "y", "impact_score": 1, "keywords": [],
             "sentiment": "neutral", "classification": "noise", "sector": ""},
        ]
        assert _align_results_to_articles(results, article_ids) is None

    def test_empty_article_ids_rejects(self):
        assert _align_results_to_articles([_result(1, "A")], []) is None

    def test_empty_results_rejects(self):
        assert _align_results_to_articles([], [1, 2]) is None
