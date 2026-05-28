"""yfinance adapter — global assets (S&P500, VIX, USD/KRW)."""

from __future__ import annotations

import logging
from datetime import date

from trading.data.cache import cached_range, upsert_ohlcv

LOG = logging.getLogger(__name__)
SOURCE = "yfinance"

# Common Yahoo symbols used in macro persona context.
DEFAULT_SYMBOLS = ("^GSPC", "^IXIC", "^VIX", "KRW=X", "GLD", "TLT", "DX=F")
# DX=F = ICE U.S. Dollar Index futures (DXY).


def fetch_ohlcv(symbol: str, start: date, end: date) -> int:
    import yfinance as yf  # lazy

    df = yf.download(
        symbol,
        start=start.isoformat(),
        end=end.isoformat(),
        progress=False,
        auto_adjust=False,
    )
    if df is None or df.empty:
        return 0

    # Yahoo returns MultiIndex when multiple tickers, single index when one — flatten.
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)

    rows = []
    for ts, row in df.iterrows():
        rows.append({
            "ts": ts.date() if hasattr(ts, "date") else ts,
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": int(row.get("Volume", 0) or 0),
            "adj_close": float(row.get("Adj Close", row["Close"])),
        })
    return upsert_ohlcv(SOURCE, symbol, rows)


def fetch_incremental(symbol: str, default_start: date) -> int:
    from datetime import date as date_t, timedelta
    today = date_t.today()
    rng = cached_range(SOURCE, symbol)
    start = (rng[1] + timedelta(days=1)) if rng else default_start
    if start > today:
        return 0
    return fetch_ohlcv(symbol, start, today)
