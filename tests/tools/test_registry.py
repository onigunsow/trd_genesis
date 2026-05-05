"""Tests for tools/registry.py — tool schema definitions."""

from __future__ import annotations

import pytest

from trading.tools.registry import (
    PERSONA_TOOLS,
    TOOL_DEFINITIONS,
    get_all_tool_definitions,
    get_tools_for_persona,
)


class TestGetAllToolDefinitions:
    """Verify registry returns all 10 tools in correct format."""

    def test_returns_10_tools(self):
        tools = get_all_tool_definitions()
        assert len(tools) == 10

    def test_each_tool_has_required_fields(self):
        tools = get_all_tool_definitions()
        for tool in tools:
            assert "name" in tool, f"Tool missing 'name': {tool}"
            assert "description" in tool, f"Tool missing 'description': {tool.get('name')}"
            assert "input_schema" in tool, f"Tool missing 'input_schema': {tool.get('name')}"

    def test_input_schema_is_valid_json_schema(self):
        """Each input_schema must be a valid JSON Schema (type: object)."""
        tools = get_all_tool_definitions()
        for tool in tools:
            schema = tool["input_schema"]
            assert schema["type"] == "object", f"{tool['name']}: schema type must be 'object'"
            assert "properties" in schema, f"{tool['name']}: schema must have 'properties'"
            assert "required" in schema, f"{tool['name']}: schema must have 'required'"

    def test_descriptions_are_korean_and_short(self):
        """Descriptions should be Korean, under 50 characters."""
        tools = get_all_tool_definitions()
        for tool in tools:
            desc = tool["description"]
            assert len(desc) <= 50, f"{tool['name']}: description too long ({len(desc)} chars)"

    def test_each_tool_has_cache_control(self):
        """REQ-TOOL-01-6: Each tool includes cache_control for SPEC-008."""
        tools = get_all_tool_definitions()
        for tool in tools:
            assert "cache_control" in tool, f"{tool['name']}: missing cache_control"
            assert tool["cache_control"] == {"type": "ephemeral"}

    def test_tool_names_are_unique(self):
        tools = get_all_tool_definitions()
        names = [t["name"] for t in tools]
        assert len(names) == len(set(names)), "Duplicate tool names found"

    def test_returns_copy_not_reference(self):
        """Modifications to returned list should not affect internal state."""
        tools1 = get_all_tool_definitions()
        tools1.pop()
        tools2 = get_all_tool_definitions()
        assert len(tools2) == 10


class TestGetToolsForPersona:
    """Verify per-persona tool assignments."""

    def test_macro_persona_tools(self):
        """REQ-PTOOL-02-3: Macro gets indicators, global assets, static context, memory."""
        tools = get_tools_for_persona("macro")
        names = {t["name"] for t in tools}
        assert names == {
            "get_macro_indicators",
            "get_global_assets",
            "get_static_context",
            "get_active_memory",
        }

    def test_micro_persona_tools(self):
        """REQ-PTOOL-02-4: Micro gets technicals, fundamentals, flows, disclosures, context, memory, watchlist."""
        tools = get_tools_for_persona("micro")
        names = {t["name"] for t in tools}
        assert names == {
            "get_ticker_technicals",
            "get_ticker_fundamentals",
            "get_ticker_flows",
            "get_recent_disclosures",
            "get_static_context",
            "get_active_memory",
            "get_watchlist",
        }

    def test_decision_persona_tools(self):
        """REQ-PTOOL-02-5: Decision gets portfolio, technicals, fundamentals, context, memory."""
        tools = get_tools_for_persona("decision")
        names = {t["name"] for t in tools}
        assert names == {
            "get_portfolio_status",
            "get_ticker_technicals",
            "get_ticker_fundamentals",
            "get_static_context",
            "get_active_memory",
        }

    def test_risk_persona_tools(self):
        """REQ-PTOOL-02-6: Risk gets portfolio, technicals, flows."""
        tools = get_tools_for_persona("risk")
        names = {t["name"] for t in tools}
        assert names == {
            "get_portfolio_status",
            "get_ticker_technicals",
            "get_ticker_flows",
        }

    def test_unknown_persona_returns_empty(self):
        tools = get_tools_for_persona("nonexistent")
        assert tools == []

    def test_persona_tools_are_subset_of_all(self):
        """All persona-specific tools must exist in the global registry."""
        all_names = {t["name"] for t in get_all_tool_definitions()}
        for persona, tool_names in PERSONA_TOOLS.items():
            for name in tool_names:
                assert name in all_names, f"Persona '{persona}' references unknown tool '{name}'"
