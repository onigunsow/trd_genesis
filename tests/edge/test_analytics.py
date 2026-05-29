"""Edge Validation Phase 1/2/3 — analytics 집계.

승률·손익비·기대값·수수료드래그·슬리피지 보정·자산곡선/MDD·미실현 병기·시간가중 지표.
"""

from __future__ import annotations

from datetime import date

from trading.edge import analytics as an
from trading.edge.roundtrips import RoundTrip


def _rt(entry, exit_, qty, ep, xp, ef=0.0, xf=0.0, conf=None, verdict=None):
    return RoundTrip(
        ticker="A",
        entry_date=date.fromisoformat(entry),
        exit_date=date.fromisoformat(exit_),
        qty=qty, entry_price=ep, exit_price=xp,
        entry_fee=ef, exit_fee=xf, confidence=conf, verdict=verdict,
    )


class TestCoreAggregates:
    def test_win_rate_profit_factor_expectancy(self):
        rts = [
            _rt("2026-01-01", "2026-01-02", 10, 100, 120),   # +200 win
            _rt("2026-01-01", "2026-01-03", 10, 100, 90),    # -100 loss
            _rt("2026-01-01", "2026-01-04", 10, 100, 110),   # +100 win
        ]
        a = an.compute(rts)
        assert a.n_closed == 3
        assert a.n_wins == 2 and a.n_losses == 1
        assert abs(a.win_rate - 2 / 3) < 1e-9
        assert a.total_net_pnl == 200
        # gross_profit=300, gross_loss=100 → PF=3.0
        assert a.profit_factor == 3.0
        assert a.expectancy == 200 / 3

    def test_fee_drag_counted(self):
        rts = [_rt("2026-01-01", "2026-01-02", 10, 100, 120, ef=50, xf=70)]
        a = an.compute(rts)
        assert a.total_fees == 120
        assert a.total_net_pnl == (120 - 100) * 10 - 120

    def test_empty(self):
        a = an.compute([])
        assert a.n_closed == 0
        assert a.profit_factor == 0.0


class TestSlippageCorrection:
    def test_adjusted_pnl_is_lower_than_raw(self):
        rts = [_rt("2026-01-01", "2026-01-02", 10, 100, 120)]
        a = an.compute(rts)
        assert a.total_net_pnl_adj < a.total_net_pnl
        assert a.slippage_drag > 0
        # 보정 차감 = 양변 슬리피지 + 매도 거래세
        from trading.backtest.engine import DEFAULT_SLIPPAGE, DEFAULT_TAX_RATE
        expected = (100 * 10) * DEFAULT_SLIPPAGE + (120 * 10) * DEFAULT_SLIPPAGE + (120 * 10) * DEFAULT_TAX_RATE
        assert abs(a.slippage_drag - expected) < 1e-6


class TestEquityCurveAndMdd:
    def test_realized_mdd_negative_on_drawdown(self):
        rts = [
            _rt("2026-01-01", "2026-01-02", 1, 0, 100),   # +100 → cum 100
            _rt("2026-01-01", "2026-01-03", 1, 0, -50),   # -50  → cum 50 (낙폭 -50)
            _rt("2026-01-01", "2026-01-04", 1, 0, 30),    # +30  → cum 80
        ]
        a = an.compute(rts)
        assert a.equity_curve[-1][1] == 80
        assert a.realized_mdd_krw == -50


class TestUnrealized:
    def test_balance_adds_unrealized(self):
        rts = [_rt("2026-01-01", "2026-01-02", 10, 100, 120)]
        a = an.compute(rts, balance={"pnl_total": 5_000})
        assert a.has_unrealized
        assert a.unrealized_pnl == 5_000
        assert a.total_pnl_incl_unrealized == a.total_net_pnl + 5_000

    def test_unrealized_with_no_roundtrips(self):
        a = an.compute([], balance={"pnl_total": 1_234})
        assert a.total_pnl_incl_unrealized == 1_234


class TestTimeWeighted:
    def test_insufficient_rows_unavailable(self):
        snaps = [(date(2026, 1, i + 1), 1_000_000 + i) for i in range(5)]
        tw = an.time_weighted_metrics(snaps)
        assert not tw.available
        assert tw.n_days == 5

    def test_sufficient_rows_compute_metrics(self):
        # 25행 단조 증가 → 양의 수익률, MDD≈0, Sharpe 유한.
        snaps = [(date(2026, 1, 1 + i), 1_000_000 * (1.001 ** i)) for i in range(25)]
        tw = an.time_weighted_metrics(snaps)
        assert tw.available
        assert tw.n_days == 25
        assert tw.total_return_pct > 0
        assert tw.mdd <= 0
        assert tw.sharpe > 0

    def test_drawdown_detected(self):
        vals = [1_000_000] * 10 + [900_000] * 15  # 10% 낙폭
        snaps = [(date(2026, 1, 1) , v) for v in vals]
        # 날짜를 고유하게
        from datetime import timedelta
        snaps = [(date(2026, 1, 1) + timedelta(days=i), v) for i, v in enumerate(vals)]
        tw = an.time_weighted_metrics(snaps)
        assert tw.available
        assert abs(tw.mdd - (-0.10)) < 1e-9
