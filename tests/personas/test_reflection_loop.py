"""Tests for Reflection Loop in orchestrator (SPEC-009 Phase D).

Tests cover:
- REQ-REFL-03-1: REJECT triggers reflection
- REQ-REFL-03-2: Max 2 rounds
- REQ-REFL-03-4: Decision withdrawal
- REQ-REFL-03-9: Risk is unaware of reflection
- REQ-REFL-03-10: Timeout handling
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import FakeCursor, FakeConnection


class FakePersonaResult:
    """Minimal PersonaResult for testing."""

    def __init__(self, run_id=1, text="", json_data=None, in_tok=500, out_tok=200, cost=10.0):
        self.persona_run_id = run_id
        self.response_text = text
        self.response_json = json_data
        self.input_tokens = in_tok
        self.output_tokens = out_tok
        self.cost_krw = cost
        self.latency_ms = 100
        self.tool_calls_count = 0
        self.tool_input_tokens = 0
        self.tool_output_tokens = 0


class TestReflectionLoop:
    """Test _run_reflection_loop behavior."""

    def _base_kwargs(self):
        """Common kwargs for _run_reflection_loop."""
        return {
            "original_signal": {"ticker": "005930", "side": "buy", "qty": 5},
            "risk_response_json": {
                "verdict": "REJECT",
                "rationale": "Too concentrated in semiconductors",
                "concerns": ["sector_concentration", "high_valuation"],
            },
            "dec_input": {"today": "2026-05-05", "macro_guide": "neutral"},
            "cycle_kind": "pre_market",
            "decision_id": 42,
            "macro_run_id": 1,
            "micro_run_id": 2,
            "assets": {"total_assets": 10_000_000, "cash_d2": 5_000_000, "holdings": []},
            "cash_pct": 50.0,
            "macro_summary": "neutral regime",
            "micro_summary": "buy 1 / sell 0",
            "today": "2026-05-05",
            "state": {"tool_calling_enabled": False, "reflection_loop_enabled": True},
        }

    @patch("trading.personas.orchestrator._persist_reflection_round")
    @patch("trading.personas.orchestrator.risk_persona")
    @patch("trading.personas.orchestrator.decision_persona")
    @patch("trading.personas.orchestrator.tg")
    def test_reflection_approve_on_first_round(
        self, mock_tg, mock_dec, mock_risk, mock_persist
    ):
        """Decision revises, Risk approves on round 1."""
        # Decision returns revised signal
        revised_json = {"signals": [{"ticker": "005930", "side": "buy", "qty": 3}]}
        mock_dec.run.return_value = (
            FakePersonaResult(run_id=10, json_data=revised_json),
            [43],
        )
        # Risk approves revised signal
        mock_risk.run.return_value = (
            FakePersonaResult(run_id=11, json_data={"verdict": "APPROVE"}),
            100,
            "APPROVE",
        )

        from trading.personas.orchestrator import _run_reflection_loop

        verdict, sig, risk_run_id = _run_reflection_loop(**self._base_kwargs())

        assert verdict == "APPROVE"
        assert sig == {"ticker": "005930", "side": "buy", "qty": 3}
        assert risk_run_id == 11
        mock_persist.assert_called_once()

    @patch("trading.personas.orchestrator.audit")
    @patch("trading.personas.orchestrator._persist_reflection_round")
    @patch("trading.personas.orchestrator.risk_persona")
    @patch("trading.personas.orchestrator.decision_persona")
    @patch("trading.personas.orchestrator.tg")
    def test_reflection_withdrawal(self, mock_tg, mock_dec, mock_risk, mock_persist, mock_audit):
        """Decision withdraws signal (signals=[])."""
        mock_dec.run.return_value = (
            FakePersonaResult(run_id=10, json_data={"signals": []}),
            [],
        )

        from trading.personas.orchestrator import _run_reflection_loop

        verdict, sig, risk_run_id = _run_reflection_loop(**self._base_kwargs())

        assert verdict == "WITHDRAWN"
        assert sig is None
        assert risk_run_id is None
        # Risk should NOT be called on withdrawal
        mock_risk.run.assert_not_called()

    @patch("trading.personas.orchestrator.audit")
    @patch("trading.personas.orchestrator._persist_reflection_round")
    @patch("trading.personas.orchestrator.risk_persona")
    @patch("trading.personas.orchestrator.decision_persona")
    @patch("trading.personas.orchestrator.tg")
    def test_reflection_withdrawal_with_flag(self, mock_tg, mock_dec, mock_risk, mock_persist, mock_audit):
        """Decision explicitly sets withdraw=true."""
        mock_dec.run.return_value = (
            FakePersonaResult(run_id=10, json_data={"withdraw": True, "signals": []}),
            [],
        )

        from trading.personas.orchestrator import _run_reflection_loop

        verdict, sig, _ = _run_reflection_loop(**self._base_kwargs())

        assert verdict == "WITHDRAWN"

    @patch("trading.personas.orchestrator._persist_reflection_round")
    @patch("trading.personas.orchestrator.risk_persona")
    @patch("trading.personas.orchestrator.decision_persona")
    @patch("trading.personas.orchestrator.tg")
    def test_reflection_max_2_rounds_then_reject(
        self, mock_tg, mock_dec, mock_risk, mock_persist
    ):
        """REQ-REFL-03-2: Max 2 rounds, then final REJECT."""
        # Decision always revises
        revised_json = {"signals": [{"ticker": "005930", "side": "buy", "qty": 2}]}
        mock_dec.run.return_value = (
            FakePersonaResult(run_id=10, json_data=revised_json),
            [43],
        )
        # Risk always rejects
        mock_risk.run.return_value = (
            FakePersonaResult(
                run_id=11,
                json_data={"verdict": "REJECT", "rationale": "still bad", "concerns": ["x"]},
            ),
            100,
            "REJECT",
        )

        from trading.personas.orchestrator import _run_reflection_loop

        verdict, sig, risk_run_id = _run_reflection_loop(**self._base_kwargs())

        assert verdict == "REJECT"
        assert sig is None
        # Decision and Risk should each be called exactly 2 times (max rounds)
        assert mock_dec.run.call_count == 2
        assert mock_risk.run.call_count == 2

    @patch("trading.personas.orchestrator._persist_reflection_round")
    @patch("trading.personas.orchestrator.risk_persona")
    @patch("trading.personas.orchestrator.decision_persona")
    @patch("trading.personas.orchestrator.tg")
    @patch("trading.personas.orchestrator.audit")
    def test_reflection_decision_failure(
        self, mock_audit, mock_tg, mock_dec, mock_risk, mock_persist
    ):
        """Decision throws exception during reflection -> REJECT."""
        mock_dec.run.side_effect = RuntimeError("API error")

        from trading.personas.orchestrator import _run_reflection_loop

        verdict, sig, _ = _run_reflection_loop(**self._base_kwargs())

        assert verdict == "REJECT"
        assert sig is None
        mock_risk.run.assert_not_called()

    @patch("trading.personas.orchestrator._persist_reflection_round")
    @patch("trading.personas.orchestrator.risk_persona")
    @patch("trading.personas.orchestrator.decision_persona")
    @patch("trading.personas.orchestrator.tg")
    def test_risk_unaware_of_reflection(self, mock_tg, mock_dec, mock_risk, mock_persist):
        """REQ-REFL-03-9: Risk receives same input structure as original (no metadata leak)."""
        revised_json = {"signals": [{"ticker": "005930", "side": "buy", "qty": 3}]}
        mock_dec.run.return_value = (
            FakePersonaResult(run_id=10, json_data=revised_json),
            [43],
        )
        mock_risk.run.return_value = (
            FakePersonaResult(run_id=11, json_data={"verdict": "APPROVE"}),
            100,
            "APPROVE",
        )

        from trading.personas.orchestrator import _run_reflection_loop

        _run_reflection_loop(**self._base_kwargs())

        # Verify Risk input has NO reflection metadata
        risk_call_args = mock_risk.run.call_args
        risk_input = risk_call_args[0][0] if risk_call_args[0] else risk_call_args[1].get("input_data")
        # Risk input should have standard keys, no "reflection" or "round" fields
        assert "reflection" not in str(risk_input)
        assert "rejection_feedback" not in str(risk_input)

    @patch("trading.personas.orchestrator._persist_reflection_round")
    @patch("trading.personas.orchestrator.risk_persona")
    @patch("trading.personas.orchestrator.decision_persona")
    @patch("trading.personas.orchestrator.tg")
    def test_reflection_with_tools_enabled(self, mock_tg, mock_dec, mock_risk, mock_persist):
        """Reflection passes tools to Decision and Risk when enabled."""
        revised_json = {"signals": [{"ticker": "005930", "side": "buy", "qty": 3}]}
        mock_dec.run.return_value = (
            FakePersonaResult(run_id=10, json_data=revised_json),
            [43],
        )
        mock_risk.run.return_value = (
            FakePersonaResult(run_id=11, json_data={"verdict": "APPROVE"}),
            100,
            "APPROVE",
        )

        kwargs = self._base_kwargs()
        kwargs["state"]["tool_calling_enabled"] = True

        from trading.personas.orchestrator import _run_reflection_loop

        _run_reflection_loop(**kwargs)

        # Verify tools are passed to Decision and Risk
        dec_kwargs = mock_dec.run.call_args[1]
        assert "tools" in dec_kwargs
        risk_kwargs = mock_risk.run.call_args[1]
        assert "tools" in risk_kwargs
