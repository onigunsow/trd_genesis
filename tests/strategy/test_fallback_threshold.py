"""SPEC-TRADING-037 REQ-037-4 — latent None-threshold bug fix.

When ATR/ohlcv is unavailable, ``get_dynamic_thresholds`` returns
``source="fixed_fallback"``. Previously it left ``effective_stop`` /
``effective_take`` as ``None``, and ``position_watchdog.classify_holding``
treats ``None`` thresholds as a skip -> such a holding could NEVER be
auto-sold (a holding with no ATR is exactly the one most in need of a stop).

The fix populates NUMERIC ``effective_stop`` / ``effective_take`` from the
fixed_fallback constants so auto-sell still works without ATR. The defensive
``None`` skip guard in ``classify_holding`` stays, but the fallback path no
longer produces ``None`` so it is never hit for fallback holdings.

Reproduction-first (money/safety logic).

@MX:SPEC: SPEC-TRADING-037
"""

from __future__ import annotations

from unittest.mock import patch


class TestFallbackPopulatesNumericThresholds:
    """REQ-037-4 (a) — fallback must not leave effective_* as None."""

    @patch("trading.strategy.volatility.thresholds.audit")
    @patch("trading.strategy.volatility.thresholds._get_cached_atr", return_value=None)
    @patch("trading.strategy.volatility.thresholds.compute_atr", return_value=None)
    def test_effective_stop_numeric_on_atr_unavailable(
        self, mock_atr, mock_cached, mock_audit
    ):
        from trading.strategy.volatility.thresholds import get_dynamic_thresholds

        res = get_dynamic_thresholds("005930")
        assert res["source"] == "fixed_fallback"
        # Must be the numeric fixed fallback stop, NOT None.
        assert res["effective_stop"] is not None
        assert res["effective_stop"] == -7.0

    @patch("trading.strategy.volatility.thresholds.audit")
    @patch("trading.strategy.volatility.thresholds._get_cached_atr", return_value=None)
    @patch("trading.strategy.volatility.thresholds.compute_atr", return_value=None)
    def test_effective_take_numeric_on_atr_unavailable(
        self, mock_atr, mock_cached, mock_audit
    ):
        from trading.strategy.volatility.thresholds import get_dynamic_thresholds

        res = get_dynamic_thresholds("005930")
        # effective_take must be a numeric (so classify_holding does not None-skip);
        # conservative engine cap so the auto-take never fires spuriously without
        # ATR (the persona still evaluates RSI-based take separately).
        assert res["effective_take"] is not None
        assert isinstance(res["effective_take"], (int, float))
        # The string RSI rule is retained for the persona prompt context.
        assert res["fixed_fallback_take"] == "RSI>85"


class TestFallbackHoldingStillSells:
    """REQ-037-4 (b) — an ATR-unavailable holding is classified as a numeric stop."""

    def test_fallback_holding_below_stop_triggers_sell(self):
        from trading.watchers.position_watchdog import classify_holding

        # Simulate the fallback threshold dict: numeric stop -7%, numeric take.
        action, qty = classify_holding(
            pnl_pct=-8.0,
            eff_stop=-7.0,
            eff_take=30.0,
            took_profit_today=False,
            qty=10,
        )
        assert action == "stop"
        assert qty == 10

    def test_fallback_holding_above_stop_skips(self):
        from trading.watchers.position_watchdog import classify_holding

        action, qty = classify_holding(
            pnl_pct=-3.0,
            eff_stop=-7.0,
            eff_take=30.0,
            took_profit_today=False,
            qty=10,
        )
        assert action == "skip"
        assert qty == 0

    def test_end_to_end_fallback_then_watchdog_stop(self):
        """ATR unavailable -> numeric fallback -> watchdog stop classification."""
        from trading.strategy.volatility.thresholds import get_dynamic_thresholds
        from trading.watchers.position_watchdog import classify_holding

        with patch(
            "trading.strategy.volatility.thresholds.audit"
        ), patch(
            "trading.strategy.volatility.thresholds._get_cached_atr", return_value=None
        ), patch(
            "trading.strategy.volatility.thresholds.compute_atr", return_value=None
        ):
            th = get_dynamic_thresholds("005930")

        action, qty = classify_holding(
            pnl_pct=-9.0,
            eff_stop=th["effective_stop"],
            eff_take=th["effective_take"],
            took_profit_today=False,
            qty=5,
        )
        assert action == "stop"  # no None-skip; the fallback holding can exit
        assert qty == 5
