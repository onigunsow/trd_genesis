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

import logging
from dataclasses import dataclass

LOG = logging.getLogger(__name__)

# Conservative baselines (the current/neutral behaviour — decision.jinja:12,15).
BASE_CASH_FLOOR_PCT = 30.0
BASE_SECTOR_CAP_PCT = 40.0

VALID_REGIMES = ("bull", "neutral", "bear")

# SPEC-TRADING-036 REQ-036-2: the AGGRESSIVE bull-mode cash floor (10%) — the
# Phase-3 profile SPEC-035 deferred. Distinct from the SPEC-035 conservative
# bull floor (20%); applied ONLY when the 3-AND gate (S-4) holds.
BULL_MODE_CASH_FLOOR_PCT = 10.0


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
    floor_override: float | None = None,
) -> tuple[list[dict], list[int]]:
    """Hard Python guard: block NEW buys when cash is below the regime floor.

    R-1 mitigation: the prompt context alone is not trusted to keep cash above
    the floor, so this guard drops buy signals when ``cash_pct`` is below the
    floor. Sell and hold signals are NEVER touched (SPEC-033/034 — exits must
    always pass).

    The floor is normally the regime's ``cash_floor_pct`` (bull = 20%,
    neutral/bear = 30%). SPEC-TRADING-036 REQ-036-2(e): when bull mode is
    active the caller passes ``floor_override=10.0`` (the aggressive floor) so
    the same hard guard enforces the looser-but-still-real 10% bull floor.

    Returns ``(kept_signals, dropped_indices)`` preserving input order, where
    ``dropped_indices`` are positions in the original ``signals`` list.
    """
    floor = (
        floor_override
        if floor_override is not None
        else adjust_for_regime(regime).cash_floor_pct
    )
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


# ===========================================================================
# SPEC-TRADING-036 REQ-036-2 — bull mode (AGGRESSIVE profile)
# ===========================================================================

# REQ-036-2 (a/b): SPEC-016 original aggressive parameters.
@dataclass(frozen=True)
class BullParams:
    """Aggressive bull-mode targets (SPEC-016 original, paper-only)."""

    target_holdings_min: int = 1
    target_holdings_max: int = 2
    cash_target_min: int = 10
    cash_target_max: int = 20
    holding_days_min: int = 4
    holding_days_max: int = 10
    event_car_threshold: float = 1.0          # strengthened from |1.5%|
    sector_cap_uplift_pct: float = 10.0       # +10%pt sector concentration
    single_stock_uplift_pct: float = 10.0     # +10%pt single-stock max


def bull_params() -> BullParams:
    """Return the aggressive bull-mode parameters (REQ-036-2 a/b)."""
    return BullParams()


# @MX:ANCHOR: SPEC-TRADING-036 REQ-036-2(g) / S-4 — the single bull-mode gate.
# @MX:REASON: fan_in >= 3 (decision.run / risk.run read it; tests assert it).
#             The 3-AND condition is the load-bearing capital-preservation
#             invariant: the aggressive profile must apply ONLY in paper, ONLY
#             when regime is bull, and ONLY when late-cycle defence is OFF.
#             Deriving it at read time (not a stored flag) removes the stale-flag
#             race (R-M3).
# @MX:SPEC: SPEC-TRADING-036
def bull_mode_active(
    regime: str | None,
    late_cycle_defense_active: bool,
    trading_mode: str | None,
) -> bool:
    """Return True iff the aggressive bull profile applies (S-4, 3-AND gate).

    ``bull_mode == (regime=='bull' AND NOT late_cycle_defense_active AND
    trading_mode=='paper')``. The paper-only and late-cycle gates are HARD
    (R-M2 / S-3) — not trusted to the prompt.
    """
    return (
        regime_branch_applied(regime) == "bull"
        and not late_cycle_defense_active
        and trading_mode == "paper"
    )


def event_car_threshold(bull_active: bool) -> float:
    """REQ-036-2(a): |1.0%| in bull mode, |1.5%| otherwise."""
    return bull_params().event_car_threshold if bull_active else 1.5


def bull_prompt_context(bull_active: bool) -> dict:
    """Return the ``{{ bull_* }}`` template vars for decision/risk prompts.

    Always returns ``bull_mode_active`` so the templates can branch; the
    aggressive numbers are only meaningful when ``bull_active`` is True.
    """
    p = bull_params()
    return {
        "bull_mode_active": bull_active,
        "bull_target_holdings_min": p.target_holdings_min,
        "bull_target_holdings_max": p.target_holdings_max,
        "bull_cash_target_min": p.cash_target_min,
        "bull_cash_target_max": p.cash_target_max,
        "bull_holding_days_min": p.holding_days_min,
        "bull_holding_days_max": p.holding_days_max,
        "bull_event_car_threshold": p.event_car_threshold,
        "bull_sector_cap_uplift_pct": p.sector_cap_uplift_pct,
        "bull_single_stock_uplift_pct": p.single_stock_uplift_pct,
        "bull_cash_floor_pct": BULL_MODE_CASH_FLOOR_PCT,
    }


# REQ-036-2 (f): ON/OFF transition Telegram alert. bull_mode is read-time
# derived (not stored), so a transition is detected by comparing against the
# last observed state held in this single-scheduler process (mirrors the
# in-memory marker pattern in position_watchdog). A container restart resets it
# — an accepted limitation; the next genuine transition re-alerts.
_LAST_BULL_ACTIVE: bool | None = None


def _reset_bull_state() -> None:
    """Test seam: clear the in-memory bull-transition tracker."""
    global _LAST_BULL_ACTIVE
    _LAST_BULL_ACTIVE = None


def maybe_notify_bull_transition(now_active: bool) -> bool:
    """Emit a Telegram alert when bull mode flips ON<->OFF. Returns True if sent.

    A Telegram failure is swallowed (the alert is best-effort and must never
    break the cycle that observed the transition).
    """
    global _LAST_BULL_ACTIVE
    if _LAST_BULL_ACTIVE is not None and _LAST_BULL_ACTIVE == now_active:
        return False
    transition = _LAST_BULL_ACTIVE is not None or now_active
    _LAST_BULL_ACTIVE = now_active
    if not transition:
        # First observation and it's OFF — establish baseline without alerting.
        return False
    message = (
        "BULL MODE ON: regime=bull, late_cycle=clear, paper"
        if now_active
        else "BULL MODE OFF: 보수/방어 모드로 전환"
    )
    try:
        system_briefing("BULL MODE", message)
    except Exception:
        LOG.warning("bull-mode transition alert failed (swallowed)")
    return True


# Module-level shim so tests can patch ``regime_branch.system_briefing`` and the
# real Telegram notifier is only imported when actually sending (keeps this pure
# module import-light and avoids a circular import at load time).
def system_briefing(category: str, message: str) -> None:  # pragma: no cover - thin shim
    from trading.alerts.telegram import system_briefing as _sb

    _sb(category, message)
