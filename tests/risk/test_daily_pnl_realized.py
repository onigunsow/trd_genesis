"""SPEC-TRADING-039 REQ-039-5 / AC-4 — daily_pnl_pct realized-P&L correction.

Reproduction-first (money/risk). The 2026-06-01 bug: ``daily_pnl_pct`` summed
net trade *cash flow* (buy outflow counted as loss), so a net-buy day with two
기아 buys (cash out 167,866 + 168,675) reported -3.34% and tripped a phantom
``daily_loss`` halt, even though the day's realized P&L was +24,283 (positive).

The fix computes daily P&L as the sum of *realized* net P&L over completed
round-trips that exit today (FIFO cost matching, reusing edge.roundtrips). A buy
that is still held contributes nothing — only completed round-trips do.

DB is patched: ``roundtrips.load_fill_rows`` returns scripted fill rows so the
test is offline.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from trading.edge import roundtrips
from trading.risk import limits

TODAY = date.today()


def _fill(ticker: str, side: str, qty: int, price: int, *, day: date, oid: int) -> dict:
    return {
        "id": oid,
        "ticker": ticker,
        "side": side,
        "fill_qty": qty,
        "fill_price": price,
        "fee": 0,
        "filled_at": day,
        "ts": day,
        "confidence": None,
        "verdict": None,
    }


class TestRealizedPnl:
    def test_net_buy_day_is_not_a_loss(self):
        """AC-4: two 기아 buys today (cash out) + a completed +24,283 round-trip.

        Old (net-cashflow) implementation: the buy outflow dominates → negative.
        New (realized) implementation: only the completed round-trip counts → positive.
        """
        # 기아 bought today, still held (no matching sell) → contributes 0.
        kia_buys = [
            _fill("000270", "buy", 1, 167_866, day=TODAY, oid=1),
            _fill("000270", "buy", 1, 168_675, day=TODAY, oid=2),
        ]
        # A separate ticker: bought earlier, sold today for +24,283 realized.
        completed = [
            _fill("005930", "buy", 1, 100_000, day=TODAY, oid=3),
            _fill("005930", "sell", 1, 124_283, day=TODAY, oid=4),
        ]
        rows = kia_buys + completed

        with patch.object(roundtrips, "load_fill_rows", return_value=rows):
            pct = limits.daily_pnl_pct(10_074_006)

        assert pct > 0, "a net-buy day with realized profit must not look like a loss"
        assert pct == pytest.approx(24_283 / 10_074_006, rel=1e-3)

    def test_held_buys_only_is_zero_not_negative(self):
        """AC-4 core: buys with no matching sell → realized P&L is 0, never negative."""
        rows = [
            _fill("000270", "buy", 1, 167_866, day=TODAY, oid=1),
            _fill("000270", "buy", 1, 168_675, day=TODAY, oid=2),
        ]
        with patch.object(roundtrips, "load_fill_rows", return_value=rows):
            pct = limits.daily_pnl_pct(10_074_006)

        assert pct == pytest.approx(0.0)

    def test_realized_loss_is_negative(self):
        """A genuine realized loss today is still reported as negative."""
        rows = [
            _fill("005930", "buy", 1, 100_000, day=TODAY, oid=1),
            _fill("005930", "sell", 1, 80_000, day=TODAY, oid=2),
        ]
        with patch.object(roundtrips, "load_fill_rows", return_value=rows):
            pct = limits.daily_pnl_pct(10_000_000)

        assert pct == pytest.approx(-20_000 / 10_000_000)

    def test_only_today_exits_counted(self):
        """Round-trips that exited on a prior day are not in today's P&L."""
        from datetime import timedelta

        yday = TODAY - timedelta(days=1)
        rows = [
            _fill("005930", "buy", 1, 100_000, day=yday, oid=1),
            _fill("005930", "sell", 1, 130_000, day=yday, oid=2),  # exited yesterday
        ]
        with patch.object(roundtrips, "load_fill_rows", return_value=rows):
            pct = limits.daily_pnl_pct(10_000_000)

        assert pct == pytest.approx(0.0)

    def test_zero_capital_safe(self):
        with patch.object(roundtrips, "load_fill_rows", return_value=[]):
            assert limits.daily_pnl_pct(0) == 0.0

    def test_check_pre_order_no_phantom_halt(self):
        """AC-4 end-to-end: the net-buy day does not trip daily_loss in check_pre_order."""
        kia_buys = [
            _fill("000270", "buy", 1, 167_866, day=TODAY, oid=1),
            _fill("000270", "buy", 1, 168_675, day=TODAY, oid=2),
        ]
        completed = [
            _fill("005930", "buy", 1, 100_000, day=TODAY, oid=3),
            _fill("005930", "sell", 1, 124_283, day=TODAY, oid=4),
        ]
        with (
            patch.object(roundtrips, "load_fill_rows", return_value=kia_buys + completed),
            patch.object(limits, "daily_order_count_today", return_value=0),
        ):
            chk = limits.check_pre_order(
                side="buy", ticker="005930", qty=1, ref_price=1,
                total_assets=10_074_006, holdings=[], mode="paper", market="KOSPI",
            )

        assert not any(b.startswith("daily_loss") for b in chk.breaches)
