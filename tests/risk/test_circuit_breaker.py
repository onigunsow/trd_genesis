"""Direct unit tests for risk/circuit_breaker.py trip()/reset() (REQ-064-B9).

Before SPEC-TRADING-064, ``trip()``/``reset()`` had no dedicated unit test —
only integration-level coverage via test_halt_notify_throttle.py (which
targets ``maybe_notify_halt``). This suite is the characterization baseline
required before REQ-064-B3 (trip() decision_id threading) and REQ-064-B8
(reset() decision_scope exemption) touch this file.

@MX:SPEC: SPEC-TRADING-064
"""

from __future__ import annotations

from unittest.mock import patch

from trading.risk import circuit_breaker as cb


class TestTrip:
    def test_trip_sets_halt_state_and_audits(self):
        with (
            patch.object(cb, "update_system_state") as upd,
            patch.object(cb, "audit") as mock_audit,
            patch.object(cb, "system_briefing") as brief,
        ):
            cb.trip(reason="pre-order limit breach", details={"breaches": ["daily_loss: x"]})

        upd.assert_called_once_with(halt_state=True, updated_by="circuit_breaker")
        mock_audit.assert_called_once()
        args, kwargs = mock_audit.call_args
        assert args[0] == "CIRCUIT_BREAKER_TRIP"
        assert kwargs["details"]["reason"] == "pre-order limit breach"
        assert kwargs["details"]["breaches"] == ["daily_loss: x"]
        brief.assert_called_once()

    def test_trip_merges_extra_details_at_top_level(self):
        """REQ-064-B3: caller-supplied `details` (e.g. decision_id) lands at
        the audit payload's top level — trip() already free-form merges it."""
        with (
            patch.object(cb, "update_system_state"),
            patch.object(cb, "audit") as mock_audit,
            patch.object(cb, "system_briefing"),
        ):
            cb.trip(reason="pre-order limit breach", details={"decision_id": 777})

        assert mock_audit.call_args.kwargs["details"]["decision_id"] == 777

    def test_trip_swallows_telegram_failure(self):
        with (
            patch.object(cb, "update_system_state"),
            patch.object(cb, "audit"),
            patch.object(cb, "system_briefing", side_effect=RuntimeError("network")),
        ):
            cb.trip(reason="x")  # must not raise


class TestReset:
    def test_reset_clears_halt_state_and_throttle(self):
        with (
            patch.object(cb, "update_system_state") as upd,
            patch.object(cb, "audit") as mock_audit,
            patch.object(cb, "system_briefing"),
        ):
            cb.reset(actor="operator")

        upd.assert_called_once_with(
            halt_state=False, halt_notified_at=None, updated_by="operator"
        )
        mock_audit.assert_called_once_with(
            "CIRCUIT_BREAKER_RESET", actor="operator",
            details={"decision_scope": "operator"},
        )

    def test_reset_is_tier5_exempt_no_decision_id(self):
        """REQ-064-B8: /resume is an operator action — decision_scope, no decision_id."""
        with (
            patch.object(cb, "update_system_state"),
            patch.object(cb, "audit") as mock_audit,
            patch.object(cb, "system_briefing"),
        ):
            cb.reset()

        details = mock_audit.call_args.kwargs["details"]
        assert details["decision_scope"] == "operator"
        assert "decision_id" not in details
