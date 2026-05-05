"""Shared pytest fixtures for the trading test suite.

Provides mock DB connections, Anthropic API mocks, and settings overrides
so that tests run without live external dependencies.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# DB mock fixtures
# ---------------------------------------------------------------------------

class FakeCursor:
    """Minimal cursor mock supporting execute/fetchone/fetchall."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []
        self._idx = 0
        self.last_sql: str = ""
        self.last_params: tuple[Any, ...] = ()

    def execute(self, sql: str, params: Any = None) -> None:
        self.last_sql = sql
        self.last_params = params or ()

    def fetchone(self) -> dict[str, Any] | None:
        if self._rows:
            return self._rows[0]
        return None

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class FakeConnection:
    """Minimal connection mock with cursor support."""

    def __init__(self, cursor: FakeCursor | None = None) -> None:
        self._cursor = cursor or FakeCursor()

    def cursor(self) -> FakeCursor:
        return self._cursor

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


@pytest.fixture()
def fake_cursor() -> FakeCursor:
    return FakeCursor()


@pytest.fixture()
def fake_conn(fake_cursor: FakeCursor) -> FakeConnection:
    return FakeConnection(fake_cursor)


@contextmanager
def mock_connection_factory(
    rows: list[dict[str, Any]] | None = None,
) -> Iterator[FakeConnection]:
    """Create a context-managed fake connection returning preset rows."""
    cursor = FakeCursor(rows or [])
    conn = FakeConnection(cursor)
    yield conn


@pytest.fixture()
def patch_db_connection():
    """Patch trading.db.session.connection to use FakeConnection."""
    def _factory(rows: list[dict[str, Any]] | None = None):
        @contextmanager
        def _conn(autocommit: bool = False):
            cursor = FakeCursor(rows or [])
            conn = FakeConnection(cursor)
            yield conn
        return patch("trading.db.session.connection", side_effect=_conn)
    return _factory


# ---------------------------------------------------------------------------
# Anthropic API mock fixtures
# ---------------------------------------------------------------------------

class FakeAnthropicMessage:
    """Mimics anthropic.types.Message for test purposes."""

    def __init__(
        self,
        text: str = '{"signals": []}',
        input_tokens: int = 1000,
        output_tokens: int = 500,
        stop_reason: str = "end_turn",
    ) -> None:
        self.content = [MagicMock(type="text", text=text)]
        self.usage = MagicMock(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        self.stop_reason = stop_reason


@pytest.fixture()
def mock_anthropic():
    """Patch Anthropic client for persona tests."""
    def _factory(response_text: str = '{"signals": []}', **kwargs):
        msg = FakeAnthropicMessage(text=response_text, **kwargs)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = msg
        return patch("trading.personas.base.Anthropic", return_value=mock_client)
    return _factory


# ---------------------------------------------------------------------------
# Settings override fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_settings():
    """Provide a fake settings object with required attributes."""
    settings = MagicMock()
    settings.trading_mode = "paper"
    settings.anthropic.api_key.get_secret_value.return_value = "test-key"
    settings.telegram.bot_token.get_secret_value.return_value = "test-token"
    settings.telegram.chat_id = "60443392"
    return settings
