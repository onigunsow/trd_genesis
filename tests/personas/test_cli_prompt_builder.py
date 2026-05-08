"""Tests for CLI prompt builder — pre-computed tool data embedding.

SPEC-015 REQ-BUILDER-01-*, REQ-PRECOMP-05-*: Verifies tool pre-computation
and prompt assembly for single-turn CLI mode.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


class TestBuildCliPrompt:
    """Verify build_cli_prompt assembles prompts correctly."""

    @patch("trading.personas.cli_prompt_builder._get_active_tool_names")
    @patch("trading.personas.cli_prompt_builder._pre_compute_tools")
    def test_builds_prompt_with_tool_data(self, mock_precompute, mock_tool_names):
        """Prompt includes system prompt, tool data section, user message, and JSON instructions."""
        mock_tool_names.return_value = ["get_portfolio_status"]
        mock_precompute.return_value = {
            "get_portfolio_status": {"total_assets": 10000000, "cash_d2": 9500000},
        }

        from trading.personas.cli_prompt_builder import build_cli_prompt

        result = build_cli_prompt(
            persona_name="decision",
            input_data={},
            system_prompt="You are a trading decision persona.",
            user_message="Analyze and respond.",
            tickers=["005930"],
        )

        assert "You are a trading decision persona." in result
        assert "=== PRE-COMPUTED TOOL DATA ===" in result
        assert "get_portfolio_status" in result
        assert "total_assets" in result
        assert "Analyze and respond." in result
        assert "Respond with valid JSON only" in result
        assert "=== END TOOL DATA ===" in result

    @patch("trading.personas.cli_prompt_builder._get_active_tool_names")
    @patch("trading.personas.cli_prompt_builder._pre_compute_tools")
    def test_empty_tools_no_tool_section(self, mock_precompute, mock_tool_names):
        """When no tools are assigned, tool data section is empty."""
        mock_tool_names.return_value = []
        mock_precompute.return_value = {}

        from trading.personas.cli_prompt_builder import build_cli_prompt

        result = build_cli_prompt(
            persona_name="macro",
            input_data={},
            system_prompt="System prompt",
            user_message="User message",
        )

        assert "System prompt" in result
        assert "User message" in result
        # Empty tools should not produce tool data markers
        assert "=== PRE-COMPUTED TOOL DATA ===" not in result


class TestPreComputeTools:
    """Verify tool pre-computation handles different tool types."""

    @patch("trading.personas.cli_prompt_builder.execute_tool")
    def test_ticker_specific_tools_iterate_tickers(self, mock_exec):
        """Ticker-specific tools are called once per ticker."""
        mock_exec.return_value = {"rsi14": 58.3, "ma20": 71200}

        from trading.personas.cli_prompt_builder import _pre_compute_tools

        results = _pre_compute_tools(
            "micro",
            ["get_ticker_technicals"],
            tickers=["005930", "000660"],
        )

        assert "get_ticker_technicals: 005930" in results
        assert "get_ticker_technicals: 000660" in results
        assert mock_exec.call_count == 2

    @patch("trading.personas.cli_prompt_builder.execute_tool")
    def test_non_ticker_tools_called_once(self, mock_exec):
        """Non-ticker tools are called once with default params."""
        mock_exec.return_value = {"total_assets": 10000000}

        from trading.personas.cli_prompt_builder import _pre_compute_tools

        results = _pre_compute_tools(
            "decision",
            ["get_portfolio_status"],
            tickers=["005930"],
        )

        assert "get_portfolio_status" in results
        assert mock_exec.call_count == 1

    @patch("trading.personas.cli_prompt_builder.execute_tool")
    def test_failed_tool_marked_unavailable(self, mock_exec):
        """REQ-BUILDER-01-3: Failed tools get (unavailable) marker."""
        mock_exec.side_effect = Exception("DB connection failed")

        from trading.personas.cli_prompt_builder import _pre_compute_tools

        results = _pre_compute_tools(
            "macro",
            ["get_macro_indicators"],
        )

        assert "get_macro_indicators" in results
        assert results["get_macro_indicators"]["status"] == "(unavailable)"


class TestFormatToolSection:
    """Verify tool data formatting follows S-3 schema."""

    def test_format_successful_result(self):
        from trading.personas.cli_prompt_builder import _format_tool_section

        results = {
            "get_portfolio_status": {"total_assets": 10000000},
        }
        section = _format_tool_section(results)

        assert "=== PRE-COMPUTED TOOL DATA ===" in section
        assert "[get_portfolio_status]" in section
        assert "10000000" in section
        assert "=== END TOOL DATA ===" in section

    def test_format_unavailable_tool(self):
        from trading.personas.cli_prompt_builder import _format_tool_section

        results = {
            "get_static_context": {"status": "(unavailable)", "error": "timeout"},
        }
        section = _format_tool_section(results)

        assert "[get_static_context] (unavailable)" in section

    def test_format_empty_results(self):
        from trading.personas.cli_prompt_builder import _format_tool_section

        section = _format_tool_section({})
        assert section == ""


class TestResolveToolParams:
    """Verify per-persona tool parameter resolution."""

    def test_macro_static_context_uses_intelligence_macro(self):
        from trading.personas.cli_prompt_builder import _resolve_tool_params

        params = _resolve_tool_params("macro", "get_static_context")
        assert params["name"] == "intelligence_macro"

    def test_micro_static_context_uses_intelligence_micro(self):
        from trading.personas.cli_prompt_builder import _resolve_tool_params

        params = _resolve_tool_params("micro", "get_static_context")
        assert params["name"] == "intelligence_micro"

    def test_macro_memory_uses_macro_memory(self):
        from trading.personas.cli_prompt_builder import _resolve_tool_params

        params = _resolve_tool_params("macro", "get_active_memory")
        assert params["table"] == "macro_memory"

    def test_micro_memory_uses_micro_memory(self):
        from trading.personas.cli_prompt_builder import _resolve_tool_params

        params = _resolve_tool_params("micro", "get_active_memory")
        assert params["table"] == "micro_memory"
