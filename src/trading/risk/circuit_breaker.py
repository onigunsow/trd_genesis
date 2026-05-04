"""Circuit breaker (REQ-RISK-05-3) — sets halt_state when any limit is breached.

Caller invokes `trip()` when a hard breach is detected. While halt_state=true,
order.submit_order() must reject all submissions (handled at orchestrator level).
"""

from __future__ import annotations

import logging

from trading.alerts.telegram import system_briefing
from trading.db.session import audit, get_system_state, update_system_state

LOG = logging.getLogger(__name__)


def is_halted() -> bool:
    return bool(get_system_state()["halt_state"])


def trip(reason: str, details: dict | None = None) -> None:
    """Engage halt_state=true and broadcast on Telegram."""
    update_system_state(halt_state=True, updated_by="circuit_breaker")
    audit("CIRCUIT_BREAKER_TRIP", actor="circuit_breaker",
          details={"reason": reason, **(details or {})})
    try:
        system_briefing(
            "회로차단",
            f"⚠ 회로차단기 발동\n사유: {reason}\n신규 주문 차단됨. /resume 명령어로 해제 가능.",
        )
    except Exception:  # noqa: BLE001
        LOG.exception("circuit-breaker telegram briefing failed")


def reset(actor: str = "operator") -> None:
    update_system_state(halt_state=False, updated_by=actor)
    audit("CIRCUIT_BREAKER_RESET", actor=actor, details={})
    try:
        system_briefing("회로차단 해제", "halt_state=false. 매매 재개 가능.")
    except Exception:  # noqa: BLE001
        LOG.exception("circuit-breaker reset briefing failed")
