"""분석 대상 선별의 관련성 우선 정렬 (2026-08-15).

유입 1,800~2,000건/일 대비 분석 상한은 500~600건/일 이라, 뽑히지 못한 기사는
7일 보존 정리에 그대로 삭제된다. 즉 이 선별이 "무엇을 영영 안 보는가"를 정한다.

여기서는 순수 재료(_has_hangul / _universe_title_patterns)만 검증한다. 실제
매칭은 Postgres 정규식(\\m \\M)이라 파이썬으로 재현하면 거짓 안심이 되므로,
라이브 실측으로 확인했다.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from trading.news.intelligence.analyzer import (
    RELEVANCE_DEFAULT_HIT_RATE,
    RELEVANCE_HIGH_IMPACT_MIN,
    RELEVANCE_MIN_SAMPLE,
    _has_hangul,
    _universe_title_patterns,
)


class TestHasHangul:
    def test_korean_names(self):
        assert _has_hangul("삼성전자")
        assert _has_hangul("SK하이닉스")  # 혼합도 한글 취급

    def test_ascii_only_names(self):
        assert not _has_hangul("SK")
        assert not _has_hangul("NAVER")
        assert not _has_hangul("KT&G")


def _patched_universe(names, tickers=("005930",)):
    """get_data_universe + ticker_metadata 조회를 대체한다."""
    cur = MagicMock()
    cur.fetchall.return_value = [{"name": n} for n in names]
    cur.__enter__ = lambda s: s
    cur.__exit__ = lambda s, *a: None
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = lambda s: s
    conn.__exit__ = lambda s, *a: None

    from trading.news.intelligence import analyzer

    return (
        patch("trading.data.universe.get_data_universe", return_value=list(tickers)),
        patch.object(analyzer, "connection", return_value=conn),
    )


class TestUniverseTitlePatterns:
    def _run(self, names):
        p1, p2 = _patched_universe(names)
        with p1, p2:
            return _universe_title_patterns()

    def test_korean_names_use_substring_like(self):
        """한글명은 조사가 붙으므로 부분문자열이어야 한다('삼성전자가')."""
        like, _ = self._run(["삼성전자", "현대차"])
        assert "%삼성전자%" in like
        assert "%현대차%" in like

    def test_ascii_names_go_to_word_boundary_regex(self):
        """ASCII 명은 부분문자열이면 안 된다 — 단어경계 정규식으로 간다."""
        like, regex = self._run(["SK", "NAVER"])
        assert like == []
        assert regex.startswith(r"\m(")
        assert regex.endswith(r")\M")
        assert "SK" in regex
        assert "NAVER" in regex

    def test_short_ascii_name_is_not_a_like_pattern(self):
        """실측 회귀: ILIKE '%SK%' 는 task·risk·disk·'G.Skill' 을 전부 잡아
        PC 부품 기사를 최우선으로 올렸다. 'SK' 는 LIKE 쪽에 있으면 안 된다."""
        like, _ = self._run(["SK", "LG", "HMM"])
        assert like == []

    def test_regex_special_chars_escaped(self):
        """'KT&G', 'S-Oil' 처럼 정규식 특수문자를 품은 이름도 안전해야 한다."""
        _, regex = self._run(["KT&G", "S-Oil"])
        assert "Oil" in regex
        assert regex.startswith(r"\m(")
        assert regex.endswith(r")\M")

    def test_one_char_and_wildcard_names_dropped(self):
        """한 글자·LIKE 와일드카드 이름은 전수 매치 위험이라 제외한다."""
        like, regex = self._run(["A", "가", "1%", "a_b"])
        assert like == []
        assert regex == ""

    def test_mixed_names_split_correctly(self):
        like, regex = self._run(["삼성전자", "SK", "카카오"])
        assert set(like) == {"%삼성전자%", "%카카오%"}
        assert "SK" in regex

    def test_universe_load_failure_disables_priority(self):
        """유니버스를 못 읽으면 우선순위를 끄고 파이프라인은 계속 돈다."""
        with patch(
            "trading.data.universe.get_data_universe", side_effect=RuntimeError("boom")
        ):
            assert _universe_title_patterns() == ([], "")

    def test_empty_universe_disables_priority(self):
        with patch("trading.data.universe.get_data_universe", return_value=[]):
            assert _universe_title_patterns() == ([], "")


class TestRelevanceConstants:
    def test_thresholds_are_sane(self):
        """섹터 등급은 실측에서 산출한다 — 상수는 그 산출의 안전장치다."""
        assert RELEVANCE_HIGH_IMPACT_MIN == 4
        assert RELEVANCE_MIN_SAMPLE >= 20, "표본이 적으면 hit_rate 를 믿을 수 없다"
        assert 0.0 < RELEVANCE_DEFAULT_HIT_RATE < 1.0, (
            "표본 부족 섹터는 중립값을 받아야 부당하게 밀리지 않는다"
        )
