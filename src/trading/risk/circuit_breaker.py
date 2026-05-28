"""Circuit breaker (REQ-RISK-05-3) — sets halt_state when any limit is breached.

Caller invokes `trip()` when a hard breach is detected. While halt_state=true,
order.submit_order() must reject all submissions (handled at orchestrator level).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone

from trading.alerts.telegram import system_briefing
from trading.db.session import audit, get_system_state, update_system_state

LOG = logging.getLogger(__name__)

# SPEC-TRADING-031 REQ-031-6: default halt-cycle briefing cooldown = 6h.
# Documented in .moai/config/sections/scheduler.yaml
# (halt_notify_cooldown_seconds), but that file is NOT loaded at runtime — every
# scheduler.yaml value is a documented mirror of a code default (see the watcher
# DEFAULT_* constants), so the constant here is the runtime source of truth.
HALT_NOTIFY_COOLDOWN_SECONDS: int = 21600


def is_halted() -> bool:
    return bool(get_system_state()["halt_state"])


# @MX:ANCHOR: SPEC-TRADING-031 cooldown gate for the per-cycle "매매 정지" briefing
# @MX:REASON: fan_in == 3 (pre_market / intraday / event-trigger halt gates call
#             this in place of tg.system_briefing); the throttle/first-cycle
#             invariant lives here so all three gates stay consistent.
# @MX:SPEC: SPEC-TRADING-031
def maybe_notify_halt(
    cooldown_seconds: int | None = None,
    now_provider: Callable[[], datetime] | None = None,
) -> bool:
    """Send the "매매 정지" halt-cycle briefing at most once per cooldown.

    Decision rule (REQ-031-1, REQ-031-2):
      - if system_state.halt_notified_at IS NULL (first cycle of a halt episode)
        OR now - halt_notified_at >= cooldown -> send + stamp halt_notified_at=now,
        return True (sent);
      - otherwise -> skip (throttled), return False.

    State lives in system_state so the cooldown survives a restart (REQ-031-1c).
    Telegram failures are swallowed (fail-safe) so the halt gate's skip/return is
    never blocked by a transient send error.

    Args:
        cooldown_seconds: Override the default cooldown (REQ-031-6 / AC-6 seam).
        now_provider: Test seam — returns the current tz-aware datetime.

    Returns:
        True if the briefing was sent this call, False if throttled.
    """
    cooldown = HALT_NOTIFY_COOLDOWN_SECONDS if cooldown_seconds is None else cooldown_seconds
    now = (now_provider or (lambda: datetime.now(timezone.utc)))()

    last = get_system_state().get("halt_notified_at")
    if last is not None and (now - last).total_seconds() < cooldown:
        return False

    update_system_state(halt_notified_at=now, updated_by="circuit_breaker")
    try:
        system_briefing("매매 정지", "halt_state=true 이므로 매매 차단됨")
    except Exception:  # noqa: BLE001
        LOG.exception("halt-cycle telegram briefing failed")
    return True


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
    # SPEC-TRADING-031 REQ-031-3: clear the halt-cycle throttle atomically with
    # the halt release so the next halt episode's first cycle notifies immediately.
    update_system_state(halt_state=False, halt_notified_at=None, updated_by=actor)
    audit("CIRCUIT_BREAKER_RESET", actor=actor, details={})
    try:
        system_briefing("회로차단 해제", "halt_state=false. 매매 재개 가능.")
    except Exception:  # noqa: BLE001
        LOG.exception("circuit-breaker reset briefing failed")
