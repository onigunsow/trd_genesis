"""SPEC-TRADING-064 REQ-064-B8 — TIER 5/6 permanent exemption lock.

Six audit events are structurally batch/account/operator/cleanup/system-scoped
and have no natural single decision_id. This SPEC fixes their `decision_scope`
mapping in ``trading.db.session.DECISION_SCOPE_EXEMPT_EVENTS`` as the single
source of truth so a future contributor cannot "fix" what looks like a
missing decision_id without first seeing (and having to edit) this contract.

Per-event production-code assertions live next to each producer
(tests/risk/test_circuit_breaker.py, tests/personas/test_orchestrator.py,
tests/kis/test_broker_truth.py, tests/kis/test_order_resolver.py,
tests/watchers/test_sell_inflight_lock.py, tests/alerts/test_system_error.py).
This file locks the SSOT contract itself.

@MX:SPEC: SPEC-TRADING-064
"""

from __future__ import annotations

from trading.db.session import DECISION_SCOPE_EXEMPT_EVENTS


def test_exemption_list_is_exactly_the_spec_tier_5_6_six_events():
    assert set(DECISION_SCOPE_EXEMPT_EVENTS) == {
        "SILENT_MODE_ON",
        "CIRCUIT_BREAKER_RESET",
        "INTRADAY_RECONCILE",
        "STUCK_ORDER_CLEANUP",
        "SELL_INFLIGHT_CLEARED",
        "SYSTEM_ERROR",
    }


def test_exemption_scope_values_match_spec_mapping():
    assert DECISION_SCOPE_EXEMPT_EVENTS == {
        "SILENT_MODE_ON": "aggregate",
        "CIRCUIT_BREAKER_RESET": "operator",
        "INTRADAY_RECONCILE": "account",
        "STUCK_ORDER_CLEANUP": "cleanup",
        "SELL_INFLIGHT_CLEARED": "cleanup",
        "SYSTEM_ERROR": "system",
    }
