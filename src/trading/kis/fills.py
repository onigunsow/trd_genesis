"""SPEC-TRADING-029 — KIS order lifecycle sync.

Post-submission lifecycle for KIS orders. Bridges the gap between
``submit_order()`` (which writes ``status='submitted'`` only) and the actual
fill confirmation reported by KIS.

Three public functions plus an orchestrator:

- ``inquire_fills_today(client)`` — REQ-029-1: query KIS for today's fills
- ``apply_fill_to_order(fill, conn)`` — REQ-029-2: orders status transition
- ``apply_fill_to_position(fill, conn, order_id)`` — REQ-029-3: positions UPSERT
- ``fill_sync(client, dry_run)`` — REQ-029-4 / REQ-029-5: orchestrator

See ``.moai/specs/SPEC-TRADING-029/`` for the full SPEC, research, and ADRs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytz

from trading.db.session import connection
from trading.kis.client import KisClient, KisError, KisResponse

LOG = logging.getLogger(__name__)

KST = pytz.timezone("Asia/Seoul")

# KIS inquire-daily-ccld endpoint constants
_INQUIRE_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
_TR_ID_PAPER = "VTTC8001R"
_TR_ID_LIVE = "TTTC8001R"

# @MX:WARN: KIS response field names are PROVISIONAL per research.md §3.3.
# @MX:REASON: KIS Developers portal was inaccessible (403/404); field mapping
#   below (odno, tot_ccld_qty, avg_prvs/pchs_avg_pric, cncl_yn, rfus_yn) is the
#   best guess from public summaries. First paper-mode deploy must log the full
#   payload and verify the mapping. Until Phase E validates this, treat
#   ``_FIRST_CALL`` logging as the calibration mechanism.
_FIRST_CALL = True


# @MX:NOTE: KIS sll_buy_dvsn_cd values: 01=매도 (sell), 02=매수 (buy).
_SLL_BUY_TO_SIDE = {"01": "sell", "02": "buy"}


def _set_conn_marker(conn: Any, order_id: int | None) -> None:
    """Stash the locked orders.id on the connection for the orchestrator.

    Best-effort: psycopg connection objects accept attribute assignment, but
    test doubles or read-only proxies may not. A failure here is harmless —
    the orchestrator has a fallback SELECT path.
    """
    try:
        conn._spec029_last_order_id = order_id
    except AttributeError:
        LOG.debug("SPEC-029 could not stash order_id on connection %r", conn)


@dataclass
class FillRow:
    """Parsed KIS inquire-daily-ccld output1 row.

    See research.md §3.3 for the field mapping. Field naming is intentionally
    Python-pythonic (snake_case) and does not preserve KIS's mixed casing.
    """

    odno: str
    ord_dt: str
    pdno: str  # ticker
    side: str  # 'buy' / 'sell'
    ord_qty: int
    tot_ccld_qty: int
    avg_fill_price: int
    cncl_yn: bool
    rfus_yn: bool
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# REQ-029-1: KIS inquire-daily-ccld
# ---------------------------------------------------------------------------


def _parse_int(value: Any) -> int:
    """Coerce a KIS string/number to int. Empty / None / non-numeric → 0."""
    if value is None or value == "":
        return 0
    try:
        # KIS sometimes returns "123.0" for avg prices; float() handles both.
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _parse_yn(value: Any) -> bool:
    """KIS Y/N flag → bool. Anything other than 'Y' (case-insensitive) is False."""
    return str(value or "").strip().upper() == "Y"


def _parse_output1_row(row: dict[str, Any]) -> FillRow:
    """Map a single KIS output1 dict into a FillRow.

    @MX:WARN: see module-level @MX:REASON. Field name fallbacks try a couple
    of common KIS conventions (avg_prvs vs pchs_avg_pric) so that the first
    live response has the best chance of parsing without further changes.
    """
    avg_price = _parse_int(
        row.get("avg_prvs") or row.get("pchs_avg_pric") or row.get("ccld_avg_unpr") or 0
    )
    return FillRow(
        odno=str(row.get("odno") or ""),
        ord_dt=str(row.get("ord_dt") or ""),
        pdno=str(row.get("pdno") or ""),
        side=_SLL_BUY_TO_SIDE.get(str(row.get("sll_buy_dvsn_cd") or ""), "buy"),
        ord_qty=_parse_int(row.get("ord_qty")),
        tot_ccld_qty=_parse_int(row.get("tot_ccld_qty")),
        avg_fill_price=avg_price,
        cncl_yn=_parse_yn(row.get("cncl_yn")),
        rfus_yn=_parse_yn(row.get("rfus_yn")),
        raw=row,
    )


def inquire_fills_today(client: KisClient) -> list[FillRow]:
    """Query KIS for today's fills and return a list of FillRow.

    REQ-029-1: GET ``/uapi/domestic-stock/v1/trading/inquire-daily-ccld`` with
    paper tr_id ``VTTC8001R`` / live tr_id ``TTTC8001R``. INQR_STRT_DT and
    INQR_END_DT are today's date in KST. CCLD_DVSN='00' returns all fills
    (full + partial + pending) so the orchestrator can detect partial states.

    Rate-limit retries are delegated to ``KisClient.get()`` (see client.py).
    On non-success rt_cd we raise ``KisError``.

    @MX:ANCHOR: this is the only entry point for KIS fill inquiry; fan_in
    includes the scheduler cron (Phase C), the CLI ``trading fill-sync``
    subcommand (Phase C), the ``fill_sync()`` orchestrator below, and tests.
    @MX:REASON: maintaining a single chokepoint guarantees tr_id dispatch and
    rate-limit handling are not duplicated across callers.
    """
    global _FIRST_CALL

    today = datetime.now(KST).strftime("%Y%m%d")
    tr_id = client.tr_id(paper_id=_TR_ID_PAPER, live_id=_TR_ID_LIVE)

    params = {
        "CANO": client.account_prefix,
        "ACNT_PRDT_CD": client.account_suffix,
        "INQR_STRT_DT": today,
        "INQR_END_DT": today,
        "SLL_BUY_DVSN_CD": "00",  # 00=전체
        "INQR_DVSN": "00",        # 00=역순
        "PDNO": "",
        "CCLD_DVSN": "00",        # 00=전체 (체결+미체결)
        "ORD_GNO_BRNO": "",
        "ODNO": "",
        "INQR_DVSN_3": "00",
        "INQR_DVSN_1": "",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }

    resp: KisResponse = client.get(_INQUIRE_PATH, tr_id=tr_id, params=params)

    if _FIRST_CALL:
        # @MX:WARN calibration logging — see module-level @MX:REASON.
        LOG.info(
            "SPEC-029 first inquire-daily-ccld payload (verify field mapping): %s",
            json.dumps(resp.raw, ensure_ascii=False)[:4000],
        )
        _FIRST_CALL = False

    if resp.rt_cd != "0":
        raise KisError(resp)

    rows: list[dict[str, Any]] = resp.raw.get("output1") or []
    return [_parse_output1_row(r) for r in rows]


# ---------------------------------------------------------------------------
# REQ-029-2: orders status transition matrix
# ---------------------------------------------------------------------------


# @MX:NOTE: status branching follows the SPEC-029 §3.4 decision matrix:
#   tot_ccld_qty == ord_qty       → filled
#   0 < tot_ccld_qty < ord_qty    → partial
#   cncl_yn == 'Y'                → cancelled
#   rfus_yn == 'Y'                → rejected
#   tot_ccld_qty == 0 (no flags)  → submitted (no-op)
# The order of checks matters: cancel/reject flags take precedence over qty
# comparisons because KIS may set tot_ccld_qty=0 alongside cncl_yn='Y'.


_TRANSITION_AUDIT = {
    "filled": "ORDER_FILLED",
    "partial": "ORDER_PARTIAL",
    "cancelled": "ORDER_CANCELLED",
    "rejected": "ORDER_REJECTED_BY_KIS",
}


def _decide_new_status(fill: FillRow) -> str:
    """Pure function: return the target orders.status for this fill row."""
    if fill.rfus_yn:
        return "rejected"
    if fill.cncl_yn:
        return "cancelled"
    if fill.tot_ccld_qty == 0:
        return "submitted"  # no-op
    if fill.tot_ccld_qty >= fill.ord_qty and fill.ord_qty > 0:
        return "filled"
    return "partial"


def apply_fill_to_order(fill: FillRow, conn: Any) -> str | None:
    """Transition orders.status for one fill row inside the given connection.

    REQ-029-2. Acquires a row-level lock via ``SELECT ... FOR UPDATE`` to
    serialise concurrent sync attempts (cron + CLI race). When the new status
    differs from the existing one, UPDATEs the row and writes an audit_log
    event in the same transaction.

    Side-effect: sets ``conn._spec029_last_order_id`` to the locked row's id
    (or ``None`` if the order was not found) so the orchestrator can reuse it
    for the position UPSERT without re-querying. This avoids a second SELECT
    on the same row in the same transaction.

    Returns:
        The new ``orders.status`` value (``'filled'`` / ``'partial'`` /
        ``'cancelled'`` / ``'rejected'`` / ``'submitted'``), or ``None`` if the
        ``odno`` was not found in the local orders table (EC-029-3).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, status, side, qty, ticker
              FROM orders
             WHERE kis_order_no = %s
             FOR UPDATE
            """,
            (fill.odno,),
        )
        row = cur.fetchone()
        if row is None:
            # Surface "not found" to the orchestrator via the conn marker too.
            _set_conn_marker(conn, None)
            return None
        _set_conn_marker(conn, row["id"])

        # Sanity check (log only — KIS truth wins per research.md §4.5)
        if row["ticker"] != fill.pdno or row["side"] != fill.side:
            LOG.warning(
                "SPEC-029 sanity mismatch for odno=%s: local ticker=%s side=%s vs "
                "KIS pdno=%s side=%s — proceeding with KIS values",
                fill.odno, row["ticker"], row["side"], fill.pdno, fill.side,
            )

        # Idempotency: if local already shows a terminal state, skip (EC-029-4).
        if row["status"] != "submitted":
            return str(row["status"])

        new_status = _decide_new_status(fill)
        if new_status == "submitted":
            # No transition yet — leave row alone, no audit_log.
            return "submitted"

        rejected_reason = None
        if new_status == "rejected":
            rejected_reason = (
                fill.raw.get("rfus_rsn")
                or fill.raw.get("reject_reason")
                or "KIS_REJECTED"
            )[:200]

        cur.execute(
            """
            UPDATE orders
               SET status = %s,
                   fill_qty = %s,
                   fill_price = %s,
                   filled_at = now(),
                   response = COALESCE(response, '{}'::jsonb) || %s::jsonb,
                   rejected_reason = COALESCE(%s, rejected_reason)
             WHERE id = %s
            """,
            (
                new_status,
                fill.tot_ccld_qty,
                fill.avg_fill_price,
                json.dumps({"kis_fill": fill.raw}),
                rejected_reason,
                row["id"],
            ),
        )

        audit_event = _TRANSITION_AUDIT[new_status]
        cur.execute(
            "INSERT INTO audit_log (event_type, actor, details) "
            "VALUES (%s, %s, %s::jsonb)",
            (
                audit_event,
                "kis",
                json.dumps({
                    "order_id": row["id"],
                    "kis_order_no": fill.odno,
                    "ticker": fill.pdno,
                    "side": fill.side,
                    "ord_qty": fill.ord_qty,
                    "fill_qty": fill.tot_ccld_qty,
                    "fill_price": fill.avg_fill_price,
                    "new_status": new_status,
                    "rejected_reason": rejected_reason,
                }),
            ),
        )

        return new_status


# ---------------------------------------------------------------------------
# REQ-029-3: positions UPSERT
# ---------------------------------------------------------------------------


def apply_fill_to_position(
    fill: FillRow,
    conn: Any,
    *,
    order_id: int,
) -> None:
    """Update positions for one fill row.

    REQ-029-3. Should be called only when the order transitions to
    ``'filled'`` or ``'partial'`` (cancelled / rejected do not change
    holdings). The orchestrator (``fill_sync``) is responsible for that gating.

    BUY path uses ``INSERT ... ON CONFLICT (ticker) DO UPDATE`` with a
    weighted-average ``avg_cost`` recomputation. SELL path is a plain UPDATE
    that decrements ``qty`` (clamped at 0 via ``GREATEST``) and preserves
    ``avg_cost`` per ADR-029-4 (qty=0 rows are retained for history).

    @MX:NOTE: weighted-average uses integer arithmetic to match the
    ``positions.avg_cost integer`` column type. Sub-KRW drift is acceptable
    because Korean equities trade at integer KRW granularity (ADR-029-5).
    """
    with conn.cursor() as cur:
        if fill.side == "buy":
            cur.execute(
                """
                INSERT INTO positions (ticker, qty, avg_cost, last_order_id, last_updated)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (ticker) DO UPDATE SET
                    qty = positions.qty + EXCLUDED.qty,
                    avg_cost = CASE
                        WHEN (positions.qty + EXCLUDED.qty) = 0 THEN EXCLUDED.avg_cost
                        ELSE
                            (positions.qty * positions.avg_cost
                             + EXCLUDED.qty * EXCLUDED.avg_cost)
                            / (positions.qty + EXCLUDED.qty)
                    END,
                    last_order_id = EXCLUDED.last_order_id,
                    last_updated = now()
                """,
                (fill.pdno, fill.tot_ccld_qty, fill.avg_fill_price, order_id),
            )
        else:  # sell
            cur.execute(
                """
                UPDATE positions
                   SET qty = GREATEST(positions.qty - %s, 0),
                       last_order_id = %s,
                       last_updated = now()
                 WHERE ticker = %s
                """,
                (fill.tot_ccld_qty, order_id, fill.pdno),
            )

        cur.execute(
            "INSERT INTO audit_log (event_type, actor, details) "
            "VALUES (%s, %s, %s::jsonb)",
            (
                "POSITION_UPDATED",
                "kis",
                json.dumps({
                    "order_id": order_id,
                    "ticker": fill.pdno,
                    "side": fill.side,
                    "fill_qty": fill.tot_ccld_qty,
                    "fill_price": fill.avg_fill_price,
                }),
            ),
        )


# ---------------------------------------------------------------------------
# REQ-029-4 / REQ-029-5: orchestrator
# ---------------------------------------------------------------------------


# @MX:ANCHOR: fill_sync is the integration point for the scheduler cron
# (Phase C), the CLI ``trading fill-sync`` subcommand (Phase C), and any
# future manual / backfill caller. fan_in >= 3.
# @MX:REASON: routing all fill-confirmation flows through a single chokepoint
# guarantees per-row transactional boundaries and consistent audit_log
# emission regardless of how the sync was triggered.
def fill_sync(client: KisClient, *, dry_run: bool = False) -> dict[str, Any]:
    """Orchestrate one fill-sync cycle.

    REQ-029-4 (called once per minute by APScheduler in Phase C).
    REQ-029-5 (``--dry-run`` previews intended transitions without DB writes).

    Algorithm:
      1. Query KIS for today's fills.
      2. For each FillRow, open a fresh DB transaction.
      3. Inside the txn: ``apply_fill_to_order`` (transition + audit).
      4. If the new status is ``filled`` or ``partial``: also
         ``apply_fill_to_position`` (UPSERT + audit) in the SAME txn so
         orders + positions are atomically consistent.
      5. On error for one row, log + audit and continue with the next row
         (best-effort; per-row isolation prevents one bad row from blocking
         the rest).

    In dry_run mode no DB connection is opened; intended transitions are
    logged to stdout/log only.

    Returns:
        Summary dict with ``queried`` / ``transitioned`` / ``errors`` /
        ``dry_run`` keys.
    """
    fills = inquire_fills_today(client)

    summary = {
        "queried": len(fills),
        "transitioned": 0,
        "errors": 0,
        "dry_run": dry_run,
    }

    for fill in fills:
        new_status: str | None
        if not fill.odno:
            LOG.warning("SPEC-029 skipping fill row with empty odno: %s", fill.raw)
            continue

        if dry_run:
            new_status = _decide_new_status(fill)
            LOG.info(
                "[DRY-RUN] SPEC-029 would transition odno=%s pdno=%s side=%s "
                "ord_qty=%d tot_ccld_qty=%d → %s",
                fill.odno, fill.pdno, fill.side,
                fill.ord_qty, fill.tot_ccld_qty, new_status,
            )
            if new_status != "submitted":
                summary["transitioned"] += 1
            continue

        try:
            with connection() as conn:
                new_status = apply_fill_to_order(fill, conn)
                if new_status is None:
                    LOG.warning(
                        "SPEC-029 KIS fill row for unknown order: odno=%s pdno=%s",
                        fill.odno, fill.pdno,
                    )
                    continue
                if new_status in ("filled", "partial"):
                    order_id = getattr(conn, "_spec029_last_order_id", None)
                    if order_id is None:
                        # Defensive: apply_fill_to_order returned a real status
                        # only after fetching the row, so order_id should be
                        # present. Fall back to a SELECT just in case.
                        with conn.cursor() as cur:
                            cur.execute(
                                "SELECT id FROM orders WHERE kis_order_no = %s",
                                (fill.odno,),
                            )
                            row = cur.fetchone()
                        order_id = row["id"] if row else None
                    if order_id is not None:
                        apply_fill_to_position(fill, conn, order_id=order_id)
                if new_status != "submitted":
                    summary["transitioned"] += 1
        except Exception as e:
            summary["errors"] += 1
            LOG.exception(
                "SPEC-029 fill_sync error for odno=%s: %s", fill.odno, e
            )
            # Best-effort audit on the failure in a fresh connection so the
            # original txn rollback does not also lose this record.
            try:
                with connection() as conn, conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO audit_log (event_type, actor, details) "
                        "VALUES (%s, %s, %s::jsonb)",
                        (
                            "ORDER_SYNC_ERROR",
                            "kis",
                            json.dumps({
                                "odno": fill.odno,
                                "pdno": fill.pdno,
                                "error": str(e)[:500],
                            }),
                        ),
                    )
            except Exception:
                LOG.exception("SPEC-029 failed to write ORDER_SYNC_ERROR audit")

    return summary
