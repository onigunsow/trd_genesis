"""SPEC-TRADING-057 M1 — as-of-date 유니버스 재구성기.

REQ-057-M1-6  : 생존편향 PRECONDITION GATE
REQ-057-M1-6a : pykrx get_index_portfolio_deposit_file(date) → as-of-date 멤버십
REQ-057-M1-6b : 재구성 불가 시 achievable=False 다운그레이드

설계 원칙:
- membership_provider는 의존성 주입 인자 — 단위 테스트는 픽스처를 주입한다.
- 기본 provider는 runtime lazy import (pykrx → KRX 네트워크 필요, 컨테이너 전용).
- 빈 목록 반환 = as-of-date 지원 불가로 간주 (survivorship-only 판단).
- 반환 종목 목록은 항상 정렬 — 결정성(REQ-057-M1-2) 보장.
- ADR-057-4: 인터페이스는 진단 전용이 아닌 범용 팩터 백테스트 재사용 표면.

# @MX:ANCHOR: [AUTO] as-of-date 유니버스 재구성 — 생존편향 PRECONDITION GATE
# @MX:REASON: SPEC-057 M1-6/M1-6a/M1-6b; SPEC-058 팩터 백테스트가 동일 함수 의존(fan_in >= 2).
#             achievable 플래그는 M2에서 반드시 검사해야 한다.
# @MX:SPEC: SPEC-TRADING-057
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

LOG = logging.getLogger(__name__)

# KOSPI200 KRX 인덱스 코드 (universe.py 와 동일)
_KOSPI200_INDEX_CODE = "1028"


@dataclass
class UniverseResult:
    """as-of-date 유니버스 재구성 결과.

    achievable=True  → M1-6a 경로, tickers에 상폐 포함 as-of-date 멤버십.
    achievable=False → M1-6b 다운그레이드, tickers=[],
                       M2 알파는 "생존편향 상한" 레이블 강제 필요.
    """

    rebalance_date: date
    tickers: list[str]
    achievable: bool
    downgrade_reason: str = ""
    # 참고: 오늘 기준 유니버스와 차이 나는 종목 수 (생존편향 제거 규모 가시화)
    extra_count: int = 0


def _default_membership_provider(rebalance_date: date) -> list[str]:
    """기본 멤버십 provider — pykrx 직접 호출 (컨테이너 런타임 전용).

    KRX 세션 로그인(KRX_ID/PW 환경변수)이 필요하며,
    네트워크가 차단된 단위 테스트 환경에서는 사용하지 말 것.
    """
    # lazy import: 테스트 컬렉션 시점에 pykrx가 로드되지 않도록 함
    from pykrx import stock

    date_str = rebalance_date.strftime("%Y%m%d")
    tickers = stock.get_index_portfolio_deposit_file(_KOSPI200_INDEX_CODE, date=date_str)
    return list(tickers)


def reconstruct_universe(
    rebalance_date: date,
    *,
    membership_provider: Callable[[date], list[str]] | None = None,
) -> UniverseResult:
    """지정 날짜 기준 KOSPI200 as-of-date 유니버스를 재구성한다.

    Args:
        rebalance_date: 유니버스를 재구성할 기준 날짜.
        membership_provider: (date) -> list[str] 콜백.
            None 이면 기본 pykrx 구현 사용 (컨테이너 전용).
            단위 테스트는 픽스처 provider를 주입한다.

    Returns:
        UniverseResult:
            achievable=True  → tickers에 as-of-date 멤버십(상폐 포함).
            achievable=False → tickers=[], downgrade_reason 에 사유.
    """
    # # @MX:NOTE: [AUTO] provider 미주입 시 기본 pykrx 호출 — 네트워크 필요
    provider = (
        membership_provider
        if membership_provider is not None
        else _default_membership_provider
    )

    try:
        tickers_raw = provider(rebalance_date)
    except Exception as exc:
        reason = f"멤버십 조회 실패: {exc!r}"
        LOG.warning(
            "universe_reconstructor: %s (date=%s) → M1-6b 다운그레이드",
            reason, rebalance_date.isoformat(),
        )
        return UniverseResult(
            rebalance_date=rebalance_date,
            tickers=[],
            achievable=False,
            downgrade_reason=reason,
        )

    if not tickers_raw:
        reason = (
            "멤버십 provider가 빈 목록 반환 — "
            "as-of-date 지원 불가(생존편향 상한으로 다운그레이드)"
        )
        LOG.warning(
            "universe_reconstructor: %s (date=%s) → M1-6b 다운그레이드",
            reason, rebalance_date.isoformat(),
        )
        return UniverseResult(
            rebalance_date=rebalance_date,
            tickers=[],
            achievable=False,
            downgrade_reason=reason,
        )

    # 결정성 보장: 항상 정렬 반환 (REQ-057-M1-2)
    sorted_tickers = sorted(str(t) for t in tickers_raw if t)

    LOG.info(
        "universe_reconstructor: date=%s → %d종목 재구성 완료 (achievable=True)",
        rebalance_date.isoformat(), len(sorted_tickers),
    )

    return UniverseResult(
        rebalance_date=rebalance_date,
        tickers=sorted_tickers,
        achievable=True,
        downgrade_reason="",
    )
