"""T-005 GREEN — 시장 중립 Kelly/heat 코어 순수 함수.

SPEC-TRADING-048 REQ-048-M1-1/2/4/6/7, REQ-048-CORE-1/2.
AC: AC-M1-2(cap), AC-M1-4(heat), AC-M1-6(호가/최소/반올림), AC-CORE-1/2.

순수 함수 — 외부 I/O / 전역 상태 / now() / DB 접근 없음.
KRX/KIS 종속(호가단위·최소주문·수수료·거래세·반올림)은 모두 파라미터로 주입.

# @MX:NOTE: [AUTO] 시장 중립 Kelly 코어 — KRX 상수 하드코딩 없음.
# @MX:SPEC: SPEC-TRADING-048 REQ-048-M1-1/2/4/6/7
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any


def kelly_fraction(win_rate: float, payoff_ratio: float) -> float:
    """Kelly 비율 계산: W - (1-W)/R.

    Args:
        win_rate:     이익 거래 비율 [0, 1].
        payoff_ratio: 손익비 R = avg_win / avg_loss.

    Returns:
        Kelly 비율. W=0 또는 R<=0 이면 0 이하 값 반환(음수 Kelly).

    Notes:
        - 순수 함수: I/O 없음 (AC-CORE-2).
        - 시장 상수 하드코딩 없음 (AC-CORE-1).
    """
    if win_rate <= 0 or payoff_ratio <= 0:
        # REQ-048-M1-2: W=0 또는 R<=0 → kelly_pct<=0 간주 → 거래 금지
        return -1.0

    w = win_rate
    r = payoff_ratio
    return w - (1 - w) / r


# @MX:ANCHOR: [AUTO] half_kelly_cap — 최종 매수 수량의 Kelly 상한 결정 함수.
# @MX:REASON: SPEC-048 REQ-048-M1-2/3/6: _execute_signal 과 테스트 모두 이 함수를 직접 호출.
def half_kelly_cap(
    kelly_pct: float,
    equity: float,
    price: float,
    *,
    lot_size: int = 1,
    tick_size: float = 1.0,
    round_fn: Callable[[float], int] | None = None,
) -> int:
    """half-Kelly 상한 수량 계산.

    half-Kelly = equity * 0.5 * kelly_pct / price (로트·반올림 적용).

    Args:
        kelly_pct:  kelly_fraction() 반환값 (양수이어야 의미있음).
        equity:     현재 자기자본 (원화 등 통화 단위).
        price:      종목 현재가.
        lot_size:   최소 주문 수량 (주입; 예: KRX=1, US=1).
        tick_size:  호가 단위 (주입; 현재는 수량 계산에 직접 사용 안 함, 미래 확장용).
        round_fn:   수량 반올림 함수 (주입; 기본 math.floor).

    Returns:
        정수 수량. 음수 Kelly 또는 최소주문 미만이면 0.

    Notes:
        - 순수 함수: I/O 없음.
        - KRX 상수(lot_size=1, tick=1) 하드코딩 금지 — 파라미터로 주입.
    """
    if kelly_pct <= 0 or price <= 0 or equity <= 0:
        return 0

    if round_fn is None:
        round_fn = math.floor

    raw_qty = equity * 0.5 * kelly_pct / price
    qty = int(round_fn(raw_qty))

    if qty < lot_size:
        return 0

    # lot_size 단위로 정렬 (floor)
    qty = (qty // lot_size) * lot_size
    if qty < lot_size:
        return 0

    return qty


def portfolio_heat(
    open_positions: Sequence[dict[str, Any]],
    equity: float,
    *,
    heat_cap: float = 0.08,
) -> float:
    """포트폴리오 총 heat 계산.

    heat = Σ(위험금액) / equity
    위험금액 = (entry_price - stop_price) * qty  [손절가 있을 때]
             = price * qty                        [손절가 없을 때 — 명목가치 fallback]

    Args:
        open_positions: 미결제 포지션 목록. 각 dict 필요 키:
                        entry_price, qty, stop_price(없으면 None/0).
        equity:         자기자본.
        heat_cap:       상한(비교용; 여기서는 참조용으로만 받음).

    Returns:
        heat 비율 (0.0 이상). equity<=0 이면 0.0.

    Notes:
        - 순수 함수: I/O 없음.
    """
    if equity <= 0:
        return 0.0

    total_risk = 0.0
    for pos in open_positions:
        entry_price = float(pos.get("entry_price", 0.0))
        qty = int(pos.get("qty", 0))
        stop_price = pos.get("stop_price")

        if qty <= 0 or entry_price <= 0:
            continue

        if stop_price and float(stop_price) > 0:
            # 손절가 기반 위험금액
            distance = abs(entry_price - float(stop_price))
            risk = distance * qty
        else:
            # 명목가치 fallback (REQ-048-M1-4, OQ-4)
            risk = entry_price * qty

        total_risk += risk

    return total_risk / equity


def reduce_qty_for_heat(
    proposed_qty: int,
    new_entry_price: float,
    new_stop_price: float | None,
    current_heat: float,
    equity: float,
    *,
    heat_cap: float = 0.08,
    lot_size: int = 1,
) -> int:
    """heat 상한을 초과하지 않도록 신규 수량 축소.

    Args:
        proposed_qty:    축소 전 신규 수량.
        new_entry_price: 신규 진입가.
        new_stop_price:  신규 손절가 (없으면 None → 명목가치 fallback).
        current_heat:    현재 포트폴리오 heat (미결제 포지션 합산).
        equity:          자기자본.
        heat_cap:        heat 상한 (기본 0.08).
        lot_size:        최소 주문 수량.

    Returns:
        heat 상한 내의 정수 수량 (0 가능).

    Notes:
        - 분기 (a): 축소 후 상한 내 → 축소 수량 반환.
        - 분기 (b): lot_size 로도 상한 초과 → 0 반환.
        - 순수 함수: I/O 없음.
    """
    if proposed_qty <= 0 or equity <= 0 or new_entry_price <= 0:
        return 0

    # 신규 진입의 단위 위험금액
    if new_stop_price and float(new_stop_price) > 0:
        unit_risk = abs(new_entry_price - float(new_stop_price))
    else:
        unit_risk = new_entry_price  # 명목가치 fallback

    available_heat = heat_cap - current_heat
    if available_heat <= 0:
        return 0

    # 상한 내 최대 수량
    # @MX:NOTE: [AUTO] heat_cap-current_heat 의 부동소수점 오차(0.08-0.07=0.00999..)가
    # int() 절삭에서 1 적게 나오는 것을 막기 위해 작은 epsilon 가산 후 floor.
    max_qty_by_heat = (
        int(available_heat * equity / unit_risk + 1e-9) if unit_risk > 0 else 0
    )

    if max_qty_by_heat < lot_size:
        # lot_size 로도 heat 초과 → 0
        return 0

    # lot_size 단위 floor
    capped = (min(proposed_qty, max_qty_by_heat) // lot_size) * lot_size
    return max(0, capped)
