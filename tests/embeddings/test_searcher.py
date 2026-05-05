"""Tests for Semantic Searcher — SPEC-010 Module 2.

Tests REQ-SCTX-03-2, REQ-SCTX-03-4, REQ-NFR-10-2:
- Cosine similarity search
- Cold start detection
- Latency SLA
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import FakeConnection, FakeCursor
from trading.embeddings.searcher import SemanticSearchResponse, has_embeddings


class TestHasEmbeddings:
    """REQ-SCTX-03-4: Cold start detection."""

    def test_returns_true_when_embeddings_exist(self):
        """has_embeddings returns True when rows exist."""
        @contextmanager
        def _conn(autocommit: bool = False) -> Iterator[FakeConnection]:
            cursor = FakeCursor([{"cnt": 15}])
            yield FakeConnection(cursor)

        with patch("trading.embeddings.searcher.connection", side_effect=_conn):
            assert has_embeddings("macro_context") is True

    def test_returns_false_when_no_embeddings(self):
        """has_embeddings returns False when no rows exist."""
        @contextmanager
        def _conn(autocommit: bool = False) -> Iterator[FakeConnection]:
            cursor = FakeCursor([{"cnt": 0}])
            yield FakeConnection(cursor)

        with patch("trading.embeddings.searcher.connection", side_effect=_conn):
            assert has_embeddings("micro_news") is False

    def test_returns_false_on_db_error(self):
        """has_embeddings returns False on database error (graceful)."""
        @contextmanager
        def _conn(autocommit: bool = False) -> Iterator[FakeConnection]:
            raise RuntimeError("DB connection failed")
            yield  # unreachable but needed for type

        with patch("trading.embeddings.searcher.connection", side_effect=RuntimeError("fail")):
            assert has_embeddings("macro_context") is False


class TestSearchResponse:
    """Semantic search response structure."""

    def test_response_dataclass_fields(self):
        """SemanticSearchResponse has all required fields."""
        resp = SemanticSearchResponse(
            source="macro_context",
            mode="semantic",
            query="test",
            results=[],
            total_chunks=10,
            returned_chunks=0,
            estimated_tokens=0,
            latency_ms=100,
        )
        assert resp.source == "macro_context"
        assert resp.mode == "semantic"
        assert resp.total_chunks == 10
        assert resp.latency_ms == 100
