"""Tests for call_persona tool-use loop (SPEC-009 Phase B).

Tests cover:
- Tool-use loop with stop_reason="tool_use" -> execute -> continue
- Max 8 rounds enforcement (REQ-PTOOL-02-2)
- Fallback after 3 consecutive failures (REQ-COMPAT-04-4)
- Token accounting for tool calls (REQ-PTOOL-02-7)
- Backward compatibility: no tools = existing behavior unchanged
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, call

import pytest

from tests.conftest import FakeCursor, FakeConnection


class FakeToolUseBlock:
    """Mimics an Anthropic tool_use content block."""

    def __init__(self, tool_id: str, name: str, input_data: dict):
        self.type = "tool_use"
        self.id = tool_id
        self.name = name
        self.input = input_data


class FakeTextBlock:
    """Mimics an Anthropic text content block."""

    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class FakeUsage:
    """Mimics Anthropic usage object."""

    def __init__(self, input_tokens: int = 500, output_tokens: int = 200):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0


class FakeMessage:
    """Mimics an Anthropic Message response."""

    def __init__(self, content, stop_reason="end_turn", usage=None):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = usage or FakeUsage()


def _make_patches(messages_side_effect):
    """Create all necessary patches for call_persona tests."""
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = messages_side_effect

    cursor = FakeCursor(rows=[{"id": 100}])
    conn = FakeConnection(cursor)

    patches = {
        "anthropic": patch("trading.personas.base.Anthropic", return_value=mock_client),
        "connection": patch("trading.personas.base.connection", return_value=conn),
        "settings": patch(
            "trading.personas.base.get_settings",
            return_value=MagicMock(
                anthropic=MagicMock(
                    api_key=MagicMock(get_secret_value=MagicMock(return_value="test"))
                )
            ),
        ),
        "audit": patch("trading.personas.base.audit"),
    }
    return patches, mock_client


class TestCallPersonaToolUseLoop:
    """Test tool-use multi-turn loop in call_persona."""

    def test_no_tools_existing_behavior(self):
        """Without tools parameter, call_persona works as before."""
        msg = FakeMessage(
            content=[FakeTextBlock('{"signals": []}')],
            stop_reason="end_turn",
        )
        patches, mock_client = _make_patches([msg])

        with patches["anthropic"], patches["connection"], patches["settings"], patches["audit"]:
            from trading.personas.base import call_persona

            result = call_persona(
                persona_name="decision",
                model="claude-sonnet-4-6",
                cycle_kind="pre_market",
                system_prompt="Test",
                user_message="Test",
                expect_json=True,
                apply_memory_ops=False,
            )

        assert result.persona_run_id == 100
        assert result.tool_calls_count == 0
        assert result.tool_input_tokens == 0
        assert result.tool_output_tokens == 0
        # Should NOT include tools in API call
        call_kwargs = mock_client.messages.create.call_args
        assert "tools" not in call_kwargs.kwargs

    def test_tools_single_round(self):
        """Tool-use: one tool call then end_turn."""
        # First response: tool_use
        tool_block = FakeToolUseBlock("tool_1", "get_watchlist", {})
        msg1 = FakeMessage(
            content=[tool_block],
            stop_reason="tool_use",
        )
        # Second response: final text
        msg2 = FakeMessage(
            content=[FakeTextBlock('{"candidates": {"buy": []}}')],
            stop_reason="end_turn",
        )
        patches, mock_client = _make_patches([msg1, msg2])

        tools = [{"name": "get_watchlist", "description": "test", "input_schema": {}}]

        with (
            patches["anthropic"],
            patches["connection"],
            patches["settings"],
            patches["audit"],
            patch("trading.tools.executor.execute_tool", return_value={"tickers": ["005930"]}),
        ):
            from trading.personas.base import call_persona

            result = call_persona(
                persona_name="micro",
                model="claude-sonnet-4-6",
                cycle_kind="pre_market",
                system_prompt="Test",
                user_message="Test",
                expect_json=True,
                apply_memory_ops=False,
                tools=tools,
            )

        assert result.tool_calls_count == 1
        assert result.response_json == {"candidates": {"buy": []}}
        assert mock_client.messages.create.call_count == 2

    def test_tools_multiple_rounds(self):
        """Tool-use: multiple rounds before end_turn."""
        # Round 1: tool call
        msg1 = FakeMessage(
            content=[FakeToolUseBlock("t1", "get_ticker_technicals", {"ticker": "005930"})],
            stop_reason="tool_use",
        )
        # Round 2: another tool call
        msg2 = FakeMessage(
            content=[FakeToolUseBlock("t2", "get_ticker_flows", {"ticker": "005930"})],
            stop_reason="tool_use",
        )
        # Round 3: final
        msg3 = FakeMessage(
            content=[FakeTextBlock('{"candidates": {"buy": [{"ticker": "005930"}]}}')],
            stop_reason="end_turn",
        )
        patches, mock_client = _make_patches([msg1, msg2, msg3])
        tools = [{"name": "get_ticker_technicals", "description": "t", "input_schema": {}}]

        with (
            patches["anthropic"],
            patches["connection"],
            patches["settings"],
            patches["audit"],
            patch("trading.tools.executor.execute_tool", return_value={"close": 80000}),
        ):
            from trading.personas.base import call_persona

            result = call_persona(
                persona_name="micro",
                model="claude-sonnet-4-6",
                cycle_kind="pre_market",
                system_prompt="Test",
                user_message="Test",
                expect_json=True,
                apply_memory_ops=False,
                tools=tools,
            )

        assert result.tool_calls_count == 2
        assert mock_client.messages.create.call_count == 3

    def test_max_rounds_enforced(self):
        """REQ-PTOOL-02-2: Stop after 8 tool rounds."""
        # Create 9 tool_use responses (should stop at 8)
        tool_msgs = [
            FakeMessage(
                content=[FakeToolUseBlock(f"t{i}", "get_watchlist", {})],
                stop_reason="tool_use",
            )
            for i in range(10)
        ]
        patches, mock_client = _make_patches(tool_msgs)
        tools = [{"name": "get_watchlist", "description": "t", "input_schema": {}}]

        with (
            patches["anthropic"],
            patches["connection"],
            patches["settings"],
            patches["audit"],
            patch("trading.tools.executor.execute_tool", return_value={"data": "x"}),
            patch("trading.alerts.telegram.system_briefing"),
        ):
            from trading.personas.base import call_persona

            result = call_persona(
                persona_name="micro",
                model="claude-sonnet-4-6",
                cycle_kind="pre_market",
                system_prompt="Test",
                user_message="Test",
                apply_memory_ops=False,
                tools=tools,
            )

        # Should have made 1 (initial) + 8 (rounds) = 9 API calls
        assert mock_client.messages.create.call_count == 9
        assert result.tool_calls_count == 8

    def test_fallback_on_consecutive_failures(self):
        """REQ-COMPAT-04-4: Fallback after 3 consecutive tool failures."""
        # All tool_use responses
        tool_msgs = [
            FakeMessage(
                content=[FakeToolUseBlock(f"t{i}", "get_watchlist", {})],
                stop_reason="tool_use",
            )
            for i in range(5)
        ]
        patches, mock_client = _make_patches(tool_msgs)
        tools = [{"name": "get_watchlist", "description": "t", "input_schema": {}}]

        # All tool calls fail
        with (
            patches["anthropic"],
            patches["connection"],
            patches["settings"],
            patches["audit"],
            patch(
                "trading.tools.executor.execute_tool",
                return_value={"error": "timeout", "tool": "get_watchlist"},
            ),
        ):
            from trading.personas.base import call_persona

            result = call_persona(
                persona_name="micro",
                model="claude-sonnet-4-6",
                cycle_kind="pre_market",
                system_prompt="Test",
                user_message="Test",
                apply_memory_ops=False,
                tools=tools,
            )

        # Flow: initial call (tool_use) -> round 1 tool fails -> API call 2 (tool_use)
        # -> round 2 tool fails -> API call 3 (tool_use) -> round 3 tool fails -> fallback
        # Total: 3 API calls (initial + 2 additional before fallback breaks loop)
        # Tool calls: 3 (one per round before fallback triggers)
        assert result.tool_calls_count == 3
        assert mock_client.messages.create.call_count == 3

    def test_token_accounting(self):
        """REQ-PTOOL-02-7: Tool token fields are recorded."""
        msg1 = FakeMessage(
            content=[FakeToolUseBlock("t1", "get_ticker_technicals", {"ticker": "005930", "lookback_days": 150})],
            stop_reason="tool_use",
            usage=FakeUsage(input_tokens=800, output_tokens=100),
        )
        msg2 = FakeMessage(
            content=[FakeTextBlock('{"signals": []}')],
            stop_reason="end_turn",
            usage=FakeUsage(input_tokens=1200, output_tokens=300),
        )
        patches, mock_client = _make_patches([msg1, msg2])
        tools = [{"name": "get_watchlist", "description": "t", "input_schema": {}}]

        with (
            patches["anthropic"],
            patches["connection"],
            patches["settings"],
            patches["audit"],
            patch("trading.tools.executor.execute_tool", return_value={"tickers": ["005930"]}),
        ):
            from trading.personas.base import call_persona

            result = call_persona(
                persona_name="micro",
                model="claude-sonnet-4-6",
                cycle_kind="pre_market",
                system_prompt="Test",
                user_message="Test",
                apply_memory_ops=False,
                tools=tools,
            )

        assert result.input_tokens == 800 + 1200  # Sum of both calls
        assert result.output_tokens == 100 + 300
        assert result.tool_calls_count == 1
        assert result.tool_input_tokens > 0
        assert result.tool_output_tokens > 0
