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
STOP_ATR_MULTIPLIER: float = float(os.environ.get("STOP_ATR_MULTIPLIER", "2.0"))
TAKE_ATR_MULTIPLIER: float = float(os.environ.get("TAKE_ATR_MULTIPLIER", "3.0"))
TRAIL_ATR_MULTIPLIER: float = float(os.environ.get("TRAIL_ATR_MULTIPLIER", "1.5"))

# REQ-DYNTH-05-4: Hard guardrail limits
MAX_STOP_LOSS_PCT: float = float(os.environ.get("MAX_STOP_LOSS_PCT", "15.0"))
MAX_TAKE_PROFIT_PCT: float = float(os.environ.get("MAX_TAKE_PROFIT_PCT", "30.0"))


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
            # REQ-DYNTH-05-5: Fallback to fixed thresholds
            result = DynamicThresholds(
                ticker=ticker,
                source="fixed_fallback",
            )
            audit("DYNAMIC_THRESHOLD_FALLBACK", actor="thresholds", details={
                "ticker": ticker, "reason": "ATR unavailable",
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
    effective_stop = max(stop_loss_pct, -MAX_STOP_LOSS_PCT)
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
