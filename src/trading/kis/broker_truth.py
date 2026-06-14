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

import json
import logging
import time
from datetime import datetime
from typing import Any

import pytz

from trading.config import TradingMode
from trading.db.session import audit, connection
from trading.kis.account import balance
from trading.kis.fills import reconcile_from_balance

LOG = logging.getLogger(__name__)

# KST timezone for trading-day date calculations (KRX).
_KST = pytz.timezone("Asia/Seoul")

# Terminal order statuses — an order already in one of these must never be
# re-transitioned (REQ-045-D3 idempotency).
_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"filled", "partial", "rejected", "cancelled", "expired", "error"}
)

# ADR-1 trade-off (correctness vs KIS rate limit): intraday reconcile is limited
# to "before a sell decision cycle" + "after each order submission", and a short
# TTL throttles repeat calls inside one cycle so we stay within KIS rate limits.
# 45s sits in the 30-60s band the SPEC bounds; tune via run-phase measurement.
INTRADAY_RECONCILE_TTL_SECONDS: float = 45.0

# Module-level throttle clock. Keyed nowhere — a single shared timestamp is
# enough because reconcile is account-wide (balance() returns the whole book).
_last_reconcile_monotonic: float | None = None


class BrokerFillInquiryNotImplemented(NotImplementedError):
    """Preserved for callers that catch this typed error (REQ-042-A3/order_resolver).

    SPEC-045 M2: ``confirm_fills`` no longer raises this on live — the live
    inquiry seam is now wired. This exception class is kept so
    ``order_resolver._attempt_fill_confirmation`` (which catches it) continues
    to compile without modification. It may also be raised by callers who
    explicitly need to signal that a fill inquiry is unavailable.
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


def _today_kst() -> str:
    """Current KST calendar date as YYYYMMDD string for inquiry parameters."""
    return datetime.now(_KST).strftime("%Y%m%d")


def _inquire_daily_ccld(client: Any) -> list[dict[str, Any]]:
    """Query KIS 주식일별주문체결조회 (inquire-daily-ccld) for today's fills.

    Routes through ``client.get()`` → SPEC-043 global TPS pacer (_GATE.acquire()
    inside KisClient.get) so this call respects the process-wide rate budget
    (REQ-045-A3, REQ-043-B1). Never calls ``client.post``.

    Returns a list of fill records from the ``output1`` array of the KIS response.
    Returns ``[]`` on any error or non-success rt_cd — callers must treat an
    empty return as "unconfirmed" and NOT fabricate a fill (REQ-045-A2).

    LIVE-only. Paper MUST NOT call this — paper uses the balance-reconcile path
    (REQ-045-A5: KIS paper inquire-daily-ccld returns empty, msg_cd 70070000,
    verified 2026-05-28).

    [확인 필요-1]: live TR_ID TTTC8001R (3개월 이내) / CTSC9115R (3개월 이전) —
    cross-verified from public sources but NOT live-tested by this project.
    Operator must verify with live credentials before full live promotion (M5 gate).

    [확인 필요-2]: response field names (ODNO, CCLD_QTY, CCLD_AVG_UNPR, etc.) —
    cross-referenced from open-source KIS wrappers but unverified against a live
    account. All field reads below are defensive (default to empty/zero on miss).
    """
    today = _today_kst()

    # [확인 필요-1]: TR_ID selection — TTTC8001R live (within 3mo) / CTSC9115R live
    # (older than 3mo) / VTTC8001R paper. For same-day fills only TTTC8001R is
    # needed; CTSC9115R is a fallback for aged orders not yet confirmed.
    tr_id = client.tr_id(
        "VTTC8001R",   # paper (unused — this function is live-only)
        "TTTC8001R",   # live (3개월 이내) # [확인 필요-1]
    )

    try:
        resp = client.get(
            "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            tr_id,
            params={
                "CANO": client.account_prefix,           # [확인 필요-2]
                "ACNT_PRDT_CD": client.account_suffix,  # [확인 필요-2]
                "INQR_STRT_DT": today,                  # [확인 필요-2] YYYYMMDD
                "INQR_END_DT": today,                   # [확인 필요-2] YYYYMMDD
                "SLL_BUY_DVSN_CD": "00",               # [확인 필요-2] 00=전체
                "INQR_DVSN": "00",                      # [확인 필요-2] 00=주문일
                "PDNO": "",                              # [확인 필요-2] 전종목
                "CCLD_DVSN": "00",                      # [확인 필요-2] 00=전체
                "ORD_GNO_BRNO": "",                     # [확인 필요-2]
                "ODNO": "",                              # [확인 필요-2] 전주문
                "INQR_DVSN_3": "00",                    # [확인 필요-2]
                "INQR_DVSN_1": "",                      # [확인 필요-2]
                "CTX_AREA_FK100": "",                   # [확인 필요-2] 페이징 커서
                "CTX_AREA_NK100": "",                   # [확인 필요-2] 페이징 커서
            },
        )
    except Exception:
        LOG.exception(
            "SPEC-045 _inquire_daily_ccld: KIS inquiry failed — "
            "treating as unconfirmed (no fabricated fill, REQ-045-A2)"
        )
        return []

    if resp.rt_cd != "0":
        LOG.warning(
            "SPEC-045 _inquire_daily_ccld: KIS rt_cd=%s msg_cd=%s msg=%s — "
            "treating as unconfirmed",
            resp.rt_cd, resp.msg_cd, resp.msg,
        )
        return []

    # [확인 필요-2]: KIS returns daily fills in output1 (list). The _parse method
    # in KisClient maps output→output1 fallback; raw always holds the full body.
    records = resp.raw.get("output1", [])
    if not isinstance(records, list):
        LOG.warning(
            "SPEC-045 _inquire_daily_ccld: output1 is not a list (%r) — "
            "treating as empty", type(records)
        )
        return []
    return records


def _apply_live_fills(
    client: Any,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Match KIS fill records to submitted orders and transition their status.

    Queries the DB for today's submitted orders with a non-null kis_order_no,
    matches them against the KIS fill records by order number (ODNO field),
    and transitions matches to ``filled`` or ``partial``.

    Returns a summary: ``{"filled_count": N, "partial_count": N,
    "unmatched_kis": N, "skipped_terminal": N, "errors": N}``.

    REQ-045-A2: orders with no matching KIS record are left unchanged — they
    are NOT fabricated filled. ``order_resolver`` will expire them after the
    window.

    REQ-045-D3: orders already in a terminal status are skipped (idempotent).

    [확인 필요-2]: field names ODNO / CCLD_QTY / CCLD_AVG_UNPR are assumed from
    public KIS API documentation. Verify against a live response before promotion.
    """
    summary: dict[str, Any] = {
        "filled_count": 0,
        "partial_count": 0,
        "unmatched_kis": 0,
        "skipped_terminal": 0,
        "errors": 0,
    }

    if not records:
        return summary

    # Index KIS fill records by ODNO for O(1) lookup.
    # [확인 필요-2]: ODNO is the KIS order number field name.
    kis_by_odno: dict[str, dict[str, Any]] = {}
    for rec in records:
        odno = str(rec.get("ODNO", "") or "").strip()  # [확인 필요-2]
        if odno:
            kis_by_odno[odno] = rec

    if not kis_by_odno:
        return summary

    # Fetch today's submitted orders with a known KIS order number.
    try:
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, qty, COALESCE(fill_qty, 0) AS fill_qty,
                           status, kis_order_no, ticker, side
                      FROM orders
                     WHERE status IN ('submitted', 'partial')
                       AND kis_order_no IS NOT NULL
                       AND kis_order_no != ''
                     ORDER BY ts ASC
                     FOR UPDATE
                    """,
                )
                open_orders = list(cur.fetchall())

            for order in open_orders:
                try:
                    _apply_one_fill(order, kis_by_odno, conn, summary)
                except Exception:
                    summary["errors"] += 1
                    LOG.exception(
                        "SPEC-045 _apply_live_fills: failed for order id=%s",
                        order.get("id"),
                    )
    except Exception:
        summary["errors"] += 1
        LOG.exception("SPEC-045 _apply_live_fills: DB query failed")

    # Count KIS records with no matching local order.
    matched_odnos = {
        str(o.get("kis_order_no", ""))
        for o in open_orders  # type: ignore[name-defined]
        if str(o.get("kis_order_no", "")) in kis_by_odno
    }
    summary["unmatched_kis"] = max(0, len(kis_by_odno) - len(matched_odnos))
    return summary


def _apply_one_fill(
    order: dict[str, Any],
    kis_by_odno: dict[str, dict[str, Any]],
    conn: Any,
    summary: dict[str, Any],
) -> None:
    """Transition one order based on its matching KIS fill record (if any).

    Skips terminal orders (REQ-045-D3). Leaves unmatched orders unchanged
    (REQ-045-A2 — no fabricated fill).
    """
    order_id = int(order["id"])
    current_status = str(order.get("status") or "")
    kis_order_no = str(order.get("kis_order_no") or "").strip()

    # REQ-045-D3: idempotent — never re-transition terminal orders.
    if current_status in _TERMINAL_STATUSES:
        summary["skipped_terminal"] += 1
        return

    rec = kis_by_odno.get(kis_order_no)
    if rec is None:
        # No matching KIS record — leave in submitted/partial; resolver will
        # expire after window (REQ-045-A2: no fabricated fill).
        return

    # [확인 필요-2]: CCLD_QTY = 체결수량, CCLD_AVG_UNPR = 체결평균가.
    # Defensive int-cast with 0 fallback so a bad schema never crashes here.
    ccld_qty = int(rec.get("CCLD_QTY", 0) or 0)      # [확인 필요-2]
    ccld_price = int(rec.get("CCLD_AVG_UNPR", 0) or 0)  # [확인 필요-2]
    order_qty = int(order.get("qty", 0) or 0)

    if ccld_qty <= 0:
        # KIS returned a record for this order but fill qty is 0 — still
        # pending; do not fabricate.
        return

    new_status = "filled" if ccld_qty >= order_qty else "partial"
    ticker = order.get("ticker", "")
    side = order.get("side", "")

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE orders
               SET status = %s,
                   fill_qty = %s,
                   fill_price = %s,
                   filled_at = now()
             WHERE id = %s
               AND status NOT IN ('filled', 'partial', 'rejected',
                                  'cancelled', 'expired', 'error')
            """,
            (new_status, ccld_qty, ccld_price, order_id),
        )
        _emit_fill_audit(
            cur, order_id, ticker, side, order_qty, ccld_qty, ccld_price, new_status
        )

    if new_status == "filled":
        summary["filled_count"] += 1
    else:
        summary["partial_count"] += 1

    LOG.info(
        "SPEC-045 live fill confirmed: order id=%s %s %s qty=%d fill_qty=%d "
        "fill_price=%d → %s",
        order_id, side, ticker, order_qty, ccld_qty, ccld_price, new_status,
    )


def _emit_fill_audit(
    cur: Any,
    order_id: int,
    ticker: str,
    side: str,
    ord_qty: int,
    fill_qty: int,
    fill_price: int,
    new_status: str,
) -> None:
    """Emit an audit_log row inside the caller's transaction (REQ-042-D3)."""
    event = "ORDER_FILLED" if new_status == "filled" else "ORDER_PARTIAL"
    cur.execute(
        "INSERT INTO audit_log (event_type, actor, details) VALUES (%s, %s, %s::jsonb)",
        (event, "live_inquiry", json.dumps({
            "order_id": order_id,
            "ticker": ticker,
            "side": side,
            "ord_qty": ord_qty,
            "fill_qty": fill_qty,
            "fill_price": fill_price,
            "new_status": new_status,
            "source": "inquire-daily-ccld",  # [확인 필요-2]
        })),
    )


# @MX:ANCHOR: single fill-confirmation code path (REQ-042-A3/REQ-045-A4 paper/live
# parity). fan_in >= 2: order_resolver._attempt_fill_confirmation and the intraday
# reconcile both route through here.
# @MX:REASON: paper and live confirm fills through ONE callable so the path
# exercised on paper is the same entry point used on live. SOURCE branches:
# paper = balance reconcile (KIS paper does not report same-day SELL fills),
# live = KIS execution inquiry 주식일별주문체결조회 (SPEC-045 M2, REQ-045-A1).
# Never fabricates a fill (REQ-042-A5/REQ-045-A2).
def confirm_fills(client: Any, *, source: str | None = None) -> dict[str, Any]:
    """Confirm fills through a single code path, source-branched (REQ-042-A3).

    ``source`` is auto-detected from ``client.mode`` when None:

    - paper → ``"balance_reconcile"``: delegates to ``reconcile_from_balance``.
      KIS paper returns empty results for same-day SELL fills (inquire-daily-ccld
      VTTC8001R → msg_cd 70070000, verified 2026-05-28), so balance-reconcile
      is the only reliable paper path (REQ-045-A5).
    - live  → ``"execution_inquiry"``: calls KIS 주식일별주문체결조회 via
      ``_inquire_daily_ccld`` (routes through ``client.get()`` → SPEC-043
      TPS pacer, REQ-045-A3). Matches fill records to submitted orders by
      kis_order_no. Unconfirmed orders are left unchanged for ``order_resolver``
      to expire (never fabricated filled, REQ-042-A5/REQ-045-A2).

    Returns a dict with ``source`` and a ``summary`` dict. On live the summary
    contains ``filled_count``, ``partial_count``, ``unmatched_kis``,
    ``skipped_terminal``, ``errors``.
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

    # ── LIVE execution inquiry (SPEC-045 M2, REQ-045-A1..A5) ──────────────
    # 1. Query KIS inquire-daily-ccld via client.get() (SPEC-043 TPS pacer).
    # 2. Match fill records to submitted orders by kis_order_no.
    # 3. Transition matched orders to filled/partial (no fabrication).
    # 4. Leave unmatched orders for order_resolver to expire after the window.
    records = _inquire_daily_ccld(client)
    summary = _apply_live_fills(client, records)
    return {"source": "execution_inquiry", "summary": summary}
