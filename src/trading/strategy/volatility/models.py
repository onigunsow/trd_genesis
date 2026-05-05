"""Pydantic models for Volatility subsystem.

REQ-DYNTH-05-2: DynamicThresholds output model.
"""

from __future__ import annotations

from pydantic import BaseModel


class DynamicThresholds(BaseModel):
    """Per-ticker ATR-based stop/take thresholds.

    REQ-DYNTH-05-2: Complete threshold output for Decision persona.
    """

    ticker: str
    atr_14: float | None = None
    atr_pct: float | None = None
    volatility_regime: str | None = None
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    trailing_stop_pct: float | None = None
    fixed_fallback_stop: float = -7.0
    fixed_fallback_take: str = "RSI>85"
    effective_stop: float | None = None
    effective_take: float | None = None
    source: str = "fixed_fallback"
    last_computed: str | None = None
