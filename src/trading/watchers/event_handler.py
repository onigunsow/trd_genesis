"""SPEC-TRADING-024 Stage 1 — shared watcher event handler.

Single dispatch point invoked by all three Stage 1 watchers. Persists the
event to `trigger_events` and triggers a standard `run_intraday_cycle`.

Stage 1 simplification (per user-resolved Q-7): tier degradation only means
logging; multi-tier dispatch is Stage 2 territory (REQ-024-8). For now the
event handler just calls `run_intraday_cycle` so cached micro/macro are
re-used (SPEC-016 guarantees). Daily LLM budget warning (Q-3 10,000 KRW)
also lives here for Stage 1.

@MX:SPEC: SPEC-TRADING-024
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import date, timedelta, timezone
from typing import Any

LOG = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# Resolved Q-3: 10,000 KRW soft budget warning for Stage 1.
# Hard enforcement (REQ-024-9) is Stage 2 territory.
DAILY_LLM_BUDGET_KRW: int = 10_000

# Coarse-grained per-process guard: while a watcher cycle is mid-flight we
# refuse to start another (prevents KIS rate-limit blowups when multiple
# tickers fire within the same scheduler tick).
_CYCLE_LOCK = threading.Lock()


def _persist_trigger_event(
    ticker: str,
    trigger_type: str,
    metadata: dict[str, Any],
    cycle_run_id: int | None = None,
) -> None:
    """Append-only insert into `trigger_events`. Non-fatal on failure."""
    try:
        from trading.db.session import connection

        sql = (
            "INSERT INTO trigger_events (ticker, trigger_type, metadata, "
            "cycle_run_id) VALUES (%s, %s, %s::jsonb, %s)"
        )
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                sql,
                (ticker, trigger_type, json.dumps(metadata or {}), cycle_run_id),
            )
    except Exception as exc:
        LOG.warning(
            "event_handler: trigger_events insert failed (ticker=%s type=%s): %s",
            ticker,
            trigger_type,
            exc,
        )


def _today_llm_cost_krw() -> float:
    """Sum today's LLM cost from llm_cost_log if the table exists.

    Stage 1 is best-effort: if the table is missing (Stage 2 will add it via
    migration 02x), return 0 silently.
    """
    try:
        from trading.db.session import connection

        sql = "SELECT COALESCE(SUM(cost_krw), 0) AS total FROM llm_cost_log WHERE ts::date = %s"
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (date.today().isoformat(),))
            row = cur.fetchone()
            if not row:
                return 0.0
            val = row.get("total") if isinstance(row, dict) else row[0]
            return float(val or 0)
    except Exception:
        # Table may not exist yet (Stage 2 introduces it). Silent return 0.
        return 0.0


def _maybe_warn_budget() -> None:
    """Log a warning if today's cost exceeds the Stage 1 soft budget."""
    spent = _today_llm_cost_krw()
    if spent >= DAILY_LLM_BUDGET_KRW:
        LOG.warning(
            "SPEC-024 Stage 1 soft budget exceeded: today=%.0f KRW cap=%d KRW "
            "(Stage 2 will enforce tier degradation)",
            spent,
            DAILY_LLM_BUDGET_KRW,
        )


def handle_trigger_event(
    ticker: str, trigger_type: str, metadata: dict[str, Any] | None = None
) -> None:
    """Dispatch entry point for all Stage 1 watcher events.

    - Persists the event to `trigger_events`.
    - Emits a budget-warning log when applicable.
    - Invokes `orchestrator.run_intraday_cycle`. Stage 1 does not narrow the
      cycle to the firing ticker; Stage 2 will introduce tier dispatch.
    """
    metadata = metadata or {}
    _persist_trigger_event(ticker, trigger_type, metadata)
    _maybe_warn_budget()

    # Single in-flight cycle at a time. Drop overlapping triggers to respect
    # KIS rate limits (모의투자 1초 5회). The event itself is already logged.
    acquired = _CYCLE_LOCK.acquire(blocking=False)
    if not acquired:
        LOG.info(
            "event_handler: cycle already in flight; %s %s queued-skip",
            trigger_type,
            ticker,
        )
        return
    try:
        from trading.personas import orchestrator

        LOG.info(
            "event_handler: invoking run_intraday_cycle (trigger=%s ticker=%s)",
            trigger_type,
            ticker,
        )
        orchestrator.run_intraday_cycle()
    except Exception as exc:
        LOG.exception(
            "event_handler: run_intraday_cycle failed for %s %s: %s",
            trigger_type,
            ticker,
            exc,
        )
    finally:
        _CYCLE_LOCK.release()
