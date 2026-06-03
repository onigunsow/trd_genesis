"""SPEC-TRADING-040 M1a/1b (REQ-040-1a/1b) — moderate profit-taking EV gate.

The SPEC mandates that any *added* moderate profit-take threshold must pass an
"expected-value non-decrease" gate against the SPEC-037 exit backtest — a take
threshold that LOWERS expectancy (the narrow-take-profit trap) must NOT be
applied. The 2026-06-03 backtest (10y KOSPI200) confirmed the trap: wider take
(3.0xATR) maximises expectancy; every tighter take (2.0x, 1.5x) reduces it.

This module pins that gate as code so the decision is not a hand-wave: given the
real backtest shape it must REFUSE to adopt a moderate take layer; given a
(hypothetical) backtest where a moderate take improves EV it must ADOPT — proving
the gate is data-driven, not hardcoded to reject.

@MX:SPEC: SPEC-TRADING-040
"""

from __future__ import annotations

from trading.backtest.exit_sweep import ExitParams, SweepMetrics
from trading.risk.profit_take_gate import select_profit_take_threshold


def _metric(take: float, expectancy: float) -> SweepMetrics:
    return SweepMetrics(
        params=ExitParams(stop_atr_mult=2.0, stop_floor_pct=-10.0, take_atr_mult=take),
        win_rate=0.5,
        expectancy=expectancy,
        avg_return_pct=expectancy,
        mdd=-0.5,
        avg_hold_days=10.0,
        trades=1000,
    )


# Real 2026-06-03 backtest shape (stop=2.0, floor=-10), expectancy by take mult:
#   take 3.0 -> +0.344   take 2.0 -> +0.06   take 1.5 -> -0.10
_REAL_SHAPE = [_metric(3.0, 0.344), _metric(2.0, 0.06), _metric(1.5, -0.10)]


class TestEvGate:
    def test_real_backtest_rejects_moderate_profit_taking(self):
        """REQ-040-1b: with the real backtest the moderate take is EV-harming → reject."""
        decision = select_profit_take_threshold(_REAL_SHAPE, current_take_atr_mult=3.0)
        assert decision.adopt is False
        assert decision.best_take_atr_mult == 3.0
        assert "expectancy" in decision.rationale.lower()

    def test_gate_is_data_driven_adopts_when_moderate_improves(self):
        """If a moderate take strictly improved EV, the gate would adopt it.

        Proves the gate is not hardcoded to reject — it follows the data.
        """
        improving = [_metric(3.0, 0.10), _metric(2.0, 0.40), _metric(1.5, 0.05)]
        decision = select_profit_take_threshold(improving, current_take_atr_mult=3.0)
        assert decision.adopt is True
        assert decision.best_take_atr_mult == 2.0

    def test_no_strict_improvement_means_no_adopt(self):
        """A tie (equal EV) does not justify adding a take layer (non-DECREASE
        is necessary but a strict improvement is required to ADOPT a change)."""
        tie = [_metric(3.0, 0.20), _metric(2.0, 0.20)]
        decision = select_profit_take_threshold(tie, current_take_atr_mult=3.0)
        assert decision.adopt is False

    def test_empty_results_no_adopt_no_crash(self):
        decision = select_profit_take_threshold([], current_take_atr_mult=3.0)
        assert decision.adopt is False
