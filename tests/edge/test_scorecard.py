"""Edge Validation Phase 1/3 — 표본 등급 + go/no-go 판정 + 한계 푸터 + 게이트."""

from __future__ import annotations

from datetime import date, timedelta

from trading.edge import analytics as an
from trading.edge import scorecard as sc
from trading.edge.benchmark import Benchmark
from trading.edge.roundtrips import RoundTrip


def _win_rt(i, profit=1000.0):
    d = date(2026, 1, 1) + timedelta(days=i)
    return RoundTrip(
        ticker="A", entry_date=d, exit_date=d + timedelta(days=1),
        qty=1, entry_price=10_000, exit_price=10_000 + profit,
        entry_fee=0, exit_fee=0, confidence=None, verdict=None,
    )


def _good_benchmark(alpha=15.0):
    b = Benchmark()
    b.available = True
    b.start, b.end = date(2026, 1, 1), date(2026, 3, 1)
    b.kospi_return_pct = 2.0
    b.strategy_return_pct = 2.0 + alpha
    b.alpha_pct = alpha
    return b


class TestGrades:
    def test_grade_boundaries(self):
        assert sc.grade_sample(5) == sc.GRADE_INSUFFICIENT
        assert sc.grade_sample(10) == sc.GRADE_WEAK
        assert sc.grade_sample(29) == sc.GRADE_WEAK
        assert sc.grade_sample(30) == sc.GRADE_MODERATE
        assert sc.grade_sample(99) == sc.GRADE_MODERATE
        assert sc.grade_sample(100) == sc.GRADE_OK


class TestVerdicts:
    def test_small_sample_is_no_go(self):
        rts = [_win_rt(i) for i in range(5)]
        a = an.compute(rts)
        card = sc.decide(a, _good_benchmark())
        assert card.verdict == sc.VERDICT_NO_GO

    def test_strong_synthetic_is_go(self):
        # 40건 전부 큰 이익 → 보정 후에도 양성, 표본 충분, 알파 양성.
        rts = [_win_rt(i, profit=2000.0) for i in range(40)]
        a = an.compute(rts)
        card = sc.decide(a, _good_benchmark(alpha=15.0))
        assert card.verdict == sc.VERDICT_GO

    def test_positive_but_small_is_weak_go(self):
        rts = [_win_rt(i, profit=2000.0) for i in range(15)]  # 10–29
        a = an.compute(rts)
        card = sc.decide(a, _good_benchmark())
        assert card.verdict == sc.VERDICT_WEAK_GO

    def test_negative_expectancy_is_no_go(self):
        rts = [_win_rt(i, profit=-500.0) for i in range(40)]  # 전부 손실
        a = an.compute(rts)
        card = sc.decide(a, _good_benchmark())
        assert card.verdict == sc.VERDICT_NO_GO

    def test_positive_sufficient_but_negative_alpha_is_inconclusive(self):
        rts = [_win_rt(i, profit=2000.0) for i in range(40)]
        a = an.compute(rts)
        b = _good_benchmark(alpha=-5.0)
        card = sc.decide(a, b)
        assert card.verdict == sc.VERDICT_INCONCLUSIVE

    def test_positive_sufficient_no_benchmark_is_inconclusive(self):
        rts = [_win_rt(i, profit=2000.0) for i in range(40)]
        a = an.compute(rts)
        card = sc.decide(a, Benchmark())  # available=False
        assert card.verdict == sc.VERDICT_INCONCLUSIVE


class TestRenderFooterAndGates:
    def test_footer_always_present(self):
        rts = [_win_rt(i, profit=2000.0) for i in range(40)]
        a = an.compute(rts)
        b = _good_benchmark()
        card = sc.decide(a, b)
        text = sc.render(a, b, card, days=90)
        assert "한계" in text
        assert "페이퍼 체결가 ≠ 실거래 체결가" in text

    def test_footer_present_even_when_empty(self):
        a = an.compute([])
        card = sc.decide(a, Benchmark())
        text = sc.render(a, Benchmark(), card)
        assert "한계" in text

    def test_go_live_gates_rendered(self):
        rts = [_win_rt(i, profit=2000.0) for i in range(40)]
        a = an.compute(rts)
        b = _good_benchmark()
        text = sc.render(a, b, sc.decide(a, b))
        assert "실거래 준비 게이트" in text
        assert "RISK_DAILY_MAX_LOSS" in text
        assert "_TOOK_PROFIT" in text
        assert "chmod 600" in text

    def test_small_sample_footer_warns_significance(self):
        rts = [_win_rt(i) for i in range(5)]
        a = an.compute(rts)
        text = sc.render(a, Benchmark(), sc.decide(a, Benchmark()))
        assert "통계적 유의성 없음" in text
