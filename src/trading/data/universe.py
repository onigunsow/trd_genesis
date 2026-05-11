"""SPEC-TRADING-019 REQ-019-6: Data universe registry.

Single source of truth for which tickers the data refresh layer should keep
hot in the cache. Returns the union of:

    DEFAULT_WATCHLIST  U  screened_tickers.json  U  active holdings (positions)
                       U  KOSPI200 top-50 (pykrx dynamic)

Per-source failures degrade gracefully — a failing source emits a warning and
is skipped, but the function never returns an empty list (catastrophic case
guard, REQ-019-6 (c)). The function is shared by refresh_ohlcv/flows/
fundamentals (REQ-019-1/2/3).

KOSPI200 source decision (Q-1, 2026-05-11): pykrx dynamic via
``pykrx.stock.get_index_portfolio_deposit_file('1028')``. Cached per call.
"""

# @MX:ANCHOR: SPEC-019 REQ-019-6 single source of truth for refresh universe
# @MX:REASON: fan_in >= 3 (refresh_ohlcv, refresh_flows, refresh_fundamentals)
# @MX:SPEC: SPEC-TRADING-019

from __future__ import annotations

import logging

from trading.db.session import connection
from trading.personas.context import DEFAULT_WATCHLIST
from trading.screener.daily_screen import load_screened_tickers

LOG = logging.getLogger(__name__)

KOSPI200_INDEX_CODE = "1028"
KOSPI200_TOP_N = 50


def _read_screened_tickers() -> list[str]:
    """Load screened_tickers.json via existing daily_screen helper."""
    return list(load_screened_tickers())


def _read_active_holdings() -> list[str]:
    """SPEC-019: Query positions table for tickers with shares > 0.

    Pattern lifted from `trading.jit.pipeline._resolve_tickers` (line 212).
    """
    out: list[str] = []
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT ticker FROM positions WHERE shares > 0")
        for row in cur.fetchall():
            t = row.get("ticker") if isinstance(row, dict) else row[0]
            if t:
                out.append(str(t))
    return out


def _fetch_kospi200_from_pykrx() -> list[str]:
    """SPEC-019 (Q-1 decision): pykrx KOSPI200 deposit-file fetch.

    Indirected through a helper so tests can monkeypatch without invoking the
    real pykrx HTTP call.
    """
    from pykrx import stock  # lazy import (heavy)

    return list(stock.get_index_portfolio_deposit_file(KOSPI200_INDEX_CODE))


def _read_kospi200_top50() -> list[str]:
    """Return top-50 KOSPI200 tickers (or [] on failure)."""
    try:
        all_tickers = _fetch_kospi200_from_pykrx()
    except Exception as exc:
        LOG.warning("KOSPI200 source unavailable: %s", exc)
        return []
    return list(all_tickers[:KOSPI200_TOP_N])


def _safe_collect(label: str, fn) -> list[str]:
    """Call a source loader and swallow failures with a WARNING."""
    try:
        return list(fn() or [])
    except Exception as exc:
        LOG.warning("universe source '%s' failed: %s", label, exc)
        return []


def get_data_universe() -> list[str]:
    """REQ-019-6: union of 4 sources, sorted and deduplicated.

    Returns:
        Sorted list of 6-digit ticker codes (e.g. ['000660', '005380', ...]).
        Falls back to DEFAULT_WATCHLIST if every other source fails — never
        returns an empty list when DEFAULT_WATCHLIST is non-empty.
    """
    default = list(DEFAULT_WATCHLIST)
    screened = _safe_collect("screened_tickers", _read_screened_tickers)
    holdings = _safe_collect("active_holdings", _read_active_holdings)
    kospi200 = _safe_collect("kospi200_top50", _read_kospi200_top50)

    universe: set[str] = set()
    for src in (default, screened, holdings, kospi200):
        for t in src:
            if isinstance(t, str) and t:
                universe.add(t)

    if not universe:
        # Catastrophic case (REQ-019-6 c): always return at least DEFAULT.
        universe = set(DEFAULT_WATCHLIST)

    return sorted(universe)
