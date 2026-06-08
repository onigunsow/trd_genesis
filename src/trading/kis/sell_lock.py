"""SPEC-TRADING-042 Module C — sell in-flight lock + cooldown (REQ-042-C1..C3).

Closes RC-3 (2026-06-08): 033780 (KT&G -7.3%) was decided for SELL **4 times in
5 minutes** (09:04 / 09:31 / 09:32 / 09:34) because the position watchdog
(``*/5``) and the persona orchestrator BOTH evaluate the same stop-loss and
neither knew the other had already fired. One was synthetic-filled, two were
rejected — a duplicate / racing sell path.

This module is the SINGLE shared in-flight lock used by BOTH firing paths
(``position_watchdog`` and ``orchestrator._execute_signal``). The lock has two
independent legs and a ticker is locked when EITHER holds:

- **submitted leg** — an unresolved ``submitted`` SELL order for the ticker is
  in flight at the broker. It clears automatically when Module B's resolver
  (``order_resolver.resolve_stuck_orders``) converges that order to a terminal
  state (filled / expired / cancelled). This is what guards the long
  09:04→09:31 gap where a pure time cooldown alone would miss (RC-2 leak).
- **cooldown leg** — a recent-fire marker (``position_action_markers``
  action='sell_inflight') within ``SELL_INFLIGHT_COOLDOWN_SECONDS``. This dedupes
  the rapid same-window cluster (09:31 / 09:32 / 09:34) and the brief gap right
  after an order resolves.

REQ-042-C2 (capital-preservation): the lock must NOT permanently block a genuine
stop-loss. A NEW exit signal that arrives AFTER the prior order resolves (the
submitted leg clears via Module B) AND the cooldown elapses is allowed through.
The marker is DB-backed (``position_action_markers``, reused from mig 028 — no
new migration; the table's ``action`` is free-form TEXT and it already carries a
``created_at``) so the lock survives a restart and is idempotent (REQ-042-C3).

Fail-open invariant (capital-preservation hard rule): a lock that WRONGLY blocks
a real stop-loss is worse than a duplicate. On ANY DB error the lock reads as
clear (allow the sell); a marker write failure is best-effort and never raised.

LIVE safety: this module only READS the orders table + reads/writes the
``position_action_markers`` cache row. It never POSTs a KIS order and never calls
``order.submit_order``; ``order.py`` / ``account.py`` / ``fills.py`` are untouched.

See ``.moai/specs/SPEC-TRADING-042-broker-truth-ledger/`` for the full SPEC.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

import pytz

from trading.db.session import audit, connection

LOG = logging.getLogger(__name__)
KST = pytz.timezone("Asia/Seoul")

# REQ-042-C1: a fired sell holds the cooldown leg of the lock for this window.
# 300s == one watchdog `*/5` cycle: long enough to dedupe a same-cycle
# persona+watchdog double and the rapid 09:31/09:32/09:34 cluster, while the
# submitted leg (an open KIS order) covers longer in-flight periods independent
# of time. Capital-preservation: kept tight so a genuine new exit after the order
# resolves is never long-blocked (REQ-042-C2).
SELL_INFLIGHT_COOLDOWN_SECONDS: float = 300.0

# Reuses position_action_markers (mig 028) with a new free-form action value —
# no migration. UNIQUE(trading_day, ticker, action) makes the lock idempotent and
# the per-day key naturally resets each trading day.
_SELL_INFLIGHT_ACTION = "sell_inflight"


def _now() -> datetime:
    """Current UTC time (test seam — patch to fix 'now')."""
    return datetime.now(pytz.UTC)


def _today_kst() -> date:
    """Current KST calendar date — the marker trading_day key (test seam)."""
    return datetime.now(KST).date()


def _has_unresolved_submitted_sell(ticker: str) -> bool:
    """True if an unresolved ``submitted`` SELL order exists for ``ticker``.

    This is the broker-in-flight leg. Module B's resolver converges such orders
    to a terminal state, so this leg self-clears once the order resolves.
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM orders "
            "WHERE ticker = %s AND side = 'sell' AND status = 'submitted' LIMIT 1",
            (ticker,),
        )
        return cur.fetchone() is not None


def _marker_created_at(ticker: str) -> datetime | None:
    """``created_at`` of the ticker's sell_inflight marker for today, or None."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT created_at FROM position_action_markers "
            "WHERE trading_day = %s AND ticker = %s AND action = %s LIMIT 1",
            (_today_kst(), ticker, _SELL_INFLIGHT_ACTION),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _cooldown_active(ticker: str, now: datetime) -> bool:
    """True if a recent-fire marker is within ``SELL_INFLIGHT_COOLDOWN_SECONDS``."""
    created = _marker_created_at(ticker)
    if created is None:
        return False
    age = (now - created).total_seconds()
    return age < SELL_INFLIGHT_COOLDOWN_SECONDS


# @MX:WARN: in-flight lock READ — a MONEY-path guard. A false True here SUPPRESSES
# a real stop-loss; a false False permits a duplicate sell.
# @MX:REASON: capital-preservation hard rule — wrongly blocking a stop is worse
# than a duplicate, so this fails OPEN (returns False = unlocked) on any DB error
# (REQ-042-C1/C2). The submitted leg clears via Module B's resolver; the cooldown
# leg clears by time — together they never permanently block a genuine exit.
def is_sell_locked(ticker: str, *, now: datetime | None = None) -> bool:
    """True if a sell for ``ticker`` is pending/in-flight (REQ-042-C1).

    Locked when EITHER an unresolved ``submitted`` SELL exists for the ticker OR a
    recent-fire marker is still inside the cooldown window. Fails OPEN (returns
    False) on any error so a stop-loss is never wrongly suppressed.
    """
    ref = now or _now()
    try:
        if _has_unresolved_submitted_sell(ticker):
            return True
        return _cooldown_active(ticker, ref)
    except Exception:
        LOG.warning(
            "SPEC-042 sell_lock: is_sell_locked read failed for %s — failing OPEN "
            "(allow the sell; a blocked stop-loss is worse than a duplicate)",
            ticker,
        )
        return False


def set_sell_inflight(ticker: str) -> None:
    """Mark ``ticker`` as having just fired a sell (take/refresh the lock).

    UPSERTs the marker with ``created_at = NOW()`` so a re-fire slides the cooldown
    window forward (idempotent, single row — REQ-042-C3). Best-effort: a write
    failure is logged but NEVER raised, so a marker hiccup cannot crash the sell
    path. Audits ``SELL_INFLIGHT_LOCKED`` (REQ-042-D3).
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO position_action_markers (trading_day, ticker, action) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (trading_day, ticker, action) "
                "DO UPDATE SET created_at = NOW()",
                (_today_kst(), ticker, _SELL_INFLIGHT_ACTION),
            )
    except Exception:
        LOG.warning(
            "SPEC-042 sell_lock: set_sell_inflight write failed for %s "
            "(best-effort; the sell already proceeded)", ticker,
        )
        return
    audit(
        "SELL_INFLIGHT_LOCKED",
        actor="sell_lock",
        details={"ticker": ticker, "cooldown_seconds": SELL_INFLIGHT_COOLDOWN_SECONDS},
    )


def clear_sell_inflight(ticker: str) -> None:
    """Delete ``ticker``'s sell_inflight marker (lock lifecycle end).

    Called when a stale marker is observed past the cooldown with no open order —
    the genuine end of the lock. Best-effort; audits ``SELL_INFLIGHT_CLEARED``.
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM position_action_markers "
                "WHERE trading_day = %s AND ticker = %s AND action = %s",
                (_today_kst(), ticker, _SELL_INFLIGHT_ACTION),
            )
    except Exception:
        LOG.warning("SPEC-042 sell_lock: clear_sell_inflight failed for %s", ticker)
        return
    audit("SELL_INFLIGHT_CLEARED", actor="sell_lock", details={"ticker": ticker})


# @MX:ANCHOR: the SINGLE shared sell-dedup gate. fan_in >= 2 — both firing paths
# (``position_watchdog`` direct exit + ``orchestrator._execute_signal`` sell
# branch) call this before a real KIS sell so the lock logic is NOT duplicated.
# @MX:REASON: RC-3 (2026-06-08) — watchdog + persona both fired 033780's stop 4x
# in 5 min. A single shared gate is the invariant that exactly ONE sell fires
# while a position is in-flight (REQ-042-C1). Returns True = the caller may sell;
# False = SUPPRESS (a duplicate). Fails OPEN (allow) via is_sell_locked so a
# stop-loss is never wrongly blocked (capital-preservation, REQ-042-C2).
def guard_sell(ticker: str, *, actor: str, now: datetime | None = None) -> bool:
    """Shared in-flight gate: True to proceed with the sell, False to suppress.

    - If the ticker is locked (in-flight / cooled-down) → audit
      ``SELL_SUPPRESSED_DUPLICATE`` and return False (REQ-042-C1).
    - Else, if a STALE marker lingers (past cooldown, order resolved) → clear it
      (``SELL_INFLIGHT_CLEARED``) so the lock lifecycle is closed, and return True
      (REQ-042-C2 — a genuine new exit is allowed).
    - Else return True.
    """
    ref = now or _now()
    if is_sell_locked(ticker, now=ref):
        try:
            audit(
                "SELL_SUPPRESSED_DUPLICATE",
                actor=actor,
                details={
                    "ticker": ticker,
                    "reason": "sell already pending/in-flight (REQ-042-C1)",
                },
            )
        except Exception:
            LOG.warning("SPEC-042 sell_lock: suppress audit failed for %s", ticker)
        LOG.info(
            "SPEC-042 sell_lock: SUPPRESS duplicate sell for %s (actor=%s)",
            ticker, actor,
        )
        return False

    # Not locked. If a stale marker survives (the cooldown elapsed and the order
    # resolved), close the lock lifecycle so the next set re-opens cleanly and the
    # CLEARED transition is auditable (REQ-042-C2/D3). Best-effort.
    try:
        if _marker_created_at(ticker) is not None:
            clear_sell_inflight(ticker)
    except Exception:
        LOG.warning("SPEC-042 sell_lock: stale-marker clear check failed for %s", ticker)
    return True
