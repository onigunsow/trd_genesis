"""Tests for run_intraday_cycle implementation (SPEC-TRADING-016 REQ-016-1-1).

Verifies that:
- cycle_kind="intraday" is set BEFORE persona records are persisted (not after).
- Micro is NOT re-run; the morning's cached Micro result is reused.
- Decision and Risk are called fresh with cycle_kind="intraday".
- Risk is skipped only when no signals exist.
- Empty / missing micro cache is handled gracefully.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from trading.personas.base import PersonaResult


def _micro_result(persona_run_id: int = 1) -> PersonaResult:
    return PersonaResult(
        persona_run_id=persona_run_id,
        response_text="{}",
        response_json={
            "candidates": {
                "buy": [{"ticker": "005930"}],
                "sell": [],
                "hold": [],
            }
        },
        input_tokens=1000,
        output_tokens=500,
        cost_krw=10.0,
        latency_ms=2000,
    )


def _decision_result(
    persona_run_id: int = 2,
    signals: list[dict] | None = None,
) -> PersonaResult:
    return PersonaResult(
        persona_run_id=persona_run_id,
        response_text="{}",
        response_json={"signals": signals or []},
        input_tokens=1200,
        output_tokens=600,
        cost_krw=12.0,
        latency_ms=3000,
    )


def _risk_result(
    persona_run_id: int = 3,
    verdict: str = "REJECT",
) -> PersonaResult:
    return PersonaResult(
        persona_run_id=persona_run_id,
        response_text="{}",
        response_json={"verdict": verdict, "rationale": "test"},
        input_tokens=800,
        output_tokens=300,
        cost_krw=8.0,
        latency_ms=1500,
    )


class TestIntradayCycleUsesCachedMicro:
    """When cached Micro exists, run_intraday_cycle must reuse it (no Micro.run)."""

    def test_intraday_uses_cached_micro(self):
        cached_micro_row = {
            "id": 42,
            "response": '{"candidates":{"buy":[{"ticker":"005930"}],"sell":[],"hold":[]}}',
            "response_json": {
                "candidates": {
                    "buy": [{"ticker": "005930"}],
                    "sell": [],
                    "hold": [],
                }
            },
        }

        with (
            patch("trading.personas.orchestrator.macro_persona") as mock_macro,
            patch("trading.personas.orchestrator.micro_persona") as mock_micro,
            patch("trading.personas.orchestrator.decision_persona") as mock_decision,
            patch("trading.personas.orchestrator.risk_persona"),
            patch("trading.personas.orchestrator.tg"),
            patch("trading.personas.orchestrator.get_settings") as mock_settings,
            patch("trading.personas.orchestrator.get_system_state") as mock_state,
            patch("trading.personas.orchestrator._gather_assets") as mock_assets,
            patch("trading.personas.orchestrator.get_blocked_tickers") as mock_blocked,
        ):
            mock_macro.latest_cached.return_value = {
                "id": 10,
                "response": "bullish",
                "response_json": {"regime": "bull", "risk_appetite": "risk-on"},
            }
            mock_micro.latest_cached.return_value = cached_micro_row
            # If implementation calls Micro.run -> we want the test to fail fast.
            mock_micro.run.side_effect = AssertionError(
                "Micro.run must NOT be called during intraday cycle"
            )
            mock_decision.run.return_value = (_decision_result(signals=[]), [])
            mock_settings.return_value = MagicMock(trading_mode="paper")
            mock_state.return_value = {"halt_state": False}
            mock_assets.return_value = {
                "total_assets": 10_000_000,
                "cash_d2": 9_600_000,
                "stock_eval": 400_000,
                "holdings": [],
            }
            mock_blocked.return_value = {"blocked": {}}

            from trading.personas.orchestrator import run_intraday_cycle

            result = run_intraday_cycle(today="2026-05-10")

        # Micro must NOT be re-run
        assert not mock_micro.run.called, "Micro.run should not be called in intraday cycle"
        # Decision called once with cycle_kind="intraday"
        assert mock_decision.run.call_count == 1
        _, kwargs = mock_decision.run.call_args
        assert kwargs.get("cycle_kind") == "intraday"
        # Result reflects intraday cycle
        assert result.cycle_kind == "intraday"


class TestIntradayCycleNoMicroCache:
    """When no cached Micro exists, run_intraday_cycle proceeds with empty candidates."""

    def test_intraday_no_micro_cache_proceeds_with_empty_candidates(self):
        with (
            patch("trading.personas.orchestrator.macro_persona") as mock_macro,
            patch("trading.personas.orchestrator.micro_persona") as mock_micro,
            patch("trading.personas.orchestrator.decision_persona") as mock_decision,
            patch("trading.personas.orchestrator.risk_persona"),
            patch("trading.personas.orchestrator.tg"),
            patch("trading.personas.orchestrator.get_settings") as mock_settings,
            patch("trading.personas.orchestrator.get_system_state") as mock_state,
            patch("trading.personas.orchestrator._gather_assets") as mock_assets,
            patch("trading.personas.orchestrator.get_blocked_tickers") as mock_blocked,
        ):
            mock_macro.latest_cached.return_value = None
            mock_micro.latest_cached.return_value = None
            mock_micro.run.side_effect = AssertionError(
                "Micro.run must NOT be called during intraday cycle"
            )
            mock_decision.run.return_value = (_decision_result(signals=[]), [])
            mock_settings.return_value = MagicMock(trading_mode="paper")
            mock_state.return_value = {"halt_state": False}
            mock_assets.return_value = {
                "total_assets": 10_000_000,
                "cash_d2": 9_600_000,
                "stock_eval": 400_000,
                "holdings": [],
            }
            mock_blocked.return_value = {"blocked": {}}

            from trading.personas.orchestrator import run_intraday_cycle

            result = run_intraday_cycle(today="2026-05-10")

        # Decision is still called even with no micro cache
        assert mock_decision.run.call_count == 1
        args, kwargs = mock_decision.run.call_args
        # The decision input should have empty / missing candidates
        decision_input = args[0] if args else kwargs.get("input_data", {})
        candidates = decision_input.get("micro_candidates", {})
        # Either empty dict / list, or dict with empty buy list — both are "no candidates"
        if isinstance(candidates, dict):
            assert not candidates.get("buy"), "buy candidates should be empty when no cache"
        else:
            assert candidates == [] or not candidates
        assert result.cycle_kind == "intraday"


class TestIntradayCyclePersistsCorrectCycleKind:
    """cycle_kind="intraday" must be passed to persona.run BEFORE DB insert."""

    def test_intraday_persists_correct_cycle_kind(self):
        cached_micro_row = {
            "id": 42,
            "response_json": {
                "candidates": {"buy": [{"ticker": "005930"}], "sell": [], "hold": []}
            },
        }

        with (
            patch("trading.personas.orchestrator.macro_persona") as mock_macro,
            patch("trading.personas.orchestrator.micro_persona") as mock_micro,
            patch("trading.personas.orchestrator.decision_persona") as mock_decision,
            patch("trading.personas.orchestrator.risk_persona"),
            patch("trading.personas.orchestrator.tg"),
            patch("trading.personas.orchestrator.get_settings") as mock_settings,
            patch("trading.personas.orchestrator.get_system_state") as mock_state,
            patch("trading.personas.orchestrator._gather_assets") as mock_assets,
            patch("trading.personas.orchestrator.get_blocked_tickers") as mock_blocked,
        ):
            mock_macro.latest_cached.return_value = {
                "id": 10,
                "response": "bullish",
                "response_json": {"regime": "bull"},
            }
            mock_micro.latest_cached.return_value = cached_micro_row
            mock_decision.run.return_value = (_decision_result(signals=[]), [])
            mock_settings.return_value = MagicMock(trading_mode="paper")
            mock_state.return_value = {"halt_state": False}
            mock_assets.return_value = {
                "total_assets": 10_000_000,
                "cash_d2": 9_600_000,
                "stock_eval": 400_000,
                "holdings": [],
            }
            mock_blocked.return_value = {"blocked": {}}

            from trading.personas.orchestrator import run_intraday_cycle

            result = run_intraday_cycle(today="2026-05-10")

        # The cycle_kind passed to decision_persona.run must be "intraday"
        _, kwargs = mock_decision.run.call_args
        assert kwargs.get("cycle_kind") == "intraday", (
            f"Decision must be invoked with cycle_kind='intraday', "
            f"got {kwargs.get('cycle_kind')!r}"
        )
        # The CycleResult must declare intraday up-front
        assert result.cycle_kind == "intraday"


class TestIntradayCycleCallsRiskWhenSignalsExist:
    """When Decision returns signals, Risk must be called with cycle_kind='intraday'."""

    def test_intraday_calls_risk_when_signals_exist(self):
        cached_micro_row = {
            "id": 42,
            "response_json": {
                "candidates": {"buy": [{"ticker": "005930"}], "sell": [], "hold": []}
            },
        }
        signals = [
            {"ticker": "005930", "side": "buy", "qty": 5, "rationale": "test"},
        ]

        with (
            patch("trading.personas.orchestrator.macro_persona") as mock_macro,
            patch("trading.personas.orchestrator.micro_persona") as mock_micro,
            patch("trading.personas.orchestrator.decision_persona") as mock_decision,
            patch("trading.personas.orchestrator.risk_persona") as mock_risk,
            patch("trading.personas.orchestrator.tg"),
            patch("trading.personas.orchestrator.get_settings") as mock_settings,
            patch("trading.personas.orchestrator.get_system_state") as mock_state,
            patch("trading.personas.orchestrator._gather_assets") as mock_assets,
            patch("trading.personas.orchestrator.get_blocked_tickers") as mock_blocked,
            patch("trading.personas.orchestrator.check_pre_order_safety") as mock_safety,
            patch("trading.personas.orchestrator.check_pre_order") as mock_limits,
            patch("trading.personas.orchestrator.KisClient"),
            patch("trading.personas.orchestrator._count_holds_today", return_value=0),
            patch("trading.personas.orchestrator._execute_signal", return_value=None),
        ):
            mock_macro.latest_cached.return_value = {
                "id": 10,
                "response": "bullish",
                "response_json": {"regime": "bull"},
            }
            mock_micro.latest_cached.return_value = cached_micro_row
            mock_decision.run.return_value = (
                _decision_result(signals=signals),
                [101],
            )
            mock_risk.run.return_value = (_risk_result(verdict="REJECT"), 201, "REJECT")
            mock_settings.return_value = MagicMock(trading_mode="paper")
            mock_state.return_value = {"halt_state": False}
            mock_assets.return_value = {
                "total_assets": 10_000_000,
                "cash_d2": 9_600_000,
                "stock_eval": 400_000,
                "holdings": [],
            }
            mock_blocked.return_value = {"blocked": {}}
            mock_safety.return_value = MagicMock(passed=True, quote={"price": 70_000})
            mock_limits.return_value = MagicMock(passed=True, breaches=[])

            from trading.personas.orchestrator import run_intraday_cycle

            result = run_intraday_cycle(today="2026-05-10")

        # Risk must have been called at least once with cycle_kind="intraday"
        assert mock_risk.run.called, "Risk.run should be called when signals exist"
        risk_kwargs = mock_risk.run.call_args.kwargs
        assert risk_kwargs.get("cycle_kind") == "intraday", (
            f"Risk must be invoked with cycle_kind='intraday', "
            f"got {risk_kwargs.get('cycle_kind')!r}"
        )
        assert result.cycle_kind == "intraday"


class TestIntradayCycleSkipsRiskWhenNoSignals:
    """When Decision returns no signals, Risk must NOT be called."""

    def test_intraday_skips_risk_when_no_signals(self):
        cached_micro_row = {
            "id": 42,
            "response_json": {
                "candidates": {"buy": [], "sell": [], "hold": []}
            },
        }

        with (
            patch("trading.personas.orchestrator.macro_persona") as mock_macro,
            patch("trading.personas.orchestrator.micro_persona") as mock_micro,
            patch("trading.personas.orchestrator.decision_persona") as mock_decision,
            patch("trading.personas.orchestrator.risk_persona") as mock_risk,
            patch("trading.personas.orchestrator.tg"),
            patch("trading.personas.orchestrator.get_settings") as mock_settings,
            patch("trading.personas.orchestrator.get_system_state") as mock_state,
            patch("trading.personas.orchestrator._gather_assets") as mock_assets,
            patch("trading.personas.orchestrator.get_blocked_tickers") as mock_blocked,
        ):
            mock_macro.latest_cached.return_value = {
                "id": 10,
                "response": "bullish",
                "response_json": {"regime": "neutral"},
            }
            mock_micro.latest_cached.return_value = cached_micro_row
            mock_decision.run.return_value = (_decision_result(signals=[]), [])
            mock_settings.return_value = MagicMock(trading_mode="paper")
            mock_state.return_value = {"halt_state": False}
            mock_assets.return_value = {
                "total_assets": 10_000_000,
                "cash_d2": 9_600_000,
                "stock_eval": 400_000,
                "holdings": [],
            }
            mock_blocked.return_value = {"blocked": {}}

            from trading.personas.orchestrator import run_intraday_cycle

            result = run_intraday_cycle(today="2026-05-10")

        # Risk must NOT be called when there are no signals
        assert not mock_risk.run.called, "Risk.run should NOT be called when no signals"
        assert result.cycle_kind == "intraday"
        assert result.executed_orders == []
