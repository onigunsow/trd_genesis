"""yfinance adapter — global assets (S&P500, VIX, USD/KRW)."""

from __future__ import annotations

import logging
from datetime import date

from trading.data.cache import cached_range, upsert_ohlcv

LOG = logging.getLogger(__name__)
SOURCE = "yfinance"

# Common Yahoo symbols used in macro persona context.
DEFAULT_SYMBOLS = (
    "^GSPC", "^IXIC", "^VIX", "KRW=X", "GLD", "TLT",
    "DX-Y.NYB",  # 달러인덱스 — DX=F 는 Yahoo 에서 사라짐(2026-08 실측 404)
    # 2026-08-16 크레딧·반도체 대리지표 — HYG/LQD(크레딧 ETF),
    # MU·SOXX(삼성/하이닉스 원화채 부재 대체)
    "HYG", "LQD", "MU", "SOXX",
)


def fetch_default_incremental(default_start: date) -> int:
    """DEFAULT_SYMBOLS 증분 갱신(심볼별 실패 격리)."""
    total = 0
    for sym in DEFAULT_SYMBOLS:
        try:
            total += fetch_incremental(sym, default_start)
        except Exception:
            LOG.warning("yfinance %s incremental fetch failed", sym, exc_info=True)
    return total
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
