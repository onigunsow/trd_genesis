"""Tests for Telegram /tool-calling and /reflection commands (REQ-COMPAT-04-7)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from trading.risk.emergency import handle


class TestToolCallingCommand:
    """Verify /tool-calling on|off command handling."""

    def test_tool_calling_on(self):
        with (
            patch("trading.risk.emergency.update_system_state") as mock_update,
            patch("trading.risk.emergency.audit") as mock_audit,
        ):
            reply = handle("/tool-calling on", actor="chat:60443392")

        mock_update.assert_called_once_with(tool_calling_enabled=True, updated_by="chat:60443392")
        mock_audit.assert_called_once_with(
            "TOOL_CALLING_ACTIVATED",
            actor="chat:60443392",
            details={"enabled": True},
        )
        assert "tool_calling_enabled=True" in reply

    def test_tool_calling_off(self):
        with (
            patch("trading.risk.emergency.update_system_state") as mock_update,
            patch("trading.risk.emergency.audit") as mock_audit,
        ):
            reply = handle("/tool-calling off", actor="chat:60443392")

        mock_update.assert_called_once_with(tool_calling_enabled=False, updated_by="chat:60443392")
        mock_audit.assert_called_once_with(
            "TOOL_CALLING_DEACTIVATED",
            actor="chat:60443392",
            details={"enabled": False},
        )
        assert "tool_calling_enabled=False" in reply

    def test_tool_calling_no_argument(self):
        reply = handle("/tool-calling", actor="chat:60443392")
        assert "사용법" in reply

    def test_tool_calling_invalid_argument(self):
        reply = handle("/tool-calling maybe", actor="chat:60443392")
        assert "사용법" in reply


class TestReflectionCommand:
    """Verify /reflection on|off command handling."""

    def test_reflection_on(self):
        with (
            patch("trading.risk.emergency.update_system_state") as mock_update,
            patch("trading.risk.emergency.audit") as mock_audit,
        ):
            reply = handle("/reflection on", actor="chat:60443392")

        mock_update.assert_called_once_with(reflection_loop_enabled=True, updated_by="chat:60443392")
        mock_audit.assert_called_once_with(
            "REFLECTION_LOOP_ACTIVATED",
            actor="chat:60443392",
            details={"enabled": True},
        )
        assert "reflection_loop_enabled=True" in reply

    def test_reflection_off(self):
        with (
            patch("trading.risk.emergency.update_system_state") as mock_update,
            patch("trading.risk.emergency.audit") as mock_audit,
        ):
            reply = handle("/reflection off", actor="chat:60443392")

        mock_update.assert_called_once_with(reflection_loop_enabled=False, updated_by="chat:60443392")
        mock_audit.assert_called_once_with(
            "REFLECTION_LOOP_DEACTIVATED",
            actor="chat:60443392",
            details={"enabled": False},
        )
        assert "reflection_loop_enabled=False" in reply

    def test_reflection_no_argument(self):
        reply = handle("/reflection", actor="chat:60443392")
        assert "사용법" in reply

    def test_reflection_invalid_argument(self):
        reply = handle("/reflection yes", actor="chat:60443392")
        assert "사용법" in reply


class TestHelpIncludesNewCommands:
    """Verify /help output includes new commands."""

    def test_help_lists_tool_calling(self):
        from trading.risk.emergency import _help
        text = _help()
        assert "/tool-calling" in text

    def test_help_lists_reflection(self):
        from trading.risk.emergency import _help
        text = _help()
        assert "/reflection" in text
