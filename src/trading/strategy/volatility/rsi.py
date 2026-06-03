"""RSI(14) computation.

Extracted from ``screener.daily_screen`` (the inline RSI block) so the position
watchdog (SPEC-TRADING-040 M1c stagnation rotation) and the screener share ONE
RSI implementation instead of two copies. The formula is byte-for-byte the same
simple-average Wilder-style RSI the screener already used:

    avg_gain / avg_loss over the last 14 daily diffs -> RSI = 100 - 100/(1+RS).

OHLCV is read from the same ``ohlcv`` table the screener / ATR use.

@MX:SPEC: SPEC-TRADING-040
"""

from __future__ import annotations

import logging

from trading.db.session import connection

LOG = logging.getLogger(__name__)

RSI_PERIOD: int = 14
# Need RSI_PERIOD diffs -> RSI_PERIOD + 1 closes (screener required >= 20 days).
_MIN_CLOSES: int = RSI_PERIOD + 1


def rsi_from_closes(closes: list[float], period: int = RSI_PERIOD) -> float | None:
    """RSI from a chronological list of closes (pure — unit-testable).

    Returns None when there are too few closes to form ``period`` diffs. Mirrors
    ``screener.daily_screen``: simple average of gains/losses over the last
    ``period`` diffs; all-up -> 100, all-down -> 0, flat -> 50.
    """
    if len(closes) < period + 1:
        return None
    diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d for d in diffs[-period:] if d > 0]
    losses = [-d for d in diffs[-period:] if d < 0]
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    if avg_loss > 0:
        return 100.0 - (100.0 / (1 + (avg_gain / avg_loss)))
    return 100.0 if avg_gain > 0 else 50.0


def compute_rsi(ticker: str, period: int = RSI_PERIOD) -> float | None:
    """Compute RSI(14) for a ticker from recent OHLCV, or None if unavailable.

    Reads the most-recent closes from the ``ohlcv`` table (same source as
    ``compute_atr``). Any DB error or insufficient history returns None so the
    caller can defensively skip (REQ-040-1c).
    """
    lookback = period + 20  # buffer, matches the screener's >= 20-day guard
    sql = """
        SELECT close
          FROM ohlcv
         WHERE symbol = %s
         ORDER BY ts DESC
         LIMIT %s
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (ticker, lookback))
            rows = list(cur.fetchall())
    except Exception as e:
        LOG.warning("RSI computation DB error for %s: %s", ticker, e)
        return None

    if len(rows) < _MIN_CLOSES:
        return None
    # rows are newest-first -> reverse to chronological for the diff math.
    closes = [float(r["close"]) for r in reversed(rows)]
    return rsi_from_closes(closes, period)
