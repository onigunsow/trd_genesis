"""시장별 정규장 세션 로더 + 주문 시각 가드.

market_session.yaml 에서 시장별(KR/US 등) 개장·마감 시각을 읽어
market-agnostic API 로 노출한다. sector_taxonomy.py 와 동일한 패턴이며
``active_market()`` 은 그 모듈의 것을 재사용한다(단일 진실 원천).

왜 필요한가 (2026-08-15 실측)
-----------------------------
장 운영시간 밖으로 주문이 나가 KIS 가 거부하는 일이 계속 있었다:

- 07:32~07:36 매수 — ``pre_market`` 사이클(07:30)이 신호를 만들어 주문까지 냈다.
  ``40570000:모의투자 장시작전 입니다.`` 2026-06-05 부터 12건 이상.
- 15:33 매수 — ``intraday_adaptive`` 크론이 ``hour="9-15", minute="*/15"`` 라
  15:30·15:45 에도 발사됐다. ``40580000:모의투자 장종료 입니다.``
- 토요일 매수 — ``40100000:모의투자 영업일이 아닙니다.``

크론 창만 좁히면 마감 쪽만 막히고 개장 전(더 잦음)은 그대로 남는다. 세 경우가
공통으로 지나는 지점은 ``kis.order.submit_order`` 하나뿐이라, 그 단일 관문에
이 가드를 건다.

거래일 판정은 ``scheduler.calendar.is_trading_day`` 를 재사용한다 — 주말·한국
공휴일·12/31 연말폐장을 이미 처리하므로 별도 달력을 만들지 않는다.
(US 는 그 달력이 KRX 기준이라 매매 개시 전 보강이 필요하다 — YAML 주석 참조.)
"""

from __future__ import annotations

import functools
import logging
from datetime import datetime, time
from pathlib import Path
from typing import Any

from trading.data.sector_taxonomy import active_market

LOG = logging.getLogger(__name__)

# YAML 위치: 이 파일과 동일 패키지 디렉터리 (sector_taxonomy 와 같은 규약)
_YAML_PATH = Path(__file__).parent / "market_session.yaml"

# 설정 없는 market 경고를 한 번만 출력하기 위한 캐시
_warned_markets: set[str] = set()


@functools.lru_cache(maxsize=1)
def _load_sessions() -> dict[str, Any]:
    """market_session.yaml 을 파싱해 반환. 오류 시 빈 dict(예외 불전파)."""
    try:
        import yaml  # pyyaml — 이미 설치됨

        with _YAML_PATH.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        LOG.error("market_session: YAML 로드 실패 — %s", exc)
        return {}


def _parse_hhmm(value: str) -> time | None:
    """'HH:MM' → time. 형식이 어긋나면 None (호출자가 폴백 판단)."""
    try:
        hh, mm = str(value).split(":", 1)
        return time(int(hh), int(mm))
    except Exception:
        return None


def session_bounds(market: str | None = None) -> tuple[time, time, str] | None:
    """``(open, close, tz)`` 반환. 설정이 없거나 깨졌으면 None.

    None 은 "판정 불가"를 뜻한다 — 호출자는 이를 차단 근거로 쓰지 않는다
    (설정 사고가 매매 전면 중단으로 번지지 않게 함, 아래 fail-open 주석 참조).
    """
    m = market if market is not None else active_market()
    cfg = _load_sessions().get(m)
    if not isinstance(cfg, dict):
        if m not in _warned_markets:
            _warned_markets.add(m)
            LOG.warning("market_session: 시장 '%s' 세션 설정 없음 — 가드 미적용", m)
        return None

    opened = _parse_hhmm(cfg.get("open", ""))
    closed = _parse_hhmm(cfg.get("close", ""))
    tz = cfg.get("tz")
    if opened is None or closed is None or not tz:
        if m not in _warned_markets:
            _warned_markets.add(m)
            LOG.error("market_session: 시장 '%s' 세션 설정 불량 — 가드 미적용", m)
        return None
    return opened, closed, str(tz)


# @MX:ANCHOR: 주문 시각 가드의 단일 판정 지점. submit_order 와 스케줄러가 공유한다.
# @MX:REASON: 장 운영시간 밖 주문이 개장 전 12건+/마감 후 1건 발생했다(2026-08-15
#   실측). 판정을 한 곳에 모아야 크론 창과 주문 가드가 같은 시각 정의를 쓴다.
# @MX:WARN: money path — 이 함수가 False 를 반환하면 주문이 나가지 않는다.
# @MX:REASON: 판정 불가(설정 없음/불량)는 True(허용)로 폴백한다. 설정 사고가
#   정상 매매를 전면 중단시키는 쪽이, 장외 주문을 몇 건 허용하는 쪽보다 위험하다
#   — 장외 주문은 어차피 KIS 가 거부하지만, 매매 전면 중단은 손절도 막는다.
def is_session_open(
    now: datetime | None = None,
    *,
    market: str | None = None,
) -> bool:
    """``now`` 가 해당 시장 정규장 안(개장 이상, 마감 미만)인지.

    거래일이 아니면(주말·공휴일·연말폐장) False. 세션 설정을 읽지 못하면
    True 로 폴백한다(위 @MX:REASON 의 fail-open 근거).

    ``now`` 는 tz-aware/naive 어느 쪽이어도 되며, 시장 타임존으로 변환해 비교한다.
    naive 는 이미 시장 현지시간으로 간주한다.
    """
    bounds = session_bounds(market)
    if bounds is None:
        return True  # fail-open — 판정 불가는 차단 근거가 아니다

    opened, closed, tzname = bounds

    try:
        import zoneinfo

        tz = zoneinfo.ZoneInfo(tzname)
    except Exception:
        LOG.error("market_session: 타임존 '%s' 해석 실패 — 가드 미적용", tzname)
        return True

    ref = datetime.now(tz) if now is None else now
    local = ref.astimezone(tz) if ref.tzinfo is not None else ref.replace(tzinfo=tz)

    # 거래일 판정 재사용 (주말·한국 공휴일·12/31). 지연 임포트로 순환 방지.
    from trading.scheduler.calendar import is_trading_day

    if not is_trading_day(local.date()):
        return False

    return opened <= local.time() < closed


def intraday_cron_slots(interval_minutes: int, market: str | None = None) -> list[time]:
    """개장부터 마감 *직전* 까지 ``interval_minutes`` 간격 슬롯 목록.

    스케줄러가 크론 창을 세션에서 파생시키기 위해 쓴다. 마감 정각은 이미 장이
    끝난 시각이므로 포함하지 않는다 — KR/15분이면 마지막 슬롯은 15:15 이다.

    설정을 읽지 못하면 빈 리스트를 반환하고, 호출자는 기존 동작을 유지한다.
    """
    bounds = session_bounds(market)
    if bounds is None or interval_minutes <= 0:
        return []
    opened, closed, _ = bounds

    start = opened.hour * 60 + opened.minute
    end = closed.hour * 60 + closed.minute
    return [
        time(m // 60, m % 60)
        for m in range(start, end, interval_minutes)
    ]
