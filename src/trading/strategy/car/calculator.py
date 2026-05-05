"""CAR (Cumulative Abnormal Return) computation from OHLCV data.

REQ-CAR-01-3: CAR formula implementation.
REQ-CAR-01-8: No lookahead data used.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from trading.db.session import connection

LOG = logging.getLogger(__name__)


def compute_car(
    ticker: str,
    event_date: date,
    benchmark_ticker: str = "KOSPI",
    windows: tuple[int, ...] = (1, 5, 10),
) -> dict[str, float | None]:
    """Compute CAR for given windows after event_date.

    REQ-CAR-01-3: CAR(N) = sum(abnormal_return(t)) for t in [event_day+1, event_day+N]
    REQ-CAR-01-8: Uses only returns from event_day+1 onward (no lookahead).

    Args:
        ticker: Stock code (e.g. '005930').
        event_date: Date the event occurred.
        benchmark_ticker: 'KOSPI' or 'KOSDAQ' for market return.
        windows: CAR window sizes in trading days.

    Returns:
        Dict with car_1d, car_5d, car_10d and benchmark returns.
    """
    max_window = max(windows)
    # Fetch trading days after event_date
    sql = """
        SELECT date, close
          FROM ohlcv
         WHERE ticker = %s AND date > %s
         ORDER BY date ASC
         LIMIT %s
    """
    benchmark_sql = """
        SELECT date, close
          FROM ohlcv
         WHERE ticker = %s AND date > %s
         ORDER BY date ASC
         LIMIT %s
    """

    try:
        with connection() as conn, conn.cursor() as cur:
            # Get stock prices after event
            cur.execute(sql, (ticker, event_date, max_window + 1))
            stock_rows = list(cur.fetchall())

            # Get benchmark prices after event
            cur.execute(benchmark_sql, (benchmark_ticker, event_date, max_window + 1))
            bench_rows = list(cur.fetchall())

            # Get the closing price on event_date for return calculation
            cur.execute(
                "SELECT close FROM ohlcv WHERE ticker = %s AND date <= %s ORDER BY date DESC LIMIT 1",
                (ticker, event_date),
            )
            prev_stock = cur.fetchone()

            cur.execute(
                "SELECT close FROM ohlcv WHERE ticker = %s AND date <= %s ORDER BY date DESC LIMIT 1",
                (benchmark_ticker, event_date),
            )
            prev_bench = cur.fetchone()

    except Exception as e:
        LOG.warning("CAR computation DB error for %s on %s: %s", ticker, event_date, e)
        return {f"car_{w}d": None for w in windows}

    if not prev_stock or not prev_bench or not stock_rows or not bench_rows:
        return {f"car_{w}d": None for w in windows}

    # Compute daily returns
    stock_prices = [prev_stock["close"]] + [r["close"] for r in stock_rows]
    bench_prices = [prev_bench["close"]] + [r["close"] for r in bench_rows]

    result: dict[str, float | None] = {}

    for w in windows:
        if len(stock_prices) < w + 1 or len(bench_prices) < w + 1:
            result[f"car_{w}d"] = None
            result[f"benchmark_return_{w}d"] = None
            continue

        # Cumulative returns
        cum_stock = 0.0
        cum_bench = 0.0
        cum_abnormal = 0.0

        for i in range(1, w + 1):
            stock_ret = (stock_prices[i] - stock_prices[i - 1]) / stock_prices[i - 1]
            bench_ret = (bench_prices[i] - bench_prices[i - 1]) / bench_prices[i - 1]
            abnormal_ret = stock_ret - bench_ret
            cum_abnormal += abnormal_ret
            cum_bench += bench_ret

        result[f"car_{w}d"] = round(cum_abnormal, 6)
        result[f"benchmark_return_{w}d"] = round(cum_bench, 6)

    return result


def get_volume_ratio(ticker: str, event_date: date, lookback: int = 20) -> float | None:
    """Compute volume on event_date relative to 20-day average.

    Returns ratio (e.g. 2.5 means 2.5x average volume).
    """
    sql = """
        SELECT date, volume
          FROM ohlcv
         WHERE ticker = %s AND date <= %s
         ORDER BY date DESC
         LIMIT %s
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (ticker, event_date, lookback + 1))
            rows = list(cur.fetchall())
    except Exception:
        return None

    if not rows:
        return None

    # First row is event_date (or closest before)
    event_vol = rows[0]["volume"] if rows[0]["date"] == event_date else None
    if event_vol is None or event_vol == 0:
        return None

    avg_vol = sum(r["volume"] for r in rows[1:]) / max(len(rows) - 1, 1)
    if avg_vol == 0:
        return None

    return round(event_vol / avg_vol, 2)
