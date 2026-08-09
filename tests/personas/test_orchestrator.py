"""Characterization tests for personas/orchestrator.py — REJECT path behavior."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestOrchestratorRejectPath:
    """Characterize current behavior: Risk REJECT discards signal immediately."""

    def test_characterize_reject_discards_signal(self):
        """When Risk returns REJECT, signal is added to rejected list, not executed."""
        from trading.personas.base import PersonaResult

        # Mock micro result
        micro_result = PersonaResult(
            persona_run_id=1,
            response_text="{}",
            response_json={"candidates": {"buy": [{"ticker": "005930"}], "sell": [], "hold": []}},
            input_tokens=1000,
            output_tokens=500,
            cost_krw=10.0,
            latency_ms=2000,
        )

        # Mock decision result with signal
        dec_result = PersonaResult(
            persona_run_id=2,
            response_text="{}",
            response_json={"signals": [{"ticker": "005930", "side": "buy", "qty": 5, "rationale": "test"}]},
            input_tokens=1200,
            output_tokens=600,
            cost_krw=12.0,
            latency_ms=3000,
        )

        # Mock risk result with REJECT
        risk_result = PersonaResult(
            persona_run_id=3,
            response_text="{}",
            response_json={"verdict": "REJECT", "rationale": "sector concentration too high"},
            input_tokens=800,
            output_tokens=300,
            cost_krw=8.0,
            latency_ms=1500,
        )

        with (
            patch("trading.personas.orchestrator.macro_persona") as mock_macro,
            patch("trading.personas.orchestrator.micro_persona") as mock_micro,
            patch("trading.personas.orchestrator.decision_persona") as mock_decision,
            patch("trading.personas.orchestrator.risk_persona") as mock_risk,
            patch("trading.personas.orchestrator.ctx") as mock_ctx,
            patch("trading.personas.orchestrator.tg") as mock_tg,
            patch("trading.personas.orchestrator.get_settings") as mock_settings,
            patch("trading.personas.orchestrator.get_system_state") as mock_state,
            patch("trading.personas.orchestrator._gather_assets") as mock_assets,
            patch("trading.personas.orchestrator.check_pre_order_safety") as mock_safety,
            patch("trading.personas.orchestrator.check_pre_order") as mock_limits,
            patch("trading.personas.orchestrator.KisClient") as mock_kis,
        ):
            # Configure mocks
            mock_macro.latest_cached.return_value = {"id": 10, "response": "bullish"}
            mock_ctx.assemble_micro_input.return_value = {"today": "2026-05-05"}
            mock_micro.run.return_value = micro_result
            mock_decision.run.return_value = (dec_result, [100])
            mock_risk.run.return_value = (risk_result, 200, "REJECT")
            mock_settings.return_value = MagicMock(trading_mode="paper")
            mock_state.return_value = {"halt_state": False}
            mock_assets.return_value = {
                "total_assets": 10_000_000,
                "cash_d2": 9_600_000,
                "stock_eval": 400_000,
                "holdings": [],
            }

            from trading.personas.orchestrator import run_pre_market_cycle

            result = run_pre_market_cycle(today="2026-05-05")

        # CHARACTERIZATION: REJECT path adds decision_id to rejected, does NOT execute
        assert 100 in result.rejected
        assert result.executed_orders == []
        # Risk run was recorded
        assert 3 in result.risk_run_ids

    def test_characterize_approve_proceeds_to_execution(self):
        """When Risk returns APPROVE, signal proceeds through code-rule checks."""
        from trading.personas.base import PersonaResult

        micro_result = PersonaResult(
            persona_run_id=1, response_text="{}", response_json={"candidates": {"buy": [], "sell": [], "hold": []}},
            input_tokens=1000, output_tokens=500, cost_krw=10.0, latency_ms=2000,
        )
        dec_result = PersonaResult(
            persona_run_id=2, response_text="{}",
            response_json={"signals": [{"ticker": "005930", "side": "buy", "qty": 3, "rationale": "good"}]},
            input_tokens=1200, output_tokens=600, cost_krw=12.0, latency_ms=3000,
        )
        risk_result = PersonaResult(
            persona_run_id=3, response_text="{}",
            response_json={"verdict": "APPROVE", "rationale": "within limits"},
            input_tokens=800, output_tokens=300, cost_krw=8.0, latency_ms=1500,
        )

        # Mock safety check pass
        safety_result = MagicMock(passed=True, quote={"price": 70000})
        # Mock limit check pass
        limit_result = MagicMock(passed=True, breaches=[])
        # Mock order execution
        order_result = {"order_id": 999}

        with (
            patch("trading.personas.orchestrator.macro_persona") as mock_macro,
            patch("trading.personas.orchestrator.micro_persona") as mock_micro,
            patch("trading.personas.orchestrator.decision_persona") as mock_decision,
            patch("trading.personas.orchestrator.risk_persona") as mock_risk,
            patch("trading.personas.orchestrator.ctx") as mock_ctx,
            patch("trading.personas.orchestrator.tg") as mock_tg,
            patch("trading.personas.orchestrator.get_settings") as mock_settings,
            patch("trading.personas.orchestrator.get_system_state") as mock_state,
            patch("trading.personas.orchestrator._gather_assets") as mock_assets,
            patch("trading.personas.orchestrator.check_pre_order_safety") as mock_safety,
            patch("trading.personas.orchestrator.check_pre_order") as mock_limits,
            patch("trading.personas.orchestrator.KisClient") as mock_kis,
            patch("trading.personas.orchestrator.kis_buy") as mock_buy,
            patch("trading.personas.orchestrator.balance") as mock_balance,
        ):
            mock_macro.latest_cached.return_value = {"id": 10, "response": "bullish"}
            mock_ctx.assemble_micro_input.return_value = {"today": "2026-05-05"}
            mock_micro.run.return_value = micro_result
            mock_decision.run.return_value = (dec_result, [100])
            mock_risk.run.return_value = (risk_result, 200, "APPROVE")
            mock_settings.return_value = MagicMock(trading_mode="paper")
            mock_state.return_value = {"halt_state": False}
            mock_assets.return_value = {
                "total_assets": 10_000_000, "cash_d2": 9_600_000,
                "stock_eval": 400_000, "holdings": [],
            }
            mock_safety.return_value = safety_result
            mock_limits.return_value = limit_result
            mock_buy.return_value = order_result
            mock_balance.return_value = {
                "total_assets": 10_000_000, "cash_d2": 9_300_000, "stock_eval": 700_000,
            }

            from trading.personas.orchestrator import run_pre_market_cycle

            result = run_pre_market_cycle(today="2026-05-05")

        # CHARACTERIZATION: APPROVE path executes the order
        assert result.rejected == []
        assert 999 in result.executed_orders


class TestPreMarketCircuitBreachClassification:
    """SPEC-TRADING-062 REQ-062-A1/A2 — avg_down 단독 breach는 회로차단을 트립하지
    않고 해당 주문만 거부해야 하며, daily_loss breach는 기존대로 트립해야 한다.

    2026-07-08 인시던트: avg_down(물타기 방지, 계좌 위험 없음) 단일 breach가 하루 종일
    전체 halt를 유발했다. 이 회귀 테스트는 그 사고의 재현이다.
    """

    def _run(self, *, breaches: list[str]):
        from trading.personas.base import PersonaResult

        micro_result = PersonaResult(
            persona_run_id=1, response_text="{}",
            response_json={"candidates": {"buy": [], "sell": [], "hold": []}},
            input_tokens=1000, output_tokens=500, cost_krw=10.0, latency_ms=2000,
        )
        dec_result = PersonaResult(
            persona_run_id=2, response_text="{}",
            response_json={
                "signals": [{"ticker": "086790", "side": "buy", "qty": 3, "rationale": "test"}]
            },
            input_tokens=1200, output_tokens=600, cost_krw=12.0, latency_ms=3000,
        )
        risk_result = PersonaResult(
            persona_run_id=3, response_text="{}",
            response_json={"verdict": "APPROVE", "rationale": "within limits"},
            input_tokens=800, output_tokens=300, cost_krw=8.0, latency_ms=1500,
        )

        safety_result = MagicMock(passed=True, quote={"price": 70000})
        limit_result = MagicMock(passed=False, breaches=breaches)

        with (
            patch("trading.personas.orchestrator.macro_persona") as mock_macro,
            patch("trading.personas.orchestrator.micro_persona") as mock_micro,
            patch("trading.personas.orchestrator.decision_persona") as mock_decision,
            patch("trading.personas.orchestrator.risk_persona") as mock_risk,
            patch("trading.personas.orchestrator.ctx") as mock_ctx,
            patch("trading.personas.orchestrator.tg") as mock_tg,
            patch("trading.personas.orchestrator.get_settings") as mock_settings,
            patch("trading.personas.orchestrator.get_system_state") as mock_state,
            patch("trading.personas.orchestrator._gather_assets") as mock_assets,
            patch("trading.personas.orchestrator.check_pre_order_safety") as mock_safety,
            patch("trading.personas.orchestrator.check_pre_order") as mock_limits,
            patch("trading.personas.orchestrator.record_breach") as mock_record_breach,
            patch("trading.personas.orchestrator.circuit_breaker") as mock_cb,
            patch("trading.personas.orchestrator.KisClient"),
        ):
            mock_macro.latest_cached.return_value = {"id": 10, "response": "bullish"}
            mock_ctx.assemble_micro_input.return_value = {"today": "2026-07-08"}
            mock_micro.run.return_value = micro_result
            mock_decision.run.return_value = (dec_result, [100])
            mock_risk.run.return_value = (risk_result, 200, "APPROVE")
            mock_settings.return_value = MagicMock(trading_mode="paper")
            mock_state.return_value = {"halt_state": False}
            mock_assets.return_value = {
                "total_assets": 10_000_000, "cash_d2": 9_600_000,
                "stock_eval": 400_000, "holdings": [],
            }
            mock_safety.return_value = safety_result
            mock_limits.return_value = limit_result

            from trading.personas.orchestrator import run_pre_market_cycle

            result = run_pre_market_cycle(today="2026-07-08")

        return result, mock_cb, mock_record_breach, mock_tg

    def test_avg_down_only_breach_rejects_order_without_tripping_circuit(self):
        result, mock_cb, mock_record_breach, mock_tg = self._run(
            breaches=["avg_down: 086790 단기과열·손실(-1.20%) 물타기 매수 거부"]
        )

        assert 100 in result.rejected
        assert not mock_cb.trip.called, "avg_down 단독 breach는 회로차단을 트립하면 안 된다"
        assert mock_record_breach.called, "LIMIT_BREACH 감사는 유지되어야 한다"
        assert mock_tg.system_briefing.called, "텔레그램 브리핑은 유지되어야 한다"
        # REQ-064-B2: decision_id is already in scope at this call site and must
        # be threaded into record_breach's context dict (record_breach lifts it
        # to details top-level internally).
        assert mock_record_breach.call_args[0][1]["decision_id"] == 100

    def test_daily_loss_breach_still_trips_circuit(self):
        result, mock_cb, mock_record_breach, mock_tg = self._run(
            breaches=["daily_loss: 오늘 손익 -3.00% ≤ 한도 -2.50%"]
        )

        assert 100 in result.rejected
        assert mock_cb.trip.called, "daily_loss breach는 기존대로 회로차단을 트립해야 한다"
        assert mock_record_breach.called
        assert mock_tg.system_briefing.called
        # REQ-064-B3: CIRCUIT_BREAKER_TRIP details must carry decision_id top-level
        # (trip() merges its `details` kwarg directly into the audit payload).
        assert mock_cb.trip.call_args.kwargs["details"]["decision_id"] == 100


class TestPreMarketTickerBlockedByHoldsDecisionId:
    """SPEC-TRADING-064 REQ-064-B3 — TICKER_BLOCKED_BY_HOLDS (orchestrator.py:1251).

    decision_id is already the loop variable in scope (zip(signals, sig_ids));
    it must be threaded into the audit details top-level, no signature change.
    """

    def test_ticker_blocked_by_holds_carries_decision_id(self):
        from trading.personas.base import PersonaResult

        micro_result = PersonaResult(
            persona_run_id=1, response_text="{}",
            response_json={"candidates": {"buy": [], "sell": [], "hold": []}},
            input_tokens=1000, output_tokens=500, cost_krw=10.0, latency_ms=2000,
        )
        dec_result = PersonaResult(
            persona_run_id=2, response_text="{}",
            response_json={
                "signals": [{"ticker": "086790", "side": "buy", "qty": 3, "rationale": "test"}]
            },
            input_tokens=1200, output_tokens=600, cost_krw=12.0, latency_ms=3000,
        )

        with (
            patch("trading.personas.orchestrator.macro_persona") as mock_macro,
            patch("trading.personas.orchestrator.micro_persona") as mock_micro,
            patch("trading.personas.orchestrator.decision_persona") as mock_decision,
            patch("trading.personas.orchestrator.risk_persona") as mock_risk,
            patch("trading.personas.orchestrator.ctx") as mock_ctx,
            patch("trading.personas.orchestrator.tg"),
            patch("trading.personas.orchestrator.get_settings") as mock_settings,
            patch("trading.personas.orchestrator.get_system_state") as mock_state,
            patch("trading.personas.orchestrator._gather_assets") as mock_assets,
            patch("trading.personas.orchestrator._count_holds_today", return_value=3),
            patch("trading.personas.orchestrator.audit") as mock_audit,
            patch("trading.personas.orchestrator.KisClient"),
        ):
            mock_macro.latest_cached.return_value = {"id": 10, "response": "bullish"}
            mock_ctx.assemble_micro_input.return_value = {"today": "2026-07-08"}
            mock_micro.run.return_value = micro_result
            mock_decision.run.return_value = (dec_result, [100])
            mock_settings.return_value = MagicMock(trading_mode="paper")
            mock_state.return_value = {"halt_state": False}
            mock_assets.return_value = {
                "total_assets": 10_000_000, "cash_d2": 9_600_000,
                "stock_eval": 400_000, "holdings": [],
            }

            from trading.personas.orchestrator import run_pre_market_cycle

            result = run_pre_market_cycle(today="2026-07-08")

        assert 100 in result.rejected
        # Risk was never reached — the ticker was blocked before it.
        assert not mock_risk.run.called
        blocked_calls = [
            c for c in mock_audit.call_args_list if c.args[0] == "TICKER_BLOCKED_BY_HOLDS"
        ]
        assert len(blocked_calls) == 1
        assert blocked_calls[0].kwargs["details"]["decision_id"] == 100


class TestSilentModeDecisionScope:
    """SPEC-TRADING-064 REQ-064-B8 — SILENT_MODE_ON is a TIER 5 permanent
    exemption: 3 consecutive no-signal Decision runs is an aggregate fact, not
    a single decision. Must carry decision_scope, must NOT carry decision_id.
    """

    def test_silent_mode_on_carries_decision_scope_aggregate(self):
        from trading.personas.orchestrator import _maybe_enter_silent_mode

        empty_rows = [{"id": i, "response_json": {"signals": []}} for i in (1, 2, 3)]

        class _Cursor:
            def execute(self, *_a, **_k):
                pass

            def fetchall(self):
                return empty_rows

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return None

        class _Conn:
            def cursor(self):
                return _Cursor()

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return None

        with (
            patch("trading.personas.orchestrator.connection", return_value=_Conn()),
            patch("trading.personas.orchestrator.update_system_state"),
            patch("trading.personas.orchestrator.audit") as mock_audit,
        ):
            _maybe_enter_silent_mode(0)

        mock_audit.assert_called_once()
        args, kwargs = mock_audit.call_args
        assert args[0] == "SILENT_MODE_ON"
        details = kwargs["details"]
        assert details["decision_scope"] == "aggregate"
        assert "decision_id" not in details
