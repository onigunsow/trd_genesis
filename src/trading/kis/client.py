"""KIS REST client base (REQ-KIS-02-1).

Provides a thin wrapper that:
- Resolves base URL from TRADING_MODE
- Attaches auth headers (Bearer + appkey + appsecret)
- Selects tr_id prefix V (paper) vs T (live)
- Auto-retries on rate-limit (rt_cd=1, EGW00201)
- Standardises error handling
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from trading.config import TradingMode, get_settings
from trading.kis.auth import base_url, get_token

LOG = logging.getLogger(__name__)

# KIS rate-limit retry config. KIS paper environment is more aggressive than docs claim.
RATE_LIMIT_RETRIES = 4
RATE_LIMIT_BACKOFF_SECONDS = 1.0
# KIS error codes that signal "wait and retry"
RATE_LIMIT_MSG_CODES = {"EGW00201"}     # 초당 거래건수 초과

# SPEC-TRADING-043 REQ-043-B1: proactive process-wide pacing. Minimum interval
# between *inquiry* (GET) requests, named so it is easy to tune. 0.4s ≈ 2.5
# req/s aggregate, comfortably below the broker per-second cap while leaving
# headroom for the reactive retry (REQ-043-B3) as a residual-burst safety net.
KIS_MIN_REQUEST_INTERVAL_SECONDS = 0.4

# @MX:SPEC: SPEC-TRADING-051 REQ-051-A4 ADR-001
# 분할 타임아웃 명명 상수. KIS 장애는 connect가 아닌 read 단계에서 발생하므로
# read를 더 여유 있게, connect는 빠른 실패로 재시도 사이클을 신속히 돌린다.
# 운영자가 필요 시 조정할 수 있도록 명명 상수로 분리.
KIS_CONNECT_TIMEOUT_SECONDS: float = 5.0   # ADR-001: connect 빠른 실패
KIS_READ_TIMEOUT_SECONDS: float = 15.0     # ADR-001: read 여유 대기

# @MX:SPEC: SPEC-TRADING-051 REQ-051-A1a ADR-005
# 타임아웃 전용 재시도 캡. RATE_LIMIT_RETRIES(4)보다 낮게 설정하여
# get() 한 번의 최악 wall-time이 워치독 */5(300s) 주기를 넘지 않도록 보장한다.
# 최악 계산: (KIS_TIMEOUT_RETRIES+1)*read + sum(backoff)
#   = 3 * 15s + (1s + 2s) = 48s < 300s (ADR-005 충족)
KIS_TIMEOUT_RETRIES: int = 2

# @MX:SPEC: SPEC-TRADING-051 REQ-051-A5
# backoff sleep 주입 가능 seam. 테스트에서 monkeypatch로 대체하여
# 실제 wall-clock sleep 없이 결정적으로 검증한다.
_sleep_fn: Callable[[float], None] = time.sleep


class _RateGate:
    """Process-wide minimum-interval pacing gate for KIS inquiry requests.

    SPEC-TRADING-043 REQ-043-B1/B6. Serializes concurrent callers so the
    aggregate request rate stays below ``1 / min_interval``. The clock and sleep
    are injectable so behaviour is deterministically testable with a fake clock
    and a sleep-counter (no live broker, no wall-clock).

    The gate reserves the next grant slot under a lock, then releases the lock
    BEFORE sleeping and BEFORE the HTTP call — it never holds the lock across the
    network, and order-submission TRs (``post``) are not gated at all.
    """

    def __init__(
        self,
        min_interval: float,
        *,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._min_interval = min_interval
        self._now = now
        self._sleep = sleep
        self._lock = threading.Lock()
        # Far in the past so the first request is granted immediately.
        self._last_grant = float("-inf")

    def acquire(self) -> None:
        with self._lock:
            now = self._now()
            earliest = self._last_grant + self._min_interval
            grant_at = earliest if earliest > now else now
            self._last_grant = grant_at  # reserve the slot
            wait = grant_at - now
        if wait > 0:
            self._sleep(wait)


# Module-level singleton shared by every KisClient instance in the process.
_GATE = _RateGate(KIS_MIN_REQUEST_INTERVAL_SECONDS)


@dataclass
class KisResponse:
    status_code: int
    rt_cd: str          # KIS response status code "0" = success, others = error
    msg_cd: str
    msg: str
    output: dict[str, Any] | list[dict[str, Any]]
    raw: dict[str, Any]


class KisError(RuntimeError):
    """KIS API returned a non-success rt_cd."""

    def __init__(self, response: KisResponse):
        self.response = response
        super().__init__(f"KIS error rt_cd={response.rt_cd} msg={response.msg!r}")


class KisTimeoutError(KisError):
    """KIS HTTP 호출이 타임아웃/전송 오류로 소진되었거나 post()가 타임아웃했을 때 raise.

    # @MX:SPEC: SPEC-TRADING-051 REQ-051-A3 ADR-002
    # KisError(→ RuntimeError) 하위로 두어 기존 호출자의 except 절이 깨지지 않는다.
    #   - cli.py L292 except KisError → 자동 포착
    #   - cli.py L295 except RuntimeError → 자동 포착
    #   - position_watchdog except Exception → 자동 포착
    # raw httpx 예외가 그대로 전파되지 않도록 래핑한다.
    """

    def __init__(self, message: str, cause: BaseException | None = None) -> None:
        # KisError.__init__은 KisResponse를 요구하므로 RuntimeError로 직접 초기화
        RuntimeError.__init__(self, message)
        self.__cause__ = cause


def _resolve_timeout(timeout: float | httpx.Timeout | None) -> httpx.Timeout:
    """스칼라, httpx.Timeout, None을 분할 httpx.Timeout으로 정규화한다.

    # @MX:SPEC: SPEC-TRADING-051 REQ-051-A4 ADR-001
    # - None(기본값): 분할 타임아웃(connect=5s, read=15s) 사용.
    # - 스칼라: httpx.Timeout(scalar)로 해석(connect=read=write=pool=scalar).
    # - httpx.Timeout: 그대로 반환.
    # 호출별 오버라이드 시그니처를 깨지 않는다.
    """
    if timeout is None:
        # 기본값: 분할 타임아웃 (ADR-001)
        return httpx.Timeout(
            connect=KIS_CONNECT_TIMEOUT_SECONDS,
            read=KIS_READ_TIMEOUT_SECONDS,
            write=KIS_CONNECT_TIMEOUT_SECONDS,
            pool=KIS_CONNECT_TIMEOUT_SECONDS,
        )
    if isinstance(timeout, httpx.Timeout):
        return timeout
    # 스칼라 오버라이드: 균일 타임아웃으로 해석
    return httpx.Timeout(float(timeout))


class KisClient:
    """Reusable KIS REST client. One instance per trading mode is sufficient."""

    def __init__(self, mode: TradingMode | None = None):
        s = get_settings()
        self.mode = mode if mode is not None else s.trading_mode
        self.base = base_url(self.mode)
        # Pin credentials for the configured mode.
        if self.mode == TradingMode.LIVE:
            self._appkey = s.kis.live_app_key.get_secret_value()
            self._appsecret = s.kis.live_app_secret.get_secret_value()
            self._account_full = s.kis.live_account
        else:
            self._appkey = s.kis.paper_app_key.get_secret_value()
            self._appsecret = s.kis.paper_app_secret.get_secret_value()
            self._account_full = s.kis.paper_account

    @property
    def cache_key(self) -> str:
        """Stable identity for read-through caches (SPEC-TRADING-043 REQ-043-B2).

        Keys cached reads by trading mode + account so paper and live never
        share a value. Owned by the client (the layer that holds these fields)
        rather than reconstructed by callers via ``getattr``.
        """
        return f"{self.mode}:{self._account_full}"

    @property
    def account_prefix(self) -> str:
        """First 8 digits of account number (CANO)."""
        return self._account_full.split("-")[0]

    @property
    def account_suffix(self) -> str:
        """2-digit product code (ACNT_PRDT_CD)."""
        return self._account_full.split("-")[1] if "-" in self._account_full else "01"

    def tr_id(self, paper_id: str, live_id: str) -> str:
        """Return correct tr_id for current mode."""
        return live_id if self.mode == TradingMode.LIVE else paper_id

    def _headers(self, tr_id: str, hashkey: str | None = None) -> dict[str, str]:
        token = get_token(self.mode).access_token
        h = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self._appkey,
            "appsecret": self._appsecret,
            "tr_id": tr_id,
        }
        if hashkey:
            h["hashkey"] = hashkey
        return h

    def _is_rate_limited(self, resp: KisResponse) -> bool:
        return resp.rt_cd == "1" and (
            resp.msg_cd in RATE_LIMIT_MSG_CODES or "초당 거래건수" in resp.msg
        )

    def get(
        self,
        path: str,
        tr_id: str,
        params: dict[str, Any] | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> KisResponse:
        # @MX:ANCHOR: [AUTO] KIS 조회(GET) 전용 진입점 — 전역 페이싱 게이트 필수 통과.
        # High fan_in: fill_sync/reconcile (SPEC-042/029), position_watchdog (SPEC-033),
        # tools.executor get_portfolio_status 등이 여기 의존.
        # @MX:REASON: TPS-breach 잔고 읽기 실패로 워치독 실명 → 손절 누락 위험.
        #   SPEC-043 게이트가 TPS를 낮추고, SPEC-051 재시도가 일시 타임아웃을 회복.
        # @MX:SPEC: SPEC-TRADING-043 REQ-043-B1/B5/B6; SPEC-TRADING-051 REQ-051-A1a/A2/A4/A5
        # @MX:NOTE: [AUTO] 재시도 루프마다 _GATE.acquire() 1회 호출 (의도적).
        #   backoff(≥1s) > gate_interval(0.4s)이므로 추가 대기 없음.
        #   타임아웃 예외는 RATE_LIMIT_RETRIES와 별도로 KIS_TIMEOUT_RETRIES 캡 사용(ADR-005).
        resolved_timeout = _resolve_timeout(timeout)
        timeout_attempts = 0  # 타임아웃 전용 카운터 (ADR-005 캡 적용)

        for attempt in range(RATE_LIMIT_RETRIES + 1):
            _GATE.acquire()
            try:
                with httpx.Client(timeout=resolved_timeout) as client:
                    r = client.get(
                        f"{self.base}{path}",
                        params=params,
                        headers=self._headers(tr_id),
                    )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                # REQ-051-A1a: 타임아웃/전송 오류 → 예산 안에서 재시도
                timeout_attempts += 1
                if timeout_attempts > KIS_TIMEOUT_RETRIES:
                    # ADR-005: 타임아웃 캡 소진 → KisTimeoutError raise
                    raise KisTimeoutError(
                        f"KIS GET 타임아웃 소진 (타임아웃 {timeout_attempts}회, "
                        f"캡={KIS_TIMEOUT_RETRIES}): {exc}"
                    ) from exc
                backoff = RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1)
                LOG.warning(
                    "KIS GET 타임아웃 (attempt %d/%d), %.1fs 후 재시도: %s",
                    timeout_attempts, KIS_TIMEOUT_RETRIES, backoff, exc,
                )
                _sleep_fn(backoff)
                continue

            resp = self._parse(r)
            if not self._is_rate_limited(resp) or attempt == RATE_LIMIT_RETRIES:
                return resp
            backoff = RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1)
            LOG.warning("KIS rate limited (attempt %d), sleeping %.1fs", attempt + 1, backoff)
            _sleep_fn(backoff)

        return resp  # unreachable, but keeps type checker happy

    def post(
        self,
        path: str,
        tr_id: str,
        body: dict[str, Any],
        timeout: float | httpx.Timeout | None = None,
    ) -> KisResponse:
        # @MX:SPEC: SPEC-TRADING-051 REQ-051-A1b ADR-002
        # post()는 rate gate 미적용 (SPEC-043 정책). 타임아웃 시 재시도 없이
        # 즉시 KisTimeoutError raise — 이중주문 방지 (mig 007 ODNO UNIQUE 인덱스 한계).
        resolved_timeout = _resolve_timeout(timeout)
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            try:
                with httpx.Client(timeout=resolved_timeout) as client:
                    r = client.post(
                        f"{self.base}{path}",
                        json=body,
                        headers=self._headers(tr_id),
                    )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                # REQ-051-A1b: post 타임아웃 → 재시도 없이 즉시 raise (이중주문 방지)
                raise KisTimeoutError(
                    f"KIS POST 타임아웃 (재시도 없음, 이중주문 방지): {exc}"
                ) from exc

            resp = self._parse(r)
            if not self._is_rate_limited(resp) or attempt == RATE_LIMIT_RETRIES:
                return resp
            backoff = RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1)
            LOG.warning("KIS rate limited (attempt %d), sleeping %.1fs", attempt + 1, backoff)
            _sleep_fn(backoff)
        return resp

    @staticmethod
    def _parse(r: httpx.Response) -> KisResponse:
        try:
            data = r.json()
        except ValueError as err:
            raise RuntimeError(
                f"KIS non-JSON response (status {r.status_code}): {r.text[:200]}"
            ) from err
        return KisResponse(
            status_code=r.status_code,
            rt_cd=data.get("rt_cd", ""),
            msg_cd=data.get("msg_cd", ""),
            msg=data.get("msg1", ""),
            output=data.get("output", data.get("output1", {})),
            raw=data,
        )
