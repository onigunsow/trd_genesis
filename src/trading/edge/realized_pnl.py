"""SPEC-TRADING-042 Module D — realized P&L aggregation (REQ-042-D1..D3).

Closes RC-4 (2026-06-08): ``daily_equity_snapshot.realized_pnl_cum`` was NULL for
every row even though round-trips had completed (064350 sells 6/4, 055550
round-trip 6/5). Realized P&L was never aggregated/persisted into the snapshot, so
the headline 자산 had no honest realized-P&L dimension to reconcile against.

Single source of truth (REQ-042-D1): the realized P&L is the FIFO round-trip
``net_pnl`` from :mod:`trading.edge.roundtrips` — fees already deducted. This
module does NOT invent a second P&L calculation; it only *aggregates* and
*persists* that existing figure into the snapshot column.

Reconciliation (REQ-042-D2 / SPEC-039): the cumulative realized P&L AS OF a given
trading day is ``sum(net_pnl)`` over every round-trip whose exit is on/before that
day. A pure buy with no matching sell produces NO round-trip, so a net cash
OUTFLOW is never mistaken for realized P&L (the SPEC-039 daily_pnl_pct correction,
restated at the cumulative level). It is a realized-only dimension, orthogonal to
``total_assets`` (D+2 headline, SPEC-041) and ``unrealized_pnl`` (KIS evaluation).

Honesty caveat (project hard rule): in PAPER mode the round-trip exit price can be
a *synthetic* fill (SPEC-039) — an estimate at submission time, NOT a real
execution price. ``aggregate_realized_pnl_cum`` surfaces the synthetic sell-fill
count so callers/reports can state that the realized figure carries paper-fill
imprecision. A synthetic fill is the single source row for its order, so FIFO
matches it exactly once — it is never double-counted.

LIVE safety: this module is read/aggregate over ``orders`` + write to
``daily_equity_snapshot``. It never POSTs a KIS order, never touches the order
submission path, and leaves ``order.py`` / ``account.py`` / ``fills.py`` and
``live_unlocked`` byte-for-byte unchanged. No migration — ``realized_pnl_cum``
already exists (mig 026).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date
from typing import Any

from trading.db.session import connection
from trading.edge.roundtrips import RoundTrip, compute_roundtrips

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure cumulative formula (DB-free, unit-testable)
# ---------------------------------------------------------------------------


def realized_pnl_as_of(roundtrips: Iterable[RoundTrip], day: date) -> int:
    """Cumulative realized P&L (fees deducted) AS OF ``day``.

    ``sum(net_pnl)`` over every round-trip whose exit is on/before ``day``. Only
    completed round-trips (a buy matched by a confirmed sell) contribute, so a
    pure buy with no matching sell returns 0 — a net cash outflow is never counted
    as realized P&L (REQ-042-D2, consistent with SPEC-039). Rounded to ``int``
    (BIGINT column).
    """
    total = sum(rt.net_pnl for rt in roundtrips if rt.exit_date <= day)
    return round(total)


# ---------------------------------------------------------------------------
# DB aggregation / backfill
# ---------------------------------------------------------------------------


def _count_synthetic_sell_fills(cur: Any) -> int:
    """Number of synthetic (paper) sell fills that feed the round-trip source.

    Used only for the honesty caveat — a synthetic exit price is an estimate, not
    a live execution. Counting, not arithmetic: the figure does not change any
    realized_pnl_cum value, it only flags paper-fill imprecision in the summary.
    """
    cur.execute(
        """
        SELECT count(*) AS n
          FROM orders
         WHERE side = 'sell'
           AND status IN ('filled', 'partial')
           AND synthetic = TRUE
        """
    )
    row = cur.fetchone() or {}
    return int(row.get("n", 0) or 0)


# @MX:WARN: realized-P&L aggregation is a MONEY path — it persists the realized
# profit/loss figure that the headline 자산 reconciliation and operator reports
# read.
# @MX:REASON: RC-4 (2026-06-08) left realized_pnl_cum NULL for every snapshot row
# despite completed round-trips. This aggregator must use the SINGLE round-trip
# net_pnl source (fees deducted) — never a second calculation — and must never let
# net cash outflow masquerade as realized P&L (REQ-042-D1/D2, SPEC-039). It is
# read/aggregate only: it never POSTs a KIS order and never touches order.py.
def aggregate_realized_pnl_cum(
    *,
    days: int | None = None,
    only_day: date | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Populate ``daily_equity_snapshot.realized_pnl_cum`` from confirmed fills.

    For each target snapshot row, ``realized_pnl_cum`` is set to the cumulative
    realized round-trip P&L (fees deducted) AS OF that ``trading_day``. Idempotent:
    a re-run recomputes the same value from the same fills, so the backfill is safe
    to repeat (REQ-042-D1).

    Parameters
    ----------
    days:
        Restrict the round-trip source to the last ``days`` of fills (passed to
        :func:`compute_roundtrips`). ``None`` = full history (the default backfill).
    only_day:
        Update just this one snapshot row (the cron path after the daily snapshot).
        ``None`` = every existing snapshot row (the backfill path).
    dry_run:
        Compute and count would-be updates without writing UPDATE/audit rows.

    Returns a summary dict: ``rows_updated`` (would-update count under dry_run),
    ``roundtrips``, ``cumulative_total`` (realized P&L through the latest snapshot
    day), ``synthetic_sell_fills``, ``synthetic_present``, ``dry_run``.
    """
    rt_result = compute_roundtrips(days)
    roundtrips = rt_result.roundtrips

    summary: dict[str, Any] = {
        "rows_updated": 0,
        "roundtrips": len(roundtrips),
        "cumulative_total": 0,
        "synthetic_sell_fills": 0,
        "synthetic_present": False,
        "dry_run": dry_run,
    }

    with connection() as conn, conn.cursor() as cur:
        # Target snapshot days.
        if only_day is not None:
            target_days: list[date] = [only_day]
        else:
            cur.execute(
                "SELECT trading_day FROM daily_equity_snapshot ORDER BY trading_day ASC"
            )
            target_days = [r["trading_day"] for r in cur.fetchall()]

        # Honesty caveat input — synthetic sell-fill count (paper estimate prices).
        synthetic = _count_synthetic_sell_fills(cur)
        summary["synthetic_sell_fills"] = synthetic
        summary["synthetic_present"] = synthetic > 0

        latest_cum = 0
        for day in target_days:
            cum = realized_pnl_as_of(roundtrips, day)
            latest_cum = cum
            summary["rows_updated"] += 1
            if dry_run:
                LOG.info(
                    "[DRY-RUN] SPEC-042 realized_pnl_cum %s -> %d", day, cum
                )
                continue
            cur.execute(
                """
                UPDATE daily_equity_snapshot
                   SET realized_pnl_cum = %(realized_pnl_cum)s
                 WHERE trading_day = %(trading_day)s
                """,
                {"realized_pnl_cum": cum, "trading_day": day},
            )

        summary["cumulative_total"] = latest_cum

        if not dry_run:
            _audit(cur, summary, only_day=only_day, days=days)

    LOG.info(
        "SPEC-042 aggregate_realized_pnl_cum rows_updated=%d roundtrips=%d "
        "cumulative_total=%d synthetic_sell_fills=%d dry_run=%s",
        summary["rows_updated"], summary["roundtrips"], summary["cumulative_total"],
        summary["synthetic_sell_fills"], summary["dry_run"],
    )
    return summary


def _audit(
    cur: Any,
    summary: dict[str, Any],
    *,
    only_day: date | None,
    days: int | None,
) -> None:
    """Insert a REALIZED_PNL_AGGREGATED audit row in the caller's txn (REQ-042-D3)."""
    import json

    details = {
        "rows_updated": summary["rows_updated"],
        "roundtrips": summary["roundtrips"],
        "cumulative_total": summary["cumulative_total"],
        "synthetic_sell_fills": summary["synthetic_sell_fills"],
        "synthetic_present": summary["synthetic_present"],
        "only_day": only_day.isoformat() if only_day else None,
        "days": days,
        "caveat": "paper synthetic fill prices are estimates, not live executions",
    }
    cur.execute(
        "INSERT INTO audit_log (event_type, actor, details) VALUES (%s, %s, %s::jsonb)",
        ("REALIZED_PNL_AGGREGATED", "realized_pnl", json.dumps(details)),
    )
