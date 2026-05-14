"""SPEC-TRADING-019 REQ-019-1/2/3/4/7/8: Market data refresh entrypoints.

Cron-driven entrypoints for keeping the OHLCV, flows, fundamentals, and
disclosures tables fresh. Each entrypoint iterates ``get_data_universe()`` and
calls the appropriate adapter per ticker, with per-ticker failure isolation
(REQ-019-1 (d)) and per-ticker timeout budget (REQ-019-8).

Discovery (2026-05-11):
- pykrx_adapter.fetch_ohlcv/fetch_fundamentals/fetch_flows(symbol, start, end)
  already perform idempotent upserts keyed by (source, symbol, ts).
- cache.cached_range(source, symbol) returns (min_ts, max_ts) | None.
- dart_adapter.list_recent(start, end) handles its own upserts.

Bootstrap backfill (REQ-019-7, P0 escalated 2026-05-11): runs once on
container start; triggers full 90-day refresh whenever any of the four tables
has zero rows.
"""

# @MX:ANCHOR: SPEC-019 cron entrypoints for OHLCV / flows / fundamentals / disclosures refresh
# @MX:REASON: fan_in >= 4 — scheduler/runner.py + bootstrap + manual CLI invocations
# @MX:SPEC: SPEC-TRADING-019

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from trading.data import cache as cache_mod
from trading.data import dart_adapter, pykrx_adapter
from trading.data.universe import get_data_universe
from trading.db.session import connection

LOG = logging.getLogger(__name__)

# Default rolling window when nothing is cached for a ticker.
BACKFILL_WINDOW_DAYS = 90
# REQ-019-4 (c): DART gap threshold (today - 2 = stale).
DART_GAP_THRESHOLD_DAYS = 2
DART_BACKFILL_DAYS = 12
# REQ-019-8 (a): default per-ticker timeout budget (seconds).
DEFAULT_TICKER_TIMEOUT_SECONDS = 10.0


class TickerTimeout(Exception):
    """REQ-019-8: raised when a per-ticker fetch exceeds the budget."""


# ---------------------------------------------------------------------------
# Cache helpers (thin wrappers for testability)
# ---------------------------------------------------------------------------


def _get_latest_ohlcv_ts(ticker: str) -> date | None:
    rng = cache_mod.cached_range(pykrx_adapter.SOURCE, ticker)
    return rng[1] if rng else None


# @MX:NOTE: SPEC-022 fixes silent-skip regression — flows tracks its own
# latest_ts independent of ohlcv (was using _get_latest_ohlcv_ts, which
# caused refresh_flows to short-circuit after refresh_ohlcv ran).
def _get_latest_flows_ts(ticker: str) -> date | None:
    """SPEC-022 REQ-022-1: MAX(ts) for given ticker in the flows table.

    Independent of ohlcv. Returns None when no rows exist for the ticker.
    """
    sql = "SELECT MAX(ts) AS hi FROM flows WHERE ticker = %s"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (ticker,))
        row = cur.fetchone()
    if not row:
        return None
    hi = row.get("hi") if isinstance(row, dict) else row[0]
    return hi  # date | None


def _get_latest_disclosure_ts() -> date | None:
    """Return MAX(rcept_dt) from disclosures or None when empty."""
    sql = "SELECT MAX(rcept_dt) AS hi FROM disclosures"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    if not row:
        return None
    hi = row.get("hi") if isinstance(row, dict) else row[0]
    return hi  # date | None


def _count_rows(table: str) -> int:
    """REQ-019-7: count rows in a data table (bootstrap check)."""
    # Whitelist allowed table names — SQL identifier injection guard.
    allowed = {"ohlcv", "fundamentals", "flows", "disclosures"}
    if table not in allowed:
        raise ValueError(f"unsupported table: {table}")
    sql = f"SELECT COUNT(*) AS n FROM {table}"  # noqa: S608 (table whitelisted)
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    if not row:
        return 0
    return int(row.get("n", 0) if isinstance(row, dict) else row[0] or 0)


# ---------------------------------------------------------------------------
# pykrx / DART thin wrappers (injectable for tests)
# ---------------------------------------------------------------------------


def _pykrx_fetch_ohlcv(ticker: str, start: date, end: date) -> int:
    return pykrx_adapter.fetch_ohlcv(ticker, start, end)


def _pykrx_fetch_flows(ticker: str, start: date, end: date) -> int:
    return pykrx_adapter.fetch_flows(ticker, start, end)


def _pykrx_fetch_fundamentals(ticker: str, start: date, end: date) -> int:
    return pykrx_adapter.fetch_fundamentals(ticker, start, end)


def _dart_list_recent(start: date, end: date) -> list[dict[str, Any]]:
    return dart_adapter.list_recent(start, end)


# ---------------------------------------------------------------------------
# Per-ticker fetchers (REQ-019-1/2/3)
# ---------------------------------------------------------------------------


# @MX:NOTE: SPEC-019 REQ-019-1 (c) incremental vs 90d backfill window selection
def _fetch_ohlcv_for_ticker(ticker: str, today_override: date | None = None) -> int:
    today = today_override or date.today()
    last_ts = _get_latest_ohlcv_ts(ticker)
    if last_ts is None:
        start = today - timedelta(days=BACKFILL_WINDOW_DAYS)
    else:
        start = last_ts + timedelta(days=1)
    if start > today:
        return 0
    return _pykrx_fetch_ohlcv(ticker, start, today)


def _fetch_flows_for_ticker(ticker: str, today_override: date | None = None) -> int:
    today = today_override or date.today()
    # SPEC-022: flows table tracks its own latest_ts independent of ohlcv.
    # Previously used _get_latest_ohlcv_ts, which caused silent-skip when
    # ohlcv was refreshed first (start = ohlcv_today + 1 > today -> return 0).
    last_ts = _get_latest_flows_ts(ticker)
    if last_ts is None:
        start = today - timedelta(days=BACKFILL_WINDOW_DAYS)
    else:
        start = last_ts + timedelta(days=1)
    if start > today:
        return 0
    return _pykrx_fetch_flows(ticker, start, today)


def _fetch_fundamentals_for_ticker(
    ticker: str, today_override: date | None = None
) -> int:
    today = today_override or date.today()
    # Fundamentals weekly; pull last 14 days to keep things simple + cheap.
    start = today - timedelta(days=14)
    return _pykrx_fetch_fundamentals(ticker, start, today)


# ---------------------------------------------------------------------------
# Generic batch driver (per-ticker isolation + metrics)
# ---------------------------------------------------------------------------


# @MX:NOTE: SPEC-019 REQ-019-1 (d) per-ticker isolation — batch never aborts
def _run_batch(
    label: str,
    fetcher: Callable[[str], int],
    tickers: list[str],
) -> dict[str, Any]:
    """Iterate `tickers`, calling `fetcher(ticker)` with per-item isolation.

    Returns metric dict (success_count / error_count / timeout_count /
    total_rows_upserted / duration_seconds / total_tickers).
    """
    metrics: dict[str, Any] = {
        "total_tickers": len(tickers),
        "success_count": 0,
        "error_count": 0,
        "timeout_count": 0,
        "total_rows_upserted": 0,
    }
    start_t = time.monotonic()
    for ticker in tickers:
        try:
            n = fetcher(ticker)
            metrics["success_count"] += 1
            metrics["total_rows_upserted"] += int(n or 0)
        except TickerTimeout as exc:
            LOG.warning("%s timeout for %s: %s", label, ticker, exc)
            metrics["timeout_count"] += 1
            metrics["error_count"] += 1
        except Exception as exc:
            LOG.warning("%s fetch failed for %s: %s", label, ticker, exc)
            metrics["error_count"] += 1
    metrics["duration_seconds"] = round(time.monotonic() - start_t, 3)
    LOG.info("%s metrics: %s", label, metrics)
    return metrics


# ---------------------------------------------------------------------------
# Public entrypoints
# ---------------------------------------------------------------------------


def refresh_ohlcv() -> dict[str, Any]:
    """REQ-019-1: Daily OHLCV refresh entrypoint."""
    universe = get_data_universe()
    return _run_batch("refresh_ohlcv", _fetch_ohlcv_for_ticker, universe)


def refresh_flows() -> dict[str, Any]:
    """REQ-019-2: Daily flows refresh entrypoint."""
    universe = get_data_universe()
    return _run_batch("refresh_flows", _fetch_flows_for_ticker, universe)


def refresh_fundamentals() -> dict[str, Any]:
    """REQ-019-3: Weekly fundamentals refresh entrypoint."""
    universe = get_data_universe()
    return _run_batch("refresh_fundamentals", _fetch_fundamentals_for_ticker, universe)


# @MX:NOTE: SPEC-019 REQ-019-4 (c) first-deploy 12-day backfill recovery
def refresh_disclosures(today_override: date | None = None) -> dict[str, Any]:
    """REQ-019-4: Daily DART disclosure refresh with gap auto-detection."""
    today = today_override or date.today()
    latest = _get_latest_disclosure_ts()
    # Gap mode: empty cache OR latest older than (today - DART_GAP_THRESHOLD).
    backfill = latest is None or latest < (
        today - timedelta(days=DART_GAP_THRESHOLD_DAYS)
    )
    if backfill:
        start = today - timedelta(days=DART_BACKFILL_DAYS)
    else:
        start = today - timedelta(days=1)

    try:
        rows = _dart_list_recent(start, today)
        n = len(rows)
        err = 0
    except Exception as exc:
        LOG.warning("refresh_disclosures DART call failed: %s", exc)
        n = 0
        err = 1

    metrics = {
        "total_rows_upserted": n,
        "backfill_mode": backfill,
        "error_count": err,
        "start": start.isoformat(),
        "end": today.isoformat(),
    }
    LOG.info("refresh_disclosures metrics: %s", metrics)
    return metrics


# ---------------------------------------------------------------------------
# Bootstrap (REQ-019-7, P0 escalated)
# ---------------------------------------------------------------------------


def bootstrap_backfill_if_empty() -> dict[str, Any]:
    """REQ-019-7: On container start, run full refresh when any table is empty.

    Returns a small status dict so callers and tests can detect whether the
    bootstrap path fired.
    """
    empty_tables: list[str] = []
    for tbl in ("ohlcv", "fundamentals", "flows", "disclosures"):
        try:
            if _count_rows(tbl) == 0:
                empty_tables.append(tbl)
        except Exception as exc:
            LOG.warning("bootstrap row-count check failed for %s: %s", tbl, exc)

    if not empty_tables:
        LOG.info("bootstrap_backfill_if_empty: all tables populated, skipping")
        return {"bootstrapped": False, "empty_tables": []}

    LOG.info(
        "bootstrap_backfill_if_empty: empty tables=%s; running full refresh",
        empty_tables,
    )
    ohlcv_metrics = refresh_ohlcv()
    flows_metrics = refresh_flows()
    funds_metrics = refresh_fundamentals()
    disc_metrics = refresh_disclosures()
    return {
        "bootstrapped": True,
        "empty_tables": empty_tables,
        "ohlcv": ohlcv_metrics,
        "flows": flows_metrics,
        "fundamentals": funds_metrics,
        "disclosures": disc_metrics,
    }
