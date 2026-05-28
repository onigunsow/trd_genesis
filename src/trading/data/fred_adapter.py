"""FRED adapter — Fed/US macro indicators."""

from __future__ import annotations

from datetime import date

from trading.config import get_settings
from trading.data.cache import upsert_macro

SOURCE = "fred"

# Series of interest for the Macro persona (M4 + M5 정밀화).
DEFAULT_SERIES = (
    "DFF",            # Federal Funds Effective Rate
    "DGS10",          # 10Y Treasury yield
    "DGS2",           # 2Y Treasury yield
    "T10Y2Y",         # 10Y-2Y spread (recession indicator)
    "CPIAUCSL",       # CPI (All Urban Consumers, monthly)
    "UNRATE",         # Unemployment rate
    "DEXKOUS",        # Korea / U.S. exchange rate
    # M5 정밀화 추가
    "RRPONTSYD",      # Overnight Reverse Repo (역레포 잔고, 유동성 신호)
    "BAMLH0A0HYM2",   # ICE BofA US High Yield OAS (HY 스프레드, 신용시장)
    "DCOILWTICO",     # WTI 원유 가격
    "STLFSI4",        # St. Louis Fed Financial Stress Index (TED 대체)
    "DTWEXBGS",       # Trade-weighted USD index (broad, 달러인덱스 대체)
)


def fetch_series(series_id: str, start: date, end: date) -> int:
    """Fetch one FRED series and upsert to macro_indicators."""
    from fredapi import Fred  # lazy

    s = get_settings()
    if s.data_apis.fred_api_key is None:
        raise RuntimeError("FRED_API_KEY missing")

    fred = Fred(api_key=s.data_apis.fred_api_key.get_secret_value())
    series = fred.get_series(series_id, observation_start=start, observation_end=end)

    rows = []
    for ts, value in series.items():
        if value is None:
            continue
        try:
            v = float(value)
        except (ValueError, TypeError):
            continue
        rows.append({
            "ts": ts.date() if hasattr(ts, "date") else ts,
            "value": v,
        })
    return upsert_macro(SOURCE, series_id, rows)
