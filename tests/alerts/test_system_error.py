"""SPEC-TRADING-064 REQ-064-B8 — SYSTEM_ERROR is a TIER 6 permanent exemption.

Fires for cross-cutting failures (Micro/Decision persona crash, etc.) that
happen before a decision_id can exist. Must carry decision_scope="system",
must NOT carry decision_id.

@MX:SPEC: SPEC-TRADING-064
"""

from __future__ import annotations

from unittest.mock import patch

from trading.alerts import telegram


def test_system_error_carries_decision_scope_system():
    with (
        patch.object(telegram, "_send_raw"),
        patch("trading.db.session.audit") as mock_audit,
    ):
        telegram.system_error("decision_persona", RuntimeError("boom"), context="cycle=intraday")

    mock_audit.assert_called_once()
    args, kwargs = mock_audit.call_args
    assert args[0] == "SYSTEM_ERROR"
    details = kwargs["details"]
    assert details["decision_scope"] == "system"
    assert "decision_id" not in details
    assert details["error_type"] == "RuntimeError"
