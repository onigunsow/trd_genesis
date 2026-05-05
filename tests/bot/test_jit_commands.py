"""Tests for SPEC-011 Telegram commands (/jit, /prototype, /prototype-status)."""

from __future__ import annotations

from unittest.mock import patch

from trading.risk.emergency import handle


class TestJitCommand:
    """Test /jit Telegram command handling."""

    @patch("trading.risk.emergency.update_system_state")
    @patch("trading.risk.emergency.audit")
    def test_jit_on(self, mock_audit, mock_update):
        result = handle("/jit on")
        mock_update.assert_called_once_with(jit_pipeline_enabled=True, updated_by="telegram")
        mock_audit.assert_called_once()
        assert "활성화" in result

    @patch("trading.risk.emergency.update_system_state")
    @patch("trading.risk.emergency.audit")
    def test_jit_off(self, mock_audit, mock_update):
        with patch("trading.jit.pipeline.get_pipeline"):
            result = handle("/jit off")
        mock_update.assert_called_once_with(jit_pipeline_enabled=False, updated_by="telegram")
        assert "비활성화" in result

    @patch("trading.risk.emergency.update_system_state")
    @patch("trading.risk.emergency.audit")
    def test_jit_ws_off(self, mock_audit, mock_update):
        with patch("trading.jit.pipeline.get_pipeline"):
            result = handle("/jit ws off")
        mock_update.assert_called_once_with(jit_websocket_enabled=False, updated_by="telegram")
        assert "WebSocket" in result
        assert "비활성화" in result

    @patch("trading.risk.emergency.update_system_state")
    @patch("trading.risk.emergency.audit")
    def test_jit_dart_on(self, mock_audit, mock_update):
        result = handle("/jit dart on")
        mock_update.assert_called_once_with(jit_dart_polling_enabled=True, updated_by="telegram")
        assert "DART" in result

    @patch("trading.risk.emergency.update_system_state")
    @patch("trading.risk.emergency.audit")
    def test_jit_news_off(self, mock_audit, mock_update):
        with patch("trading.jit.pipeline.get_pipeline"):
            result = handle("/jit news off")
        mock_update.assert_called_once_with(jit_news_polling_enabled=False, updated_by="telegram")
        assert "News" in result

    def test_jit_no_args(self):
        result = handle("/jit")
        assert "사용법" in result

    def test_jit_invalid_sub(self):
        result = handle("/jit xyz")
        assert "사용법" in result


class TestPrototypeCommand:
    """Test /prototype Telegram command handling."""

    @patch("trading.risk.emergency.update_system_state")
    @patch("trading.risk.emergency.audit")
    def test_prototype_on(self, mock_audit, mock_update):
        result = handle("/prototype on")
        mock_update.assert_called_once_with(prototype_risk_enabled=True, updated_by="telegram")
        assert "활성화" in result

    @patch("trading.risk.emergency.update_system_state")
    @patch("trading.risk.emergency.audit")
    def test_prototype_off(self, mock_audit, mock_update):
        result = handle("/prototype off")
        mock_update.assert_called_once_with(prototype_risk_enabled=False, updated_by="telegram")
        assert "비활성화" in result

    def test_prototype_no_args(self):
        result = handle("/prototype")
        assert "사용법" in result


class TestPrototypeStatusCommand:
    """Test /prototype-status Telegram command."""

    @patch("trading.risk.emergency.get_system_state", return_value={"prototype_risk_enabled": False})
    def test_disabled(self, mock_state):
        result = handle("/prototype-status")
        assert "비활성화" in result
