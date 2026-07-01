"""SPEC-TRADING-019 REQ-019-6 + SPEC-TRADING-020 REQ-020-1: Data universe registry.

Single source of truth for which tickers the data refresh layer should keep
hot in the cache.

SPEC-020 semantics (revised, 2026-05-12):

    if screened_tickers non-empty (autonomous discovery active):
        universe = screened U holdings U KOSPI200_top50  (DEFAULT excluded)
    else (cold-start fallback):
        universe = DEFAULT_WATCHLIST U holdings U KOSPI200_top50

The previous behaviour (DEFAULT always merged) caused incidents where DEFAULT
tickers acted as a hardcoded bias even when daily_screen had produced an
authoritative screened list. See SPEC-020 for the 2026-05-12 055550 incident.

Per-source failures degrade gracefully — a failing source emits a warning and
is skipped, but the function never returns an empty list (catastrophic case
guard, REQ-019-6 (c)). The function is shared by refresh_ohlcv/flows/
fundamentals (REQ-019-1/2/3) and blocked_cache (SPEC-020 REQ-020-2).

KOSPI200 source decision (Q-1, 2026-05-11): pykrx dynamic via
``pykrx.stock.get_index_portfolio_deposit_file('1028')``. 멤버십은 분기별
리밸런싱에만 바뀌므로 JSON 파일 캐시로 장외 재조회를 막는다 (SPEC-058-FIX).
"""

# @MX:ANCHOR: [AUTO] SPEC-019 REQ-019-6 + SPEC-020 REQ-020-1 single source of truth for universe
# @MX:REASON: fan_in >= 4 (refresh_ohlcv, refresh_flows, refresh_fundamentals, blocked_cache)
# @MX:SPEC: SPEC-TRADING-019, SPEC-TRADING-020

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import date, datetime, time
from pathlib import Path

import pytz

from trading.config import project_root
from trading.db.session import connection
from trading.personas.context import DEFAULT_WATCHLIST
from trading.scheduler.calendar import is_trading_day
from trading.screener.daily_screen import load_screened_tickers

LOG = logging.getLogger(__name__)

KOSPI200_INDEX_CODE = "1028"
KOSPI200_TOP_N = 50

# KRX 정규장 시간 (KST): 09:00 ~ 15:30
_KRX_OPEN = time(9, 0)
_KRX_CLOSE = time(15, 30)
_KST = pytz.timezone("Asia/Seoul")


def _now_kst() -> datetime:
    """현재 KST 시각 반환. 테스트에서 monkeypatch 용으로 분리."""
    return datetime.now(_KST)


def _kospi200_cache_path() -> Path:
    """KOSPI200 top-50 멤버십 캐시 파일 경로.

    테스트에서 monkeypatch 로 교체할 수 있도록 별도 함수로 분리.
    prod 경로: data/kospi200_top50.json (screened_tickers.json 과 동일 볼륨).
    """
    return project_root() / "data" / "kospi200_top50.json"


def _read_kospi200_cache() -> tuple[list[str], str | None]:
    """캐시 파일에서 KOSPI200 멤버십을 읽는다.

    Returns:
        (tickers, trading_day) — 파일 없음/파싱 오류 시 ([], None).
        절대 예외를 발생시키지 않는다 (방어적 try/except).
    """
    try:
        data = json.loads(_kospi200_cache_path().read_text())
        tickers = list(data.get("tickers", []))
        trading_day: str | None = data.get("trading_day")
        return tickers, trading_day
    except Exception as exc:
        LOG.debug("KOSPI200 캐시 읽기 실패 (무시): %s", exc)
        return [], None


def _write_kospi200_cache(tickers: list[str], day: date) -> None:
    """KOSPI200 멤버십 캐시를 원자적으로 파일에 쓴다.

    tmp 파일에 먼저 쓰고 os.replace() 로 교체해 부분 쓰기를 방지.
    쓰기 오류는 WARNING 으로 기록하고 삼킨다 — 캐시 실패가 유니버스 조립을 막으면 안 됨.
    """
    try:
        target = _kospi200_cache_path()
        payload = {
            "tickers": tickers,
            "trading_day": day.isoformat(),
            "fetched_at": _now_kst().isoformat(),
        }
        # 같은 디렉토리에 임시 파일 생성 → os.replace 원자적 교체
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f)
            os.replace(tmp_path, target)
        except Exception:
            # 임시 파일 정리 시도
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as exc:
        LOG.warning("KOSPI200 캐시 쓰기 실패 (무시): %s", exc)


def _read_screened_tickers() -> list[str]:
    """Load screened_tickers.json via existing daily_screen helper."""
    return list(load_screened_tickers())


def _read_active_holdings() -> list[str]:
    """SPEC-022 REQ-022-2: Query positions for tickers with qty > 0.

    The positions table column is `qty` (verified 2026-05-14 via `\\d positions`).
    SPEC-019 originally assumed `shares`, which raised UndefinedColumn every
    cycle. Wrapped in a defensive try/except so any future schema drift or
    transient DB error degrades to an empty list (with WARNING) instead of
    propagating out of universe assembly.
    """
    try:
        out: list[str] = []
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT DISTINCT ticker FROM positions WHERE qty > 0")
            for row in cur.fetchall():
                t = row.get("ticker") if isinstance(row, dict) else row[0]
                if t:
                    out.append(str(t))
        return out
    except Exception as exc:
        LOG.warning("active_holdings query failed (schema mismatch?): %s", exc)
        return []


def _fetch_kospi200_from_pykrx() -> list[str]:
    """SPEC-019 (Q-1 decision): pykrx KOSPI200 deposit-file fetch.

    Indirected through a helper so tests can monkeypatch without invoking the
    real pykrx HTTP call.

    pykrx_adapter._quiet_pykrx() 로 HTTP 호출을 감싸 pykrx의 bare print()/
    broken-logging 소음이 스케줄러 로그에 섞이지 않도록 한다.
    서킷 브레이커 확인 — OPEN이면 KrxCircuitOpen 발생, pykrx 미호출.
    예외는 그대로 전파 — _read_kospi200_top50 의 except가 처리함.
    """
    # 서킷 브레이커 확인 — OPEN이면 KrxCircuitOpen 발생, pykrx 미호출
    from trading.data.krx_circuit_breaker import _get_shared_breaker

    breaker = _get_shared_breaker()
    breaker.check_or_raise()

    from pykrx import stock  # lazy import (heavy)

    from trading.data.pykrx_adapter import _quiet_pykrx

    try:
        with _quiet_pykrx():
            result = list(stock.get_index_portfolio_deposit_file(KOSPI200_INDEX_CODE))
    except Exception:
        breaker.record_failure()
        raise

    breaker.record_success()
    return result


def _read_kospi200_top50() -> list[str]:
    """Return top-50 KOSPI200 tickers (or [] on failure or off-hours).

    캐시 전략 (SPEC-058-FIX):
    1. 당일 캐시 존재 → 바로 반환 (pykrx 미호출 — 멤버십은 분기별 변경)
    2. 캐시 미존재/구버전 + 장중 → pykrx 재조회 후 캐시 갱신
    3. 캐시 미존재/구버전 + 장외 → 구버전 캐시 반환 (없으면 [])

    장외 시간 가드: KRX 비거래일이거나 09:00~15:30 KST 범위 밖이면
    pykrx HTTP 요청을 건너뛴다. pykrx 는 장외 로그인 시도 시
    JSONDecodeError 와 TypeError 스팸을 자체 로거로 쏟아내므로
    HTTP 호출 자체를 막는 것이 유일한 해결책.
    """
    now = _now_kst()
    today = now.date()
    is_market = is_trading_day(today) and (_KRX_OPEN <= now.time() <= _KRX_CLOSE)

    cached_tickers, cached_day = _read_kospi200_cache()
    cache_fresh = bool(cached_tickers) and cached_day == today.isoformat()

    # 1. 당일 캐시 → pykrx 미호출 (멤버십은 분기별 변경으로 재조회 불필요)
    if cache_fresh:
        return list(cached_tickers)

    # 2. 캐시 구버전/미존재 + 장중 → 신규 조회 후 캐시 갱신
    if is_market:
        try:
            fresh = list(_fetch_kospi200_from_pykrx())[:KOSPI200_TOP_N]
        except Exception as exc:
            LOG.warning("KOSPI200 source unavailable: %s", exc)
            fresh = []
        if fresh:
            _write_kospi200_cache(fresh, today)
            return fresh
        # 장중 조회 실패/빈 결과 → 구버전 캐시 폴백으로 이동

    # 3. 장외이거나 장중 조회 실패 → 구버전 캐시 반환 (없으면 [])
    if cached_tickers:
        LOG.info(
            "KOSPI200 캐시 사용 (%d종목, %s) — pykrx 미호출",
            len(cached_tickers),
            cached_day,
        )
        return list(cached_tickers)

    LOG.info(
        "KOSPI200 캐시 없음 + 장외/실패 → [] 반환 (%s KST)",
        now.strftime("%H:%M"),
    )
    return []


def _read_dynamic_tickers() -> list[str]:
    """SPEC-023 REQ-023-5 (a): contribution from the dynamic_universe registry.

    Wrapped in its own helper so universe assembly stays decoupled from the
    table's import surface and so tests can monkeypatch a stub without going
    through the DB layer.
    """
    from trading.data.dynamic_universe import list_active

    return list(list_active())


def _safe_collect(label: str, fn) -> list[str]:
    """Call a source loader and swallow failures with a WARNING."""
    try:
        return list(fn() or [])
    except Exception as exc:
        LOG.warning("universe source '%s' failed: %s", label, exc)
        return []


def get_data_universe() -> list[str]:
    """REQ-019-6 + SPEC-020 REQ-020-1: screened-first, DEFAULT-as-fallback.

    Returns:
        Sorted list of 6-digit ticker codes (e.g. ['000660', '005380', ...]).
        When ``screened_tickers.json`` is non-empty, DEFAULT_WATCHLIST is
        excluded (autonomous discovery is authoritative). Otherwise DEFAULT
        is used as cold-start fallback. Falls back to DEFAULT_WATCHLIST if
        every other source fails — never returns an empty list when
        DEFAULT_WATCHLIST is non-empty.
    """
    screened = _safe_collect("screened_tickers", _read_screened_tickers)
    dynamic = _safe_collect("dynamic_tickers", _read_dynamic_tickers)
    holdings = _safe_collect("active_holdings", _read_active_holdings)
    kospi200 = _safe_collect("kospi200_top50", _read_kospi200_top50)

    universe: set[str] = set()
    # SPEC-020 REQ-020-1: DEFAULT is included only on cold-start (empty screened).
    # SPEC-023 REQ-023-5: dynamic_tickers always contribute (priority just below
    # screened, above holdings/KOSPI200/DEFAULT). They survive a cold-start
    # screened-empty event so previously auto-expanded tickers stay monitored.
    primary = screened if screened else list(DEFAULT_WATCHLIST)
    for src in (primary, dynamic, holdings, kospi200):
        for t in src:
            if isinstance(t, str) and t:
                universe.add(t)

    if not universe:
        # Catastrophic case (REQ-019-6 c): always return at least DEFAULT.
        universe = set(DEFAULT_WATCHLIST)

    return sorted(universe)
