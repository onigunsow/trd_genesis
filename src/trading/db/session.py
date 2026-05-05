"""Postgres session helper. Plain psycopg, no SQLAlchemy session for M2.

Implements REQ-KIS-02-4 helpers (orders, audit_log writes).
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row


def dsn() -> str:
    """Resolve in-container DATABASE_URL or build from POSTGRES_* env."""
    raw = os.environ.get("DATABASE_URL")
    if raw:
        # SQLAlchemy-style 'postgresql+psycopg://' is not understood by libpq.
        return raw.replace("postgresql+psycopg://", "postgresql://")
    user = os.environ["POSTGRES_USER"]
    pw = os.environ["POSTGRES_PASSWORD"]
    db = os.environ["POSTGRES_DB"]
    host = os.environ.get("POSTGRES_HOST", "postgres")
    port = os.environ.get("POSTGRES_PORT", "5432")
    return f"postgresql://{user}:{pw}@{host}:{port}/{db}"


@contextmanager
def connection(autocommit: bool = False) -> Iterator[psycopg.Connection]:
    """Context-managed psycopg connection. Caller decides commit semantics."""
    conn = psycopg.connect(dsn(), autocommit=autocommit, row_factory=dict_row)
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


def audit(event_type: str, actor: str, details: dict[str, Any] | None = None) -> None:
    """Append a row to audit_log.

    Used by REQ-KIS-02-4 (order audit), REQ-MODE-02-7 (mode change audit),
    REQ-RISK-05-3 (circuit breaker), REQ-FUTURE-08-2 (live unlock attempts).
    """
    payload = json.dumps(details or {})
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO audit_log (event_type, actor, details) VALUES (%s, %s, %s::jsonb)",
            (event_type, actor, payload),
        )


def get_system_state() -> dict[str, Any]:
    """Read system_state singleton row."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, live_unlocked, halt_state, silent_mode, trading_mode, "
            "tool_calling_enabled, reflection_loop_enabled, "
            "updated_at, updated_by FROM system_state WHERE id = 1"
        )
        row = cur.fetchone()
        if not row:
            raise RuntimeError("system_state row missing — migration 001 not applied?")
        return dict(row)


def update_system_state(**fields: Any) -> None:
    """Update system_state singleton. Caller specifies fields to change."""
    if not fields:
        return
    fields["updated_at"] = "NOW()"  # marker for SQL substitution
    set_parts = []
    params: list[Any] = []
    for k, v in fields.items():
        if k == "updated_at":
            set_parts.append("updated_at = NOW()")
        else:
            set_parts.append(f"{k} = %s")
            params.append(v)
    sql = f"UPDATE system_state SET {', '.join(set_parts)} WHERE id = 1"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
