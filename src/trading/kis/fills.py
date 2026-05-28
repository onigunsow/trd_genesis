"""SPEC-TRADING-029 v0.2.0 — KIS order lifecycle sync via balance reconcile.

v0.1.0 used ``inquire-daily-ccld`` (VTTC8001R) to detect fills, but that
endpoint returns an empty result for same-day fills in KIS paper mode (verified
live 2026-05-28: msg_cd 70070000, output1=[]). v0.2.0 switches the data source
to the verified ``inquire-balance`` (VTTC8434R) via ``account.balance()``.

Because balance reports only the *current cumulative held quantity* per ticker
(not per-order fills), we reconcile by FIFO attribution (REQ-029-7): for each
ticker, the newly observed shares are allocated oldest-first to the ticker's
open BUY orders. ``positions`` is then a direct mirror of balance holdings
(REQ-029-8) — no local weighted-average reconstruction.

Public surface:

- ``reconcile_from_balance(client, *, dry_run)`` — v0.2.0 orchestrator (anchor).
- ``fill_sync(client, *, dry_run)`` — thin alias kept for the scheduler cron and
  the ``trading fill-sync`` CLI subcommand (delegates to reconcile).

See ``.moai/specs/SPEC-TRADING-029/`` for the full SPEC, ADRs, and acceptance.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from trading.db.session import connection
from trading.kis.account import balance
from trading.kis.client import KisClient

LOG = logging.getLogger(__name__)


def _audit(cur: Any, event_type: str, details: dict[str, Any]) -> None:
    """Insert an audit_log row inside the caller's transaction."""
    cur.execute(
        "INSERT INTO audit_log (event_type, actor, details) "
        "VALUES (%s, %s, %s::jsonb)",
        (event_type, "kis", json.dumps(details)),
    )


# ---------------------------------------------------------------------------
# REQ-029-7: FIFO orders transition from balance holdings
# ---------------------------------------------------------------------------


# @MX:NOTE: FIFO attribution rule (SPEC-029 v0.2.0 REQ-029-7). balance gives
# only the cumulative held qty per ticker, so newly_filled = held_qty -
# already_accounted (clamped at 0) is allocated oldest-first to the ticker's
# open BUY orders. already_accounted = sum(fill_qty) over the ticker's orders
# that are already filled/partial; an order's remaining capacity is qty -
# current fill_qty, so a 'partial' order can advance to 'filled' on a later
# cycle. balance is the source of truth — we never push qty above held_qty.
def _transition_orders_fifo(
    holdings: list[dict[str, Any]],
    conn: Any,
    *,
    dry_run: bool,
) -> int:
    """Transition open BUY orders to filled/partial by FIFO allocation.

    Returns the number of orders whose status was advanced this cycle.
    """
    transitioned = 0

    for h in holdings:
        ticker = h["ticker"]
        held_qty = int(h.get("qty", 0) or 0)
        fill_price = int(h.get("avg_cost", 0) or 0)
        if held_qty <= 0:
            continue

        with conn.cursor() as cur:
            # already_accounted = shares we have previously attributed to orders.
            cur.execute(
                """
                SELECT COALESCE(SUM(fill_qty), 0) AS accounted
                  FROM orders
                 WHERE ticker = %s AND side = 'buy'
                   AND status IN ('filled', 'partial')
                """,
                (ticker,),
            )
            row = cur.fetchone()
            already_accounted = int((row or {}).get("accounted", 0) or 0)

            newly_filled = max(0, held_qty - already_accounted)

            # Open BUY orders for this ticker, oldest first, row-locked so a
            # concurrent cron + CLI run cannot double-transition (REQ-029-7).
            cur.execute(
                """
                SELECT id, qty, COALESCE(fill_qty, 0) AS fill_qty, status
                  FROM orders
                 WHERE ticker = %s AND side = 'buy'
                   AND kis_order_no IS NOT NULL
                   AND status IN ('submitted', 'partial')
                 ORDER BY ts ASC
                 FOR UPDATE
                """,
                (ticker,),
            )
            open_orders = list(cur.fetchall())

            remaining = newly_filled
            for order in open_orders:
                if remaining <= 0:
                    break
                order_qty = int(order["qty"])
                prior_fill = int(order.get("fill_qty", 0) or 0)
                capacity = order_qty - prior_fill
                if capacity <= 0:
                    continue

                alloc = min(remaining, capacity)
                if alloc <= 0:
                    continue

                new_fill_qty = prior_fill + alloc
                new_status = "filled" if new_fill_qty >= order_qty else "partial"
                remaining -= alloc
                transitioned += 1

                if dry_run:
                    LOG.info(
                        "[DRY-RUN] SPEC-029 order id=%s %s -> %s "
                        "(fill_qty=%d, fill_price=%d)",
                        order["id"], order["status"], new_status,
                        new_fill_qty, fill_price,
                    )
                    continue

                cur.execute(
                    """
                    UPDATE orders
                       SET status = %s,
                           fill_qty = %s,
                           fill_price = %s,
                           filled_at = now()
                     WHERE id = %s
                    """,
                    (new_status, new_fill_qty, fill_price, order["id"]),
                )
                _audit(
                    cur,
                    "ORDER_FILLED" if new_status == "filled" else "ORDER_PARTIAL",
                    {
                        "order_id": order["id"],
                        "ticker": ticker,
                        "side": "buy",
                        "ord_qty": order_qty,
                        "fill_qty": new_fill_qty,
                        "fill_price": fill_price,
                        "new_status": new_status,
                        "source": "inquire-balance",
                    },
                )

    return transitioned


# ---------------------------------------------------------------------------
# REQ-029-8: positions = balance mirror
# ---------------------------------------------------------------------------


def _mirror_positions(
    holdings: list[dict[str, Any]],
    conn: Any,
    *,
    dry_run: bool,
) -> int:
    """Mirror balance holdings into the positions table.

    Held tickers are UPSERTed with avg_cost taken verbatim from KIS
    ``pchs_avg_pric`` (no weighted-average reconstruction — balance is the
    source of truth). Positions present locally but absent from balance are set
    to ``qty=0`` and retained (never DELETEd) per ADR-029-4.

    Returns the number of position rows touched (UPSERTed + zeroed).
    """
    held_tickers = [h["ticker"] for h in holdings if int(h.get("qty", 0) or 0) > 0]
    touched = 0

    with conn.cursor() as cur:
        for h in holdings:
            ticker = h["ticker"]
            qty = int(h.get("qty", 0) or 0)
            avg_cost = int(h.get("avg_cost", 0) or 0)
            if qty <= 0:
                continue

            touched += 1
            if dry_run:
                LOG.info(
                    "[DRY-RUN] SPEC-029 positions mirror %s qty=%d avg_cost=%d",
                    ticker, qty, avg_cost,
                )
                continue

            cur.execute(
                """
                INSERT INTO positions (ticker, qty, avg_cost, last_updated)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (ticker) DO UPDATE SET
                    qty = EXCLUDED.qty,
                    avg_cost = EXCLUDED.avg_cost,
                    last_updated = now()
                """,
                (ticker, qty, avg_cost),
            )
            _audit(
                cur,
                "POSITION_SYNCED",
                {"ticker": ticker, "qty": qty, "avg_cost": avg_cost},
            )

        # Zero out positions no longer held (retain the row for history).
        if held_tickers:
            cur.execute(
                """
                SELECT ticker FROM positions
                 WHERE qty > 0 AND ticker <> ALL(%s)
                """,
                (held_tickers,),
            )
        else:
            cur.execute("SELECT ticker FROM positions WHERE qty > 0")
        stale = list(cur.fetchall())

        for srow in stale:
            ticker = srow["ticker"]
            touched += 1
            if dry_run:
                LOG.info("[DRY-RUN] SPEC-029 positions zero-out %s", ticker)
                continue
            cur.execute(
                """
                UPDATE positions
                   SET qty = 0, last_updated = now()
                 WHERE ticker = %s
                """,
                (ticker,),
            )
            _audit(
                cur,
                "POSITION_SYNCED",
                {"ticker": ticker, "qty": 0, "avg_cost": None},
            )

    return touched


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


# @MX:ANCHOR: reconcile_from_balance is the single fill-sync entry point.
# fan_in >= 3: the scheduler cron (scheduler/runner._run_fill_sync), the CLI
# ``trading fill-sync`` subcommand, and the test suite all reach it (via the
# fill_sync alias).
# @MX:REASON: routing every fill reconcile through one chokepoint keeps the
# balance->FIFO->positions sequence transactional and audit_log emission
# consistent regardless of trigger.
def reconcile_from_balance(
    client: KisClient,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Reconcile local orders/positions against KIS balance holdings.

    REQ-029-6: data source is ``inquire-balance`` via ``account.balance()``;
    ``inquire-daily-ccld`` is never called.

    Steps (single transaction per cycle):
      1. ``balance(client)`` -> current holdings.
      2. FIFO-transition open BUY orders (REQ-029-7).
      3. Mirror holdings into ``positions`` (REQ-029-8).

    In ``dry_run`` mode no UPDATE/INSERT is issued (SELECTs only) and intended
    transitions are logged.

    Returns a summary dict with ``queried`` (held-ticker count), ``transitioned``
    (orders advanced), ``positions_synced`` (rows mirrored/zeroed), ``errors``,
    and ``dry_run``.
    """
    bal = balance(client)
    holdings = bal.get("holdings", []) or []

    summary: dict[str, Any] = {
        "queried": len(holdings),
        "transitioned": 0,
        "positions_synced": 0,
        "errors": 0,
        "dry_run": dry_run,
    }

    try:
        with connection() as conn:
            summary["transitioned"] = _transition_orders_fifo(
                holdings, conn, dry_run=dry_run
            )
            summary["positions_synced"] = _mirror_positions(
                holdings, conn, dry_run=dry_run
            )
    except Exception as exc:  # log + count, never crash the cron
        summary["errors"] += 1
        LOG.exception("SPEC-029 reconcile_from_balance failed: %s", exc)

    LOG.info(
        "SPEC-029 reconcile queried=%d transitioned=%d positions_synced=%d "
        "errors=%d dry_run=%s",
        summary["queried"], summary["transitioned"], summary["positions_synced"],
        summary["errors"], summary["dry_run"],
    )
    return summary


def fill_sync(client: KisClient, *, dry_run: bool = False) -> dict[str, Any]:
    """Backward-compatible alias used by the scheduler cron and CLI subcommand.

    Delegates to :func:`reconcile_from_balance`. Kept so the Phase C wiring
    (``scheduler/runner._run_fill_sync`` and ``cli._cmd_fill_sync``) does not
    need to change its import target.
    """
    return reconcile_from_balance(client, dry_run=dry_run)
