"""SPEC-TRADING-019 REQ-019-6 + SPEC-TRADING-020 REQ-020-1: Data universe registry.

Single source of truth for which tickers the data refresh layer should keep
hot in the cache.

SPEC-020 semantics (revised, 2026-05-12):

    if screened_tickers non-empty (autonomous discovery active):
        universe = screened U holdings U KOSPI200_top50  (DEFAULT excluded)
    else (cold-start fallback):
        universe = DEFAULT_WATCHLIST U holdings U KOSPI200_top50

The previous behaviour (DEFAULT always merged) caused incidents where DEFAULT
tickers acted as a hardcoded bias even when daily_screen had produced an
authoritative screened list. See SPEC-020 for the 2026-05-12 055550 incident.

Per-source failures degrade gracefully — a failing source emits a warning and
is skipped, but the function never returns an empty list (catastrophic case
guard, REQ-019-6 (c)). The function is shared by refresh_ohlcv/flows/
fundamentals (REQ-019-1/2/3) and blocked_cache (SPEC-020 REQ-020-2).

KOSPI200 source decision (Q-1, 2026-05-11): pykrx dynamic via
``pykrx.stock.get_index_portfolio_deposit_file('1028')``. Cached per call.
"""

# @MX:ANCHOR: SPEC-019 REQ-019-6 + SPEC-020 REQ-020-1 single source of truth for universe
# @MX:REASON: fan_in >= 4 (refresh_ohlcv, refresh_flows, refresh_fundamentals, blocked_cache)
# @MX:SPEC: SPEC-TRADING-019, SPEC-TRADING-020

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
    """SPEC-022 REQ-022-2: Query positions for tickers with qty > 0.

    The positions table column is `qty` (verified 2026-05-14 via `\\d positions`).
    SPEC-019 originally assumed `shares`, which raised UndefinedColumn every
    cycle. Wrapped in a defensive try/except so any future schema drift or
    transient DB error degrades to an empty list (with WARNING) instead of
    propagating out of universe assembly.
    """
    try:
        out: list[str] = []
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT DISTINCT ticker FROM positions WHERE qty > 0")
            for row in cur.fetchall():
                t = row.get("ticker") if isinstance(row, dict) else row[0]
                if t:
                    out.append(str(t))
        return out
    except Exception as exc:
        LOG.warning("active_holdings query failed (schema mismatch?): %s", exc)
        return []


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


def _read_dynamic_tickers() -> list[str]:
    """SPEC-023 REQ-023-5 (a): contribution from the dynamic_universe registry.

    Wrapped in its own helper so universe assembly stays decoupled from the
    table's import surface and so tests can monkeypatch a stub without going
    through the DB layer.
    """
    from trading.data.dynamic_universe import list_active

    return list(list_active())


def _safe_collect(label: str, fn) -> list[str]:
    """Call a source loader and swallow failures with a WARNING."""
    try:
        return list(fn() or [])
    except Exception as exc:
        LOG.warning("universe source '%s' failed: %s", label, exc)
        return []


def get_data_universe() -> list[str]:
    """REQ-019-6 + SPEC-020 REQ-020-1: screened-first, DEFAULT-as-fallback.

    Returns:
        Sorted list of 6-digit ticker codes (e.g. ['000660', '005380', ...]).
        When ``screened_tickers.json`` is non-empty, DEFAULT_WATCHLIST is
        excluded (autonomous discovery is authoritative). Otherwise DEFAULT
        is used as cold-start fallback. Falls back to DEFAULT_WATCHLIST if
        every other source fails — never returns an empty list when
        DEFAULT_WATCHLIST is non-empty.
    """
    screened = _safe_collect("screened_tickers", _read_screened_tickers)
    dynamic = _safe_collect("dynamic_tickers", _read_dynamic_tickers)
    holdings = _safe_collect("active_holdings", _read_active_holdings)
    kospi200 = _safe_collect("kospi200_top50", _read_kospi200_top50)

    universe: set[str] = set()
    # SPEC-020 REQ-020-1: DEFAULT is included only on cold-start (empty screened).
    # SPEC-023 REQ-023-5: dynamic_tickers always contribute (priority just below
    # screened, above holdings/KOSPI200/DEFAULT). They survive a cold-start
    # screened-empty event so previously auto-expanded tickers stay monitored.
    primary = screened if screened else list(DEFAULT_WATCHLIST)
    for src in (primary, dynamic, holdings, kospi200):
        for t in src:
            if isinstance(t, str) and t:
                universe.add(t)

    if not universe:
        # Catastrophic case (REQ-019-6 c): always return at least DEFAULT.
        universe = set(DEFAULT_WATCHLIST)

    return sorted(universe)
