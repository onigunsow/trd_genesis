"""Market data tool functions — pure data fetchers wrapping context.py logic.

REQ-TOOL-01-3: Standalone functions wrapping existing context.py logic.
REQ-TOOL-01-8: No Anthropic API or persona calls. Data-only.
"""

from __future__ import annotations

from typing import Any

from trading.personas.context import (
    _flows_5d,
    _fundamentals,
    _latest_close,
    _latest_macro,
    _recent_disclosures,
    _technicals,
)


def get_macro_indicators(series_ids: list[str]) -> dict[str, Any]:
    """Fetch latest macro indicator values from FRED/ECOS cache.

    Args:
        series_ids: List of FRED or ECOS series identifiers.

    Returns:
        Dict mapping series_id to its latest value and timestamp.
    """
    results: dict[str, Any] = {}
    for sid in series_ids:
        # Try FRED first, then ECOS
        row = _latest_macro("fred", sid)
        if not row:
            row = _latest_macro("ecos", sid)
        if row:
            results[sid] = {
                "value": row["value"],
                "ts": str(row["ts"]),
                "units": row.get("units", ""),
            }
        else:
            results[sid] = {"value": None, "ts": None, "error": "not_found"}
    return results


def get_global_assets(symbols: list[str], days: int = 10) -> dict[str, Any]:
    """Fetch recent global asset prices from yfinance cache.

    Args:
        symbols: Yahoo Finance symbols (e.g. ^GSPC, ^VIX, KRW=X).
        days: Number of recent days to fetch.

    Returns:
        Dict mapping symbol to latest close price and change info.
    """
    results: dict[str, Any] = {}
    for sym in symbols:
        info = _latest_close("yfinance", sym, days=days)
        if info:
            results[sym] = {
                "close": info["close"],
                "pct_change_1d": info["pct_change_1d"],
                "ts": info["ts"],
            }
        else:
            results[sym] = {"close": None, "error": "no_data"}
    return results


def get_ticker_technicals(ticker: str, lookback_days: int = 150) -> dict[str, Any]:
    """Compute technical indicators for a KRX stock.

    Args:
        ticker: KRX stock code (e.g. '005930').
        lookback_days: Days of OHLCV for calculation.

    Returns:
        Dict with close, ma20, ma60, rsi14, vs_ma20_pct or error.
    """
    result = _technicals(ticker, lookback_days)
    if result is None:
        return {"error": "insufficient_data", "ticker": ticker}
    return result


def get_ticker_fundamentals(ticker: str) -> dict[str, Any]:
    """Fetch fundamental data for a KRX stock.

    Args:
        ticker: KRX stock code.

    Returns:
        Dict with market_cap, per, pbr, eps, bps, div_yield or error.
    """
    result = _fundamentals(ticker)
    if result is None:
        return {"error": "not_found", "ticker": ticker}
    # Convert date fields to string for JSON serialization
    cleaned = {}
    for k, v in result.items():
        if hasattr(v, "isoformat"):
            cleaned[k] = v.isoformat()
        else:
            cleaned[k] = v
    return cleaned


def get_ticker_flows(ticker: str, days: int = 5) -> dict[str, Any]:
    """Fetch cumulative foreign/institution/individual net buying.

    Args:
        ticker: KRX stock code.
        days: Lookback period (DB query uses 7-day window).

    Returns:
        Dict with foreign_5d, institution_5d, individual_5d or error.
    """
    result = _flows_5d(ticker)
    if result is None:
        return {"error": "no_flow_data", "ticker": ticker}
    return result


def get_recent_disclosures(tickers: list[str], days: int = 3) -> list[dict[str, Any]]:
    """Fetch recent DART disclosures for specified tickers.

    Args:
        tickers: List of KRX stock codes.
        days: Recent days to search.

    Returns:
        List of disclosure dicts with rcept_dt, corp_name, stock_code, report_nm.
    """
    return _recent_disclosures(tickers, days=days)
