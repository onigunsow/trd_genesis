"""SPEC-TRADING-029 v0.2.0 — trade briefing pct + name render (REQ-029-10/9).

Two concerns:

1. The orchestrator helper ``compute_balance_pcts(bal)`` returns
   ``(cash_pct, equity_pct)`` computed on a single ``invest_basis`` denominator
   so they sum to 100% (AC-029-14), with a zero-basis guard (AC-029-15).
2. ``telegram.trade_briefing`` renders the resolved ``name`` next to the ticker
   (AC-029-13).
"""

from __future__ import annotations

from unittest.mock import patch


class TestComputeBalancePcts:
    def test_pcts_sum_to_100_on_invest_basis(self):
        """AC-029-14: live values → 73.7% / 26.3%, sum == 100."""
        from trading.personas.orchestrator import compute_balance_pcts

        bal = {
            "cash_d2": 8_787_740,
            "stock_eval": 3_128_400,
            "invest_basis": 11_916_140,
            "total_assets": 9_919_870,
        }
        cash_pct, equity_pct = compute_balance_pcts(bal)
        assert round(cash_pct, 1) == 73.7
        assert round(equity_pct, 1) == 26.3
        assert round(cash_pct + equity_pct, 1) == 100.0

    def test_zero_basis_guard(self):
        """AC-029-15: invest_basis=0 → (0.0, 0.0), no ZeroDivisionError."""
        from trading.personas.orchestrator import compute_balance_pcts

        bal = {
            "cash_d2": 0,
            "stock_eval": 0,
            "invest_basis": 0,
            "total_assets": 0,
        }
        assert compute_balance_pcts(bal) == (0.0, 0.0)

    def test_missing_invest_basis_falls_back_to_cash_plus_stock(self):
        """Defensive: if invest_basis is absent, derive it from cash + stock."""
        from trading.personas.orchestrator import compute_balance_pcts

        bal = {"cash_d2": 100, "stock_eval": 100, "total_assets": 500}
        cash_pct, equity_pct = compute_balance_pcts(bal)
        assert round(cash_pct + equity_pct, 1) == 100.0
        assert round(cash_pct, 1) == 50.0


class TestTradeBriefingNameRender:
    def test_name_is_rendered_in_message(self):
        """AC-029-13: a non-None name appears in the briefing text."""
        from trading.alerts import telegram

        with patch.object(telegram, "_send_raw") as send:
            telegram.trade_briefing(
                side="buy",
                ticker="005930",
                name="삼성전자",
                qty=1,
                fill_price=None,
                fee=0,
                mode="paper",
                total_assets=9_919_870,
                cash_pct=73.7,
                equity_pct=26.3,
                note="",
            )

        send.assert_called_once()
        text = send.call_args.args[0]
        assert "005930" in text
        assert "삼성전자" in text

    def test_none_name_renders_without_crash(self):
        """AC-029-13: name=None still renders gracefully (ticker only)."""
        from trading.alerts import telegram

        with patch.object(telegram, "_send_raw") as send:
            telegram.trade_briefing(
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
                note="",
            )

        send.assert_called_once()
        text = send.call_args.args[0]
        assert "000660" in text
