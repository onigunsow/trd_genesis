"""pykrx adapter — Korean stock OHLCV (no API key).

Backfills cached OHLCV using pykrx.stock.get_market_ohlcv_by_date.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import socket
import sys
import threading
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date

import requests

from trading.data.cache import (
    cached_range,
    upsert_flows,
    upsert_fundamentals,
    upsert_ohlcv,
)

LOG = logging.getLogger(__name__)
SOURCE = "pykrx"

# 2026-07-02 인시던트: pykrx가 requests에 타임아웃을 지정하지 않아 데드 소켓에서 무한 블로킹.
# _quiet_pykrx() 진입 시 소켓 전역 타임아웃 + requests per-request 타임아웃을 이 값으로 건다.
# 호출부가 테스트에서 PYKRX_SOCKET_TIMEOUT 환경변수로 재정의할 수 있도록 가드 내부에서 읽는다.
# 2026-07-06 재발: 값이 per-ticker 예산(REFRESH_PER_TICKER_TIMEOUT 기본 10s)보다 작아야 워커가
# 예산 초과로 방치되기 전에 스스로 풀려 finally(fd 복원·락 해제)를 실행한다. 30→8로 낮춤.
_PYKRX_SOCKET_TIMEOUT_S_DEFAULT = 8.0

# 2026-07-02 인시던트: APScheduler 스레드풀에서 여러 잡(16:00 daily_report ‖ ohlcv ‖ flows ‖
# fundamentals)이 동시에 _quiet_pykrx()에 진입하면:
#   T1 진입 → fd 1/2를 /dev/null로 교체
#   T2 진입 → 현재 fd 1/2(= devnull)를 "원본"으로 저장
#   T1 종료 → 실제 fd 복원
#   T2 종료 → devnull 복원 = fd 1/2 영구 devnull → 스케줄러 로그 26h 전체 소실
# Lock으로 진입을 직렬화해 이 경쟁을 차단한다.
_QUIET_LOCK = threading.Lock()


def _silence_pykrx_auth_prints() -> None:
    """pykrx auth 모듈의 bare print() 를 no-op 로 영구 치환한다.

    pykrx 의 ``login_krx`` / 세션 refresh 는 'KRX 로그인 시도...', 'KRX 세션 만료,
    재로그인 시도...' 등을 bare ``print()`` 로 stdout 에 출력한다. 이 print 는
    (1) pykrx import 시 1회, (2) 로그인 실패(장외 JSONDecodeError) 후 매 호출 재시도
    시점에 발생하며, stdout 버퍼링 탓에 좁은 _quiet_pykrx 구간(개별 stock.get_* 호출)을
    빠져나가 스케줄러 로그를 오염시킨다(2026-06-25 라이브 실측).

    bare ``print`` 는 모듈 전역 → builtins 순으로 해석되므로, auth 모듈 전역에
    no-op print 를 주입하면 호출 시점과 무관하게 모든 auth print 를 무력화한다.
    트레이스백·내부 logging 소음은 _quiet_pykrx(fd 리다이렉트)가 별도로 담당한다.
    """
    try:
        from pykrx.website.comm import auth as _auth

        _auth.print = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    except Exception as exc:
        # 라이브러리 내부 구조 변경 시 graceful skip
        LOG.debug("pykrx auth print 침묵화 skip: %s", exc)


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

    [2026-07-02 인시던트 경위 및 보강]
    ─────────────────────────────────────────────────────────────────────────
    결함 1 — fd 영구 devnull (26h 스케줄러 로그 전체 소실):
      APScheduler 스레드풀에서 16:00 잡 4개(daily_report / ohlcv / flows /
      fundamentals)가 동시에 진입하면 스레드 인터리브가 발생한다:
        T1 진입 → fd 1/2 = devnull
        T2 진입 → fd 1/2(= devnull)를 "원본"으로 저장
        T1 종료 → 실제 fd 복원
        T2 종료 → devnull "복원" → fd 1/2 영구 devnull
      보강: _QUIET_LOCK(threading.Lock)으로 진입을 직렬화. 한 스레드가 가드를
      완전히 빠져나간 뒤에만 다음 스레드가 fd 스왑을 수행한다.

    결함 2 — pykrx 소켓 무한 블로킹 (26h ESTABLISHED TCP hang):
      pykrx는 requests에 타임아웃을 지정하지 않는다. 데드 피어(KRX 210.89.168.42:80)
      연결이 ESTABLISHED 상태를 유지하면 읽기 블로킹이 무한 지속된다.
      보강: socket.setdefaulttimeout(_PYKRX_SOCKET_TIMEOUT_S)로 가드 진입 시
      전역 소켓 타임아웃을 설정한다. 가드 종료 시 이전 값을 복원한다.
      - httpx(KIS 주문)는 connect_timeout/read_timeout을 명시적으로 전달하므로
        전역 defaulttimeout의 영향을 받지 않는다.
      - psycopg/libpq는 소켓을 C 레벨에서 생성하므로 Python defaulttimeout
        무관하다.
      - 타임아웃 값은 환경변수 PYKRX_SOCKET_TIMEOUT(float 초)으로 재정의 가능.
        기본값 30.0초. 가드 내부에서 읽어 테스트에서 monkeypatch로 덮어쓸 수 있다.
    """
    # auth 모듈 print 를 출처에서 침묵화(버퍼링으로 fd 구간을 빠져나가는 print 대비)
    _silence_pykrx_auth_prints()

    # 결함 1 보강: 진입 직렬화 — 스레드 인터리브에 의한 fd 영구 devnull 차단
    with _QUIET_LOCK:
        # 결함 2 보강: pykrx 호출 동안 소켓 타임아웃 설정(가드 내부에서 env 읽어 테스트 재정의 허용)
        _env_val = os.environ.get("PYKRX_SOCKET_TIMEOUT")
        _sock_timeout_s = float(_env_val) if _env_val else _PYKRX_SOCKET_TIMEOUT_S_DEFAULT
        _prev_sock_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(_sock_timeout_s)

        # 결함 3 보강(2026-07-06 재발): pykrx(webio._session)는 재사용 requests.Session에
        # timeout 미지정으로 .get/.post를 호출한다. keep-alive로 재사용되는 죽은 소켓은
        # socket.setdefaulttimeout(소켓 생성 이후 설정)이 적용되지 않아 읽기가 무한 블로킹되고,
        # _call_with_timeout(shutdown wait=False)이 워커를 이 yield 지점에서 방치하면 아래
        # finally(fd 복원·락 해제)가 영원히 실행되지 않는다 → fd 영구 devnull + 락 영구 점유.
        # requests.Session.request에 per-request timeout을 주입해 죽은 소켓 읽기를 강제 종료 →
        # 예외 전파 → finally 보장. _QUIET_LOCK으로 직렬화되므로 클래스 패치는 스레드 안전하다.
        _orig_session_request = requests.Session.request

        def _request_with_timeout(self, method, url, **kwargs):  # noqa: ANN001, ANN202
            if kwargs.get("timeout") is None:
                kwargs["timeout"] = _sock_timeout_s
            return _orig_session_request(self, method, url, **kwargs)

        requests.Session.request = _request_with_timeout  # type: ignore[method-assign]

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
            # 소켓 타임아웃 복원 (None 포함)
            socket.setdefaulttimeout(_prev_sock_timeout)
            # requests.Session.request 원복 (패치 누수 방지)
            requests.Session.request = _orig_session_request  # type: ignore[method-assign]


def fetch_ohlcv(symbol: str, start: date, end: date) -> int:
    """Fetch OHLCV for a Korean ticker and upsert to cache. Returns row count."""
    # 서킷 브레이커 확인 — OPEN이면 KrxCircuitOpen 발생, pykrx 미호출
    from trading.data.krx_circuit_breaker import _get_shared_breaker

    breaker = _get_shared_breaker()
    breaker.check_or_raise()

    from pykrx import stock  # lazy import (heavy)

    s = start.strftime("%Y%m%d")
    e = end.strftime("%Y%m%d")
    try:
        with _quiet_pykrx():
            df = stock.get_market_ohlcv_by_date(s, e, symbol)
    except Exception:
        breaker.record_failure()
        raise

    breaker.record_success()
    if df is None or df.empty:
        return 0

    rows = []
    for ts, row in df.iterrows():
        rows.append(
            {
                "ts": ts.date() if hasattr(ts, "date") else ts,
                "open": row.get("시가", row.get("Open", 0)),
                "high": row.get("고가", row.get("High", 0)),
                "low": row.get("저가", row.get("Low", 0)),
                "close": row.get("종가", row.get("Close", 0)),
                "volume": row.get("거래량", row.get("Volume", 0)),
            }
        )
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
    # 서킷 브레이커 확인 — OPEN이면 KrxCircuitOpen 발생, pykrx 미호출
    from trading.data.krx_circuit_breaker import _get_shared_breaker

    breaker = _get_shared_breaker()
    breaker.check_or_raise()

    from pykrx import stock  # lazy import

    s = start.strftime("%Y%m%d")
    e = end.strftime("%Y%m%d")
    try:
        with _quiet_pykrx():
            df = stock.get_market_fundamental_by_date(s, e, symbol)
    except Exception:
        breaker.record_failure()
        raise

    breaker.record_success()
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
        rows.append(
            {
                "ts": d,
                "market_cap": int(cap) if cap is not None else None,
                "per": row.get("PER"),
                "pbr": row.get("PBR"),
                "eps": row.get("EPS"),
                "bps": row.get("BPS"),
                "div_yield": row.get("DIV"),
                "dps": row.get("DPS"),
            }
        )
    return upsert_fundamentals(symbol, rows)


def fetch_flows(symbol: str, start: date, end: date) -> int:
    """Fetch daily foreign/institution/individual net trading values."""
    # 서킷 브레이커 확인 — OPEN이면 KrxCircuitOpen 발생, pykrx 미호출
    from trading.data.krx_circuit_breaker import _get_shared_breaker

    breaker = _get_shared_breaker()
    breaker.check_or_raise()

    from pykrx import stock  # lazy import

    s = start.strftime("%Y%m%d")
    e = end.strftime("%Y%m%d")
    try:
        with _quiet_pykrx():
            df = stock.get_market_trading_value_by_date(s, e, symbol)
    except Exception:
        breaker.record_failure()
        raise

    breaker.record_success()
    if df is None or df.empty:
        return 0

    rows = []
    for ts, row in df.iterrows():
        d = ts.date() if hasattr(ts, "date") else ts
        rows.append(
            {
                "ts": d,
                "foreign_net": int(row.get("외국인합계", row.get("외국인", 0)) or 0),
                "institution_net": int(row.get("기관합계", 0) or 0),
                "individual_net": int(row.get("개인", 0) or 0),
            }
        )
    return upsert_flows(symbol, rows)
