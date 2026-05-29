"""SPEC-TRADING-037 REQ-037-2 — exit-rule parameter sweep harness tests.

Phase A. Pure-unit (no network, no DB). Validates the DETERMINISTIC exit-rule
simulator and the parameter sweep over small, hand-computable synthetic price
series.

CRITICAL scope note (mirrors SPEC C-1): these tests exercise only the
deterministic stop/take EXIT rules. The mechanical entry model is a look-ahead-
free control variable used to generate many entry points to stress-test exits;
it does NOT model or validate the LLM entry edge.
"""

from __future__ import annotations

from datetime import date, timedelta


def _series(closes, *, lows=None, highs=None, start=date(2020, 1, 1)):
    """Build a list of OHLC bars from close prices.

    When ``lows``/``highs`` are omitted each bar's low/high equals its close
    (close-to-close behaviour). Returns list[dict] with ts/open/high/low/close.
    """
    bars = []
    for i, c in enumerate(closes):
        lo = lows[i] if lows is not None else c
        hi = highs[i] if highs is not None else c
        bars.append({
            "ts": start + timedelta(days=i),
            "open": c,
            "high": hi,
            "low": lo,
            "close": c,
        })
    return bars


class TestMechanicalEntries:
    """REQ-037-2 (c): deterministic, look-ahead-free entry generation."""

    def test_every_nth_day_entries_are_deterministic(self):
        from trading.backtest.exit_sweep import mechanical_entries

        bars = _series([100.0] * 10)
        idx = mechanical_entries(bars, every_n=3)
        # Indices 0, 3, 6, 9 — deterministic, repeatable.
        assert idx == [0, 3, 6, 9]
        assert mechanical_entries(bars, every_n=3) == idx


class TestSimulatePositionExits:
    """REQ-037-2 (a): deterministic stop/take/time exits at the rule level."""

    def test_position_breaching_floor_exits_at_floor(self):
        """atr_stop deeper than floor -> floor governs; exit AT the floor level."""
        from trading.backtest.exit_sweep import ExitParams, simulate_position

        # atr_pct=10, stop_atr_mult=2 -> atr_stop=-20%; floor=-7% -> eff_stop=-7%.
        params = ExitParams(stop_atr_mult=2.0, stop_floor_pct=-7.0, take_atr_mult=5.0)
        # day2 low 92 = -8% breaches -7% floor.
        bars = _series([100.0, 100.0, 92.0], lows=[100.0, 100.0, 92.0])

        trade = simulate_position(
            bars, entry_idx=0, atr_pct=10.0, params=params,
            fee_rate=0.0, tax_rate=0.0, slippage=0.0,
        )

        assert trade.exit_reason == "stop"
        # Exits AT the floor, not at the (worse) intraday low.
        assert round(trade.gross_return_pct, 6) == -7.0
        assert round(trade.exit_price, 4) == 93.0

    def test_take_profit_exits_at_take_level(self):
        from trading.backtest.exit_sweep import ExitParams, simulate_position

        # atr_pct=5, take_atr_mult=2 -> take=+10%.
        params = ExitParams(stop_atr_mult=5.0, stop_floor_pct=-20.0, take_atr_mult=2.0)
        # day2 high 115 >= +10%.
        bars = _series([100.0, 100.0, 115.0], highs=[100.0, 100.0, 115.0])

        trade = simulate_position(
            bars, entry_idx=0, atr_pct=5.0, params=params,
            fee_rate=0.0, tax_rate=0.0, slippage=0.0,
        )

        assert trade.exit_reason == "take"
        assert round(trade.gross_return_pct, 6) == 10.0
        assert round(trade.exit_price, 4) == 110.0

    def test_time_exit_at_last_close_when_no_threshold_hit(self):
        from trading.backtest.exit_sweep import ExitParams, simulate_position

        params = ExitParams(stop_atr_mult=5.0, stop_floor_pct=-20.0, take_atr_mult=5.0)
        # Meanders within band; never hits -25% stop or +25% take.
        bars = _series([100.0, 101.0, 99.0, 103.0])

        trade = simulate_position(
            bars, entry_idx=0, atr_pct=5.0, params=params,
            fee_rate=0.0, tax_rate=0.0, slippage=0.0,
        )

        assert trade.exit_reason == "time"
        assert round(trade.gross_return_pct, 6) == 3.0  # 103 vs 100
        assert trade.holding_days == 3

    def test_costs_reduce_net_return_below_gross(self):
        from trading.backtest.exit_sweep import ExitParams, simulate_position

        params = ExitParams(stop_atr_mult=5.0, stop_floor_pct=-20.0, take_atr_mult=2.0)
        bars = _series([100.0, 110.0], highs=[100.0, 110.0])

        trade = simulate_position(
            bars, entry_idx=0, atr_pct=5.0, params=params,
            fee_rate=0.001, tax_rate=0.002, slippage=0.001,
        )

        assert trade.gross_return_pct > trade.net_return_pct
        # round-trip: buy fee+slip, sell fee+slip+tax.
        expected_cost = (0.001 + 0.001) * 2 * 100 + 0.002 * 100
        assert round(trade.gross_return_pct - trade.net_return_pct, 6) == round(expected_cost, 6)


class TestRunExitSimulationMetrics:
    """REQ-037-2 (a, b): per-parameter metrics from a full simulation."""

    def test_metrics_match_hand_computed_scenario(self):
        from trading.backtest.exit_sweep import ExitParams, run_exit_simulation

        # Two symbols, each yields exactly one trade (entry every 100 days -> idx 0).
        # Symbol UP: take exit +10%. Symbol DOWN: stop exit -7%.
        up = _series([100.0, 110.0], highs=[100.0, 110.0])
        down = _series([100.0, 92.0], lows=[100.0, 92.0])
        price_data = {"UP": up, "DOWN": down}
        atr_by_symbol = {"UP": 5.0, "DOWN": 10.0}

        params = ExitParams(stop_atr_mult=2.0, stop_floor_pct=-7.0, take_atr_mult=2.0)
        metrics = run_exit_simulation(
            price_data, atr_by_symbol, params, every_n=100,
            fee_rate=0.0, tax_rate=0.0, slippage=0.0,
        )

        assert metrics.trades == 2
        assert round(metrics.win_rate, 6) == 0.5  # one win, one loss
        # expectancy = mean(+10, -7) = +1.5
        assert round(metrics.expectancy, 6) == 1.5
        assert round(metrics.avg_return_pct, 6) == 1.5

    def test_monotonic_rising_series_is_sane(self):
        from trading.backtest.exit_sweep import ExitParams, run_exit_simulation

        # Strictly rising -> every position should hit the take, MDD non-deep.
        rising = _series([100.0 + i for i in range(40)],
                         highs=[100.0 + i for i in range(40)])
        price_data = {"BULL": rising}
        atr_by_symbol = {"BULL": 2.0}  # take = 1.5*2 = +3%

        params = ExitParams(stop_atr_mult=2.0, stop_floor_pct=-7.0, take_atr_mult=1.5)
        metrics = run_exit_simulation(
            price_data, atr_by_symbol, params, every_n=5,
            fee_rate=0.0, tax_rate=0.0, slippage=0.0,
        )

        assert metrics.trades > 0
        assert metrics.win_rate == 1.0          # all take exits in a rising market
        assert metrics.expectancy > 0
        assert metrics.mdd >= -0.05             # shallow drawdown in a pure uptrend


class TestParameterSweep:
    """REQ-037-2 (b, d): grid sweep + robust recommendation."""

    def test_sweep_evaluates_at_least_nine_combinations(self):
        from trading.backtest.exit_sweep import run_sweep

        prices = _series([100.0 + (i % 5) for i in range(60)],
                         lows=[98.0 + (i % 5) for i in range(60)],
                         highs=[103.0 + (i % 5) for i in range(60)])
        price_data = {"X": prices}
        atr_by_symbol = {"X": 3.0}

        results = run_sweep(
            price_data, atr_by_symbol,
            stop_atr_mults=[1.0, 1.5, 2.0],
            stop_floor_pcts=[-5.0, -7.0, -10.0],
            take_atr_mults=[2.0],
            every_n=5,
        )

        # 3 x 3 x 1 = 9 combinations.
        assert len(results) >= 9
        for m in results:
            assert hasattr(m, "win_rate")
            assert hasattr(m, "expectancy")
            assert hasattr(m, "mdd")
            assert hasattr(m, "avg_hold_days")
            assert hasattr(m, "trades")

    def test_recommend_returns_a_param_set_from_the_grid(self):
        from trading.backtest.exit_sweep import recommend, run_sweep

        prices = _series([100.0 + (i % 7) for i in range(80)],
                         lows=[97.0 + (i % 7) for i in range(80)],
                         highs=[104.0 + (i % 7) for i in range(80)])
        price_data = {"X": prices}
        atr_by_symbol = {"X": 3.0}

        results = run_sweep(
            price_data, atr_by_symbol,
            stop_atr_mults=[1.0, 1.5, 2.0],
            stop_floor_pcts=[-5.0, -7.0, -10.0],
            take_atr_mults=[2.0, 3.0],
            every_n=5,
        )

        rec = recommend(results)
        # Recommendation is one of the evaluated param sets.
        evaluated = {
            (m.params.stop_atr_mult, m.params.stop_floor_pct, m.params.take_atr_mult)
            for m in results
        }
        assert (
            rec.params.stop_atr_mult,
            rec.params.stop_floor_pct,
            rec.params.take_atr_mult,
        ) in evaluated
        # Rationale is attached and references robustness, not a single peak.
        assert rec.rationale
        assert isinstance(rec.rationale, str)
