"""Postgres session helper. Plain psycopg, no SQLAlchemy session for M2.

Implements REQ-KIS-02-4 helpers (orders, audit_log writes).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row

LOG = logging.getLogger(__name__)

# SPEC-TRADING-035 REQ-035-1: sentinel marking a field whose value is the SQL
# ``NOW()`` function rather than a bound parameter. ``update_system_state``
# renders these as ``col = NOW()`` (see _NOW_FIELDS).
NOW = "NOW()"

# Fields that must be substituted with the SQL NOW() function, never bound as a
# parameter. ``updated_at`` is always stamped; ``regime_updated_at`` is stamped
# only when the caller passes it (REQ-035-1b / R-4 / Q-2 resolution).
_NOW_FIELDS = frozenset({"updated_at", "regime_updated_at"})

# SPEC-TRADING-035 REQ-035-1: regime cache TTL + domain.
REGIME_TTL_DAYS = 7
VALID_REGIMES = ("bull", "neutral", "bear")
VALID_RISK_APPETITES = ("risk-on", "neutral", "risk-off")


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
    """Read system_state singleton row.

    Uses SELECT * to be resilient to column additions across migrations.
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM system_state WHERE id = 1")
        row = cur.fetchone()
        if not row:
            raise RuntimeError("system_state row missing — migration 001 not applied?")
        return dict(row)


def update_system_state(**fields: Any) -> None:
    """Update system_state singleton. Caller specifies fields to change.

    Fields in ``_NOW_FIELDS`` (``updated_at``, ``regime_updated_at``) are rendered
    as the SQL ``NOW()`` function instead of a bound parameter; their passed value
    is ignored (pass ``regime_updated_at=session.NOW`` to stamp it — REQ-035-1b).
    ``updated_at`` is always stamped on every update.
    """
    if not fields:
        return
    fields["updated_at"] = NOW  # always stamp the generic mtime
    set_parts = []
    params: list[Any] = []
    for k, v in fields.items():
        if k in _NOW_FIELDS:
            set_parts.append(f"{k} = NOW()")
        else:
            set_parts.append(f"{k} = %s")
            params.append(v)
    sql = f"UPDATE system_state SET {', '.join(set_parts)} WHERE id = 1"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)


def _notify_regime_stale(updated_at: datetime | None, age_days: float) -> None:
    """REQ-035-1c: emit one Telegram warning when the regime cache is stale.

    Lazy-imports the Telegram notifier (avoids a circular import at module load)
    and swallows any send failure — a stale-read warning must never break the
    read path or the cycle that triggered it.
    """
    try:
        from trading.alerts.telegram import system_briefing

        stamp = updated_at.isoformat() if updated_at else "(없음)"
        system_briefing(
            "Regime 신선도 경고",
            f"매크로 regime 캐시가 {age_days:.1f}일 경과(>{REGIME_TTL_DAYS}일) — "
            f"안전하게 'neutral' 로 폴백합니다. 마지막 갱신: {stamp}.",
        )
    except Exception:
        LOG.warning("regime stale warning failed (swallowed)", exc_info=True)


# @MX:ANCHOR: SPEC-TRADING-035 REQ-035-1(e) — single regime-read path.
# @MX:REASON: fan_in == 3 (decision.run / risk.run / portfolio_gate all read
#             current_regime through this helper). The 7-day TTL safe-fallback to
#             'neutral' and the domain guard are the load-bearing invariants;
#             bypassing this helper would let a stale or out-of-domain regime drive
#             live buy sizing, violating the capital-preservation policy.
# @MX:SPEC: SPEC-TRADING-035
def get_effective_regime(
    now_provider: Callable[[], datetime] | None = None,
) -> tuple[str, str]:
    """Return ``(regime, risk_appetite)`` with a read-time TTL safe fallback.

    Reads the cached values from ``system_state`` (REQ-035-1a). If the cache is
    older than ``REGIME_TTL_DAYS`` (or the timestamp is missing, or the stored
    regime is out of domain) the read result falls back to ``('neutral',
    'neutral')`` — the *stored* columns are never mutated — and a single Telegram
    warning is emitted (REQ-035-1c).

    Args:
        now_provider: Test seam returning the current tz-aware datetime.
    """
    now = (now_provider or (lambda: datetime.now(UTC)))()
    state = get_system_state()
    regime = state.get("current_regime") or "neutral"
    risk = state.get("current_risk_appetite") or "neutral"
    updated_at = state.get("regime_updated_at")

    # Defensive: missing timestamp -> treat as stale (safe neutral fallback).
    if updated_at is None:
        return ("neutral", "neutral")

    age = now - updated_at
    if age > timedelta(days=REGIME_TTL_DAYS):
        _notify_regime_stale(updated_at, age.total_seconds() / 86400.0)
        return ("neutral", "neutral")

    # Domain guard: an out-of-domain stored value (should be impossible given the
    # DB CHECK, but defensive against pre-migration rows) also falls back.
    if regime not in VALID_REGIMES:
        regime = "neutral"
    if risk not in VALID_RISK_APPETITES:
        risk = "neutral"
    return (regime, risk)
