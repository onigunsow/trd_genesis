"""SPEC-TRADING-035 REQ-035-2/4 — conservative macro-regime branching.

Pure (no DB, no network) policy table + hard cash-floor guard that translate the
cached macro ``current_regime`` into Decision/Risk/Portfolio behaviour.

Design stance (capital-preservation first, per SPEC-016 Q-2/Q-4 and SPEC-035):
- **bull** loosens only *gently* — cash floor 30%->20% (NOT the Phase-3 10%),
  confidence threshold -0.05 (NOT -0.1), sector cap modestly raised, cash target
  shifted to the low end of the guide. Phase-3 aggressive concentration is OUT OF
  SCOPE.
- **bear** tightens — confidence +0.1, sector cap lowered, leverage/margin buys
  blocked, cash target shifted to the high end.
- **neutral** is unchanged (identity).

The two halves are kept independent and unit-tested directly:
- ``adjust_for_regime`` -> the numbers injected into the prompt context.
- ``enforce_cash_floor`` -> the Python guard that the LLM cannot bypass (R-1).

@MX:SPEC: SPEC-TRADING-035
"""

from __future__ import annotations

from dataclasses import dataclass

# Conservative baselines (the current/neutral behaviour — decision.jinja:12,15).
BASE_CASH_FLOOR_PCT = 30.0
BASE_SECTOR_CAP_PCT = 40.0

VALID_REGIMES = ("bull", "neutral", "bear")


@dataclass(frozen=True)
class RegimeAdjustment:
    """Regime-derived threshold adjustments (conservative branch).

    Attributes:
        regime: The normalised regime ('bull'|'neutral'|'bear').
        cash_floor_pct: Hard minimum cash %. New buys are blocked below this.
        confidence_delta: Added to the decision confidence threshold (bull
            lowers it slightly, bear raises it).
        sector_cap_pct: Single-sector concentration cap (direction only — Q-3).
        block_leverage: When True, leverage/margin buys are disallowed (bear).
        cash_target_shift: 'low' | 'keep' | 'high' — which end of the portfolio
            cash guide range to target (REQ-035-4).
    """

    regime: str
    cash_floor_pct: float
    confidence_delta: float
    sector_cap_pct: float
    block_leverage: bool
    cash_target_shift: str


def regime_branch_applied(regime: str | None) -> str:
    """Normalise a (possibly out-of-domain) regime to a valid value.

    Out-of-domain or missing -> 'neutral' (the safe default). Used both for the
    ``regime_branch_applied`` audit field and to drive ``adjust_for_regime``.
    """
    return regime if regime in VALID_REGIMES else "neutral"


def adjust_for_regime(regime: str | None) -> RegimeAdjustment:
    """Return the conservative threshold adjustments for ``regime``."""
    norm = regime_branch_applied(regime)
    if norm == "bull":
        # Gentle loosen — never the Phase-3 aggressive profile.
        return RegimeAdjustment(
            regime="bull",
            cash_floor_pct=20.0,            # 30 -> 20 (NOT 10)
            confidence_delta=-0.05,         # NOT -0.1
            sector_cap_pct=BASE_SECTOR_CAP_PCT + 5.0,  # 40 -> 45 (modest)
            block_leverage=False,
            cash_target_shift="low",
        )
    if norm == "bear":
        return RegimeAdjustment(
            regime="bear",
            cash_floor_pct=BASE_CASH_FLOOR_PCT,         # stay conservative (30)
            confidence_delta=0.1,
            sector_cap_pct=BASE_SECTOR_CAP_PCT - 5.0,   # 40 -> 35 (tighten)
            block_leverage=True,                        # block leverage/margin buys
            cash_target_shift="high",
        )
    # neutral — identity (no change).
    return RegimeAdjustment(
        regime="neutral",
        cash_floor_pct=BASE_CASH_FLOOR_PCT,
        confidence_delta=0.0,
        sector_cap_pct=BASE_SECTOR_CAP_PCT,
        block_leverage=False,
        cash_target_shift="keep",
    )


def prompt_context(regime: str | None, risk_appetite: str | None) -> dict:
    """Build the regime context vars injected into decision.jinja / risk.jinja.

    Returns the normalised regime/risk_appetite plus the conservative adjusted
    numbers (REQ-035-2d). Keys mirror the ``{{ regime_* }}`` template variables.
    """
    adj = adjust_for_regime(regime)
    return {
        "current_regime": adj.regime,
        "current_risk_appetite": risk_appetite or "neutral",
        "regime_cash_floor_pct": adj.cash_floor_pct,
        "regime_confidence_delta": adj.confidence_delta,
        "regime_sector_cap_pct": adj.sector_cap_pct,
        "regime_block_leverage": adj.block_leverage,
    }


def enforce_cash_floor(
    signals: list[dict],
    cash_pct: float,
    regime: str | None,
) -> tuple[list[dict], list[int]]:
    """Hard Python guard: block NEW buys when cash is below the regime floor.

    R-1 mitigation: the prompt context alone is not trusted to keep cash above
    the floor, so this guard drops buy signals when ``cash_pct`` is below the
    regime's ``cash_floor_pct`` (bull = 20%, neutral/bear = 30%). Sell and hold
    signals are NEVER touched (SPEC-033/034 — exits must always pass).

    Returns ``(kept_signals, dropped_indices)`` preserving input order, where
    ``dropped_indices`` are positions in the original ``signals`` list.
    """
    floor = adjust_for_regime(regime).cash_floor_pct
    if cash_pct >= floor:
        return list(signals), []

    kept: list[dict] = []
    dropped: list[int] = []
    for idx, sig in enumerate(signals):
        if sig.get("side") == "buy":
            dropped.append(idx)
            continue
        kept.append(sig)
    return kept, dropped
