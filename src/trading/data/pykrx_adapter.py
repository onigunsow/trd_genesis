"""pykrx adapter — Korean stock OHLCV (no API key).

Backfills cached OHLCV using pykrx.stock.get_market_ohlcv_by_date.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import sys
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date

from trading.data.cache import (
    cached_range,
    upsert_flows,
    upsert_fundamentals,
    upsert_ohlcv,
)

LOG = logging.getLogger(__name__)
SOURCE = "pykrx"


@contextmanager
def _quiet_pykrx() -> Generator[None]:
    """pykrx HTTP 호출 구간의 stdout/stderr 소음을 억제하는 컨텍스트 매니저.

    pykrx는 KRX 세션 만료·재로그인 시도를 bare print()로 stdout에 출력하고,
    내부 로거의 TypeError(not all arguments converted during string formatting)가
    Python의 '--- Logging error ---' + 전체 트레이스백을 stderr에 쏟아낸다
    (2026-06-25 실측: 17,046 로그라인/일, print x107 + 트레이스백 x321).

    이 CM은 pykrx의 실제 HTTP 호출 행(stock.get_*) 만 감쌌을 때만 사용한다.
    DataFrame 처리·자체 logging 구문은 CM 밖에 있으므로 우리 로그는 영향 없음.

    예외는 억제하지 않고 그대로 전파한다 — 호출자(_run_batch/_safe_collect)가
    except로 잡아 단일 WARNING을 남긴다.

    Python 레벨 redirect_stdout/stderr 만으로는 부족하다 — pykrx 내부 트레이스백·
    logging 핸들러(생성 시점의 원본 stderr fd 에 바인딩)·일부 print 는 sys.stdout/
    stderr 객체 교체를 우회해 fd 1/2 로 직접 나간다(2026-06-25 라이브 실측). 따라서
    os.dup2 로 fd 1/2 자체를 /dev/null 로 돌려 모든 채널(print·logging·traceback·
    C 레벨)을 차단한다. Python 레벨 redirect 도 병행해 우리 코드가 이 구간에서
    로깅하더라도 throwaway 로 흘려보낸다.

    주의: fd 리다이렉트는 프로세스-글로벌이다. 이 구간(단일 stock.get_* 호출,
    종목당 ~0.5s)에 다른 스레드가 로깅하면 그 출력도 함께 버려질 수 있다.
    pykrx 호출이 짧고 _run_batch for-loop 안에서 직렬 실행되므로 영향은 미미하다.
    """
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    saved_out_fd = os.dup(1)
    saved_err_fd = os.dup(2)
    sys.stdout.flush()
    sys.stderr.flush()
    os.dup2(devnull_fd, 1)
    os.dup2(devnull_fd, 2)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            with contextlib.redirect_stderr(io.StringIO()):
                yield
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(saved_out_fd, 1)
        os.dup2(saved_err_fd, 2)
        os.close(devnull_fd)
        os.close(saved_out_fd)
        os.close(saved_err_fd)


def fetch_ohlcv(symbol: str, start: date, end: date) -> int:
    """Fetch OHLCV for a Korean ticker and upsert to cache. Returns row count."""
    from pykrx import stock  # lazy import (heavy)

    s = start.strftime("%Y%m%d")
    e = end.strftime("%Y%m%d")
    with _quiet_pykrx():
        df = stock.get_market_ohlcv_by_date(s, e, symbol)
    if df is None or df.empty:
        return 0

    rows = []
    for ts, row in df.iterrows():
        rows.append({
            "ts": ts.date() if hasattr(ts, "date") else ts,
            "open": row.get("시가", row.get("Open", 0)),
            "high": row.get("고가", row.get("High", 0)),
            "low": row.get("저가", row.get("Low", 0)),
            "close": row.get("종가", row.get("Close", 0)),
            "volume": row.get("거래량", row.get("Volume", 0)),
        })
    return upsert_ohlcv(SOURCE, symbol, rows)


def fetch_incremental(symbol: str, default_start: date) -> int:
    """Fetch only data after the last cached date (or default_start if none)."""
    from datetime import date as date_t
    from datetime import timedelta

    today = date_t.today()
    rng = cached_range(SOURCE, symbol)
    start = (rng[1] + timedelta(days=1)) if rng else default_start
    if start > today:
        return 0
    return fetch_ohlcv(symbol, start, today)


def fetch_fundamentals(symbol: str, start: date, end: date) -> int:
    """Fetch daily fundamentals (PER/PBR/EPS/BPS/Div). Upsert to fundamentals table."""
    from pykrx import stock  # lazy import

    s = start.strftime("%Y%m%d")
    e = end.strftime("%Y%m%d")
    with _quiet_pykrx():
        df = stock.get_market_fundamental_by_date(s, e, symbol)
    if df is None or df.empty:
        return 0

    # Optional market cap (separate API)
    try:
        with _quiet_pykrx():
            cap_df = stock.get_market_cap_by_date(s, e, symbol)
    except Exception:
        cap_df = None

    rows = []
    for ts, row in df.iterrows():
        d = ts.date() if hasattr(ts, "date") else ts
        cap = None
        if cap_df is not None and ts in cap_df.index:
            cap = cap_df.loc[ts].get("시가총액")
        rows.append({
            "ts": d,
            "market_cap": int(cap) if cap is not None else None,
            "per": row.get("PER"),
            "pbr": row.get("PBR"),
            "eps": row.get("EPS"),
            "bps": row.get("BPS"),
            "div_yield": row.get("DIV"),
            "dps": row.get("DPS"),
        })
    return upsert_fundamentals(symbol, rows)


def fetch_flows(symbol: str, start: date, end: date) -> int:
    """Fetch daily foreign/institution/individual net trading values."""
    from pykrx import stock  # lazy import

    s = start.strftime("%Y%m%d")
    e = end.strftime("%Y%m%d")
    with _quiet_pykrx():
        df = stock.get_market_trading_value_by_date(s, e, symbol)
    if df is None or df.empty:
        return 0

    rows = []
    for ts, row in df.iterrows():
        d = ts.date() if hasattr(ts, "date") else ts
        rows.append({
            "ts": d,
            "foreign_net": int(row.get("외국인합계", row.get("외국인", 0)) or 0),
            "institution_net": int(row.get("기관합계", 0) or 0),
            "individual_net": int(row.get("개인", 0) or 0),
        })
    return upsert_flows(symbol, rows)
