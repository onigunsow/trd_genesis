"""SPEC-TRADING-062 REQ-062-B2/B3/B4 — content-anchor(title_head) 순수 검증 함수.

idx 집합 정렬(SPEC-061)은 통과하지만 콘텐츠가 뒤바뀐 경우(모델이 idx는 완전한
순열을 echo하되 내용은 엉뚱한 기사에 붙이는 제2 실패모드, 2026-07-08 인시던트
재현)를 잡기 위한 title_head 앵커 대조를 검증한다. 순수 함수 — DB/네트워크 없음
(REQ-062-B4).
"""

from __future__ import annotations

from trading.news.intelligence.analyzer import (
    ANCHOR_MISMATCH_MAX,
    _anchor_mismatch_count,
)


def _aligned(pairs: list[tuple[int, str]]) -> list[tuple[int, dict]]:
    """[(article_id, title_head), ...] -> _align_results_to_articles 반환 형식."""
    return [
        (aid, {"idx": i + 1, "title_head": title_head, "summary_2line": "x"})
        for i, (aid, title_head) in enumerate(pairs)
    ]


class TestAnchorMismatchCount:
    """REQ-062-B2: echo된 title_head 와 매핑된 기사의 실제 제목 앞부분 대조."""

    def test_all_matching_returns_zero(self):
        aligned = _aligned([
            (101, "Samsung Q1 p"),
            (102, "Hyundai laun"),
        ])
        titles = {
            101: "Samsung Q1 profit surges on chip demand",
            102: "Hyundai launches new EV model today",
        }
        assert _anchor_mismatch_count(aligned, titles) == 0

    def test_scrambled_content_with_valid_idx_detected_as_mismatches(self):
        """idx 는 완전한 순열이지만(SPEC-061 통과) 내용이 뒤바뀐 제2 실패모드."""
        aligned = _aligned([
            (101, "Hyundai laun"),  # 실제로는 102의 title_head
            (102, "Samsung Q1 p"),  # 실제로는 101의 title_head
        ])
        titles = {
            101: "Samsung Q1 profit surges on chip demand",
            102: "Hyundai launches new EV model today",
        }
        assert _anchor_mismatch_count(aligned, titles) == 2

    def test_single_mismatch_counted(self):
        aligned = _aligned([
            (101, "Samsung Q1 p"),
            (102, "WRONG HEAD!!"),
        ])
        titles = {
            101: "Samsung Q1 profit surges on chip demand",
            102: "Hyundai launches new EV model today",
        }
        assert _anchor_mismatch_count(aligned, titles) == 1

    def test_missing_title_head_not_counted_as_mismatch(self):
        """REQ-062-B3: 구버전 응답(title_head 없음)은 존재할 때만 대조한다."""
        aligned = [
            (101, {"idx": 1, "title_head": None, "summary_2line": "x"}),
            (102, {"idx": 2, "summary_2line": "y"}),  # 키 자체가 없음
        ]
        titles = {101: "whatever title here", 102: "another title entirely"}
        assert _anchor_mismatch_count(aligned, titles) == 0

    def test_whitespace_normalized_before_comparison(self):
        aligned = _aligned([(101, "Samsung  Q1  profit")])  # 연속 공백(정규화 대상)
        titles = {101: "Samsung Q1 profit surges"}
        assert _anchor_mismatch_count(aligned, titles) == 0

    def test_trailing_space_at_cut_boundary_not_mismatch(self):
        """2026-07-09 라이브 오탐 재현: 제목의 12번째 문자가 공백일 때 모델은
        후행 공백 없이 echo한다 — 후행 공백 차이는 불일치가 아니다."""
        # "가스기술공사, 중장기 로드맵…"의 앞 12자는 '가스기술공사, 중장기 '(끝=공백).
        # 모델 echo는 '가스기술공사, 중장기'(후행 공백 없음) — 동일 기사여야 한다.
        aligned = _aligned([(101, "가스기술공사, 중장기")])
        titles = {101: "가스기술공사, 중장기 로드맵 발표"}
        assert _anchor_mismatch_count(aligned, titles) == 0

    def test_default_threshold_constant(self):
        assert ANCHOR_MISMATCH_MAX == 1
