"""변동성 타기팅 기반 결정적 포지션 사이징 (SPEC-TRADING-046 M1/M2).

# @MX:ANCHOR: [AUTO] compute_qty -- 결정적 사이징의 단일 진입점.
# @MX:REASON: SPEC-TRADING-046 REQ-046-A2: 순수 함수, 외부 상태 없음.
#   SPEC-044 walk_forward 하니스가 이 함수를 그리드 스윕한다.
#   fan_in: orchestrator._execute_signal + walk_forward + 향후 피드백루프.

핵심 공식 (변동성 타기팅):
    notional = (vol_target_per_trade x total_assets) / atr_pct

근거:
    1-ATR 역풍이 자산의 vol_target_per_trade 비율을 초과하지 않도록 사이즈를 정한다.
    atr_pct = ATR / close_price (일일 변동성 추정치, 백분율 단위).

    예시: total_assets=10,000,000원, vol_target=1%, atr_pct=2%
        notional = (0.01 x 10,000,000) / 0.02 = 5,000,000원

confidence 는 qty 를 키우지 않는다 [HARD] (REQ-046-B1):
    SPEC-044 scorecard 2026-06-14: confidence <-> P&L Spearman -0.455 (반예측적).
    기본 동작: confidence 무시 (confidence_damp_enabled=False).
    선택적 하향 damp: confidence 낮을수록 사이즈 축소만 허용 (절대 확대 금지).

SELL 신호:
    사이징은 BUY 전용. SELL 은 SPEC-042 clamp_sell_to_confirmed 가 처리한다.
    sizing_reason='sell_bypass' 반환 -> orchestrator 에서 기존 경로 사용.
"""

from __future__ import annotations

import math

from trading.config import SizingParams


def compute_qty(
    *,
    candidate: dict,
    portfolio_state: dict,
    params: SizingParams,
) -> dict:
    """변동성 타기팅으로 qty 를 계산한다 (순수 함수, 네트워크/DB 없음).

    Args:
        candidate: LLM 제안 신호. 키:
            - ticker (str): 종목 코드
            - side (str): 'buy' | 'sell'
            - qty (int, optional): LLM 어드바이저리 qty (영속용)
            - confidence (float | None, optional): LLM 신뢰도 0-1
        portfolio_state: 포트폴리오 스냅샷. 키:
            - total_assets (int): 총 자산 (KRW)
            - cash (int): 가용 현금 (KRW)
            - atr_pct (float | None): ATR % (예: 2.0 = 2%). None 이면 fallback.
            - ref_price (int): 기준가 (KRW/주)
            - holdings (list): 보유 목록 (이중 캡 계산용, 현재 미사용)
        params: SizingParams 단일 외부 진실원천 (REQ-046-C).

    Returns:
        dict with keys:
            - qty (int): 결정적 사이징 결과 주수 (>= 0)
            - sizing_reason (str): 'vol_target' | 'vol_unavailable' | 'below_min_lot'
                                   | 'sell_bypass' | 'no_cash'
            - advisory_qty (int): LLM 이 낸 원본 qty (A/B 비교/감사용, REQ-046-E3)
    """
    # LLM 어드바이저리 qty 보존 (REQ-046-E3)
    advisory_qty: int = int(candidate.get("qty", 0) or 0)

    side: str = candidate.get("side", "hold")

    # SELL 사이징은 이 모듈 범위 밖 (SPEC-042 clamp_sell_to_confirmed 가 담당)
    if side != "buy":
        return {
            "qty": 0,
            "sizing_reason": "sell_bypass",
            "advisory_qty": advisory_qty,
        }

    total_assets: int = int(portfolio_state.get("total_assets", 0) or 0)
    cash: int = int(portfolio_state.get("cash", 0) or 0)
    atr_pct: float | None = portfolio_state.get("atr_pct")
    ref_price: int = int(portfolio_state.get("ref_price", 0) or 0)

    # 현금/자산 없으면 0
    if total_assets <= 0 or cash <= 0 or ref_price <= 0:
        return {
            "qty": 0,
            "sizing_reason": "no_cash",
            "advisory_qty": advisory_qty,
        }

    # -------------------------------------------------------------------------
    # Step 1: 목표 notional 계산
    # -------------------------------------------------------------------------

    if atr_pct is not None and atr_pct > 0:
        # 변동성 타기팅: notional = (vol_target x total_assets) / atr_pct
        # atr_pct 는 백분율 단위 (예: 2.0 = 2%)
        atr_fraction = atr_pct / 100.0
        target_notional = (params.vol_target_per_trade * total_assets) / atr_fraction
        sizing_reason = "vol_target"
    else:
        # ATR 부재 -> 보수 고정 분율 fallback (REQ-046-A3)
        # fallback_fraction 은 단건 상한(10%)보다 충분히 보수적 (기본 2%)
        target_notional = params.fallback_fraction * total_assets
        sizing_reason = "vol_unavailable"

    # -------------------------------------------------------------------------
    # Step 2: confidence 하향 damp (선택적, 기본 OFF) [HARD: 절대 확대 금지]
    # REQ-046-B2: damp 는 하향 전용. confidence 가 낮을수록 target_notional 축소.
    # -------------------------------------------------------------------------
    if params.confidence_damp_enabled:
        confidence = candidate.get("confidence")
        if confidence is not None:
            # confidence in [0, 1]. clamp 안전.
            c = float(confidence)
            c = max(0.0, min(1.0, c))
            # 하향 damp: confidence 1.0 -> 배율 1.0 (no damp)
            #            confidence 0.0 -> 배율 0.0 (전부 제거)
            # 절대 확대 금지: c <= 1 이므로 target_notional 은 줄어들거나 동일.
            target_notional = target_notional * c

    # -------------------------------------------------------------------------
    # Step 3: cash 경계 (주문 가능성 기초)
    # REQ-046-D2: RISK_SINGLE_ORDER_MAX / per-ticker / total-invested 캡은
    #   check_pre_order 가 단일 판정자 -- 사이징은 재구현/이중 적용 금지.
    #   사이징이 캡 내에 있으면 check_pre_order 는 no-op(REQ-046-D3).
    #   사이징이 캡을 초과하더라도 check_pre_order 가 거부함.
    # 단, 현금을 초과할 수는 없으므로 cash 로만 상한을 둔다.
    # -------------------------------------------------------------------------
    target_notional = min(target_notional, cash)

    # -------------------------------------------------------------------------
    # Step 4: 주수 변환 + floor (REQ-046-A4: 1주 강제 금지)
    # -------------------------------------------------------------------------
    if target_notional <= 0 or ref_price <= 0:
        return {
            "qty": 0,
            "sizing_reason": "below_min_lot",
            "advisory_qty": advisory_qty,
        }

    raw_qty = target_notional / ref_price
    qty = math.floor(raw_qty)  # 내림 (초과 매수 방지)

    if qty <= 0:
        return {
            "qty": 0,
            "sizing_reason": "below_min_lot",
            "advisory_qty": advisory_qty,
        }

    return {
        "qty": qty,
        "sizing_reason": sizing_reason,
        "advisory_qty": advisory_qty,
    }
