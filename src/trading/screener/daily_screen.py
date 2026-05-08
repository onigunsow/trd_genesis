"""Daily stock screener -- expand watchlist beyond hardcoded 5 stocks.

Runs at 06:35 KST cron (after micro_context build at 06:30).
Screens from OHLCV universe for tradeable candidates.
Results saved to data/screened_tickers.json.

Screening criteria (no LLM needed):
- Market cap > 1 trillion KRW (liquidity)
- Average daily volume value > 10 billion KRW (tradeable)
- RSI between 30-70 (not overheated, not crashed)
- OR: PER < 15 (value opportunity)
- OR: foreign 5-day net buy > 0 (smart money inflow)

Selects top 20 from this screen.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from trading.config import project_root
from trading.db.session import connection
from trading.personas.context import DEFAULT_WATCHLIST

LOG = logging.getLogger(__name__)
SCREEN_FILE = project_root() / "data" / "screened_tickers.json"
MAX_SCREENED = 20


def _get_universe_tickers() -> list[str]:
    """Get broad universe of tickers from OHLCV data (pykrx source).

    Selects tickers with recent trading activity (last 5 days).
    """
    sql = """
        SELECT DISTINCT symbol
          FROM ohlcv
         WHERE source = 'pykrx'
           AND ts >= CURRENT_DATE - 7
         ORDER BY symbol
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return [row["symbol"] for row in cur.fetchall()]


def _screen_ticker(ticker: str) -> dict[str, Any] | None:
    """Screen a single ticker against criteria. Returns score dict or None."""
    # Get fundamentals
    sql_fund = """
        SELECT market_cap, per, pbr, div_yield
          FROM fundamentals
         WHERE ticker = %s
         ORDER BY ts DESC LIMIT 1
    """
    # Get recent OHLCV for RSI + volume
    sql_ohlcv = """
        SELECT ts, close, volume
          FROM ohlcv
         WHERE source = 'pykrx' AND symbol = %s
         ORDER BY ts DESC LIMIT 60
    """
    # Get flows for foreign net
    sql_flows = """
        SELECT COALESCE(SUM(foreign_net), 0) AS f5
          FROM flows
         WHERE ticker = %s AND ts >= CURRENT_DATE - 7
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql_fund, (ticker,))
        fund = cur.fetchone()

        cur.execute(sql_ohlcv, (ticker,))
        ohlcv = list(cur.fetchall())

        cur.execute(sql_flows, (ticker,))
        flows = cur.fetchone()

    if not ohlcv or len(ohlcv) < 20:
        return None

    # Market cap filter: > 1 trillion KRW
    market_cap = float(fund["market_cap"]) if fund and fund.get("market_cap") else 0
    if market_cap < 1e12:
        return None

    # Volume filter: avg daily trading value > 10 billion KRW (last 20 days)
    closes = [float(r["close"]) for r in reversed(ohlcv[:20])]
    volumes = [int(r["volume"]) for r in reversed(ohlcv[:20])]
    avg_value = sum(c * v for c, v in zip(closes, volumes)) / len(closes)
    if avg_value < 10e9:
        return None

    # Calculate RSI(14)
    all_closes = [float(r["close"]) for r in reversed(ohlcv)]
    diffs = [all_closes[i] - all_closes[i - 1] for i in range(1, len(all_closes))]
    gains = [d for d in diffs[-14:] if d > 0]
    losses = [-d for d in diffs[-14:] if d < 0]
    avg_gain = sum(gains) / 14 if gains else 0.0
    avg_loss = sum(losses) / 14 if losses else 0.0
    rsi = 100.0 - (100.0 / (1 + (avg_gain / avg_loss))) if avg_loss > 0 else (
        100.0 if avg_gain > 0 else 50.0
    )

    # PER
    per = float(fund["per"]) if fund and fund.get("per") else None

    # Foreign 5-day net
    foreign_5d = int(flows["f5"]) if flows else 0

    # Scoring: multiple criteria (any one qualifies)
    score = 0.0
    reasons: list[str] = []

    # RSI 30-70 (healthy range)
    if 30 <= rsi <= 70:
        score += 2.0
        reasons.append(f"RSI={rsi:.1f} (healthy)")

    # PER < 15 (value)
    if per is not None and 0 < per < 15:
        score += 1.5
        reasons.append(f"PER={per:.1f} (value)")

    # Foreign net buy (smart money)
    if foreign_5d > 0:
        score += 1.0
        # Extra score for strong foreign buying
        if foreign_5d > 50e8:  # > 50 billion
            score += 0.5
        reasons.append(f"Foreign 5D net={foreign_5d / 1e8:+.0f} billion")

    # Market cap bonus (larger = more liquid)
    if market_cap > 10e12:
        score += 0.5

    # Must have at least one qualifying criterion
    if score < 1.0:
        return None

    return {
        "ticker": ticker,
        "score": score,
        "market_cap_t": market_cap / 1e12,
        "rsi": round(rsi, 1),
        "per": per,
        "foreign_5d_b": foreign_5d / 1e8,
        "avg_value_b": avg_value / 1e9,
        "reasons": reasons,
    }


def run() -> dict[str, Any]:
    """Run daily screening. Returns screened result dict."""
    universe = _get_universe_tickers()
    LOG.info("daily_screen: scanning %d tickers from pykrx universe", len(universe))

    # Exclude base watchlist (they're always included)
    base_set = set(DEFAULT_WATCHLIST)
    candidates = [t for t in universe if t not in base_set]

    results: list[dict[str, Any]] = []
    for ticker in candidates:
        try:
            result = _screen_ticker(ticker)
            if result:
                results.append(result)
        except Exception as e:  # noqa: BLE001
            LOG.debug("daily_screen: error screening %s: %s", ticker, e)
            continue

    # Sort by score descending, take top N
    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[:MAX_SCREENED]

    output = {
        "date": date.today().isoformat(),
        "total_scanned": len(candidates),
        "qualified": len(results),
        "selected": len(top),
        "tickers": [r["ticker"] for r in top],
        "details": top,
    }

    SCREEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCREEN_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    LOG.info("daily_screen: %d qualified, %d selected from %d scanned",
             len(results), len(top), len(candidates))
    return output


def load_screened_tickers() -> list[str]:
    """Load screened tickers from cache file. Returns empty list if stale/missing."""
    try:
        if SCREEN_FILE.exists():
            data = json.loads(SCREEN_FILE.read_text())
            # Accept if from today or yesterday (context build runs early)
            if data.get("date") in (
                date.today().isoformat(),
                (date.today().replace(day=date.today().day)).isoformat(),
            ):
                return data.get("tickers", [])
    except Exception:  # noqa: BLE001
        pass
    return []
