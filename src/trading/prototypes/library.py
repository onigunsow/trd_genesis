"""Prototype CRUD operations — create, read, activate/deactivate.

REQ-PROTO-03-1: Market Prototype Library management.
REQ-PROTO-03-8: Audit + Telegram notification on prototype addition.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from trading.db.session import audit, connection

LOG = logging.getLogger(__name__)


def add_prototype(
    name: str,
    description: str,
    category: str,
    time_period_start: date,
    time_period_end: date,
    market_conditions: dict[str, Any],
    key_indicators: dict[str, Any],
    outcome: dict[str, Any],
    risk_recommendation: dict[str, Any],
    embedding: list[float],
    source: str = "manual",
    is_active: bool = True,
) -> int | None:
    """Insert a new market prototype.

    REQ-PROTO-03-8: Writes audit_log and emits Telegram notification.

    Returns:
        Inserted row id, or None on failure.
    """
    sql = """
        INSERT INTO market_prototypes
            (name, description, category, time_period_start, time_period_end,
             market_conditions, key_indicators, outcome, risk_recommendation,
             embedding, source, is_active)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s)
        RETURNING id
    """
    try:
        # Format embedding as pgvector literal
        embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (
                name,
                description,
                category,
                time_period_start,
                time_period_end,
                json.dumps(market_conditions),
                json.dumps(key_indicators),
                json.dumps(outcome),
                json.dumps(risk_recommendation),
                embedding_str,
                source,
                is_active,
            ))
            row = cur.fetchone()
            if not row:
                return None

            proto_id = row["id"]

        # Audit and notify
        audit(
            "PROTOTYPE_ADDED",
            actor="prototype_library",
            details={"id": proto_id, "name": name, "category": category},
        )
        try:
            from trading.alerts.telegram import send_alert
            send_alert(f"Market prototype added: {name} ({category})")
        except Exception:
            pass

        LOG.info("Prototype added: %s (id=%d, category=%s)", name, proto_id, category)
        return proto_id

    except Exception:
        LOG.exception("Failed to add prototype: %s", name)
        return None


def get_active_prototypes() -> list[dict[str, Any]]:
    """Fetch all active prototypes (for similarity search)."""
    sql = """
        SELECT id, name, description, category,
               time_period_start, time_period_end,
               market_conditions, key_indicators, outcome, risk_recommendation,
               source, is_active, created_at
        FROM market_prototypes
        WHERE is_active = true
        ORDER BY name
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def get_prototype_by_name(name: str) -> dict[str, Any] | None:
    """Fetch a single prototype by name."""
    sql = """
        SELECT id, name, description, category,
               time_period_start, time_period_end,
               market_conditions, key_indicators, outcome, risk_recommendation,
               source, is_active, created_at
        FROM market_prototypes WHERE name = %s
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (name,))
        return cur.fetchone()


def activate_prototype(name: str) -> bool:
    """Activate a prototype (make it queryable for similarity)."""
    sql = "UPDATE market_prototypes SET is_active = true, updated_at = NOW() WHERE name = %s"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (name,))
        return (cur.rowcount or 0) > 0


def deactivate_prototype(name: str) -> bool:
    """Deactivate a prototype without deleting it."""
    sql = "UPDATE market_prototypes SET is_active = false, updated_at = NOW() WHERE name = %s"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (name,))
        return (cur.rowcount or 0) > 0


def count_prototypes() -> dict[str, int]:
    """Count prototypes by status."""
    sql = """
        SELECT
            COUNT(*) FILTER (WHERE is_active) as active,
            COUNT(*) FILTER (WHERE NOT is_active) as inactive,
            COUNT(*) as total
        FROM market_prototypes
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
        return dict(row) if row else {"active": 0, "inactive": 0, "total": 0}
