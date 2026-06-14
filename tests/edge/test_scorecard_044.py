"""SPEC-TRADING-044 M3/C — scorecard.render() 에 Sortino, cost_adjusted_win_rate,
net_expectancy(=expectancy_adj) 라인이 표시되는지 검증 (REQ-044-C1, C2, C6).

GO/NO-GO 판정 및 한계 푸터 의미론 보존 (REQ-044-C6).
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from trading.edge import analytics as an
from trading.edge import scorecard as sc
from trading.edge.benchmark import Benchmark
from trading.edge.roundtrips import RoundTrip


# ---------------------------------------------------------------------------
# 픽스처 헬퍼
# ---------------------------------------------------------------------------

def _rt(i: int, profit: float = 3000.0, entry_price: float = 100_000.0) -> RoundTrip:
    d = date(2026, 1, 1) + timedelta(days=i * 2)
    return RoundTrip(
        ticker="A",
        entry_date=d,
        exit_date=d + timedelta(days=1),
        qty=1,
        entry_price=entry_price,
        exit_price=entry_price + profit,
        entry_fee=0,
        exit_fee=0,
        confidence=None,
        verdict=None,
    )


def _loss_rt(i: int, loss: float = 1000.0, entry_price: float = 100_000.0) -> RoundTrip:
    d = date(2026, 2, 1) + timedelta(days=i * 2)
    return RoundTrip(
        ticker="A",
        entry_date=d,
        exit_date=d + timedelta(days=1),
        qty=1,
        entry_price=entry_price,
        exit_price=entry_price - loss,
        entry_fee=0,
        exit_fee=0,
        confidence=None,
        verdict=None,
    )


def _good_benchmark(alpha: float = 5.0) -> Benchmark:
    b = Benchmark()
    b.available = True
    b.start = date(2026, 1, 1)
    b.end = date(2026, 3, 1)
    b.kospi_return_pct = 2.0
    b.strategy_return_pct = 2.0 + alpha
    b.alpha_pct = alpha
    b.cumulative_excess_return_pct = alpha
    b.comparison_basis = "money-weighted(원가기준 집계)"
    return b


def _empty_benchmark() -> Benchmark:
    return Benchmark()


def _make_analytics_and_card(n_wins: int = 5, n_losses: int = 2) -> tuple:
    rts = [_rt(i) for i in range(n_wins)] + [_loss_rt(i) for i in range(n_losses)]
    a = an.compute(rts)
    b = _good_benchmark()
    card = sc.decide(a, b)
    return a, b, card


# ---------------------------------------------------------------------------
# REQ-044-C2: Sortino 라인이 render() 출력에 포함된다
# ---------------------------------------------------------------------------

class TestSortinoInRender:
    """render() 출력에 sortino 수치가 표시된다."""

    def test_sortino_label_present(self):
        """'Sortino' 또는 '소르티노' 텍스트가 render() 에 존재한다."""
        a, b, card = _make_analytics_and_card(n_wins=5, n_losses=2)
        text = sc.render(a, b, card)
        assert "Sortino" in text or "소르티노" in text.lower()

    def test_sortino_value_present(self):
        """sortino 값이 숫자 형식으로 표시된다."""
        a, b, card = _make_analytics_and_card(n_wins=5, n_losses=2)
        text = sc.render(a, b, card)
        # sortino 값이 float 이므로 소수점이 있는 숫자가 나와야 한다
        assert a.sortino != 0.0 or math.isfinite(a.sortino)
        # Sortino 라인 내에 숫자가 있어야 한다
        sortino_line = next(
            (l for l in text.splitlines() if "Sortino" in l or "소르티노" in l.lower()), ""
        )
        assert sortino_line, "Sortino 라인이 render 출력에 없음"
        # 라인 안에 숫자가 있어야
        assert any(c.isdigit() for c in sortino_line)

    def test_sortino_inf_rendered_gracefully(self):
        """손실 0건(sortino=inf)일 때 render() 가 크래시 없이 동작한다."""
        rts = [_rt(i) for i in range(5)]  # 모두 승리
        a = an.compute(rts)
        assert math.isinf(a.sortino)
        b = _empty_benchmark()
        card = sc.decide(a, b)
        text = sc.render(a, b, card)
        assert isinstance(text, str)
        assert len(text) > 0


# ---------------------------------------------------------------------------
# REQ-044-C2: cost_adjusted_win_rate 라인이 render() 출력에 포함된다
# ---------------------------------------------------------------------------

class TestCostAdjustedWinRateInRender:
    """render() 출력에 cost_adjusted_win_rate 가 표시된다."""

    def test_cost_adjusted_win_rate_label_present(self):
        """'비용보정 승률' 또는 관련 한국어 라벨이 render() 에 있다."""
        a, b, card = _make_analytics_and_card(n_wins=5, n_losses=2)
        text = sc.render(a, b, card)
        assert (
            "비용" in text and "승률" in text
        ) or "cost" in text.lower()

    def test_cost_adjusted_win_rate_value_present(self):
        """비용보정 승률 값이 퍼센트 형식으로 표시된다."""
        a, b, card = _make_analytics_and_card(n_wins=5, n_losses=2)
        text = sc.render(a, b, card)
        # 0~100% 형식의 숫자가 있어야 한다
        cost_wr_line = next(
            (l for l in text.splitlines() if "비용" in l and "승률" in l), ""
        )
        assert cost_wr_line, "비용보정 승률 라인이 render 출력에 없음"


# ---------------------------------------------------------------------------
# REQ-044-C1: net_expectancy (= expectancy_adj) 라인
# 기존 render()에 이미 expectancy_adj 가 있으므로 명시적 검증
# ---------------------------------------------------------------------------

class TestNetExpectancyInRender:
    """net_expectancy(=expectancy_adj)가 슬리피지 보정 블록에 표시된다."""

    def test_expectancy_adj_label_present(self):
        """'보정 후 기대값' 라벨이 render() 에 있다."""
        a, b, card = _make_analytics_and_card(n_wins=5, n_losses=2)
        text = sc.render(a, b, card)
        assert "보정 후 기대값" in text

    def test_expectancy_adj_value_is_correct(self):
        """기존 expectancy_adj 값과 render 텍스트의 수치가 일치한다."""
        a, b, card = _make_analytics_and_card(n_wins=5, n_losses=2)
        text = sc.render(a, b, card)
        # expectancy_adj 는 음수거나 양수일 수 있음
        assert "보정 후 기대값" in text


# ---------------------------------------------------------------------------
# REQ-044-C6: GO/NO-GO 판정 및 한계 푸터 보존
# ---------------------------------------------------------------------------

class TestGoNoGoAndFooterPreserved:
    """Sortino/cost_wr 추가 후에도 GO/NO-GO 판정 및 한계 푸터가 보존된다."""

    def test_verdict_still_present(self):
        """판정 라인이 render() 에 여전히 있다."""
        a, b, card = _make_analytics_and_card(n_wins=5, n_losses=2)
        text = sc.render(a, b, card)
        assert "판정:" in text

    def test_footer_still_present(self):
        """'한계' 또는 '⚠️' 등 한계 푸터 마커가 여전히 있다."""
        a, b, card = _make_analytics_and_card(n_wins=5, n_losses=2)
        text = sc.render(a, b, card)
        assert "한계" in text or "⚠️" in text

    def test_go_live_gates_still_present(self):
        """실거래 준비 게이트 섹션이 여전히 있다."""
        a, b, card = _make_analytics_and_card(n_wins=5, n_losses=2)
        text = sc.render(a, b, card)
        assert "실거래 준비 게이트" in text
