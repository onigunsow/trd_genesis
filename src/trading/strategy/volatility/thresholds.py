"""Dynamic threshold computation with ATR-based guardrails.

REQ-DYNTH-05-2: Compute per-ticker stop/take/trailing thresholds.
REQ-DYNTH-05-3: ATR multiplier formulas.
REQ-DYNTH-05-4: Guardrail hard limits.
REQ-DYNTH-05-5: Fallback to fixed thresholds when ATR unavailable.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from trading.db.session import audit, connection
from trading.strategy.volatility.atr import compute_atr
from trading.strategy.volatility.models import DynamicThresholds
from trading.strategy.volatility.regime import classify_regime

LOG = logging.getLogger(__name__)

# REQ-DYNTH-05-3: Configurable ATR multipliers
#
# 2026-08-15 재튜닝 (stop 2.0→4.0, take 3.0→4.0, floor -10→-15). 근거는 두 개의
# exit-backtest 스윕이 같은 답을 낸 것이다:
#   실거래 19종목 · 2021~2026 (n=5,238):  기대값 +0.60% → +3.16%, 승률 56→62%
#   KOSPI200 전체 · 2016~2026 (n=92,423): 기대값 +0.39% → +1.23%, 승률 42→52%
# 종전 조합(2.0/-10/3.0)은 두 우주 모두에서 격자 최하위권이었다. 종전 주석의
# "넓은 floor 는 기대값을 해친다"는 결론은 이 격자에서 성립하지 않는다.
#
# 실측 65건 왕복도 같은 방향을 가리킨다: 2~15일 보유가 승률 17~20%로 -35만원,
# 16~30일 보유가 승률 83%로 +5만원. 좁은 stop 이 오를 종목을 바닥에서 내보냈다.
# 평균 보유가 14일 → 49일로 길어지는 것이 이 재튜닝의 본질이다.
#
# 대가: 종목당 최대 손실이 -10% → -15% 로 커진다. 계좌 전체 위험을 같게 두려면
# 포지션 사이즈/종목 수로 상쇄해야 한다(별도 확인). 진입 엣지는 검증 밖이다.
STOP_ATR_MULTIPLIER: float = float(os.environ.get("STOP_ATR_MULTIPLIER", "4.0"))
TAKE_ATR_MULTIPLIER: float = float(os.environ.get("TAKE_ATR_MULTIPLIER", "4.0"))
TRAIL_ATR_MULTIPLIER: float = float(os.environ.get("TRAIL_ATR_MULTIPLIER", "1.5"))

# REQ-DYNTH-05-4: Hard guardrail limits
MAX_STOP_LOSS_PCT: float = float(os.environ.get("MAX_STOP_LOSS_PCT", "15.0"))
MAX_TAKE_PROFIT_PCT: float = float(os.environ.get("MAX_TAKE_PROFIT_PCT", "30.0"))

# SPEC-TRADING-037 REQ-037-3: hard stop FLOOR. Caps how WIDE the stop can be so a
# single position can never lose more than this before exit. Applied via
# ``max(atr_stop, STOP_FLOOR_PCT)``: only the wide side is clamped; a narrow ATR
# stop is left untouched.
# 2026-08-15: -10 → -15 (근거는 위 ATR 승수 주석). MAX_STOP_LOSS_PCT(15.0)와
# 일치하므로 캡 충돌 없음.
STOP_FLOOR_PCT: float = float(os.environ.get("STOP_FLOOR_PCT", "-15.0"))


def get_dynamic_thresholds(ticker: str) -> dict[str, Any]:
    """Compute dynamic thresholds for a ticker.

    REQ-DYNTH-05-2: Returns DynamicThresholds with ATR-based levels.
    REQ-DYNTH-05-5: Falls back to fixed thresholds if ATR unavailable.

    This function is registered as a tool in the SPEC-009 tool registry.

    Args:
        ticker: KRX stock code (e.g. '005930').

    Returns:
        Dict representation of DynamicThresholds model.
    """
    # Try cached ATR first
    cached = _get_cached_atr(ticker)

    if cached:
        atr_pct = cached["atr_pct"]
        atr_14 = cached["atr_14"]
        regime = cached["volatility_regime"]
        last_computed = str(cached.get("computed_at", ""))
    else:
        # Compute fresh ATR
        atr_data = compute_atr(ticker)
        if atr_data is None:
            # REQ-DYNTH-05-5: Fallback to fixed thresholds.
            # SPEC-TRADING-037 REQ-037-4: populate NUMERIC effective_stop /
            # effective_take from the fixed_fallback constants so the position
            # watchdog can still auto-sell an ATR-unavailable holding. Leaving
            # these as None caused a permanent never-sell skip (latent bug).
            fb = DynamicThresholds(ticker=ticker, source="fixed_fallback")
            result = fb.model_copy(
                update={
                    "effective_stop": fb.fixed_fallback_stop,
                    "effective_take": fb.fixed_fallback_take_pct,
                }
            )
            audit("DYNAMIC_THRESHOLD_FALLBACK", actor="thresholds", details={
                "ticker": ticker, "reason": "ATR unavailable",
                "effective_stop": result.effective_stop,
                "effective_take": result.effective_take,
            })
            return result.model_dump()

        atr_pct = atr_data["atr_pct"]
        atr_14 = atr_data["atr_14"]
        regime = classify_regime(ticker, atr_pct)
        last_computed = datetime.now().isoformat()

    # REQ-DYNTH-05-3: Compute dynamic levels
    stop_loss_pct = -STOP_ATR_MULTIPLIER * atr_pct
    take_profit_pct = TAKE_ATR_MULTIPLIER * atr_pct
    trailing_stop_pct = -TRAIL_ATR_MULTIPLIER * atr_pct

    # REQ-DYNTH-05-4: Apply guardrails
    atr_stop = max(stop_loss_pct, -MAX_STOP_LOSS_PCT)
    # SPEC-TRADING-037 REQ-037-3: hard stop FLOOR — clamp the wide side only.
    # ``max`` keeps the SHALLOWER (less negative) stop, so -14% -> -10% (faster
    # exit) while -6% stays -6% (no over-sensitive whipsaw on calm names).
    effective_stop = max(atr_stop, STOP_FLOOR_PCT)
    effective_take = min(take_profit_pct, MAX_TAKE_PROFIT_PCT)

    result = DynamicThresholds(
        ticker=ticker,
        atr_14=round(atr_14, 2),
        atr_pct=round(atr_pct, 4),
        volatility_regime=regime,
        stop_loss_pct=round(stop_loss_pct, 2),
        take_profit_pct=round(take_profit_pct, 2),
        trailing_stop_pct=round(trailing_stop_pct, 2),
        effective_stop=round(effective_stop, 2),
        effective_take=round(effective_take, 2),
        source="dynamic",
        last_computed=last_computed,
    )

    audit("DYNAMIC_THRESHOLD_SERVED", actor="thresholds", details={
        "ticker": ticker,
        "atr_pct": atr_pct,
        "regime": regime,
        "effective_stop": effective_stop,
        "effective_take": effective_take,
    })

    return result.model_dump()


def _get_cached_atr(ticker: str) -> dict[str, Any] | None:
    """Retrieve today's cached ATR value from atr_cache table."""
    sql = """
        SELECT atr_14, atr_pct, close_price, volatility_regime, computed_at
          FROM atr_cache
         WHERE ticker = %s
         ORDER BY date DESC
         LIMIT 1
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (ticker,))
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        LOG.warning("ATR cache lookup failed for %s: %s", ticker, e)
        return None
