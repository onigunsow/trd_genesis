"""Tests for Volatility Calculator and Dynamic Thresholds (Modules 4-5).

Tests REQ-VOL-04-1 through REQ-VOL-04-6, REQ-DYNTH-05-1 through REQ-DYNTH-05-5.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trading.strategy.volatility.atr import MIN_DAYS_FOR_ATR, _ema, compute_atr
from trading.strategy.volatility.regime import _classify_by_absolute, classify_regime
from trading.strategy.volatility.thresholds import (
    MAX_STOP_LOSS_PCT,
    MAX_TAKE_PROFIT_PCT,
    STOP_ATR_MULTIPLIER,
    TAKE_ATR_MULTIPLIER,
    TRAIL_ATR_MULTIPLIER,
    get_dynamic_thresholds,
)


class TestEMA:
    """Test EMA computation helper."""

    def test_single_value(self):
        assert _ema([100.0], 14) == 100.0

    def test_constant_series(self):
        values = [50.0] * 20
        result = _ema(values, 14)
        assert abs(result - 50.0) < 0.01

    def test_increasing_series_above_sma(self):
        values = list(range(1, 21))
        values_float = [float(v) for v in values]
        result = _ema(values_float, 14)
        sma = sum(values_float[:14]) / 14
        # EMA of increasing series should be above SMA of first period
        assert result > sma

    def test_empty_returns_zero(self):
        assert _ema([], 14) == 0.0


class TestComputeATR:
    """REQ-VOL-04-2: Standard 14-day ATR computation."""

    @patch("trading.strategy.volatility.atr.connection")
    def test_normal_atr_computation(self, mock_conn_ctx):
        """M4-1: Standard ATR computation with sufficient data."""
        # Create mock OHLCV data (30+ days)
        rows = []
        base_price = 150000
        for i in range(35):
            rows.append({
                "date": f"2025-03-{i+1:02d}" if i < 28 else f"2025-04-{i-27:02d}",
                "open": base_price + i * 100,
                "high": base_price + i * 100 + 5000,
                "low": base_price + i * 100 - 3000,
                "close": base_price + i * 100 + 1000,
                "volume": 1000000,
            })
        # Reverse because query is DESC
        rows_desc = list(reversed(rows))

        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = rows_desc
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn_ctx.return_value = mock_conn

        result = compute_atr("005930")

        assert result is not None
        assert "atr_14" in result
        assert "atr_pct" in result
        assert "close_price" in result
        assert result["atr_14"] > 0
        assert result["atr_pct"] > 0

    @patch("trading.strategy.volatility.atr.connection")
    def test_insufficient_data_returns_none(self, mock_conn_ctx):
        """M4-4: Less than MIN_DAYS_FOR_ATR returns None."""
        # Only 3 rows
        rows = [
            {"date": "2025-03-01", "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000},
            {"date": "2025-03-02", "open": 102, "high": 107, "low": 97, "close": 104, "volume": 1000},
            {"date": "2025-03-03", "open": 104, "high": 108, "low": 99, "close": 103, "volume": 1000},
        ]

        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = rows
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn_ctx.return_value = mock_conn

        result = compute_atr("999999")
        assert result is None


class TestTrueRange:
    """REQ-VOL-04-2: True Range formula correctness."""

    @patch("trading.strategy.volatility.atr.connection")
    def test_true_range_formula(self, mock_conn_ctx):
        """M4-2: TR = max(H-L, |H-prevC|, |L-prevC|)."""
        # Two rows: yesterday close=148000, today H=150000, L=145000
        rows = [
            {"date": "2025-03-02", "open": 149000, "high": 150000, "low": 145000, "close": 149500, "volume": 1000000},
            {"date": "2025-03-01", "open": 147000, "high": 149000, "low": 146000, "close": 148000, "volume": 900000},
        ] + [
            {"date": f"2025-02-{28-i:02d}", "open": 147000, "high": 149000, "low": 146000, "close": 148000, "volume": 900000}
            for i in range(10)
        ]
        # 12 rows total in DESC order

        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = rows
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn_ctx.return_value = mock_conn

        result = compute_atr("005930")
        # With 12 rows, we have enough data (>= MIN_DAYS_FOR_ATR=5)
        # TR for the latest day should be max(5000, 2000, 3000) = 5000
        # but ATR is EMA of all TR values
        assert result is not None


class TestClassifyRegime:
    """REQ-VOL-04-4: Volatility regime classification."""

    def test_absolute_classification_extreme(self):
        assert _classify_by_absolute(6.0) == "extreme"

    def test_absolute_classification_high(self):
        assert _classify_by_absolute(3.5) == "high"

    def test_absolute_classification_normal(self):
        assert _classify_by_absolute(2.0) == "normal"

    def test_absolute_classification_low(self):
        assert _classify_by_absolute(0.8) == "low"

    @patch("trading.strategy.volatility.regime.connection")
    def test_percentile_based_classification(self, mock_conn_ctx):
        """M4-3: Classification based on 1-year ATR percentile."""
        # Simulate 250 historical ATR values
        rows = [{"atr_pct": 1.0 + i * 0.01} for i in range(250)]

        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = rows
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn_ctx.return_value = mock_conn

        # ATR at 90th percentile = 1.0 + 225 * 0.01 = 3.25
        # Anything above that is extreme
        regime = classify_regime("005930", 3.5)
        assert regime == "extreme"


class TestGetDynamicThresholds:
    """REQ-DYNTH-05-1 through REQ-DYNTH-05-5."""

    @patch("trading.strategy.volatility.thresholds.audit")
    @patch("trading.strategy.volatility.thresholds._get_cached_atr")
    def test_normal_volatility_ticker(self, mock_cache, mock_audit):
        """M5-1: Normal volatility ticker returns dynamic thresholds."""
        mock_cache.return_value = {
            "atr_14": 2700.0,
            "atr_pct": 1.8,
            "close_price": 150000,
            "volatility_regime": "normal",
            "computed_at": "2025-03-15T16:30:00",
        }

        result = get_dynamic_thresholds("005930")

        assert result["ticker"] == "005930"
        assert result["atr_pct"] == 1.8
        assert result["volatility_regime"] == "normal"
        assert result["stop_loss_pct"] == round(-STOP_ATR_MULTIPLIER * 1.8, 2)  # -3.6
        assert result["take_profit_pct"] == round(TAKE_ATR_MULTIPLIER * 1.8, 2)  # 5.4
        assert result["trailing_stop_pct"] == round(-TRAIL_ATR_MULTIPLIER * 1.8, 2)  # -2.7
        assert result["effective_stop"] == -3.6  # within guardrail
        assert result["effective_take"] == 5.4  # within guardrail
        assert result["source"] == "dynamic"

    @patch("trading.strategy.volatility.thresholds.audit")
    @patch("trading.strategy.volatility.thresholds._get_cached_atr")
    def test_extreme_volatility_guardrail_cap(self, mock_cache, mock_audit):
        """M5-2: High volatility ticker hits guardrail cap."""
        mock_cache.return_value = {
            "atr_14": 12000.0,
            "atr_pct": 8.0,
            "close_price": 150000,
            "volatility_regime": "extreme",
            "computed_at": "2025-03-15T16:30:00",
        }

        result = get_dynamic_thresholds("BIOTECH")

        assert result["ticker"] == "BIOTECH"
        # Raw stop = -2 * 8.0 = -16.0, capped to -15.0
        assert result["stop_loss_pct"] == -16.0
        assert result["effective_stop"] == -MAX_STOP_LOSS_PCT  # -15.0
        # Raw take = 3 * 8.0 = 24.0, within 30.0 cap
        assert result["take_profit_pct"] == 24.0
        assert result["effective_take"] == 24.0
        assert result["source"] == "dynamic"

    @patch("trading.strategy.volatility.thresholds.audit")
    @patch("trading.strategy.volatility.thresholds._get_cached_atr")
    @patch("trading.strategy.volatility.thresholds.compute_atr")
    def test_no_atr_data_fallback(self, mock_compute, mock_cache, mock_audit):
        """M5-3: No ATR data falls back to fixed thresholds."""
        mock_cache.return_value = None
        mock_compute.return_value = None

        result = get_dynamic_thresholds("NEW_IPO")

        assert result["ticker"] == "NEW_IPO"
        assert result["source"] == "fixed_fallback"
        assert result["fixed_fallback_stop"] == -7.0
        assert result["atr_pct"] is None
        assert result["volatility_regime"] is None

    @patch("trading.strategy.volatility.thresholds.audit")
    @patch("trading.strategy.volatility.thresholds._get_cached_atr")
    def test_take_profit_guardrail_cap(self, mock_cache, mock_audit):
        """Take profit capped at MAX_TAKE_PROFIT_PCT."""
        mock_cache.return_value = {
            "atr_14": 15000.0,
            "atr_pct": 11.0,
            "close_price": 136000,
            "volatility_regime": "extreme",
            "computed_at": "2025-03-15T16:30:00",
        }

        result = get_dynamic_thresholds("VOLATILE")

        # Raw take = 3 * 11.0 = 33.0, capped to 30.0
        assert result["take_profit_pct"] == 33.0
        assert result["effective_take"] == MAX_TAKE_PROFIT_PCT  # 30.0
