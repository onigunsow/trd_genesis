"""T-001 GREEN — 거래단위 통계 순수 함수 (net-of-tax 보정 포함).

SPEC-TRADING-048 REQ-048-M2-1(net), REQ-048-CORE-1/2.
AC: AC-M2-1(net 입력), AC-CORE-2.

# @MX:NOTE: [AUTO] 시장 중립 순수 함수 — 외부 I/O / 전역 상태 / now() 없음.
# KRX/KIS 종속(거래세율)은 호출자가 sell_tax_rate 파라미터로 주입한다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TradeStats:
    """거래단위 집계 통계 (채점기 입력 단위).

    n            : 총 거래 건수
    win_rate     : 이익 거래 비율 [0, 1]
    avg_win      : 이익 거래 평균 net 손익 (세금 보정 후)
    avg_loss     : 손실 거래 평균 net 손익 절댓값 (세금 보정 후)
    profit_factor: 총 이익 / 총 손실 (손실 0 이면 0.0)
    expectancy   : win_rate*avg_win - (1-win_rate)*avg_loss (세금 보정 후)
    """

    n: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    expectancy: float


# @MX:ANCHOR: [AUTO] compute_trade_stats — 채점기(evaluate_backtest)와 M1 Kelly 연산의 공통 입력.
# @MX:REASON: SPEC-048 REQ-048-M2-1, REQ-048-CORE-1/2: fan_in ≥ 3 예상(채점기·Kelly·postmortem).
def compute_trade_stats(
    roundtrips: Sequence[Any],
    *,
    sell_tax_rate: float = 0.0,
) -> TradeStats:
    """라운드트립 목록에서 거래단위 통계를 계산한다.

    Args:
        roundtrips: RoundTrip 객체 또는 dict(net_pnl, exit_price, qty 키 포함).
                    RoundTrip.net_pnl = gross - fees (거래세 미포함).
        sell_tax_rate: 매도측 거래세율 (예: KRX KOSPI 0.0018). 호출자가 주입.
                       0.0 이면 세금 보정 없음.

    Returns:
        TradeStats — 세금 보정 후 집계.

    Notes:
        - 순수 함수: I/O·전역 상태·시각·DB 접근 없음 (AC-CORE-2).
        - KRX 특유 상수(거래세율 등) 하드코딩 금지 — 인자로만 수신 (AC-CORE-1).
    """
    if not roundtrips:
        return TradeStats(
            n=0,
            win_rate=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            profit_factor=0.0,
            expectancy=0.0,
        )

    nets: list[float] = []
    for rt in roundtrips:
        # dict 또는 RoundTrip 객체 양쪽 지원
        if isinstance(rt, dict):
            raw_net = float(rt["net_pnl"])
            exit_price = float(rt.get("exit_price", 0.0))
            qty = int(rt.get("qty", 0))
        else:
            raw_net = float(rt.net_pnl)
            exit_price = float(rt.exit_price)
            qty = int(rt.qty)

        # 거래세 추가 차감: 청산 대금 × sell_tax_rate
        sell_tax = exit_price * qty * sell_tax_rate
        nets.append(raw_net - sell_tax)

    wins = [v for v in nets if v > 0]
    losses = [v for v in nets if v <= 0]
    n = len(nets)

    win_rate = len(wins) / n
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0

    total_win = sum(wins)
    total_loss = abs(sum(losses))
    if total_loss == 0.0:
        profit_factor = math.inf if total_win > 0 else 0.0
    else:
        profit_factor = total_win / total_loss

    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss

    return TradeStats(
        n=n,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        expectancy=expectancy,
    )
