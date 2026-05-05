"""Tests for SPEC-012 feature flags and Telegram commands (Module 7).

Tests REQ-MIGR-07-2 through REQ-MIGR-07-5.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trading.risk.emergency import handle, _handle_car_filter, _handle_dyn_threshold


class TestCarFilterCommand:
    """REQ-MIGR-07-3: /car-filter on|off command."""

    @patch("trading.risk.emergency.audit")
    @patch("trading.risk.emergency.update_system_state")
    def test_car_filter_on(self, mock_update, mock_audit):
        """M7-1: Enable CAR filter via Telegram."""
        reply = _handle_car_filter("/car-filter on", actor="chat:60443392")

        assert "car_filter_enabled=True" in reply
        mock_update.assert_called_once_with(car_filter_enabled=True, updated_by="chat:60443392")
        mock_audit.assert_called_once_with("CAR_FILTER_ENABLED", actor="chat:60443392", details={"enabled": True})

    @patch("trading.risk.emergency.audit")
    @patch("trading.risk.emergency.update_system_state")
    def test_car_filter_off(self, mock_update, mock_audit):
        """M7-2: Disable CAR filter via Telegram."""
        reply = _handle_car_filter("/car-filter off", actor="chat:60443392")

        assert "car_filter_enabled=False" in reply
        mock_update.assert_called_once_with(car_filter_enabled=False, updated_by="chat:60443392")
        mock_audit.assert_called_once_with("CAR_FILTER_DISABLED", actor="chat:60443392", details={"enabled": False})

    def test_car_filter_invalid_arg(self):
        """Invalid argument returns usage."""
        reply = _handle_car_filter("/car-filter maybe", actor="test")
        assert "사용법" in reply

    def test_car_filter_no_arg(self):
        """No argument returns usage."""
        reply = _handle_car_filter("/car-filter", actor="test")
        assert "사용법" in reply


class TestDynThresholdCommand:
    """REQ-MIGR-07-3: /dyn-threshold on|off command."""

    @patch("trading.risk.emergency.audit")
    @patch("trading.risk.emergency.update_system_state")
    def test_dyn_threshold_on(self, mock_update, mock_audit):
        """Enable dynamic thresholds via Telegram."""
        reply = _handle_dyn_threshold("/dyn-threshold on", actor="chat:60443392")

        assert "dynamic_thresholds_enabled=True" in reply
        mock_update.assert_called_once_with(dynamic_thresholds_enabled=True, updated_by="chat:60443392")
        mock_audit.assert_called_once_with(
            "DYNAMIC_THRESHOLDS_ENABLED", actor="chat:60443392", details={"enabled": True}
        )

    @patch("trading.risk.emergency.audit")
    @patch("trading.risk.emergency.update_system_state")
    def test_dyn_threshold_off(self, mock_update, mock_audit):
        """Disable dynamic thresholds via Telegram."""
        reply = _handle_dyn_threshold("/dyn-threshold off", actor="chat:60443392")

        assert "dynamic_thresholds_enabled=False" in reply
        mock_update.assert_called_once_with(dynamic_thresholds_enabled=False, updated_by="chat:60443392")

    def test_dyn_threshold_invalid_arg(self):
        reply = _handle_dyn_threshold("/dyn-threshold blah", actor="test")
        assert "사용법" in reply


class TestHandleRoutingToNewCommands:
    """Test that the main handle() function routes to new commands."""

    @patch("trading.risk.emergency._handle_car_filter")
    def test_routes_car_filter(self, mock_handler):
        mock_handler.return_value = "ok"
        result = handle("/car-filter on", actor="test")
        mock_handler.assert_called_once_with("/car-filter on", "test")

    @patch("trading.risk.emergency._handle_dyn_threshold")
    def test_routes_dyn_threshold(self, mock_handler):
        mock_handler.return_value = "ok"
        result = handle("/dyn-threshold off", actor="test")
        mock_handler.assert_called_once_with("/dyn-threshold off", "test")


class TestHelpIncludesNewCommands:
    """REQ-MIGR-07-3: Help text includes new commands."""

    @patch("trading.risk.emergency.get_system_state")
    @patch("trading.risk.emergency.connection")
    def test_help_lists_car_filter(self, mock_conn, mock_state):
        reply = handle("/help", actor="test")
        assert "/car-filter" in reply
        assert "/dyn-threshold" in reply


class TestFixedRulesAlwaysPresent:
    """REQ-MIGR-07-5: Fixed rules always in config.py."""

    def test_fixed_constants_exist(self):
        from trading.config import FIXED_STOP_LOSS_PCT, FIXED_TAKE_PROFIT_RSI

        assert FIXED_STOP_LOSS_PCT == -7.0
        assert FIXED_TAKE_PROFIT_RSI == 85
