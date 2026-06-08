"""SPEC-TRADING-042 Module A — broker-truth single ledger + intraday reconcile.

The KIS account balance (and, on live, the execution inquiry) is the
**authoritative** source of held positions; the local ``positions`` table is a
cache that reconverges to it intraday. This module is the broker-truth layer
that sits in front of every sell decision so a phantom position (a local row
absent from the KIS account) can never drive a real KIS sell.

Root cause it closes (RC-1, 2026-06-08): the SPEC-039 paper synthetic fill
fabricates a LOCAL ``positions`` row for a paper BUY, but the KIS paper account
holds no such balance. A later SELL then routed a REAL KIS order which KIS
rejected with ``40240000:모의투자 잔고내역이 없습니다`` (000270 기아 -10.8% stop-loss).
Reconcile previously ran once daily (15:59), so the divergence persisted all day.

Public surface (REQ-042-A1..A5):

- ``confirm_held_qty(client, ticker)`` — KIS-confirmed held qty (single truth).
- ``clamp_sell_to_confirmed(client, ticker, qty)`` — PRE-submission clamp; a
  phantom (confirmed 0) clamps to 0 so no real KIS sell is ever POSTed; an
  over-sized request clamps to the confirmed qty. Capital-preservation: a
  genuine held position is never blocked.
- ``intraday_reconcile(client, *, reason, force)`` — TTL-throttled reconcile of
  the local cache against the KIS account (REQ-042-A2). ``force=True`` (used
  immediately after an order submission) bypasses the throttle.
- ``confirm_fills(client, *, source)`` — the SINGLE fill-confirmation code path
  (REQ-042-A3); paper branches to ``reconcile_from_balance``, live branches to a
  guarded seam for 주식일별주문체결조회 polling (not yet wired — see
  ``BrokerFillInquiryNotImplemented``; we never fabricate live fills, REQ-042-A5).

LIVE safety: this module never fabricates a fill. The clamp only READS the KIS
balance; the live fill-inquiry is a typed NotImplemented seam, not a silent
fall-through to the paper reconcile. The SPEC-039 ``mode != PAPER`` synthetic-
fill no-op and ``order.submit_order`` are untouched (byte-for-byte unchanged).

See ``.moai/specs/SPEC-TRADING-042-broker-truth-ledger/`` for the full SPEC.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from trading.config import TradingMode
from trading.db.session import audit
from trading.kis.account import balance
from trading.kis.fills import reconcile_from_balance

LOG = logging.getLogger(__name__)

# ADR-1 trade-off (correctness vs KIS rate limit): intraday reconcile is limited
# to "before a sell decision cycle" + "after each order submission", and a short
# TTL throttles repeat calls inside one cycle so we stay within KIS rate limits.
# 45s sits in the 30-60s band the SPEC bounds; tune via run-phase measurement.
INTRADAY_RECONCILE_TTL_SECONDS: float = 45.0

# Module-level throttle clock. Keyed nowhere — a single shared timestamp is
# enough because reconcile is account-wide (balance() returns the whole book).
_last_reconcile_monotonic: float | None = None


class BrokerFillInquiryNotImplemented(NotImplementedError):
    """Live fill confirmation (주식일별주문체결조회) is a guarded seam, not wired.

    REQ-042-A3/A5: live fill confirmation must use the KIS execution-inquiry
    poll, NOT the paper balance reconcile. The KIS TR id for that endpoint is
    not yet verified, so ``confirm_fills`` raises this typed error on live
    rather than fabricating a live fill or silently falling back to the paper
    path. Wiring the live inquiry is a drop-in at the marked seam below.
    """


def reset_reconcile_throttle() -> None:
    """Test seam — clear the intraday-reconcile throttle clock."""
    global _last_reconcile_monotonic
    _last_reconcile_monotonic = None


def confirm_held_qty(client: Any, ticker: str) -> int:
    """KIS-confirmed held quantity for ``ticker`` (REQ-042-A1, single truth).

    Reads a fresh ``balance()`` — the authoritative position source — and
    returns the held qty (0 if the ticker is not in the KIS account). Mirrors
    ``position_watchdog._confirm_qty`` / ``order._held_qty`` (SPEC-033/039).
    """
    for h in balance(client).get("holdings", []) or []:
        if h.get("ticker") == ticker:
            return int(h.get("qty", 0) or 0)
    return 0


# @MX:ANCHOR: pre-submission sell guard. fan_in >= 2 — the orchestrator sell
# decision and any future direct-sell caller clamp through here before a real
# KIS sell is POSTed.
# @MX:REASON: RC-1 (2026-06-08) — a phantom local position drove a real KIS sell
# that KIS rejected '잔고내역이 없습니다'. Clamping the sell qty to the KIS-confirmed
# held qty BEFORE submission is the broker-truth invariant: a sell is only ever
# issued for a quantity the KIS account actually holds (REQ-042-A1/A5). This is a
# READ-ONLY guard (never fills) so it is safe on both paper and live.
def clamp_sell_to_confirmed(client: Any, ticker: str, qty: int) -> int:
    """Clamp a sell qty to the KIS-confirmed held qty (REQ-042-A1/A5).

    Returns the qty that may be safely sold:

    - confirmed == 0 → returns 0 and audits ``PHANTOM_SELL_BLOCKED`` (a phantom
      position must never POST a real KIS sell).
    - confirmed < qty → returns confirmed and audits ``OVERSELL_CLAMPED_PRESUBMIT``
      (the excess is dropped pre-submission so an over-sized KIS sell is never
      issued — this is distinct from SPEC-039's post-POST synthetic clamp).
    - confirmed >= qty → returns qty unchanged (capital-preservation: a genuine
      held position is never blocked).
    """
    confirmed = confirm_held_qty(client, ticker)

    if confirmed <= 0:
        audit(
            "PHANTOM_SELL_BLOCKED",
            actor="risk",
            details={
                "ticker": ticker,
                "requested_qty": qty,
                "confirmed_qty": 0,
                "mode": getattr(client.mode, "value", str(client.mode)),
                "reason": "KIS account holds no such position (phantom) — "
                "real KIS sell suppressed (REQ-042-A1/A5)",
            },
        )
        return 0

    if qty > confirmed:
        audit(
            "OVERSELL_CLAMPED_PRESUBMIT",
            actor="risk",
            details={
                "ticker": ticker,
                "requested_qty": qty,
                "confirmed_qty": confirmed,
                "dropped_excess": qty - confirmed,
                "mode": getattr(client.mode, "value", str(client.mode)),
            },
        )
        return confirmed

    return qty


# @MX:WARN: intraday reconcile is a MONEY path — it reconverges the local
# positions cache to the KIS account, which then governs sell decisions.
# @MX:REASON: REQ-042-A2 — running reconcile only once daily (15:59) let phantom
# positions persist all day (RC-1). Raising it intraday (pre-sell-cycle + after
# each order) closes the divergence window. The TTL throttle bounds KIS round
# trips (ADR-1 rate-limit trade-off); force=True (post-submission) bypasses it so
# a just-changed book is always reconverged before the next decision.
def intraday_reconcile(
    client: Any,
    *,
    reason: str,
    force: bool = False,
) -> dict[str, Any]:
    """Reconcile the local positions cache against the KIS account (REQ-042-A2).

    TTL-throttled: a call within ``INTRADAY_RECONCILE_TTL_SECONDS`` of the last
    reconcile is skipped (``{"throttled": True}``) unless ``force=True`` (used
    immediately after an order submission, where the book just changed).

    Emits an ``INTRADAY_RECONCILE`` audit row with the reconcile summary so every
    reconcile/drift action is trackable (REQ-042-D3 cross-cutting).

    Never raises — the underlying ``reconcile_from_balance`` already isolates DB
    errors and counts them; this wrapper only adds throttling + audit.
    """
    global _last_reconcile_monotonic
    now = time.monotonic()

    if (
        not force
        and _last_reconcile_monotonic is not None
        and (now - _last_reconcile_monotonic) < INTRADAY_RECONCILE_TTL_SECONDS
    ):
        return {"reconciled": False, "throttled": True, "reason": reason}

    summary = reconcile_from_balance(client, dry_run=False)
    _last_reconcile_monotonic = now

    # Drift audit: positions_synced > 0 means the local cache diverged from the
    # KIS account and was reconverged this cycle (drift detected + corrected).
    drift = int(summary.get("positions_synced", 0) or 0)
    audit(
        "INTRADAY_RECONCILE",
        actor="kis",
        details={
            "reason": reason,
            "forced": force,
            "queried": summary.get("queried", 0),
            "transitioned": summary.get("transitioned", 0),
            "positions_synced": drift,
            "errors": summary.get("errors", 0),
            "drift_corrected": drift,
        },
    )

    LOG.info(
        "SPEC-042 intraday_reconcile reason=%s forced=%s queried=%s "
        "transitioned=%s positions_synced=%s errors=%s",
        reason,
        force,
        summary.get("queried"),
        summary.get("transitioned"),
        drift,
        summary.get("errors"),
    )
    return {"reconciled": True, "throttled": False, "reason": reason,
            "summary": summary}


# @MX:ANCHOR: single fill-confirmation code path (REQ-042-A3 paper/live parity).
# @MX:REASON: paper and live must confirm fills through ONE callable so the path
# validated on paper is the same one used on live. Only the SOURCE branches:
# paper = balance reconcile (KIS paper does not report same-day sell fills),
# live = KIS execution inquiry (주식일별주문체결조회). The live branch is a guarded
# seam — we raise rather than fabricate (REQ-042-A5) until the TR id is verified.
def confirm_fills(client: Any, *, source: str | None = None) -> dict[str, Any]:
    """Confirm fills through a single code path, source-branched (REQ-042-A3).

    ``source`` is auto-detected from ``client.mode`` when None:

    - paper → ``"balance_reconcile"``: delegates to ``reconcile_from_balance``.
    - live  → ``"execution_inquiry"``: KIS 주식일별주문체결조회 polling. NOT yet
      wired — raises :class:`BrokerFillInquiryNotImplemented` rather than
      fabricating a live fill or falling back to the paper reconcile (REQ-042-A5).

    Returns a dict with ``source`` and the reconcile ``summary`` on paper.
    """
    if source is None:
        source = (
            "balance_reconcile"
            if client.mode == TradingMode.PAPER
            else "execution_inquiry"
        )

    if source == "balance_reconcile":
        summary = reconcile_from_balance(client, dry_run=False)
        return {"source": "balance_reconcile", "summary": summary}

    # ── LIVE seam (REQ-042-A3/A5) ──────────────────────────────────────────
    # Drop-in point for the KIS execution-inquiry (주식일별주문체결조회) poll. The
    # TR id is not yet verified, so we DO NOT guess — raise the typed seam error.
    # When wired, this branch must map KIS execution states to filled/partial
    # WITHOUT touching the paper synthetic path and WITHOUT fabricating fills.
    raise BrokerFillInquiryNotImplemented(
        "live fill confirmation (주식일별주문체결조회) is not yet wired; "
        "the KIS execution-inquiry TR id must be verified before live use "
        "(REQ-042-A3/A5) — never fabricate a live fill"
    )
