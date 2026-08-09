"""SPEC-TRADING-064 REQ-064-B2 — record_breach top-level decision_id.

``LIMIT_BREACH`` (risk/limits.py:248) previously nested decision_id one level
too deep (``details.context.decision_id``), breaking the single top-level
``details.decision_id`` correlation contract (REQ-064-B1). This locks in the
fix: top-level decision_id added, existing ``details.context`` payload (the
full ``signal`` dict, including rationale text) preserved byte-for-byte.

@MX:SPEC: SPEC-TRADING-064
"""

from __future__ import annotations

from unittest.mock import patch

from trading.risk.limits import LimitCheck, record_breach


def test_record_breach_emits_top_level_decision_id_and_preserves_context():
    chk = LimitCheck(passed=False, breaches=["avg_down: 086790 물타기 매수 거부"], warnings=[])
    sig = {"ticker": "086790", "side": "buy", "qty": 3, "rationale": "단기 반등 기대"}
    context = {"signal": sig, "decision_id": 555}

    with patch("trading.risk.limits.audit") as mock_audit:
        record_breach(chk, context)

    mock_audit.assert_called_once()
    args, kwargs = mock_audit.call_args
    assert args[0] == "LIMIT_BREACH"
    details = kwargs["details"]
    # REQ-064-B1: top-level decision_id is the single correlation key.
    assert details["decision_id"] == 555
    # REQ-064-B2: existing context payload preserved (observability regression 0).
    assert details["context"] == context
    assert details["context"]["signal"]["rationale"] == "단기 반등 기대"
    assert details["breaches"] == chk.breaches


def test_record_breach_decision_id_null_when_absent_from_context():
    """No fabrication (HARD constraint): missing decision_id -> null, not omitted."""
    chk = LimitCheck(passed=False, breaches=["total_invested: 초과"], warnings=[])
    context = {"signal": {"ticker": "005930"}}

    with patch("trading.risk.limits.audit") as mock_audit:
        record_breach(chk, context)

    details = mock_audit.call_args.kwargs["details"]
    assert details["decision_id"] is None
    assert "decision_id" in details
