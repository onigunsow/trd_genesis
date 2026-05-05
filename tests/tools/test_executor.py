"""Tests for tools/executor.py — dispatch, timeout, error handling, logging."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from trading.tools.executor import TOOL_TIMEOUT_SECONDS, execute_tool


class TestExecuteTool:
    """Verify tool execution dispatch and error handling."""

    def test_dispatches_to_correct_function(self):
        """execute_tool dispatches by name to the correct tool function."""
        mock_result = {"close": 70000, "ma20": 68000, "rsi14": 55.0}

        with patch("trading.tools.executor._get_dispatch_table") as mock_dispatch:
            mock_fn = MagicMock(return_value=mock_result)
            mock_dispatch.return_value = {"get_ticker_technicals": mock_fn}
            # Patch logging to avoid DB write
            with patch("trading.tools.executor._log_tool_call"):
                result = execute_tool("get_ticker_technicals", {"ticker": "005930", "lookback_days": 150})

        mock_fn.assert_called_once_with(ticker="005930", lookback_days=150)
        assert result == mock_result

    def test_unknown_tool_returns_error(self):
        """Unknown tool name returns structured error dict."""
        with patch("trading.tools.executor._log_tool_call"):
            result = execute_tool("nonexistent_tool", {"param": "value"})

        assert result["error"] == "unknown_tool"
        assert result["tool"] == "nonexistent_tool"

    def test_timeout_returns_structured_error(self):
        """Tool exceeding timeout returns {"error": "timeout", "tool": "<name>"}."""
        def slow_fn(**kwargs):
            time.sleep(TOOL_TIMEOUT_SECONDS + 1)
            return {}

        with patch("trading.tools.executor._get_dispatch_table") as mock_dispatch:
            mock_dispatch.return_value = {"slow_tool": slow_fn}
            with patch("trading.tools.executor._log_tool_call"):
                result = execute_tool("slow_tool", {})

        assert result["error"] == "timeout"
        assert result["tool"] == "slow_tool"

    def test_exception_returns_structured_error(self):
        """Tool raising exception returns {"error": "<type>", "message": "..."}."""
        def broken_fn(**kwargs):
            raise ConnectionError("DB connection refused")

        with patch("trading.tools.executor._get_dispatch_table") as mock_dispatch:
            mock_dispatch.return_value = {"broken_tool": broken_fn}
            with patch("trading.tools.executor._log_tool_call"):
                result = execute_tool("broken_tool", {})

        assert result["error"] == "ConnectionError"
        assert result["tool"] == "broken_tool"
        assert "DB connection refused" in result["message"]

    def test_logs_successful_call(self):
        """Successful tool call is logged with success=True."""
        mock_result = {"data": "value"}

        with patch("trading.tools.executor._get_dispatch_table") as mock_dispatch:
            mock_fn = MagicMock(return_value=mock_result)
            mock_dispatch.return_value = {"test_tool": mock_fn}
            with patch("trading.tools.executor._log_tool_call") as mock_log:
                execute_tool("test_tool", {"key": "val"}, persona_run_id=42)

        mock_log.assert_called_once()
        call_kwargs = mock_log.call_args
        assert call_kwargs[1]["success"] is True or call_kwargs[0][4] is True

    def test_logs_failed_call(self):
        """Failed tool call is logged with success=False."""
        def broken_fn(**kwargs):
            raise ValueError("bad input")

        with patch("trading.tools.executor._get_dispatch_table") as mock_dispatch:
            mock_dispatch.return_value = {"fail_tool": broken_fn}
            with patch("trading.tools.executor._log_tool_call") as mock_log:
                execute_tool("fail_tool", {}, persona_run_id=99)

        mock_log.assert_called_once()
        args = mock_log.call_args[0]
        kwargs = mock_log.call_args[1]
        # persona_run_id, tool_name, input_hash, execution_ms (positional)
        assert args[0] == 99  # persona_run_id
        assert args[1] == "fail_tool"  # tool_name
        assert kwargs.get("success") is False or (len(args) > 4 and args[4] is False)

    def test_persona_run_id_passed_to_log(self):
        """persona_run_id is correctly forwarded to logging."""
        mock_result = {"ok": True}

        with patch("trading.tools.executor._get_dispatch_table") as mock_dispatch:
            mock_dispatch.return_value = {"my_tool": MagicMock(return_value=mock_result)}
            with patch("trading.tools.executor._log_tool_call") as mock_log:
                execute_tool("my_tool", {}, persona_run_id=123)

        args = mock_log.call_args[0]
        assert args[0] == 123


class TestHashInput:
    """Verify input hashing for privacy."""

    def test_hash_is_deterministic(self):
        from trading.tools.executor import _hash_input
        h1 = _hash_input({"ticker": "005930", "days": 5})
        h2 = _hash_input({"ticker": "005930", "days": 5})
        assert h1 == h2

    def test_different_inputs_different_hash(self):
        from trading.tools.executor import _hash_input
        h1 = _hash_input({"ticker": "005930"})
        h2 = _hash_input({"ticker": "000660"})
        assert h1 != h2

    def test_hash_is_truncated(self):
        from trading.tools.executor import _hash_input
        h = _hash_input({"key": "value"})
        assert len(h) == 16  # SHA-256 truncated to 16 chars
