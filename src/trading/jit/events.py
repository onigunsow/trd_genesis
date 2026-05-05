"""Delta event persistence — insert, query, and cleanup operations.

REQ-DELTA-01-2: Insert delta events to database.
REQ-DELTA-01-9: Nightly cleanup for events older than 7 days with merged=true.
REQ-DELTA-01-12: Mark deltas as merged when new snapshot arrives.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from trading.db.session import connection
from trading.jit.models import DeltaEvent

LOG = logging.getLogger(__name__)

# REQ-DELTA-01-9: Retention period
DELTA_RETENTION_DAYS: int = 7


def insert_delta(event: DeltaEvent) -> int | None:
    """Persist a single delta event to the database.

    Returns the inserted row id, or None on failure.
    """
    sql = """
        INSERT INTO delta_events (event_type, source, ticker, payload, event_ts, snapshot_id)
        VALUES (%s, %s, %s, %s::jsonb, %s, %s)
        RETURNING id
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (
                event.event_type,
                event.source,
                event.ticker,
                json.dumps(event.payload, default=str),
                event.event_ts,
                event.snapshot_id,
            ))
            row = cur.fetchone()
            return row["id"] if row else None
    except Exception:
        LOG.exception("Failed to insert delta event: %s/%s", event.event_type, event.ticker)
        return None


def insert_deltas_batch(events: list[DeltaEvent]) -> int:
    """Batch insert delta events. Returns count of successfully inserted rows.

    REQ-NFR-11-1: Batch size 10 events per insert for throughput.
    """
    if not events:
        return 0

    sql = """
        INSERT INTO delta_events (event_type, source, ticker, payload, event_ts, snapshot_id)
        VALUES (%s, %s, %s, %s::jsonb, %s, %s)
    """
    inserted = 0
    try:
        with connection() as conn, conn.cursor() as cur:
            for event in events:
                cur.execute(sql, (
                    event.event_type,
                    event.source,
                    event.ticker,
                    json.dumps(event.payload, default=str),
                    event.event_ts,
                    event.snapshot_id,
                ))
                inserted += 1
    except Exception:
        LOG.exception("Batch insert failed after %d events", inserted)
    return inserted


def get_unmerged_deltas(
    snapshot_id: int | None = None,
    event_type: str | None = None,
    ticker: str | None = None,
    since: datetime | None = None,
    limit: int = 10000,
) -> list[dict[str, Any]]:
    """Query un-merged delta events with optional filters.

    REQ-MERGE-02-3: Used by merge engine to load pending deltas.
    """
    conditions = ["merged = false"]
    params: list[Any] = []

    if snapshot_id is not None:
        conditions.append("snapshot_id = %s")
        params.append(snapshot_id)
    if event_type:
        conditions.append("event_type = %s")
        params.append(event_type)
    if ticker:
        conditions.append("ticker = %s")
        params.append(ticker)
    if since:
        conditions.append("event_ts >= %s")
        params.append(since)

    where = " AND ".join(conditions)
    sql = f"""
        SELECT id, event_type, source, ticker, payload, event_ts, ingested_at, snapshot_id
        FROM delta_events
        WHERE {where}
        ORDER BY event_ts ASC
        LIMIT %s
    """
    params.append(limit)

    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def get_recent_deltas_for_ticker(ticker: str, limit: int = 50) -> list[dict[str, Any]]:
    """Fetch recent delta events for a specific ticker (newest first).

    Used by get_delta_events tool and get_intraday_price_history tool.
    """
    sql = """
        SELECT id, event_type, source, ticker, payload, event_ts, ingested_at
        FROM delta_events
        WHERE ticker = %s
        ORDER BY event_ts DESC
        LIMIT %s
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (ticker, limit))
        return cur.fetchall()


def mark_deltas_merged(snapshot_id: int) -> int:
    """Mark all un-merged deltas for a snapshot as merged.

    REQ-DELTA-01-12: Called when a new cron snapshot is generated.
    Returns count of rows updated.
    """
    sql = """
        UPDATE delta_events SET merged = true
        WHERE snapshot_id = %s AND merged = false
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (snapshot_id,))
        # psycopg3: rowcount available after execute
        return cur.rowcount if hasattr(cur, "rowcount") else 0


def cleanup_old_deltas() -> int:
    """Delete merged delta events older than retention period.

    REQ-DELTA-01-9: Nightly cleanup at 03:00 KST.
    Deletes events where age > 7 days AND merged = true.
    """
    cutoff = datetime.now() - timedelta(days=DELTA_RETENTION_DAYS)
    sql = """
        DELETE FROM delta_events
        WHERE merged = true AND event_ts < %s
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (cutoff,))
        count = cur.rowcount if hasattr(cur, "rowcount") else 0
    LOG.info("Delta cleanup: deleted %d events older than %s", count, cutoff.isoformat())
    return count


def get_delta_count_today() -> dict[str, int]:
    """Get today's delta event counts by type. Used for daily summary."""
    sql = """
        SELECT event_type, COUNT(*) as cnt
        FROM delta_events
        WHERE event_ts::date = CURRENT_DATE
        GROUP BY event_type
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    return {row["event_type"]: row["cnt"] for row in rows}
