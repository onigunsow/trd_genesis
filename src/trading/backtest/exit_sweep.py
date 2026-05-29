"""SPEC-TRADING-037 REQ-037-2 — exit-rule parameter sweep harness.

Extends the existing vectorised backtest engine (``backtest.engine``, whose
cost constants and max-drawdown idea are reused) with a per-position,
look-ahead-free DETERMINISTIC exit-rule simulator and a parameter sweep over
candidate stop/take settings.

================================ SCOPE LIMIT ================================
This harness validates ONLY the deterministic exit rules (ATR stop multiplier,
hard stop FLOOR, take-profit). It does NOT — and cannot — validate the LLM
entry edge: reproducing "what the LLM would have bought in the past" injects
look-ahead bias. The mechanical entry model below ("buy every Nth day") is a
purely deterministic control variable used to generate many entry points so the
EXIT rules can be stress-tested. Its output is "a robust exit-parameter set",
NOT "evidence the strategy is profitable" (SPEC C-1). Entry-edge profitability
is confirmed only by forward paper trading via ``edge-report``.
============================================================================
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from typing import Any

from trading.backtest.engine import (
    DEFAULT_FEE_RATE,
    DEFAULT_SLIPPAGE,
    DEFAULT_TAX_RATE,
)

LOG = logging.getLogger(__name__)

Bar = dict[str, Any]  # {ts, open, high, low, close}


@dataclass(frozen=True)
class ExitParams:
    """A candidate deterministic exit-rule parameter set.

    ``stop_floor_pct`` is the hard stop FLOOR (a negative percent, e.g. -7.0).
    The effective stop is ``max(-stop_atr_mult * atr_pct, stop_floor_pct)`` —
    the same conservative ``max`` semantics as the live watchdog (REQ-037-3):
    the floor pulls a too-deep ATR stop up so losses are cut sooner, while a
    shallow ATR stop is kept as-is.
    """

    stop_atr_mult: float
    stop_floor_pct: float
    take_atr_mult: float


@dataclass
class TradeResult:
    """One simulated round-trip (deterministic exit only — see module scope)."""

    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float
    gross_return_pct: float
    net_return_pct: float
    holding_days: int
    exit_reason: str  # "stop" | "take" | "time"


@dataclass
class SweepMetrics:
    """Aggregate metrics for one parameter set across all simulated trades."""

    params: ExitParams
    win_rate: float
    expectancy: float
    avg_return_pct: float
    mdd: float
    avg_hold_days: float
    trades: int


@dataclass
class Recommendation:
    """The robust recommended parameter set plus supporting rationale."""

    params: ExitParams
    metrics: SweepMetrics
    rationale: str = ""
    ranked: list[SweepMetrics] = field(default_factory=list)


def mechanical_entries(bars: list[Bar], every_n: int) -> list[int]:
    """Look-ahead-free entry indices: buy every ``every_n``-th bar.

    This is a deterministic CONTROL VARIABLE used to generate many entry points
    to stress-test the exit rules. It deliberately does NOT model the LLM entry
    edge (doing so would inject look-ahead bias — SPEC C-1).
    """
    if every_n < 1:
        raise ValueError("every_n must be >= 1")
    return list(range(0, len(bars), every_n))


def _round_trip_cost_pct(fee_rate: float, tax_rate: float, slippage: float) -> float:
    """Round-trip cost as a percent: fee+slip on both legs, tax on the sell."""
    return (fee_rate + slippage) * 2 * 100.0 + tax_rate * 100.0


def simulate_position(
    bars: list[Bar],
    entry_idx: int,
    atr_pct: float,
    params: ExitParams,
    *,
    fee_rate: float = DEFAULT_FEE_RATE,
    tax_rate: float = DEFAULT_TAX_RATE,
    slippage: float = DEFAULT_SLIPPAGE,
) -> TradeResult:
    """Walk a single position forward and exit on the first rule hit.

    Deterministic rules:
      effective_stop = max(-stop_atr_mult * atr_pct, stop_floor_pct)  (negative %)
      take_pct       = +take_atr_mult * atr_pct                        (positive %)

    Each subsequent bar is checked intraday: if the bar's LOW reaches the stop
    level the position exits AT the stop level ("stop"); else if the bar's HIGH
    reaches the take level it exits AT the take level ("take"). Stop is checked
    first (conservative — capital preservation). If no threshold is ever hit the
    position exits at the final close ("time").
    """
    entry_price = float(bars[entry_idx]["close"])
    effective_stop = max(-params.stop_atr_mult * atr_pct, params.stop_floor_pct)
    take_pct = params.take_atr_mult * atr_pct

    stop_price = entry_price * (1.0 + effective_stop / 100.0)
    take_price = entry_price * (1.0 + take_pct / 100.0)

    exit_idx = len(bars) - 1
    exit_price = float(bars[exit_idx]["close"])
    exit_reason = "time"

    for j in range(entry_idx + 1, len(bars)):
        low = float(bars[j]["low"])
        high = float(bars[j]["high"])
        if low <= stop_price:
            exit_idx = j
            exit_price = stop_price          # exit AT the stop level
            exit_reason = "stop"
            break
        if high >= take_price:
            exit_idx = j
            exit_price = take_price          # exit AT the take level
            exit_reason = "take"
            break

    gross = (exit_price / entry_price - 1.0) * 100.0
    net = gross - _round_trip_cost_pct(fee_rate, tax_rate, slippage)

    return TradeResult(
        entry_idx=entry_idx,
        exit_idx=exit_idx,
        entry_price=entry_price,
        exit_price=exit_price,
        gross_return_pct=gross,
        net_return_pct=net,
        holding_days=exit_idx - entry_idx,
        exit_reason=exit_reason,
    )


def _max_drawdown(returns_pct: list[float]) -> float:
    """Max drawdown of an equity curve built by compounding trade returns.

    Reuses the engine's drawdown idea (peak-to-trough on cumulative equity).
    Returns a non-positive fraction (e.g. -0.12 for a 12% drawdown).
    """
    if not returns_pct:
        return 0.0
    equity = 1.0
    peak = 1.0
    mdd = 0.0
    for r in returns_pct:
        equity *= 1.0 + r / 100.0
        peak = max(peak, equity)
        dd = (equity - peak) / peak
        mdd = min(mdd, dd)
    return mdd


def run_exit_simulation(
    price_data: dict[str, list[Bar]],
    atr_by_symbol: dict[str, float],
    params: ExitParams,
    *,
    every_n: int = 5,
    fee_rate: float = DEFAULT_FEE_RATE,
    tax_rate: float = DEFAULT_TAX_RATE,
    slippage: float = DEFAULT_SLIPPAGE,
) -> SweepMetrics:
    """Simulate mechanical entries + the given exit rule across the universe.

    Trades are collected in chronological order of entry to build a single
    compounded equity curve for the drawdown metric.
    """
    trades: list[TradeResult] = []
    for symbol, bars in price_data.items():
        atr_pct = atr_by_symbol.get(symbol)
        if not atr_pct or len(bars) < 2:
            continue
        for entry_idx in mechanical_entries(bars, every_n):
            if entry_idx >= len(bars) - 1:
                continue  # no room to exit
            trades.append(simulate_position(
                bars, entry_idx, atr_pct, params,
                fee_rate=fee_rate, tax_rate=tax_rate, slippage=slippage,
            ))

    n = len(trades)
    if n == 0:
        return SweepMetrics(params, 0.0, 0.0, 0.0, 0.0, 0.0, 0)

    nets = [t.net_return_pct for t in trades]
    wins = sum(1 for r in nets if r > 0)
    win_rate = wins / n
    expectancy = sum(nets) / n
    avg_return = expectancy
    avg_hold = sum(t.holding_days for t in trades) / n
    mdd = _max_drawdown(nets)

    return SweepMetrics(
        params=params,
        win_rate=win_rate,
        expectancy=expectancy,
        avg_return_pct=avg_return,
        mdd=mdd,
        avg_hold_days=avg_hold,
        trades=n,
    )


def run_sweep(
    price_data: dict[str, list[Bar]],
    atr_by_symbol: dict[str, float],
    *,
    stop_atr_mults: list[float],
    stop_floor_pcts: list[float],
    take_atr_mults: list[float],
    every_n: int = 5,
    fee_rate: float = DEFAULT_FEE_RATE,
    tax_rate: float = DEFAULT_TAX_RATE,
    slippage: float = DEFAULT_SLIPPAGE,
) -> list[SweepMetrics]:
    """Evaluate every combination in the parameter grid (REQ-037-2 (b)).

    Reminder (SPEC C-1): this evaluates DETERMINISTIC EXIT rules only; the
    mechanical entry model is a control variable, not the LLM entry edge.
    """
    results: list[SweepMetrics] = []
    for sa, sf, ta in itertools.product(
        stop_atr_mults, stop_floor_pcts, take_atr_mults
    ):
        params = ExitParams(stop_atr_mult=sa, stop_floor_pct=sf, take_atr_mult=ta)
        results.append(run_exit_simulation(
            price_data, atr_by_symbol, params,
            every_n=every_n, fee_rate=fee_rate, tax_rate=tax_rate, slippage=slippage,
        ))
    return results


def recommend(results: list[SweepMetrics]) -> Recommendation:
    """Pick a ROBUST parameter set, not the single highest peak (REQ-037-2 (d)).

    To avoid over-fitting, each parameter set is scored by blending its own
    expectancy with the mean expectancy of its grid neighbours (sets sharing
    two of three parameters). A set sitting in a stable, broadly-good region
    therefore beats an isolated lucky peak.
    """
    if not results:
        raise ValueError("no sweep results to recommend from")

    by_key = {
        (m.params.stop_atr_mult, m.params.stop_floor_pct, m.params.take_atr_mult): m
        for m in results
    }

    def neighbours(m: SweepMetrics) -> list[SweepMetrics]:
        p = m.params
        out: list[SweepMetrics] = []
        for (sa, sf, ta), other in by_key.items():
            if other is m:
                continue
            shared = (
                (sa == p.stop_atr_mult)
                + (sf == p.stop_floor_pct)
                + (ta == p.take_atr_mult)
            )
            if shared == 2:  # differs in exactly one axis — a grid neighbour
                out.append(other)
        return out

    def robust_score(m: SweepMetrics) -> float:
        nbrs = neighbours(m)
        if not nbrs:
            return m.expectancy
        nbr_mean = sum(n.expectancy for n in nbrs) / len(nbrs)
        return 0.5 * m.expectancy + 0.5 * nbr_mean

    ranked = sorted(results, key=robust_score, reverse=True)
    best = ranked[0]
    rationale = (
        "Robust pick (not single peak): chosen by blending own expectancy with "
        "grid-neighbour mean to favour a stable region. "
        f"stop_atr_mult={best.params.stop_atr_mult}, "
        f"floor={best.params.stop_floor_pct}%, "
        f"take_atr_mult={best.params.take_atr_mult} -> "
        f"win_rate={best.win_rate:.2%}, expectancy={best.expectancy:.3f}%, "
        f"mdd={best.mdd:.2%}, avg_hold={best.avg_hold_days:.1f}d, "
        f"n={best.trades}. NOTE: exit rules only; entry edge NOT validated "
        "(look-ahead) — confirm via forward paper edge-report."
    )
    return Recommendation(
        params=best.params, metrics=best, rationale=rationale, ranked=ranked,
    )
