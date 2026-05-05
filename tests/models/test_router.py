"""Tests for Model Router — SPEC-010 Module 1.

Tests REQ-ROUTER-01-1 through REQ-ROUTER-01-7:
- Basic model resolution per persona
- Haiku eligible + enabled -> Haiku
- Haiku eligible + disabled -> Sonnet fallback
- Non-eligible personas always use configured model
- Decision/Risk NEVER route to Haiku
- Cache behavior
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator
from unittest.mock import patch

import pytest

from tests.conftest import FakeConnection, FakeCursor


@pytest.fixture(autouse=True)
def _clear_router_cache():
    """Clear model router cache before each test."""
    from trading.models.router import invalidate_cache
    invalidate_cache()
    yield
    invalidate_cache()


def _mock_db_routing(routing: dict[str, Any]):
    """Create a DB mock returning the given model_routing dict."""
    @contextmanager
    def _conn(autocommit: bool = False) -> Iterator[FakeConnection]:
        cursor = FakeCursor([{"model_routing": routing}])
        conn = FakeConnection(cursor)
        yield conn
    return patch("trading.models.router.connection", side_effect=_conn)


class TestResolveModel:
    """REQ-ROUTER-01-4: Model resolution logic."""

    def test_haiku_eligible_and_enabled_returns_haiku(self):
        """M1-1: haiku_eligible=true AND haiku_enabled=true -> configured model."""
        routing = {
            "micro": {"model": "claude-haiku-4-5", "haiku_eligible": True, "haiku_enabled": True},
        }
        with _mock_db_routing(routing):
            from trading.models.router import resolve_model
            assert resolve_model("micro") == "claude-haiku-4-5"

    def test_haiku_eligible_but_disabled_returns_sonnet_fallback(self):
        """M1-2: haiku_eligible=true AND haiku_enabled=false -> Sonnet fallback."""
        routing = {
            "micro": {"model": "claude-haiku-4-5", "haiku_eligible": True, "haiku_enabled": False},
        }
        with _mock_db_routing(routing):
            from trading.models.router import resolve_model
            assert resolve_model("micro") == "claude-sonnet-4-6"

    def test_non_eligible_returns_configured_model(self):
        """Non-eligible persona uses configured model (Sonnet or Opus)."""
        routing = {
            "decision": {"model": "claude-sonnet-4-6", "haiku_eligible": False},
            "macro": {"model": "claude-opus-4-7", "haiku_eligible": False},
        }
        with _mock_db_routing(routing):
            from trading.models.router import resolve_model
            assert resolve_model("decision") == "claude-sonnet-4-6"
            assert resolve_model("macro") == "claude-opus-4-7"

    def test_unknown_persona_returns_fallback(self):
        """Unknown persona name defaults to Sonnet."""
        routing = {}
        with _mock_db_routing(routing):
            from trading.models.router import resolve_model
            assert resolve_model("unknown_persona") == "claude-sonnet-4-6"

    def test_daily_report_haiku_routing(self):
        """Daily report persona routes to Haiku when enabled."""
        routing = {
            "daily_report": {"model": "claude-haiku-4-5", "haiku_eligible": True, "haiku_enabled": True},
        }
        with _mock_db_routing(routing):
            from trading.models.router import resolve_model
            assert resolve_model("daily_report") == "claude-haiku-4-5"

    def test_macro_news_haiku_routing(self):
        """Macro news persona routes to Haiku when enabled."""
        routing = {
            "macro_news": {"model": "claude-haiku-4-5", "haiku_eligible": True, "haiku_enabled": True},
        }
        with _mock_db_routing(routing):
            from trading.models.router import resolve_model
            assert resolve_model("macro_news") == "claude-haiku-4-5"


class TestUpdateModelRouting:
    """REQ-ROUTER-01-5, REQ-ROUTER-01-7: Model routing updates."""

    def test_block_haiku_for_decision(self):
        """M1-3: Reject Haiku enabling for Decision persona."""
        routing = {
            "decision": {"model": "claude-sonnet-4-6", "haiku_eligible": False},
        }
        with _mock_db_routing(routing):
            from trading.models.router import ModelRoutingError, update_model_routing
            with pytest.raises(ModelRoutingError, match="Decision/Risk personas require Sonnet"):
                update_model_routing("decision", haiku_enabled=True)

    def test_block_haiku_for_risk(self):
        """M1-3: Reject Haiku enabling for Risk persona."""
        routing = {
            "risk": {"model": "claude-sonnet-4-6", "haiku_eligible": False},
        }
        with _mock_db_routing(routing):
            from trading.models.router import ModelRoutingError, update_model_routing
            with pytest.raises(ModelRoutingError, match="Decision/Risk personas require Sonnet"):
                update_model_routing("risk", haiku_enabled=True)

    def test_toggle_haiku_for_eligible_persona(self):
        """M1-4: Toggle haiku_enabled for eligible persona."""
        routing = {
            "micro": {"model": "claude-haiku-4-5", "haiku_eligible": True, "haiku_enabled": True},
        }

        @contextmanager
        def _conn(autocommit: bool = False) -> Iterator[FakeConnection]:
            cursor = FakeCursor([{"model_routing": routing}])
            conn = FakeConnection(cursor)
            yield conn

        with patch("trading.models.router.connection", side_effect=_conn):
            from trading.models.router import update_model_routing
            result = update_model_routing("micro", haiku_enabled=False)
            assert result["haiku_enabled"] is False

    def test_unknown_persona_raises(self):
        """Unknown persona raises ModelRoutingError."""
        routing = {"micro": {"model": "claude-haiku-4-5", "haiku_eligible": True}}
        with _mock_db_routing(routing):
            from trading.models.router import ModelRoutingError, update_model_routing
            with pytest.raises(ModelRoutingError, match="Unknown persona"):
                update_model_routing("nonexistent", haiku_enabled=True)


class TestCaching:
    """Model router caching behavior."""

    def test_cache_avoids_repeated_db_calls(self):
        """Cache prevents DB calls within TTL."""
        routing = {
            "micro": {"model": "claude-haiku-4-5", "haiku_eligible": True, "haiku_enabled": True},
        }
        call_count = 0

        @contextmanager
        def _conn(autocommit: bool = False) -> Iterator[FakeConnection]:
            nonlocal call_count
            call_count += 1
            cursor = FakeCursor([{"model_routing": routing}])
            conn = FakeConnection(cursor)
            yield conn

        with patch("trading.models.router.connection", side_effect=_conn):
            from trading.models.router import resolve_model
            # First call hits DB
            resolve_model("micro")
            assert call_count == 1
            # Second call uses cache
            resolve_model("micro")
            assert call_count == 1

    def test_invalidate_cache_forces_reload(self):
        """invalidate_cache() forces next call to hit DB."""
        routing = {
            "micro": {"model": "claude-haiku-4-5", "haiku_eligible": True, "haiku_enabled": True},
        }
        call_count = 0

        @contextmanager
        def _conn(autocommit: bool = False) -> Iterator[FakeConnection]:
            nonlocal call_count
            call_count += 1
            cursor = FakeCursor([{"model_routing": routing}])
            conn = FakeConnection(cursor)
            yield conn

        with patch("trading.models.router.connection", side_effect=_conn):
            from trading.models.router import invalidate_cache, resolve_model
            resolve_model("micro")
            assert call_count == 1
            invalidate_cache()
            resolve_model("micro")
            assert call_count == 2
