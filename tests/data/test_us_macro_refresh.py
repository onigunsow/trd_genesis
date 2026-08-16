"""2026-08-16 — FRED/yfinance 일일 증분 갱신(시리즈별 실패 격리)."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from trading.data import fred_adapter, yfinance_adapter


def test_fred_incremental_isolates_failures_and_uses_lookback():
    calls = []

    def fake_fetch(sid, start, end):
        calls.append((sid, start))
        if sid == "DFF":
            raise RuntimeError("boom")
        return 1

    with (
        patch.object(fred_adapter, "fetch_series", side_effect=fake_fetch),
        patch.object(fred_adapter, "DEFAULT_SERIES", ("DFF", "BAMLH0A3HYC", "NEWONE")),
        patch("trading.data.cache.macro_latest_ts",
              side_effect=lambda src, sid: None if sid == "NEWONE" else date(2026, 5, 1)),
    ):
        n = fred_adapter.fetch_default_incremental(lookback_days=14)
    assert n == 2
    assert dict(calls)["BAMLH0A3HYC"] == date(2026, 4, 17)   # 14일 겹쳐 받음
    assert dict(calls)["NEWONE"] == date(2019, 1, 1)         # 미캐시 → 백필 시작일


def test_new_credit_series_registered():
    for sid in ("BAMLH0A3HYC", "BAMLC0A4CBBB", "BAMLC0A0CM", "NFCI"):
        assert sid in fred_adapter.DEFAULT_SERIES
    for sym in ("HYG", "LQD", "MU", "SOXX"):
        assert sym in yfinance_adapter.DEFAULT_SYMBOLS


def test_yfinance_incremental_isolates_failures():
    def fake(sym, start):
        if sym == "HYG":
            raise RuntimeError("boom")
        return 3
    with patch.object(yfinance_adapter, "fetch_incremental", side_effect=fake):
        n = yfinance_adapter.fetch_default_incremental(date(2019, 1, 1))
    assert n == 3 * (len(yfinance_adapter.DEFAULT_SYMBOLS) - 1)
