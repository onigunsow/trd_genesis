"""FRED adapter — Fed/US macro indicators."""

from __future__ import annotations

import logging
from datetime import date

from trading.config import BACKFILL_START_DATE, get_settings
from trading.data.cache import upsert_macro

LOG = logging.getLogger(__name__)
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
    # 2026-08-16 크레딧 등급별 스프레드 — HY 지수만으론 꼬리(약한 발행사) 스트레스가 안 보인다.
    "BAMLH0A3HYC",    # ICE BofA CCC & Lower OAS (HY 꼬리 — K자형 크레딧 감지)
    "BAMLC0A4CBBB",   # ICE BofA BBB OAS (IG 최하단, 강등 후보군)
    "BAMLC0A0CM",     # ICE BofA US Corporate (IG) OAS
    "NFCI",           # Chicago Fed National Financial Conditions Index (주간)
)


def fetch_default_incremental(lookback_days: int = 14) -> int:
    """DEFAULT_SERIES 를 마지막 캐시일-lookback 부터 오늘까지 증분 갱신. 시리즈별 실패 격리.

    FRED 는 최근 값을 소급 수정하므로 lookback 만큼 겹쳐 받아 upsert 한다.
    """
    from datetime import timedelta

    from trading.data.cache import macro_latest_ts

    today = date.today()
    total = 0
    for sid in DEFAULT_SERIES:
        last = macro_latest_ts(SOURCE, sid)
        if last:
            start = last - timedelta(days=lookback_days)
        else:
            start = date.fromisoformat(BACKFILL_START_DATE)
        try:
            total += fetch_series(sid, start, today)
        except Exception:  # 한 시리즈 실패가 나머지를 막지 않는다
            LOG.warning("FRED %s incremental fetch failed", sid, exc_info=True)
    return total


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
