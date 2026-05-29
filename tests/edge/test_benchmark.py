"""Edge Validation Phase 1 — KOSPI 매수후보유 대비 알파."""

from __future__ import annotations

from datetime import date

from trading.edge import benchmark as bm
from trading.edge.roundtrips import RoundTrip


def _rt(entry, exit_, qty, ep, xp):
    return RoundTrip(
        ticker="A", entry_date=date.fromisoformat(entry), exit_date=date.fromisoformat(exit_),
        qty=qty, entry_price=ep, exit_price=xp, entry_fee=0, exit_fee=0,
        confidence=None, verdict=None,
    )


class TestAlpha:
    def test_strategy_beats_kospi_positive_alpha(self):
        rts = [_rt("2026-01-01", "2026-01-31", 10, 100, 120)]  # +20% on cost
        closes = [(date(2026, 1, 1), 2500.0), (date(2026, 1, 31), 2550.0)]  # KOSPI +2%
        b = bm.compute(rts, closes=closes)
        assert b.available
        assert abs(b.strategy_return_pct - 20.0) < 1e-6
        assert abs(b.kospi_return_pct - 2.0) < 1e-6
        assert abs(b.alpha_pct - 18.0) < 1e-6

    def test_strategy_lags_kospi_negative_alpha(self):
        rts = [_rt("2026-01-01", "2026-01-31", 10, 100, 101)]  # +1%
        closes = [(date(2026, 1, 1), 2500.0), (date(2026, 1, 31), 2750.0)]  # KOSPI +10%
        b = bm.compute(rts, closes=closes)
        assert b.alpha_pct < 0


class TestGuards:
    def test_no_roundtrips_unavailable(self):
        b = bm.compute([], closes=[(date(2026, 1, 1), 2500.0), (date(2026, 1, 2), 2510.0)])
        assert not b.available

    def test_no_kospi_data_unavailable(self):
        rts = [_rt("2026-01-01", "2026-01-31", 10, 100, 120)]
        b = bm.compute(rts, closes=[])
        assert not b.available

    def test_single_close_point_unavailable(self):
        rts = [_rt("2026-01-01", "2026-01-31", 10, 100, 120)]
        b = bm.compute(rts, closes=[(date(2026, 1, 1), 2500.0)])
        assert not b.available
