"""Tests for tools/context_tools.py — static context and memory tools."""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestGetStaticContext:
    """Verify static .md file loading."""

    def test_valid_name_returns_content(self):
        with patch("trading.tools.context_tools._read_md", return_value="# Macro Context\nBullish outlook"):
            from trading.tools.context_tools import get_static_context
            result = get_static_context(name="macro_context")

        assert result["name"] == "macro_context"
        assert "Macro Context" in result["content"]

    def test_all_valid_names(self):
        """All four valid names are accepted."""
        valid = ["macro_context", "micro_context", "macro_news", "micro_news"]
        for name in valid:
            with patch("trading.tools.context_tools._read_md", return_value=f"Content for {name}"):
                from trading.tools.context_tools import get_static_context
                result = get_static_context(name=name)
            assert result["name"] == name
            assert "content" in result

    def test_invalid_name_returns_error(self):
        from trading.tools.context_tools import get_static_context
        result = get_static_context(name="invalid_file")
        assert result["error"] == "invalid_name"
        assert "valid_names" in result


class TestGetActiveMemory:
    """Verify dynamic memory table querying."""

    def test_valid_table_returns_rows(self):
        mock_rows = [
            {"id": 1, "scope": "macro", "scope_id": None, "kind": "insight",
             "summary": "Fed likely to cut rates", "importance": 5,
             "valid_until": None, "updated_at": "2026-05-04"},
        ]
        with patch("trading.tools.context_tools._load_memory", return_value=mock_rows):
            with patch("trading.tools.context_tools._format_memory", return_value="- [#1 macro] Fed cut"):
                from trading.tools.context_tools import get_active_memory
                result = get_active_memory(table="macro_memory", limit=10)

        assert result["table"] == "macro_memory"
        assert result["count"] == 1
        assert result["rows"][0]["summary"] == "Fed likely to cut rates"
        assert "formatted" in result

    def test_invalid_table_returns_error(self):
        from trading.tools.context_tools import get_active_memory
        result = get_active_memory(table="hacker_table")
        assert result["error"] == "invalid_table"
        assert "valid_tables" in result

    def test_scope_filter_passed_through(self):
        with patch("trading.tools.context_tools._load_memory") as mock_load:
            mock_load.return_value = []
            with patch("trading.tools.context_tools._format_memory", return_value=""):
                from trading.tools.context_tools import get_active_memory
                get_active_memory(table="micro_memory", limit=5, scope_filter=["005930", "000660"])

        mock_load.assert_called_once_with("micro_memory", limit=5, scope_filter=["005930", "000660"])

    def test_empty_memory_returns_zero_count(self):
        with patch("trading.tools.context_tools._load_memory", return_value=[]):
            with patch("trading.tools.context_tools._format_memory", return_value="_(활성 메모리 없음)_"):
                from trading.tools.context_tools import get_active_memory
                result = get_active_memory(table="macro_memory")

        assert result["count"] == 0
        assert result["rows"] == []
