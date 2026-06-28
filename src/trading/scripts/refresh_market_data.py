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

import concurrent.futures
import logging
import os
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

# REQ-023-1 (b): recency window — tickers with OHLCV in the last N days do not
# need to be expanded.
RECENT_OHLCV_DAYS = 7
# REQ-023-4: per-ticker and total batch timeout budgets for auto-expansion.
DEFAULT_AUTO_EXPANSION_PER_TICKER_S = 30.0
DEFAULT_AUTO_EXPANSION_TOTAL_S = 120.0


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


def _register_dynamic_ticker(ticker: str, source: str) -> bool:
    """Thin wrapper around dynamic_universe.register for monkeypatching."""
    from trading.data import dynamic_universe

    return dynamic_universe.register(ticker, source=source)


def _list_dynamic_universe() -> list[str]:
    """Thin wrapper for monkeypatching."""
    from trading.data import dynamic_universe

    return dynamic_universe.list_active()


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


def _check_krx_circuit() -> str | None:
    """KRX 서킷 브레이커 상태를 확인한다.

    OPEN이면 open_until 시각 문자열을 반환, CLOSED이면 None 반환.
    서킷 모듈 import 실패 시 None 반환(fail-open).
    """
    try:
        from trading.data.krx_circuit_breaker import KrxCircuitOpen, _get_shared_breaker

        try:
            _get_shared_breaker().check_or_raise()
            return None
        except KrxCircuitOpen as exc:
            return str(exc)
    except Exception:
        # krx_circuit_breaker 모듈 자체 import 실패 — fail-open
        return None


def _circuit_open_metrics(label: str, reason: str) -> dict[str, Any]:
    """서킷 OPEN으로 배치를 조기 종료할 때 반환할 빈 메트릭."""
    LOG.warning(
        "%s — KRX 서킷 OPEN, 종목 루프 생략 (%s)",
        label,
        reason,
    )
    return {
        "total_tickers": 0,
        "success_count": 0,
        "error_count": 0,
        "timeout_count": 0,
        "total_rows_upserted": 0,
        "duration_seconds": 0.0,
        "circuit_open": True,
        "circuit_reason": reason,
    }


def refresh_ohlcv() -> dict[str, Any]:
    """REQ-019-1: Daily OHLCV refresh entrypoint."""
    # 배치 시작 시 서킷 단일 확인 — OPEN이면 종목 루프 없이 조기 종료
    if (reason := _check_krx_circuit()) is not None:
        return _circuit_open_metrics("refresh_ohlcv", reason)
    universe = get_data_universe()
    return _run_batch("refresh_ohlcv", _fetch_ohlcv_for_ticker, universe)


def refresh_flows() -> dict[str, Any]:
    """REQ-019-2: Daily flows refresh entrypoint."""
    if (reason := _check_krx_circuit()) is not None:
        return _circuit_open_metrics("refresh_flows", reason)
    universe = get_data_universe()
    return _run_batch("refresh_flows", _fetch_flows_for_ticker, universe)


def refresh_fundamentals() -> dict[str, Any]:
    """REQ-019-3: Weekly fundamentals refresh entrypoint."""
    if (reason := _check_krx_circuit()) is not None:
        return _circuit_open_metrics("refresh_fundamentals", reason)
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


# ---------------------------------------------------------------------------
# SPEC-023: on-demand universe expansion for micro-recommended out-of-universe
# tickers.
# ---------------------------------------------------------------------------


def _resolve_timeouts(
    per_ticker_timeout_s: float | None,
    total_timeout_s: float | None,
) -> tuple[float, float]:
    """REQ-023-4 (c): env-overridable timeout budgets."""
    if per_ticker_timeout_s is None:
        env = os.environ.get("AUTO_EXPANSION_PER_TICKER_TIMEOUT")
        per_ticker_timeout_s = (
            float(env) if env else DEFAULT_AUTO_EXPANSION_PER_TICKER_S
        )
    if total_timeout_s is None:
        env = os.environ.get("AUTO_EXPANSION_TOTAL_TIMEOUT")
        total_timeout_s = (
            float(env) if env else DEFAULT_AUTO_EXPANSION_TOTAL_S
        )
    return per_ticker_timeout_s, total_timeout_s


def _expand_single(
    ticker: str,
    start: date,
    end: date,
    per_ticker_timeout_s: float,
) -> int:
    """REQ-023-1 (c) + REQ-023-4 (a): fetch OHLCV + flows for one ticker
    under a per-ticker timeout. Returns rows upserted across both feeds.

    Wraps the pykrx call in a thread executor so that a blocking pykrx hang
    can be aborted via ``Future.result(timeout=...)`` — pykrx is a synchronous
    library, signal.alarm is unsafe in scheduler threads, so the executor
    pattern is the safest option.
    """

    def _do_fetch() -> int:
        rows = 0
        rows += int(_pykrx_fetch_ohlcv(ticker, start, end) or 0)
        rows += int(_pykrx_fetch_flows(ticker, start, end) or 0)
        return rows

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_do_fetch)
        return fut.result(timeout=per_ticker_timeout_s)


# @MX:ANCHOR: SPEC-023 REQ-023-1 on-demand universe expansion
# @MX:REASON: fan_in >= 3 — pre_market hook, intraday hook, manual CLI invocations
# @MX:SPEC: SPEC-TRADING-023
def expand_universe_for_tickers(
    tickers: list[str],
    *,
    cycle_kind: str,
    per_ticker_timeout_s: float | None = None,
    total_timeout_s: float | None = None,
    today_override: date | None = None,
) -> dict[str, Any]:
    """REQ-023-1: on-demand OHLCV/flows backfill for universe-out candidates.

    For each ``ticker``:
      1. If OHLCV exists within the last ``RECENT_OHLCV_DAYS`` days, skip the
         fetch (it counts as a no-op success).
      2. Otherwise fetch ``BACKFILL_WINDOW_DAYS`` of OHLCV + flows from pykrx
         under a per-ticker timeout (REQ-023-4 (a)).
      3. On success, register the ticker in dynamic_tickers (REQ-023-1 (d)).
      4. On failure or timeout, drop the ticker — never register, never retry
         (REQ-023-3 (b, f)).

    Total batch is bounded by ``total_timeout_s`` (REQ-023-4 (b)); when the
    budget is exhausted, unprocessed tickers are dropped and the function
    returns early.

    Returns a metric dict suitable for INFO logging and orchestrator-side
    candidate filtering (REQ-023-6 (a)).
    """
    per_t, total_t = _resolve_timeouts(per_ticker_timeout_s, total_timeout_s)
    today = today_override or date.today()
    start = today - timedelta(days=BACKFILL_WINDOW_DAYS)
    deadline = time.monotonic() + total_t

    metrics: dict[str, Any] = {
        "cycle_kind": cycle_kind,
        "requested_tickers": list(tickers),
        "success_count": 0,
        "error_count": 0,
        "timeout_count": 0,
        "total_rows_upserted": 0,
        "successful_tickers": [],
    }

    started = time.monotonic()
    for ticker in tickers:
        # REQ-023-4 (b): total batch timeout — drop remaining and break.
        if time.monotonic() >= deadline:
            LOG.warning(
                "auto_expansion total budget exhausted; dropping remaining %s",
                ticker,
            )
            metrics["timeout_count"] += 1
            metrics["error_count"] += 1
            continue

        # REQ-023-1 (b): skip when OHLCV is already recent.
        last_ts = _get_latest_ohlcv_ts(ticker)
        if last_ts is not None and last_ts >= today - timedelta(
            days=RECENT_OHLCV_DAYS
        ):
            metrics["success_count"] += 1
            metrics["successful_tickers"].append(ticker)
            continue

        # Honour per-ticker budget AND respect remaining total budget.
        remaining = max(0.0, deadline - time.monotonic())
        budget = min(per_t, remaining) if remaining > 0 else 0
        if budget <= 0:
            LOG.warning(
                "auto_expansion total budget exhausted before %s", ticker
            )
            metrics["timeout_count"] += 1
            metrics["error_count"] += 1
            continue

        try:
            rows = _expand_single(ticker, start, today, budget)
        except concurrent.futures.TimeoutError:
            LOG.warning(
                "auto_expansion timeout for %s after %.1fs", ticker, budget
            )
            metrics["timeout_count"] += 1
            metrics["error_count"] += 1
            continue
        except Exception as exc:
            LOG.warning("auto_expansion failed for %s: %s", ticker, exc)
            metrics["error_count"] += 1
            continue

        # REQ-023-1 (d): success path -> register in dynamic_tickers.
        try:
            _register_dynamic_ticker(ticker, source="micro_recommendation")
        except Exception as exc:
            LOG.warning(
                "auto_expansion register failed for %s: %s (proceeding)",
                ticker,
                exc,
            )

        metrics["success_count"] += 1
        metrics["total_rows_upserted"] += rows
        metrics["successful_tickers"].append(ticker)

    metrics["duration_ms"] = int((time.monotonic() - started) * 1000)
    try:
        metrics["dynamic_universe_size"] = len(_list_dynamic_universe())
    except Exception:
        metrics["dynamic_universe_size"] = -1

    LOG.info("expand_universe_for_tickers: %s", metrics)
    return metrics


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
