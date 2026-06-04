"""SPEC-TRADING-041 REQ-041-2 — sell realized-P&L (average-cost basis).

Two layers:

1. ``compute_sell_pnl(fill_price, avg_cost, qty, fee)`` — a pure function that
   returns ``(amount, pct)`` on the average-cost basis, or ``None`` when avg_cost
   is missing / non-positive / fill_price unknown (AC-2.2, REQ-041-4b).
2. ``trade_briefing`` renders the realized-P&L line ONLY for ``side='sell'`` when
   ``avg_cost`` is valid; buy alerts are byte-unchanged (AC-2.3, REQ-041-2c).
"""

from __future__ import annotations

from unittest.mock import patch


class TestComputeSellPnl:
    def test_profit_positive_sign_and_pct(self):
        """AC-2.1: (105,000-100,000)*10 - 660 = +49,340원 (+4.9%)."""
        from trading.alerts.telegram import compute_sell_pnl

        res = compute_sell_pnl(fill_price=105_000, avg_cost=100_000, qty=10, fee=660)
        assert res is not None
        amount, pct = res
        assert amount == 49_340
        # net return on basis: 49,340 / (100,000*10) = 4.934% → 4.9%
        assert round(pct, 1) == 4.9

    def test_loss_negative_sign(self):
        """A loss returns a negative amount and negative pct."""
        from trading.alerts.telegram import compute_sell_pnl

        amount, pct = compute_sell_pnl(fill_price=95_000, avg_cost=100_000, qty=10, fee=660)
        assert amount == -50_660  # (95000-100000)*10 - 660
        assert pct < 0
        # -50,660 / 1,000,000 = -5.066% → -5.1%
        assert round(pct, 1) == -5.1

    def test_breakeven_minus_fee(self):
        """Flat price still nets the fee as a small loss."""
        from trading.alerts.telegram import compute_sell_pnl

        amount, pct = compute_sell_pnl(fill_price=100_000, avg_cost=100_000, qty=10, fee=660)
        assert amount == -660
        # net return on basis: -660 / 1,000,000 = -0.066% → -0.1%
        assert round(pct, 1) == -0.1

    def test_avg_cost_missing_returns_none(self):
        """AC-2.2 / REQ-041-4b: avg_cost None → None (omit the line)."""
        from trading.alerts.telegram import compute_sell_pnl

        assert compute_sell_pnl(fill_price=105_000, avg_cost=None, qty=10, fee=660) is None

    def test_avg_cost_zero_returns_none(self):
        """avg_cost <= 0 cannot yield a meaningful basis → None."""
        from trading.alerts.telegram import compute_sell_pnl

        assert compute_sell_pnl(fill_price=105_000, avg_cost=0, qty=10, fee=660) is None

    def test_fill_price_missing_returns_none(self):
        """No fill price → cannot compute realized P&L → None."""
        from trading.alerts.telegram import compute_sell_pnl

        assert compute_sell_pnl(fill_price=None, avg_cost=100_000, qty=10, fee=660) is None


class TestTradeBriefingSellPnl:
    def _send_and_capture(self, **kwargs):
        from trading.alerts import telegram

        with patch.object(telegram, "_send_raw") as send:
            telegram.trade_briefing(**kwargs)
        send.assert_called_once()
        return send.call_args.args[0]

    def test_sell_with_avg_cost_renders_pnl_line(self):
        """AC-2.1: sell + valid avg_cost → realized-P&L line with sign + pct."""
        text = self._send_and_capture(
            side="sell",
            ticker="064350",
            name="현대로템",
            qty=10,
            fill_price=105_000,
            fee=660,
            mode="paper",
            total_assets=1_000_000,
            cash_pct=50.0,
            equity_pct=50.0,
            avg_cost=100_000,
        )
        assert "실현손익" in text
        assert "+49,340원" in text
        assert "+4.9%" in text

    def test_sell_loss_shows_minus_sign(self):
        text = self._send_and_capture(
            side="sell",
            ticker="064350",
            name="현대로템",
            qty=10,
            fill_price=95_000,
            fee=660,
            mode="paper",
            total_assets=1_000_000,
            cash_pct=50.0,
            equity_pct=50.0,
            avg_cost=100_000,
        )
        assert "실현손익" in text
        assert "-50,660원" in text
        assert "-5.1%" in text

    def test_sell_without_avg_cost_omits_pnl_line(self):
        """AC-2.2: avg_cost absent → no realized-P&L line, no fake 0원."""
        text = self._send_and_capture(
            side="sell",
            ticker="064350",
            name="현대로템",
            qty=10,
            fill_price=105_000,
            fee=660,
            mode="paper",
            total_assets=1_000_000,
            cash_pct=50.0,
            equity_pct=50.0,
            avg_cost=None,
        )
        assert "실현손익" not in text

    def test_buy_never_shows_pnl_line(self):
        """AC-2.3 / REQ-041-2c: buy alert never shows realized P&L."""
        text = self._send_and_capture(
            side="buy",
            ticker="064350",
            name="현대로템",
            qty=10,
            fill_price=105_000,
            fee=660,
            mode="paper",
            total_assets=1_000_000,
            cash_pct=50.0,
            equity_pct=50.0,
            avg_cost=100_000,  # even if passed, buy ignores it
        )
        assert "실현손익" not in text

    def test_default_call_unchanged_no_avg_cost_param(self):
        """REQ-041-2c regression: existing callers (no avg_cost) still work."""
        text = self._send_and_capture(
            side="sell",
            ticker="000660",
            name=None,
            qty=2,
            fill_price=150_000,
            fee=0,
            mode="paper",
            total_assets=1_000_000,
            cash_pct=50.0,
            equity_pct=50.0,
        )
        # No avg_cost passed → line omitted, no crash.
        assert "실현손익" not in text
        assert "000660" in text
