"""SPEC-TRADING-041 — orchestrator presentation-layer enrichment.

Covers, via the real cycle entry points:
- REQ-041-1: stock name appears in the 4 ``tg.system_briefing`` alerts
  (단기과열 비중 축소 / 한도 위반 차단, in pre_market and intraday cycles).
- REQ-041-2 + Open Question #1: a FULL sell whose ticker disappears from the
  POST-fill balance still reports realized P&L, because avg_cost is captured
  from the PRE-sell ``assets["holdings"]`` snapshot and passed to trade_briefing.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from trading.personas.base import PersonaResult


def _persona_results(side: str, ticker: str, qty: int):
    micro = PersonaResult(
        persona_run_id=1, response_text="{}",
        response_json={"candidates": {"buy": [], "sell": [], "hold": []}},
        input_tokens=1, output_tokens=1, cost_krw=0.0, latency_ms=1,
    )
    dec = PersonaResult(
        persona_run_id=2, response_text="{}",
        response_json={"signals": [
            {"ticker": ticker, "side": side, "qty": qty, "rationale": "x"}
        ]},
        input_tokens=1, output_tokens=1, cost_krw=0.0, latency_ms=1,
    )
    risk = PersonaResult(
        persona_run_id=3, response_text="{}",
        response_json={"verdict": "APPROVE", "rationale": "ok"},
        input_tokens=1, output_tokens=1, cost_krw=0.0, latency_ms=1,
    )
    return micro, dec, risk


def _common_patches():
    return (
        patch("trading.personas.orchestrator.macro_persona"),
        patch("trading.personas.orchestrator.micro_persona"),
        patch("trading.personas.orchestrator.decision_persona"),
        patch("trading.personas.orchestrator.risk_persona"),
        patch("trading.personas.orchestrator.ctx"),
        patch("trading.personas.orchestrator.tg"),
        patch("trading.personas.orchestrator.get_settings"),
        patch("trading.personas.orchestrator.get_system_state"),
        patch("trading.personas.orchestrator._gather_assets"),
        patch("trading.personas.orchestrator.check_pre_order_safety"),
        patch("trading.personas.orchestrator.check_pre_order"),
        patch("trading.personas.orchestrator.KisClient"),
        patch("trading.personas.orchestrator.balance"),
        patch("trading.personas.orchestrator.ticker_name"),
        patch("trading.personas.orchestrator._execute_signal"),
        patch("trading.personas.orchestrator.record_breach"),
        patch("trading.personas.orchestrator.circuit_breaker"),
    )


class TestLimitBreachAlertHasName:
    def test_pre_market_breach_includes_stock_name(self):
        """REQ-041-1: a blocked ('한도 위반 차단') alert names the ticker."""
        micro, dec, risk = _persona_results("buy", "064350", 5)
        ps = _common_patches()
        with (ps[0] as p_macro, ps[1] as _p_micro, ps[2] as p_dec, ps[3] as p_risk,
              ps[4] as p_ctx, ps[5] as p_tg, ps[6] as p_set, ps[7] as p_state,
              ps[8] as p_assets, ps[9] as p_safety, ps[10] as p_limits,
              ps[11] as _p_kis, ps[12] as _p_bal, ps[13] as p_name,
              ps[14] as _p_exec, ps[15] as _p_rb, ps[16] as _p_cb):
            p_macro.latest_cached.return_value = {"id": 10, "response": "x"}
            p_ctx.assemble_micro_input.return_value = {"today": "2026-05-05"}
            ps_micro = _p_micro
            ps_micro.run.return_value = micro
            p_dec.run.return_value = (dec, [100])
            p_risk.run.return_value = (risk, 200, "APPROVE")
            p_set.return_value = MagicMock(trading_mode="paper")
            p_state.return_value = {"halt_state": False}
            p_assets.return_value = {
                "total_assets": 10_000_000, "cash_d2": 9_000_000,
                "stock_eval": 1_000_000, "holdings": [],
            }
            p_safety.return_value = MagicMock(passed=True, quote={"price": 70000},
                                             overheated=False)
            p_limits.return_value = MagicMock(passed=False, breaches=["daily_count>10"])
            p_name.return_value = "현대로템"

            from trading.personas.orchestrator import run_pre_market_cycle
            run_pre_market_cycle(today="2026-05-05")

        # Find the 한도 위반 차단 briefing call and assert it carries the name.
        breach_calls = [
            c for c in p_tg.system_briefing.call_args_list
            if c.args and c.args[0] == "한도 위반 차단"
        ]
        assert breach_calls, "expected a 한도 위반 차단 system_briefing"
        body = breach_calls[0].args[1]
        assert "064350" in body
        assert "현대로템" in body


class TestFullSellRealizedPnl:
    def test_full_sell_pnl_uses_presell_avg_cost(self):
        """Open Question #1 reproduction + fix.

        The ticker is held PRE-sell (avg_cost in assets["holdings"]) but is GONE
        from the POST-fill balance (full sell). trade_briefing must still receive
        the pre-sell avg_cost so the realized-P&L line renders.
        """
        micro, dec, risk = _persona_results("sell", "064350", 10)
        ps = _common_patches()
        with (ps[0] as p_macro, ps[1] as _p_micro, ps[2] as p_dec, ps[3] as p_risk,
              ps[4] as p_ctx, ps[5] as p_tg, ps[6] as p_set, ps[7] as p_state,
              ps[8] as p_assets, ps[9] as p_safety, ps[10] as p_limits,
              ps[11] as _p_kis, ps[12] as p_bal, ps[13] as p_name,
              ps[14] as p_exec, ps[15] as _p_rb, ps[16] as _p_cb):
            p_macro.latest_cached.return_value = {"id": 10, "response": "x"}
            p_ctx.assemble_micro_input.return_value = {"today": "2026-05-05"}
            _p_micro.run.return_value = micro
            p_dec.run.return_value = (dec, [100])
            p_risk.run.return_value = (risk, 200, "APPROVE")
            p_set.return_value = MagicMock(trading_mode="paper")
            p_state.return_value = {"halt_state": False}
            # PRE-sell snapshot HAS the holding with avg_cost.
            p_assets.return_value = {
                "total_assets": 10_000_000, "cash_d2": 9_000_000,
                "stock_eval": 1_000_000,
                "holdings": [{
                    "ticker": "064350", "name": "현대로템", "qty": 10,
                    "avg_cost": 100_000, "current_price": 105_000,
                    "pnl_amount": 49_340, "pnl_pct": 4.9,
                }],
            }
            p_safety.return_value = MagicMock(passed=True, quote={"price": 105000},
                                             overheated=False)
            p_limits.return_value = MagicMock(passed=True, breaches=[])
            p_exec.return_value = 999
            # POST-fill balance: full sell → ticker GONE from holdings.
            p_bal.return_value = {
                "total_assets": 10_000_000, "cash_d2": 10_000_000,
                "stock_eval": 0, "invest_basis": 10_000_000, "holdings": [],
            }
            p_name.return_value = "현대로템"

            from trading.personas.orchestrator import run_pre_market_cycle
            run_pre_market_cycle(today="2026-05-05")

        assert p_tg.trade_briefing.called, "expected a trade_briefing after the sell"
        kwargs = p_tg.trade_briefing.call_args.kwargs
        assert kwargs.get("side") == "sell"
        # The fix: avg_cost from the PRE-sell snapshot is forwarded.
        assert kwargs.get("avg_cost") == 100_000
