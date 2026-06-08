"""SPEC-TRADING-042 Module B — order-state resolver / timeout (REQ-042-B1..B3).

Closes RC-2 (2026-06-01..06-08): an order accepted by KIS (``rt_cd=0`` →
``status='submitted'``) whose synthetic/fill step then threw was left in
``submitted`` forever — ``submit_order`` audits ``ORDER_SYNTHETIC_ERROR`` and
returns success, but nothing ever resolved the order-state ledger. Five SELL
orders leaked this way (086790/055550/064350/000270/071050). A stuck SELL also
poisons downstream invariants: an in-flight lock (Module C) keyed on unresolved
``submitted`` would never release, and realized-P&L aggregation (Module D) would
miss the round-trip.

This module is the resolver: any order in ``submitted`` beyond a bounded window
is converged **deterministically** to a terminal state.

Resolution per stuck order (REQ-042-B1/B3):

1. Row-lock the order and re-read its status. If it is no longer ``submitted``
   (a concurrent reconcile/cleanup already resolved it), skip — never
   double-transition (REQ-042-B3, idempotent).
2. Attempt a fill confirmation through the **single** Module-A seam
   ``broker_truth.confirm_fills`` (paper → balance reconcile; live → the guarded
   execution-inquiry seam, which raises ``BrokerFillInquiryNotImplemented`` until
   the TR id is verified). This is the ONLY fill-confirmation path — the resolver
   never opens a parallel one and never fabricates a fill (REQ-042-A5/B3).
3. Re-read the status. If the confirmation advanced it to ``filled``/``partial``
   (reconcile can advance an open BUY), it is resolved — audit ``ORDER_RESOLVED``.
4. Otherwise the window has elapsed and the fill could not be confirmed → mark
   the order ``expired`` (NOT ``filled`` — we never fabricate; NOT ``cancelled`` —
   we did not issue a verified KIS cancel) and audit ``STUCK_ORDER_EXPIRED``.

Capital-preservation: marking a stuck SELL ``expired`` does NOT drop the exit
intent. It only converges the order-state ledger. The genuine held position
(KIS truth) is re-evaluated on the next decision cycle, where Module A's
intraday reconcile + phantom-sell clamp will re-fire a fresh stop if still
warranted. The resolver's job is to unstick the ledger, not to re-issue exits.

LIVE safety: this module never POSTs a KIS order and never calls
``order.submit_order``. ``order.py`` / ``fills.py`` / ``account.py`` are untouched
(byte-for-byte unchanged). The live fill-inquiry stays a guarded seam — an
unconfirmable live order is ``expired`` and loudly audited for a human, never
fabricated ``filled``.

See ``.moai/specs/SPEC-TRADING-042-broker-truth-ledger/`` for the full SPEC.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from trading.db.session import connection
from trading.kis.broker_truth import BrokerFillInquiryNotImplemented, confirm_fills

LOG = logging.getLogger(__name__)

# REQ-042-B1: a SELL/BUY that does not confirm a fill within this window is
# considered stuck. 15 min is generous relative to the reconcile cadence (every
# sell cycle + after each submission) yet tight enough to unstick within a
# trading session, so a normally-filling order is never prematurely expired.
SUBMITTED_RESOLVE_WINDOW_SECONDS: float = 900.0

# A status that is already terminal (or advanced) must never be re-transitioned
# (REQ-042-B3). 'partial' is treated as advanced — reconcile owns its progression.
_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"filled", "partial", "rejected", "cancelled", "expired", "error"}
)


def _now() -> datetime:
    return datetime.now(UTC)


def _attempt_fill_confirmation(client: Any) -> None:
    """Best-effort fill confirmation through the single Module-A seam.

    Paper reconciles against the KIS balance; live raises the guarded
    ``BrokerFillInquiryNotImplemented`` (TR id unverified). Either way this NEVER
    fabricates a fill — it only gives an already-executed order a chance to be
    confirmed before the window forces an ``expired`` terminal state.
    """
    try:
        confirm_fills(client)
    except BrokerFillInquiryNotImplemented:
        # Live execution-inquiry not yet wired — we cannot confirm a live fill,
        # so an unconfirmable live order proceeds to 'expired' (never fabricated
        # filled). REQ-042-A5/B3.
        LOG.info(
            "SPEC-042 resolver: live fill confirmation is a guarded seam "
            "(unverified TR id); unconfirmable orders expire, never fabricated"
        )
    except Exception:
        LOG.exception(
            "SPEC-042 resolver: fill-confirmation attempt failed; "
            "proceeding to window-based resolution"
        )


# @MX:WARN: order-state resolver is a MONEY path — it writes terminal order
# statuses that gate downstream realized-P&L aggregation and the sell in-flight
# lock.
# @MX:REASON: RC-2 (2026-06-01..06-08) left 5 SELL orders stuck in 'submitted'
# forever (synthetic-fill threw, no resolver). The resolver converges every stuck
# order to a KIS-confirmed 'filled' or an honest 'expired' (REQ-042-B1/B3). It
# NEVER fabricates a fill and NEVER POSTs a KIS order — it only writes a terminal
# status for an order already accepted by KIS, so the live order path is unchanged.
def resolve_stuck_orders(
    client: Any,
    *,
    now: datetime | None = None,
    window_seconds: float = SUBMITTED_RESOLVE_WINDOW_SECONDS,
    order_ids: list[int] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Resolve every order stuck in ``submitted`` beyond ``window_seconds``.

    Parameters
    ----------
    now:
        Reference time (defaults to ``utcnow``); injectable for tests.
    window_seconds:
        Orders older than this (by ``ts``) and still ``submitted`` are resolved.
        Pass ``0`` (or use :func:`cleanup_stuck_orders`) to resolve regardless of
        age — the one-time cleanup of the 5 leaked orders (REQ-042-B2).
    order_ids:
        If given, restrict resolution to these order ids (still age-gated by
        ``window_seconds``). Used by the targeted cleanup path.
    dry_run:
        SELECT-only preview; no UPDATE/audit is written.

    Returns a summary dict: ``scanned``, ``resolved_filled`` (confirmed by KIS),
    ``resolved_expired`` (window-expired, unconfirmable), ``skipped``
    (already-terminal / concurrently-resolved), ``errors``, ``dry_run``.
    """
    reference = now or _now()
    cutoff = reference - timedelta(seconds=window_seconds)

    summary: dict[str, Any] = {
        "scanned": 0,
        "resolved_filled": 0,
        "resolved_expired": 0,
        "skipped": 0,
        "errors": 0,
        "dry_run": dry_run,
    }

    # ── Select candidate stuck orders (read-only, no lock held across the seam). ──
    try:
        with connection() as conn, conn.cursor() as cur:
            if order_ids:
                cur.execute(
                    """
                    SELECT id, ts, side, ticker, qty, status
                      FROM orders
                     WHERE status = 'submitted'
                       AND ts <= %s
                       AND id = ANY(%s)
                     ORDER BY ts ASC
                    """,
                    (cutoff, order_ids),
                )
            else:
                cur.execute(
                    """
                    SELECT id, ts, side, ticker, qty, status
                      FROM orders
                     WHERE status = 'submitted'
                       AND ts <= %s
                     ORDER BY ts ASC
                    """,
                    (cutoff,),
                )
            candidates = list(cur.fetchall())
    except Exception:
        summary["errors"] += 1
        LOG.exception("SPEC-042 resolver: candidate selection failed")
        return summary

    summary["scanned"] = len(candidates)
    if not candidates:
        return summary

    # ── One fill-confirmation attempt for the whole batch (single seam). ──
    # confirm_fills is account-wide (balance reconcile), so one call covers every
    # candidate; per-order re-reads below detect any that the reconcile advanced.
    if not dry_run:
        _attempt_fill_confirmation(client)

    for cand in candidates:
        try:
            _resolve_one(cand, summary, dry_run=dry_run, reference=reference)
        except Exception:
            summary["errors"] += 1
            LOG.exception(
                "SPEC-042 resolver: failed to resolve order id=%s", cand.get("id")
            )

    LOG.info(
        "SPEC-042 resolve_stuck_orders scanned=%d filled=%d expired=%d "
        "skipped=%d errors=%d dry_run=%s",
        summary["scanned"], summary["resolved_filled"], summary["resolved_expired"],
        summary["skipped"], summary["errors"], summary["dry_run"],
    )
    return summary


def _resolve_one(
    cand: dict[str, Any],
    summary: dict[str, Any],
    *,
    dry_run: bool,
    reference: datetime,
) -> None:
    """Resolve a single candidate inside its own row-locked transaction."""
    order_id = int(cand["id"])
    side = cand.get("side")
    ticker = cand.get("ticker")

    with connection() as conn, conn.cursor() as cur:
        # Re-read under FOR UPDATE so a concurrent reconcile/cleanup cannot make us
        # double-transition (REQ-042-B3). Status may have advanced since selection.
        cur.execute(
            "SELECT status FROM orders WHERE id = %s FOR UPDATE",
            (order_id,),
        )
        row = cur.fetchone() or {}
        current = str(row.get("status") or "")

        if current != "submitted":
            # Already terminal/advanced (e.g. the batch reconcile filled it, or a
            # prior cleanup run resolved it). Idempotent no-op.
            summary["skipped"] += 1
            if current in ("filled", "partial"):
                # The fill confirmation advanced it — record as a confirmed resolve.
                summary["resolved_filled"] += 1
                summary["skipped"] -= 1
                _audit(cur, "ORDER_RESOLVED", {
                    "order_id": order_id, "ticker": ticker, "side": side,
                    "resolved_status": current, "source": "fill_confirmation",
                }, dry_run=dry_run)
            return

        # Still submitted after the confirmation attempt → window-expire it. We do
        # NOT mark it 'filled' (no KIS confirmation — REQ-042-B3 no-arbitrary-fill)
        # and NOT 'cancelled' (no verified KIS cancel was issued).
        age_seconds = (reference - cand["ts"]).total_seconds() if cand.get("ts") else None

        if dry_run:
            LOG.info(
                "[DRY-RUN] SPEC-042 resolver: order id=%s %s %s would expire "
                "(age=%ss)", order_id, side, ticker, age_seconds,
            )
            summary["resolved_expired"] += 1
            return

        cur.execute(
            "UPDATE orders SET status = 'expired' WHERE id = %s AND status = 'submitted'",
            (order_id,),
        )
        summary["resolved_expired"] += 1
        _audit(cur, "STUCK_ORDER_EXPIRED", {
            "order_id": order_id,
            "ticker": ticker,
            "side": side,
            "age_seconds": age_seconds,
            "reason": "submitted beyond resolve window; fill not confirmed — "
                      "order-state ledger converged to 'expired' without "
                      "fabricating a fill (REQ-042-B1/B3). Genuine exit intent "
                      "is re-evaluated next cycle from KIS truth.",
        }, dry_run=dry_run)


def _audit(cur: Any, event_type: str, details: dict[str, Any], *, dry_run: bool) -> None:
    """Insert an audit_log row inside the caller's transaction (REQ-042-D3)."""
    if dry_run:
        return
    import json

    cur.execute(
        "INSERT INTO audit_log (event_type, actor, details) VALUES (%s, %s, %s::jsonb)",
        (event_type, "resolver", json.dumps(details)),
    )


# REQ-042-B2: the leaked orders from 2026-06-01..06-08. Resolving by explicit id
# keeps the one-time cleanup auditable and lets the operator re-run it safely.
STUCK_ORDER_TICKERS: tuple[str, ...] = (
    "086790", "055550", "064350", "000270", "071050",
)


def cleanup_stuck_orders(
    client: Any,
    *,
    order_ids: list[int] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """One-time cleanup of the currently-leaked stuck orders (REQ-042-B2).

    Resolves every order still in ``submitted`` regardless of age (``window=0``)
    through the SAME :func:`resolve_stuck_orders` logic — there is no parallel
    resolution path. Idempotent: a re-run finds no ``submitted`` rows (or only
    ones a prior run could not resolve) and transitions nothing further.

    ``order_ids`` restricts the cleanup to a specific set; when ``None`` every
    stuck ``submitted`` order is targeted (which, for the live DB, is exactly the
    5 leaked rows). Each order's terminal state is determined from KIS/DB evidence
    by the resolver — never blanket-marked filled.

    Emits a ``STUCK_ORDER_CLEANUP`` summary audit (REQ-042-D3).
    """
    summary = resolve_stuck_orders(
        client,
        window_seconds=0.0,
        order_ids=order_ids,
        dry_run=dry_run,
    )

    if not dry_run:
        from trading.db.session import audit

        audit("STUCK_ORDER_CLEANUP", actor="resolver", details={
            "scanned": summary["scanned"],
            "resolved_filled": summary["resolved_filled"],
            "resolved_expired": summary["resolved_expired"],
            "skipped": summary["skipped"],
            "errors": summary["errors"],
            "targeted_ids": order_ids,
        })

    return summary
