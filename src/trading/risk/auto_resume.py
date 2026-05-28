"""Pre-market auto-resume — re-enable trading after a benign automatic halt.

SPEC-TRADING-032: a 07:25 KST weekday scheduler job (registered just before the
07:30 pre_market cycle) auto-resumes trading IF the active halt was caused by a
*benign automatic* limit breach (daily_count / single_order / per_ticker / total
invested) and NOT by a real loss (daily_loss) nor a manual /halt. Otherwise it
leaves the halt in place and sends a notify-only "수동 검토 필요" briefing so an
operator reviews it.

The halt cause is derived from ``audit_log`` (single source of truth): the most
recent ``CIRCUIT_BREAKER_TRIP`` that has not been superseded by a later
``CIRCUIT_BREAKER_RESET``. This module only *calls* ``circuit_breaker.reset()``
and *queries* audit_log — it never modifies trip()/reset()/limits logic
(REQ-032-6).

@MX:SPEC: SPEC-TRADING-032
"""

from __future__ import annotations

import logging

from trading.alerts.telegram import system_briefing
from trading.db.session import audit, connection, get_system_state
from trading.risk import circuit_breaker

LOG = logging.getLogger(__name__)

# Trip-reason literal written by the automatic pre-order limit gate
# (orchestrator.py: trip(reason="pre-order limit breach", details={"breaches": ...})).
_AUTO_LIMIT_REASON = "pre-order limit breach"
# Prefix of a real-loss breach string (limits.py: "daily_loss: ..."). A loss is a
# capital-preservation event and is never auto-resumed (REQ-032-3b).
_DAILY_LOSS_PREFIX = "daily_loss"
# Prefix of every manual halt reason ("manual /halt", "manual cli /halt") —
# capital-preservation hard rule: never auto-resume (REQ-032-3a).
_MANUAL_PREFIX = "manual"


def classify_halt(
    halt_state: bool, active_trip: dict | None
) -> tuple[bool, str, str]:
    """Decide whether a halt is a benign automatic limit breach safe to resume.

    Pure function (no I/O) so every branch is unit-testable. ``active_trip`` is
    the ``details`` dict of the active ``CIRCUIT_BREAKER_TRIP`` audit row, i.e.
    ``{"reason": <str>, "breaches": [<str>, ...]}`` for automatic trips or
    ``{"reason": "manual /halt", "actor": <str>}`` for manual halts.

    Returns:
        ``(should_resume, cause, detail)``:
          - ``should_resume`` — True only for a benign automatic limit breach.
          - ``cause`` — short machine-friendly classification token.
          - ``detail`` — human-readable context (reason or breach strings).
    """
    # REQ-032-4: not halted -> nothing to do.
    if not halt_state:
        return (False, "not_halted", "")

    # REQ-032-3c: halt is engaged but no active trip could be identified.
    if not active_trip:
        return (False, "undeterminable", "활성 트립 행 없음")

    reason = str(active_trip.get("reason", ""))

    # REQ-032-3a: manual halt — capital-preservation hard rule.
    if reason.startswith(_MANUAL_PREFIX):
        return (False, "manual", reason)

    # REQ-032-3c: only the automatic limit-breach reason is eligible.
    if reason != _AUTO_LIMIT_REASON:
        return (False, "unknown_reason", reason)

    breaches = active_trip.get("breaches")
    # REQ-032-3c: malformed/missing/empty breaches -> defensive HOLD.
    if not isinstance(breaches, list) or not breaches:
        return (False, "undeterminable", "breaches 형식 불량")

    # REQ-032-3b: any real-loss breach (even mixed with benign ones) -> HOLD.
    if any(str(b).startswith(_DAILY_LOSS_PREFIX) for b in breaches):
        return (False, "daily_loss", "; ".join(str(b) for b in breaches))

    # REQ-032-2: benign automatic limit breach (no loss) -> resume.
    prefixes = sorted({str(b).split(":", 1)[0].strip() for b in breaches})
    cause = ",".join(prefixes)
    return (True, cause, "; ".join(str(b) for b in breaches))


def _fetch_active_trip() -> dict | None:
    """Return the active trip's ``details`` dict, or None if undeterminable.

    Defensive identification (SPEC-TRADING-032 Q-4): read the latest
    ``CIRCUIT_BREAKER_TRIP`` and the latest ``CIRCUIT_BREAKER_RESET`` from
    audit_log. If there is no TRIP, or a RESET is at least as recent as the TRIP,
    the cause is undeterminable (return None). Otherwise the TRIP is active.

    audit_log is ordered by ``ts`` (TIMESTAMPTZ; index ``audit_log_ts_idx``),
    NOT ``created_at``. Reuses the ``session.connection()`` context manager
    (precedent: news/intelligence/scheduler.py).
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT event_type, ts, details
              FROM audit_log
             WHERE event_type IN ('CIRCUIT_BREAKER_TRIP', 'CIRCUIT_BREAKER_RESET')
             ORDER BY ts DESC
             LIMIT 1
            """
        )
        row = cur.fetchone()

    # No trip/reset history at all, or the newest event is a RESET (halt was
    # released after the last trip) -> cannot attribute the current halt.
    if not row or row["event_type"] != "CIRCUIT_BREAKER_TRIP":
        return None

    details = row.get("details")
    return details if isinstance(details, dict) else None


def run_premarket_auto_resume() -> None:
    """Auto-resume the trading halt iff its cause is a benign automatic breach.

    Decision flow (REQ-032-2 .. REQ-032-5):
      1. Read system_state; if not halted -> log and return (NO telegram, AC-6).
      2. Identify the active trip from audit_log (defensive, Q-4).
      3. Classify; on benign automatic breach -> reset + "자동 재개" briefing +
         resumed audit. Otherwise -> "수동 검토 필요" briefing + held audit.

    Telegram failures are swallowed so the scheduled job never crashes
    (mirrors circuit_breaker's try/except precedent).
    """
    state = get_system_state()
    if not state.get("halt_state"):
        LOG.info("premarket_auto_resume: 정지 아님 — 자동 재개 스킵")
        return

    active_trip = _fetch_active_trip()
    should_resume, cause, detail = classify_halt(True, active_trip)

    if should_resume:
        circuit_breaker.reset(actor="auto_resume_premarket")
        LOG.info("premarket_auto_resume: 자동 재개 (cause=%s)", cause)
        _notify("자동 재개", f"장 시작 전 자동 매매 재개 (사유: {cause})")
        audit(
            "AUTO_RESUME_PREMARKET",
            actor="auto_resume_premarket",
            details={"decision": "resumed", "cause": cause, "detail": detail},
        )
        return

    LOG.info("premarket_auto_resume: 재개 보류 (cause=%s)", cause)
    _notify(
        "수동 검토 필요",
        f"자동 재개 보류 (사유: {cause}). 수동 확인 필요.",
    )
    audit(
        "AUTO_RESUME_PREMARKET",
        actor="auto_resume_premarket",
        details={"decision": "held", "cause": cause, "detail": detail},
    )


def _notify(category: str, message: str) -> None:
    """Send a system briefing, swallowing telegram failures (fail-safe)."""
    try:
        system_briefing(category, message)
    except Exception:  # noqa: BLE001
        LOG.exception("premarket_auto_resume telegram briefing failed")
