"""SPEC-TRADING-036 REQ-036-3 — late-cycle ceiling defence.

Evaluates 5 late-cycle risk signals (weekday 16:05, after the 16:00 daily
report) and, on breach, forces stage-specific cash floors / entry blocks / a
severe forced partial sell. Defence always takes priority over bull mode (S-3):
while ``late_cycle_defense_active`` is true, REQ-036-2 bull mode is auto-OFF.

Signals (SPEC-016 draft thresholds, REQ-036-3 a):

    신용잔고 (margin)   > 35조  -> moderate ; > 40조 -> severe
    투자자예탁금        > 140조 -> top warning
    V-KOSPI            >= 30   -> immediate de-risk
    KOSPI 일일          <= -3%  -> flash de-risk

``(unavailable)`` signals (``None``) are SKIPPED, never triggered (REQ-036-3 c).
Robust signals (KOSPI daily %, from pykrx) are the floor of the defence — even
if every external signal is unavailable, the flash signal still fires.

The severe-stage 30%-of-quantity forced sell reuses SPEC-033's direct
``kis_sell`` bypass: risk-reducing exits must NOT pass through the orchestrator
halt gate or the daily-order-count pre-check.

@MX:SPEC: SPEC-TRADING-036
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from trading.alerts.telegram import system_briefing
from trading.data.korea_momentum import gather_momentum
from trading.db.session import (
    get_system_state,
    log_late_cycle_event,
    set_late_cycle_defense,
)
from trading.kis.order import sell as kis_sell

LOG = logging.getLogger(__name__)

# REQ-036-3 a — thresholds (조원 / index / %).
MARGIN_MODERATE_JO = 35.0
MARGIN_SEVERE_JO = 40.0
DEPOSITS_TOP_JO = 140.0
VKOSPI_IMMEDIATE = 30.0
KOSPI_FLASH_PCT = -3.0

# SPEC-036 observation mode: V-KOSPI reads ~71 on this data feed (KOSPI is also
# ~3x real scale), so the threshold 30 would put the system into permanent
# "immediate de-risk" (block all new buys). Decision (risk owner): collect/log
# V-KOSPI but do NOT let it trigger the defense yet — recalibrate after 1-2
# weeks of paper observation. The other 3 signals (margin, deposits, KOSPI -3%)
# stay active. When calibrated, flip this to True and adjust VKOSPI_IMMEDIATE.
VKOSPI_TRIGGER_ENABLED = False

# REQ-036-3 b — per-stage enforcement (cash floor %; severe also forces a sell).
STAGE_CASH_FLOOR = {
    "moderate": 30.0,
    "severe": 50.0,
    "top": 60.0,
    "immediate": 30.0,
    "flash": 30.0,
}
SEVERE_FORCED_SELL_PCT = 0.30
COOLDOWN_HOURS = 24

# Stages that block all NEW entries (REQ-036-3 b).
_BLOCK_ENTRY_STAGES = {"severe", "top", "immediate", "flash"}


@dataclass(frozen=True)
class DefenseInput:
    """The late-cycle signal values. ``None`` == (unavailable) -> skipped."""

    margin_jo: float | None = None
    deposits_jo: float | None = None
    vkospi: float | None = None
    kospi_daily_pct: float | None = None


@dataclass(frozen=True)
class SignalTrigger:
    signal_name: str   # 'margin'|'deposits'|'vkospi'|'kospi_daily'
    level: str
    value: float
    unit: str


@dataclass(frozen=True)
class DefenseResult:
    triggered: bool
    level: str | None
    cash_floor_pct: float
    forced_sell_pct: float
    block_new_entry: bool
    triggers: list[SignalTrigger] = field(default_factory=list)


def _detect(signals: DefenseInput) -> list[SignalTrigger]:
    """Return every breached signal (skipping ``None`` / unavailable values)."""
    out: list[SignalTrigger] = []
    if signals.margin_jo is not None:
        if signals.margin_jo > MARGIN_SEVERE_JO:
            out.append(SignalTrigger("margin", "severe", signals.margin_jo, "조원"))
        elif signals.margin_jo > MARGIN_MODERATE_JO:
            out.append(SignalTrigger("margin", "moderate", signals.margin_jo, "조원"))
    if signals.deposits_jo is not None and signals.deposits_jo > DEPOSITS_TOP_JO:
        out.append(SignalTrigger("deposits", "top", signals.deposits_jo, "조원"))
    # SPEC-036 observation mode: V-KOSPI is collected/logged elsewhere but is
    # gated out of triggering until recalibrated (VKOSPI_TRIGGER_ENABLED).
    if (
        VKOSPI_TRIGGER_ENABLED
        and signals.vkospi is not None
        and signals.vkospi >= VKOSPI_IMMEDIATE
    ):
        out.append(SignalTrigger("vkospi", "immediate", signals.vkospi, ""))
    if signals.kospi_daily_pct is not None and signals.kospi_daily_pct <= KOSPI_FLASH_PCT:
        out.append(SignalTrigger("kospi_daily", "flash", signals.kospi_daily_pct, "%"))
    return out


# @MX:ANCHOR: SPEC-TRADING-036 REQ-036-3 — single late-cycle signal evaluator.
# @MX:REASON: fan_in >= 3 (16:05 scheduler job, the run entrypoint, and unit
#             tests). The governing-level selection and the (unavailable)->skip
#             rule are the load-bearing invariants: bypassing this would let a
#             stale/partial signal set drive the forced-sell path, violating the
#             capital-preservation policy.
# @MX:SPEC: SPEC-TRADING-036
def evaluate(signals: DefenseInput) -> DefenseResult:
    """Evaluate the 5 signals into a governing defence stage (pure)."""
    triggers = _detect(signals)
    if not triggers:
        return DefenseResult(
            triggered=False, level=None, cash_floor_pct=0.0,
            forced_sell_pct=0.0, block_new_entry=False, triggers=[],
        )

    # Governing enforcement = the strongest of every breached stage. The cash
    # floor is the MAX across triggers; a severe trigger additionally forces a
    # partial sell; any block-entry stage blocks new entries.
    levels = [t.level for t in triggers]
    cash_floor = max(STAGE_CASH_FLOOR[lv] for lv in levels)
    forced = SEVERE_FORCED_SELL_PCT if "severe" in levels else 0.0
    block = any(lv in _BLOCK_ENTRY_STAGES for lv in levels)

    # The reported governing level is the one whose cash floor is the max
    # (tie-break: prefer 'severe' so the forced-sell stage is visible).
    if "severe" in levels:
        governing = "severe"
    else:
        governing = max(levels, key=lambda lv: STAGE_CASH_FLOOR[lv])

    return DefenseResult(
        triggered=True,
        level=governing,
        cash_floor_pct=cash_floor,
        forced_sell_pct=forced,
        block_new_entry=block,
        triggers=triggers,
    )


# ---------------------------------------------------------------------------
# Severe forced deleverage — direct kis_sell bypass (REQ-036-3 e, Q-4)
# ---------------------------------------------------------------------------
def _build_client() -> Any:
    """Build a KIS client for the active trading mode (test seam)."""
    from trading.config import get_settings
    from trading.kis.client import KisClient

    return KisClient(get_settings().trading_mode)


def _read_holdings(client: Any) -> list[dict[str, Any]]:
    """Return the current ``balance()`` holdings list (test seam)."""
    from trading.kis.account import balance

    return balance(client).get("holdings", []) or []


# @MX:WARN: direct kis_sell bypasses the orchestrator halt gate and the
#           daily-order-count pre-check (REQ-036-3 e — same path as SPEC-033's
#           position_watchdog).
# @MX:REASON: risk-reducing exits must always execute; a buy-oriented gate or a
#             tripped halt must never block a capital-preservation deleverage.
#             Per-ticker isolation absorbs real market rejections.
# @MX:SPEC: SPEC-TRADING-036
def forced_deleverage(pct: float = SEVERE_FORCED_SELL_PCT) -> int:
    """Force-sell ``pct`` of each holding's quantity (Q-4: 30% of qty).

    Returns the number of tickers a sell was issued for. Best-effort and
    isolated per ticker — a single market rejection (lower-limit/locked) must
    not abort the sweep. Never raises (the 16:05 job must always complete).
    """
    try:
        client = _build_client()
        holdings = _read_holdings(client)
    except Exception:
        LOG.exception("late_cycle: could not read balance for forced deleverage")
        return 0

    sold = 0
    for holding in holdings:
        ticker = holding.get("ticker", "")
        try:
            qty = int(holding.get("qty", 0) or 0)
            if qty <= 0:
                continue
            sell_qty = max(1, math.floor(qty * pct))
            kis_sell(
                client,
                ticker=ticker,
                qty=sell_qty,
                order_type="market",
                persona_decision_id=None,
            )
            sold += 1
            LOG.info("late_cycle forced deleverage ticker=%s qty=%d", ticker, sell_qty)
        except Exception:
            LOG.exception("late_cycle: forced deleverage failed for %s", ticker)
    return sold


# ---------------------------------------------------------------------------
# Cooldown (REQ-036-3 f)
# ---------------------------------------------------------------------------
def cooldown_elapsed(entered_at: datetime | None, now: datetime | None = None) -> bool:
    """True when at least ``COOLDOWN_HOURS`` have passed since ``entered_at``."""
    if entered_at is None:
        return True
    now = now or datetime.now(UTC)
    return (now - entered_at) >= timedelta(hours=COOLDOWN_HOURS)


# ---------------------------------------------------------------------------
# Scheduler entrypoint (REQ-036-3 b/f/g/h)
# ---------------------------------------------------------------------------
def _signals_from_momentum() -> DefenseInput:
    snap = gather_momentum()
    return DefenseInput(
        margin_jo=getattr(snap, "margin_jo", None),
        deposits_jo=getattr(snap, "deposits_jo", None),
        vkospi=getattr(snap, "vkospi", None),
        kospi_daily_pct=getattr(snap, "kospi_daily_pct", None),
    )


def _alert(message: str) -> None:
    """Best-effort Telegram alert — a send failure must not abort the job."""
    try:
        system_briefing("LATE-CYCLE DEFENSE", message)
    except Exception:
        LOG.warning("late_cycle: telegram alert failed (swallowed)")


def run_late_cycle_evaluation() -> dict[str, Any]:
    """Weekday-16:05 entrypoint. Evaluate signals; enforce / clear defence.

    Returns a metrics dict ``{triggered, cleared, level, deleveraged}`` for
    observability. Never raises (R-2 / C-9): a dead data source degrades to a
    no-trigger evaluation, never an abort.
    """
    metrics = {"triggered": False, "cleared": False, "level": None, "deleveraged": 0}

    try:
        signals = _signals_from_momentum()
    except Exception:
        LOG.exception("late_cycle: momentum gather failed — treating as no signal")
        signals = DefenseInput()

    result = evaluate(signals)

    if result.triggered:
        now = datetime.now(UTC)
        set_late_cycle_defense(active=True, level=result.level, entered_at=now)
        for trig in result.triggers:
            log_late_cycle_event(
                event_type="trigger", signal_name=trig.signal_name,
                value=trig.value, unit=trig.unit, level=trig.level,
            )
        primary = max(result.triggers, key=lambda t: STAGE_CASH_FLOOR[t.level])
        _alert(
            f"⚠️ {primary.signal_name}={primary.value:g}{primary.unit}, "
            f"level={result.level} — 현금 바닥 {result.cash_floor_pct:.0f}%, "
            f"신규 진입 {'차단' if result.block_new_entry else '제한'}, 불장 OFF"
        )
        if result.forced_sell_pct > 0:
            metrics["deleveraged"] = forced_deleverage(result.forced_sell_pct)
        metrics["triggered"] = True
        metrics["level"] = result.level
        return metrics

    # No signal currently breached — clear only if defence was active AND the
    # 24h cooldown has elapsed (REQ-036-3 f).
    state = get_system_state()
    if state.get("late_cycle_defense_active"):
        entered_at = state.get("late_cycle_entered_at")
        if cooldown_elapsed(entered_at):
            set_late_cycle_defense(active=False, level=None, entered_at=None)
            log_late_cycle_event(
                event_type="clear", signal_name="all", value=None, unit="", level=None,
            )
            _alert("후기 사이클 방어 해제 (24h 쿨다운 경과, 신호 해소) — 불장 모드 재허용")
            metrics["cleared"] = True
    return metrics
