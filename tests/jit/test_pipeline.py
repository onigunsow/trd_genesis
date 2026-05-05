"""Tests for JIT pipeline manager — market hours and feature flag checks."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytz

from trading.jit.pipeline import JitPipelineManager


KST = pytz.timezone("Asia/Seoul")


class TestMarketHours:
    """Test market hours detection."""

    def test_during_market_hours(self):
        now = KST.localize(datetime(2026, 5, 5, 10, 30))  # Monday 10:30
        assert JitPipelineManager._is_market_hours(now) is True

    def test_before_market_open(self):
        now = KST.localize(datetime(2026, 5, 5, 8, 59))  # Monday 08:59
        assert JitPipelineManager._is_market_hours(now) is False

    def test_at_market_open(self):
        now = KST.localize(datetime(2026, 5, 5, 9, 0))  # Monday 09:00
        assert JitPipelineManager._is_market_hours(now) is True

    def test_at_market_close(self):
        now = KST.localize(datetime(2026, 5, 5, 15, 30))  # Monday 15:30
        assert JitPipelineManager._is_market_hours(now) is True

    def test_after_market_close(self):
        now = KST.localize(datetime(2026, 5, 5, 15, 31))  # Monday 15:31
        assert JitPipelineManager._is_market_hours(now) is False


class TestPipelineStart:
    """Test pipeline start conditions."""

    @patch("trading.jit.pipeline.is_trading_day", return_value=False)
    def test_rejects_non_trading_day(self, mock_day):
        """REQ-DELTA-01-10: Reject on non-trading days."""
        mgr = JitPipelineManager()
        with patch("trading.jit.pipeline.audit"):
            result = mgr.start(tickers=["005930"])
        assert result is False
        assert mgr.active is False

    @patch("trading.jit.pipeline.is_trading_day", return_value=True)
    @patch("trading.jit.pipeline.get_system_state", return_value={"jit_pipeline_enabled": False})
    def test_rejects_when_disabled(self, mock_state, mock_day):
        """Pipeline not started if feature flag is false."""
        mgr = JitPipelineManager()
        now = KST.localize(datetime(2026, 5, 5, 10, 0))
        with patch("trading.jit.pipeline.datetime") as mock_dt:
            mock_dt.now.return_value = now
            result = mgr.start(tickers=["005930"])
        assert result is False

    def test_stop_when_not_active(self):
        """Stop when not active should be a no-op."""
        mgr = JitPipelineManager()
        mgr.stop()  # Should not raise
        assert mgr.active is False
