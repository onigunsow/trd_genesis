"""ATR (Average True Range) computation.

REQ-VOL-04-2: Standard 14-day ATR using EMA smoothing.
REQ-VOL-04-6: Handles data gaps gracefully.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from trading.db.session import connection

LOG = logging.getLogger(__name__)

# Minimum trading days required for ATR computation (REQ-VOL-04-6)
MIN_DAYS_FOR_ATR: int = 5
# Standard ATR period
ATR_PERIOD: int = 14


def compute_atr(ticker: str, period: int = ATR_PERIOD) -> dict[str, Any] | None:
    """Compute ATR for a ticker using most recent OHLCV data.

    REQ-VOL-04-2: True Range and EMA-smoothed ATR.
    REQ-VOL-04-6: Returns None if fewer than MIN_DAYS_FOR_ATR days available.

    Args:
        ticker: Stock code (e.g. '005930').
        period: EMA period for ATR smoothing (default 14).

    Returns:
        Dict with atr_14, atr_pct, close_price, or None if insufficient data.
    """
    # Fetch recent OHLCV (need period + some buffer for EMA warm-up)
    lookback = period + 20  # Extra days for EMA initialization
    sql = """
        SELECT date, open, high, low, close, volume
          FROM ohlcv
         WHERE ticker = %s
         ORDER BY date DESC
         LIMIT %s
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (ticker, lookback))
            rows = list(cur.fetchall())
    except Exception as e:
        LOG.warning("ATR computation DB error for %s: %s", ticker, e)
        return None

    if len(rows) < MIN_DAYS_FOR_ATR:
        LOG.info("Insufficient data for ATR: %s has %d days (min %d)", ticker, len(rows), MIN_DAYS_FOR_ATR)
        return None

    # Reverse to chronological order
    rows.reverse()

    # Compute True Range for each day (REQ-VOL-04-2)
    true_ranges: list[float] = []
    for i in range(1, len(rows)):
        high = rows[i]["high"]
        low = rows[i]["low"]
        prev_close = rows[i - 1]["close"]

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        true_ranges.append(tr)

    if not true_ranges:
        return None

    # Compute EMA of True Range
    ema_period = min(period, len(true_ranges))
    atr = _ema(true_ranges, ema_period)

    latest_close = rows[-1]["close"]
    if latest_close <= 0:
        return None

    atr_pct = (atr / latest_close) * 100.0

    return {
        "atr_14": round(atr, 2),
        "atr_pct": round(atr_pct, 4),
        "close_price": latest_close,
        "date": rows[-1]["date"],
    }


def _ema(values: list[float], period: int) -> float:
    """Compute Exponential Moving Average of a series.

    Uses the standard EMA formula: EMA(t) = alpha * value(t) + (1-alpha) * EMA(t-1)
    where alpha = 2 / (period + 1).
    """
    if not values:
        return 0.0

    alpha = 2.0 / (period + 1)

    # Initialize EMA with SMA of first 'period' values
    if len(values) >= period:
        ema = sum(values[:period]) / period
        start = period
    else:
        ema = values[0]
        start = 1

    for i in range(start, len(values)):
        ema = alpha * values[i] + (1 - alpha) * ema

    return ema
