"""SPEC-TRADING-037 REQ-037-3 — hard stop FLOOR (backtest-derived -10%).

The 10y KOSPI200 exit-rule sweep found the best per-trade expectancy at
stop=2.0xATR / FLOOR=-10% / take=3.0xATR. The FLOOR caps catastrophic
single-position loss: a wide ATR stop (e.g. -14%) is clamped to -10%, while a
narrow ATR stop (e.g. -6%) is left untouched (the floor only caps the wide
side, via ``max(atr_stop, FLOOR)``).

Reproduction-first (money logic): these assertions fail until the FLOOR is wired
into ``thresholds.get_dynamic_thresholds``.

@MX:SPEC: SPEC-TRADING-037
"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest

from trading.strategy.volatility.thresholds import get_dynamic_thresholds


def _reload_thresholds(monkeypatch, **env):
    """Reload thresholds module so module-level env constants pick up overrides."""
    for key, val in env.items():
        monkeypatch.setenv(key, val)
    import trading.strategy.volatility.thresholds as th

    return importlib.reload(th)


@pytest.fixture(autouse=True)
def _restore_thresholds():
    """Restore default module-level constants after env-override reloads."""
    yield
    import trading.strategy.volatility.thresholds as th

    importlib.reload(th)


class TestStopFloorConstant:
    """REQ-037-3 (b) — FLOOR is a named, configurable, backtest-derived constant."""

    def test_floor_default_is_minus_ten(self):
        import trading.strategy.volatility.thresholds as th

        assert th.STOP_FLOOR_PCT == -10.0

    def test_floor_env_override(self, monkeypatch):
        th = _reload_thresholds(monkeypatch, STOP_FLOOR_PCT="-8.0")
        assert th.STOP_FLOOR_PCT == -8.0


def _atr_data(atr_pct: float) -> dict:
    return {"atr_pct": atr_pct, "atr_14": 1000.0}


class TestStopFloorClamping:
    """REQ-037-3 (a) — effective_stop = max(atr_stop, FLOOR)."""

    @patch("trading.strategy.volatility.thresholds.audit")
    @patch("trading.strategy.volatility.thresholds.classify_regime", return_value="extreme")
    @patch("trading.strategy.volatility.thresholds._get_cached_atr", return_value=None)
    @patch("trading.strategy.volatility.thresholds.compute_atr")
    def test_wide_atr_stop_is_clamped_to_floor(
        self, mock_atr, mock_cached, mock_regime, mock_audit
    ):
        # STOP_ATR_MULTIPLIER default 2.0; atr_pct 7.0 -> atr_stop -14% (wide).
        mock_atr.return_value = _atr_data(7.0)
        res = get_dynamic_thresholds("005930")
        # FLOOR -10% caps the -14% wide stop.
        assert res["effective_stop"] == -10.0

    @patch("trading.strategy.volatility.thresholds.audit")
    @patch("trading.strategy.volatility.thresholds.classify_regime", return_value="low")
    @patch("trading.strategy.volatility.thresholds._get_cached_atr", return_value=None)
    @patch("trading.strategy.volatility.thresholds.compute_atr")
    def test_narrow_atr_stop_is_unchanged(
        self, mock_atr, mock_cached, mock_regime, mock_audit
    ):
        # atr_pct 3.0 -> atr_stop -6% (narrower than -10% floor).
        mock_atr.return_value = _atr_data(3.0)
        res = get_dynamic_thresholds("005930")
        # floor only caps the WIDE side -> narrow stop untouched.
        assert res["effective_stop"] == -6.0

    @patch("trading.strategy.volatility.thresholds.audit")
    @patch("trading.strategy.volatility.thresholds.classify_regime", return_value="extreme")
    @patch("trading.strategy.volatility.thresholds._get_cached_atr", return_value=None)
    @patch("trading.strategy.volatility.thresholds.compute_atr")
    def test_floor_does_not_break_max_stop_loss_guardrail(
        self, mock_atr, mock_cached, mock_regime, mock_audit
    ):
        # atr_pct 20.0 -> stop_loss_pct -40%, MAX_STOP_LOSS_PCT cap -15%, then
        # FLOOR -10% takes over: effective_stop must be -10% (not -40/-15).
        mock_atr.return_value = _atr_data(20.0)
        res = get_dynamic_thresholds("005930")
        assert res["effective_stop"] == -10.0
        # take side unaffected by the stop floor.
        assert res["effective_take"] is not None
