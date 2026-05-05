"""Volatility regime classification based on ATR percentile rank.

REQ-VOL-04-4: Classify regime as low/normal/high/extreme.
"""

from __future__ import annotations

import logging
from typing import Any

from trading.db.session import connection

LOG = logging.getLogger(__name__)


def classify_regime(ticker: str, current_atr_pct: float) -> str:
    """Classify volatility regime based on historical ATR percentile.

    REQ-VOL-04-4:
    - low: below 25th percentile of 1-year history
    - normal: 25th to 75th percentile
    - high: 75th to 90th percentile
    - extreme: above 90th percentile

    Args:
        ticker: Stock code.
        current_atr_pct: Today's ATR percentage.

    Returns:
        Regime string: 'low', 'normal', 'high', or 'extreme'.
    """
    # Fetch 1-year ATR history for percentile calculation
    sql = """
        SELECT atr_pct
          FROM atr_cache
         WHERE ticker = %s
         ORDER BY date DESC
         LIMIT 250
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (ticker,))
            rows = list(cur.fetchall())
    except Exception as e:
        LOG.warning("Regime classification DB error for %s: %s", ticker, e)
        return "normal"  # Default to normal if history unavailable

    if len(rows) < 20:
        # Not enough history for meaningful percentile
        return _classify_by_absolute(current_atr_pct)

    historical_values = sorted(r["atr_pct"] for r in rows)
    n = len(historical_values)

    p25 = historical_values[int(n * 0.25)]
    p75 = historical_values[int(n * 0.75)]
    p90 = historical_values[int(n * 0.90)]

    if current_atr_pct > p90:
        return "extreme"
    elif current_atr_pct > p75:
        return "high"
    elif current_atr_pct < p25:
        return "low"
    else:
        return "normal"


def _classify_by_absolute(atr_pct: float) -> str:
    """Fallback classification using absolute ATR thresholds for Korean stocks."""
    if atr_pct > 5.0:
        return "extreme"
    elif atr_pct > 3.0:
        return "high"
    elif atr_pct < 1.0:
        return "low"
    else:
        return "normal"
