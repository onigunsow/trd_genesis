"""SPEC-TRADING-037 REQ-037-1 — 10-year KOSPI200 OHLCV backfill.

One-shot loader that populates the existing ``ohlcv`` cache with ~10 years of
daily bars for the full KOSPI200 constituent universe plus the KOSPI index
(symbol ``1001``). This is the input data for the deterministic exit-rule
parameter sweep (REQ-037-2 / ``backtest.exit_sweep``).

Design (per SPEC C-5 — reuse over reinvent):

- Universe source: pykrx ``get_index_portfolio_deposit_file('1028')`` — the same
  source as ``data.universe`` but WITHOUT the top-50 cap (the backfill wants the
  whole index, REQ-037-1 (a)).
- Fetch + cache: reuses ``data.pykrx_adapter.fetch_incremental`` which resumes
  from ``MAX(ts)+1`` via ``data.cache`` and upserts idempotently
  (REQ-037-1 (b, d) — no new schema, incremental/staleness handled by reuse).
- Resilience: per-symbol backoff retry on rate-limit/timeout/exception
  (REQ-037-1 (c)); a symbol that keeps failing is SKIPPED, never aborting the
  whole run.
- Reporting: loaded/skipped counts and coverage are logged (REQ-037-1 (e)).

The real 10-year fetch is long-running and is executed operationally via the
``trading kospi200-backfill`` CLI; this module is fully unit-tested with the
pykrx adapter patched (no network, no DB in tests).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date

from trading.data import pykrx_adapter
from trading.data.cache import upsert_ohlcv
from trading.data.universe import KOSPI200_INDEX_CODE

LOG = logging.getLogger(__name__)

# KOSPI composite index symbol (distinct from the KOSPI200 index code 1028
# used only to enumerate constituents).
KOSPI_INDEX_SYMBOL = "1001"
# KOSDAQ composite index (included for completeness; KOSPI is the primary one).
KOSDAQ_INDEX_SYMBOL = "2001"
# Index symbols need pykrx's get_index_ohlcv, not the stock get_market_ohlcv.
INDEX_SYMBOLS: frozenset[str] = frozenset({KOSPI_INDEX_SYMBOL, KOSDAQ_INDEX_SYMBOL})

# Default ~10-year backfill horizon.
DEFAULT_BACKFILL_YEARS = 10

# Backoff defaults for rate-limit resilience (REQ-037-1 (c)).
DEFAULT_MAX_RETRIES = 4
DEFAULT_BASE_DELAY = 2.0
# Courtesy delay between symbols to stay under pykrx/KRX rate limits.
DEFAULT_SYMBOL_DELAY = 0.3


@dataclass
class BackfillReport:
    """Outcome of a backfill run (REQ-037-1 (e))."""

    loaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    total_rows: int = 0


def _sleep(seconds: float) -> None:
    """Indirection over ``time.sleep`` so tests can patch out real delays."""
    time.sleep(seconds)


def _fetch_kospi200_constituents() -> list[str]:
    """Full KOSPI200 constituent list via pykrx (no top-N cap, REQ-037-1 (a)).

    Indirected so tests can patch without invoking the real pykrx HTTP call.
    """
    from pykrx import stock  # lazy import (heavy)

    return list(stock.get_index_portfolio_deposit_file(KOSPI200_INDEX_CODE))


def _is_index(symbol: str) -> bool:
    """True when ``symbol`` is a KRX index code that needs get_index_ohlcv.

    Indices (KOSPI 1001, KOSDAQ 2001) are served by pykrx's
    ``stock.get_index_ohlcv`` — the stock OHLCV API (``get_market_ohlcv`` used
    by ``pykrx_adapter.fetch_ohlcv``) returns nothing for an index code.
    """
    return symbol in INDEX_SYMBOLS


def _fetch_index_full_range(symbol: str, default_start: date) -> int:
    """Fetch a KRX INDEX over ``[default_start, today]`` via get_index_ohlcv.

    Reuses the SAME ``ohlcv`` table, source label, schema columns and idempotent
    upsert as the stock path (``pykrx_adapter`` mirror) so downstream consumers
    (ATR, benchmark, exit sweep) read indices identically to stocks. KRX index
    frames have no reliable volume column; volume defaults to 0.
    """
    from pykrx import stock  # lazy import (heavy)

    s = default_start.strftime("%Y%m%d")
    e = date.today().strftime("%Y%m%d")
    df = stock.get_index_ohlcv(s, e, symbol)
    if df is None or df.empty:
        return 0

    rows = []
    for ts, row in df.iterrows():
        close = float(row.get("종가", row.get("Close", 0)) or 0)
        rows.append({
            "ts": ts.date() if hasattr(ts, "date") else ts,
            "open": float(row.get("시가", row.get("Open", close)) or close),
            "high": float(row.get("고가", row.get("High", close)) or close),
            "low": float(row.get("저가", row.get("Low", close)) or close),
            "close": close,
            "volume": int(row.get("거래량", row.get("Volume", 0)) or 0),
        })
    return upsert_ohlcv(pykrx_adapter.SOURCE, symbol, rows)


def _fetch_full_range(symbol: str, default_start: date) -> int:
    """Fetch the FULL ``[default_start, today]`` window for one symbol.

    This is a ONE-SHOT HISTORICAL backfill, not a daily incremental top-up.
    It deliberately fetches the whole window instead of ``fetch_incremental``
    (which only resumes FORWARD from the last cached date). The forward-only
    path leaves symbols that already have a partial RECENT cache without any
    historical data — e.g. 000270 cached only 2026-02..2026-05 would fetch 0
    rows and never load 2015..2026-02. Requesting the full window guarantees
    ``[default_start, today]`` coverage regardless of any pre-existing partial
    cache; ``cache.upsert_ohlcv`` is idempotent (``ON CONFLICT ... DO UPDATE``),
    so re-writing the already cached recent portion is harmless.

    INDEX symbols (1001/2001) are routed to ``get_index_ohlcv``; stocks use
    ``pykrx_adapter.fetch_ohlcv`` (``get_market_ohlcv``). Both land in the same
    ``ohlcv`` table with the same schema.
    """
    if _is_index(symbol):
        return _fetch_index_full_range(symbol, default_start)
    return pykrx_adapter.fetch_ohlcv(symbol, default_start, date.today())


def kospi200_universe() -> list[str]:
    """Return the backfill target list: KOSPI index + full KOSPI200 members.

    The KOSPI index (``1001``) is always included. If the constituent fetch
    fails the run still proceeds with just the index (graceful — the index is
    the most important series for regime/benchmark context).
    """
    targets: list[str] = [KOSPI_INDEX_SYMBOL]
    try:
        constituents = _fetch_kospi200_constituents()
    except Exception as exc:
        LOG.warning("KOSPI200 constituent fetch failed: %s", exc)
        constituents = []
    for t in constituents:
        if isinstance(t, str) and t and t not in targets:
            targets.append(t)
    return targets


def backfill_symbol(
    symbol: str,
    default_start: date,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
) -> int:
    """Backfill the full historical window for one symbol with backoff retry.

    Fetches ``[default_start, today]`` in full (one-shot historical load —
    see ``_fetch_full_range``), so a pre-existing partial recent cache does not
    block historical coverage. Returns the number of rows written. Raises the
    last exception if every attempt fails (the caller — ``backfill_all`` — turns
    that into a graceful per-symbol skip).
    """
    attempt = 0
    while True:
        try:
            return _fetch_full_range(symbol, default_start)
        except Exception as exc:
            attempt += 1
            if attempt >= max_retries:
                LOG.warning(
                    "backfill %s: giving up after %d attempts (%s)",
                    symbol, attempt, exc,
                )
                raise
            delay = base_delay * (2 ** (attempt - 1))
            LOG.info(
                "backfill %s: attempt %d failed (%s) — backing off %.1fs",
                symbol, attempt, exc, delay,
            )
            _sleep(delay)


def backfill_all(
    symbols: list[str],
    default_start: date,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    symbol_delay: float = DEFAULT_SYMBOL_DELAY,
) -> BackfillReport:
    """Backfill every symbol, skipping (not aborting on) permanent failures.

    REQ-037-1 (c): a symbol whose retries are exhausted is recorded in
    ``skipped`` and the run continues. REQ-037-1 (e): a summary is logged.
    """
    report = BackfillReport()
    total = len(symbols)
    for i, symbol in enumerate(symbols, start=1):
        try:
            rows = backfill_symbol(
                symbol, default_start,
                max_retries=max_retries, base_delay=base_delay,
            )
        except Exception as exc:
            LOG.warning("backfill skip %s (%d/%d): %s", symbol, i, total, exc)
            report.skipped.append(symbol)
            continue
        report.loaded.append(symbol)
        report.total_rows += rows
        LOG.info("backfill %s (%d/%d): %d rows", symbol, i, total, rows)
        if symbol_delay and i < total:
            _sleep(symbol_delay)

    LOG.info(
        "backfill complete: %d loaded, %d skipped, %d rows total "
        "(coverage from %s)",
        len(report.loaded), len(report.skipped), report.total_rows,
        default_start.isoformat(),
    )
    return report
