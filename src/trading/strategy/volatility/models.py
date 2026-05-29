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
    # SPEC-TRADING-037 REQ-037-4 (Q-2): the human-facing fallback take is an RSI
    # rule (string, used by the decision persona prompt), but the deterministic
    # watchdog needs a NUMERIC effective_take so it does not None-skip an
    # ATR-unavailable holding. ``fixed_fallback_take_pct`` is a conservative
    # engine-cap value (== MAX_TAKE_PROFIT_PCT default 30%) so the auto-take never
    # fires spuriously without ATR; the stop side (-7%) does the real protecting.
    fixed_fallback_take: str = "RSI>85"
    fixed_fallback_take_pct: float = 30.0
    effective_stop: float | None = None
    effective_take: float | None = None
    source: str = "fixed_fallback"
    last_computed: str | None = None
