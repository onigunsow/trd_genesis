"""SPEC-TRADING-062 REQ-062-B2/B3/B4 — content-anchor(title_head) 순수 검증 함수.

idx 집합 정렬(SPEC-061)은 통과하지만 콘텐츠가 뒤바뀐 경우(모델이 idx는 완전한
순열을 echo하되 내용은 엉뚱한 기사에 붙이는 제2 실패모드, 2026-07-08 인시던트
재현)를 잡기 위한 title_head 앵커 대조를 검증한다. 순수 함수 — DB/네트워크 없음
(REQ-062-B4).
"""

from __future__ import annotations

from trading.news.intelligence.analyzer import (
    ANCHOR_MIN_CHARS,
    ANCHOR_MISMATCH_MAX,
    _anchor_matches,
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


class TestLiveFalsePositives2026_08_15:
    """라이브 실측 오탐 재현 — 청크 2개 38건 중 14건이 부당 거부됐다.

    종전 규칙("앞 12자 완전일치")이 깨진 두 유형을 그대로 넣는다. 진짜
    오정렬은 0건이었고, 이 오탐 때문에 하루 기사의 절반 이상이 버려졌다
    (2026-08-09~15 폐기율 53%, 2,071/3,876건).
    """

    def test_model_echoes_fewer_than_12_chars(self):
        """유형 A: 모델이 12자를 정확히 못 센다 — got 은 exp 의 접두사."""
        cases = [
            ("26일 '인베스터 데이' 여는 현대차…자사주 매입 발표 가능성은",
             "26일 '인베스터 데"),
            ("학생 없어 펑펑 남아돌던 교육교부금…내년부터 확 달라진다",
             "학생 없어 펑펑 남"),
            ("Somali Piracy Surges Amid Hormuz Blockade", "Somali Pira"),
            ("[속보] 다카이치, 야스쿠니 공물 대금 봉납…\"자민당 총재 자격\"",
             "[속보] 다카이치,"),
        ]
        for title, head in cases:
            assert _anchor_matches(title, head), f"오탐: {head!r} in {title!r}"

    def test_model_echoes_middle_of_title(self):
        """유형 B: 모델이 제목 앞이 아니라 중간 조각을 echo — 같은 기사다."""
        cases = [
            ("Consumer Warning Lights Flash, Oil and Treasury Yields Rise "
             "| Markets P.M. For August 14 - WSJ", "Treasury Yie"),
            ("Trump family's World Liberty crypto venture granted conditional "
             "approval for bank licence - Financial Times", "World Libert"),
        ]
        for title, head in cases:
            assert _anchor_matches(title, head), f"오탐: {head!r} in {title!r}"

    def test_live_chunk_would_now_pass_threshold(self):
        """result_03 표본: 종전 11/20 불일치 → 포함 비교에서 0건."""
        titles = {
            101: "학생 없어 펑펑 남아돌던 교육교부금…내년부터 확 달라진다",
            102: "Somali Piracy Surges Amid Hormuz Blockade",
            103: "이달 18% 뛰었는데도 \"더 간다\"…증권가가 꽂힌 이 종목",
        }
        aligned = _aligned([
            (101, "학생 없어 펑펑 남"),
            (102, "Somali Pira"),
            (103, "이달 18% 뛰었는데"),
        ])
        assert _anchor_mismatch_count(aligned, titles) == 0


class TestScrambleStillDetected:
    """오탐을 없애면서 SPEC-062 가 막던 제2 실패모드는 그대로 잡아야 한다."""

    def test_head_from_another_article_is_mismatch(self):
        """내용이 뒤바뀌면 title_head 는 매핑된 제목에 없다."""
        assert not _anchor_matches("Samsung Q1 profit surges", "Hyundai laun")
        assert not _anchor_matches("Hyundai launches new EV", "Samsung Q1 p")

    def test_korean_scramble_detected(self):
        titles = {
            101: "학생 없어 펑펑 남아돌던 교육교부금…내년부터 확 달라진다",
            102: "Somali Piracy Surges Amid Hormuz Blockade",
        }
        aligned = _aligned([  # 서로 뒤바뀐 head
            (101, "Somali Pira"),
            (102, "학생 없어 펑펑 남"),
        ])
        assert _anchor_mismatch_count(aligned, titles) == 2

    def test_too_short_head_is_not_evidence(self):
        """짧은 조각은 아무 제목에나 우연히 들어가므로 정렬 증거가 아니다."""
        short = "a" * (ANCHOR_MIN_CHARS - 1)
        assert not _anchor_matches(f"prefix {short} suffix", short)

    def test_min_length_head_still_compared(self):
        ok = "Samsung"[:ANCHOR_MIN_CHARS]
        assert _anchor_matches("Samsung Q1 profit surges", ok)
