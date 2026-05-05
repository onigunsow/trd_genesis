"""Tests for Embedding Generator — SPEC-010 Module 2.

Tests REQ-PGVEC-02-7:
- Batch embedding generation
- Rate limiting and backoff
- Error handling
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trading.embeddings.config import EmbeddingConfig
from trading.embeddings.embedder import EmbeddingError, embed_query, embed_texts


@pytest.fixture()
def mock_config() -> EmbeddingConfig:
    """Test embedding config."""
    return EmbeddingConfig(
        model_name="voyage-3",
        dimensions=1024,
        api_url="https://api.voyageai.com/v1/embeddings",
        api_key="test-key",
        price_per_mtok=0.06,
        max_batch_size=3,  # Small batch for testing
        rate_limit_rps=100,
    )


class TestEmbedTexts:
    """REQ-PGVEC-02-7: Embedding generation tests."""

    def test_empty_input_returns_empty(self, mock_config: EmbeddingConfig):
        """Empty text list returns empty embedding list."""
        result = embed_texts([], config=mock_config)
        assert result == []

    def test_single_text_embedding(self, mock_config: EmbeddingConfig):
        """Single text produces one embedding vector."""
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "data": [{"embedding": [0.1] * 1024}],
        }

        with patch("trading.embeddings.embedder.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = fake_response
            mock_client_cls.return_value = mock_client

            result = embed_texts(["test text"], config=mock_config)
            assert len(result) == 1
            assert len(result[0]) == 1024

    def test_batch_splitting(self, mock_config: EmbeddingConfig):
        """Texts exceeding batch_size are split into multiple API calls."""
        fake_response = MagicMock()
        fake_response.status_code = 200
        # Return 3 embeddings per call (matching max_batch_size=3)
        fake_response.json.return_value = {
            "data": [{"embedding": [0.1] * 1024} for _ in range(3)],
        }

        with patch("trading.embeddings.embedder.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = fake_response
            mock_client_cls.return_value = mock_client

            # 5 texts with batch_size=3 should make 2 API calls
            texts = [f"text {i}" for i in range(5)]

            # Need different responses for different batch sizes
            def side_effect(*args, **kwargs):
                payload = kwargs.get("json", {})
                n = len(payload.get("input", []))
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {
                    "data": [{"embedding": [0.1] * 1024} for _ in range(n)],
                }
                return resp

            mock_client.post.side_effect = side_effect

            result = embed_texts(texts, config=mock_config)
            assert len(result) == 5
            # Should have made 2 calls (3 + 2)
            assert mock_client.post.call_count == 2

    def test_api_error_raises_embedding_error(self, mock_config: EmbeddingConfig):
        """Non-200 response raises EmbeddingError."""
        fake_response = MagicMock()
        fake_response.status_code = 500
        fake_response.text = "Internal Server Error"

        with patch("trading.embeddings.embedder.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = fake_response
            mock_client_cls.return_value = mock_client

            with pytest.raises(EmbeddingError, match="Embedding API returned 500"):
                embed_texts(["test"], config=mock_config)


class TestEmbedQuery:
    """Query embedding tests."""

    def test_query_embedding_returns_vector(self, mock_config: EmbeddingConfig):
        """embed_query returns a single embedding vector."""
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "data": [{"embedding": [0.5] * 1024}],
        }

        with patch("trading.embeddings.embedder.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = fake_response
            mock_client_cls.return_value = mock_client

            result = embed_query("What is the Fed rate?", config=mock_config)
            assert len(result) == 1024
            assert result[0] == 0.5

    def test_query_uses_query_input_type_for_voyage(self, mock_config: EmbeddingConfig):
        """Voyage AI queries use input_type='query'."""
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "data": [{"embedding": [0.1] * 1024}],
        }

        with patch("trading.embeddings.embedder.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = fake_response
            mock_client_cls.return_value = mock_client

            embed_query("test query", config=mock_config)

            # Check the payload has input_type="query"
            call_kwargs = mock_client.post.call_args
            payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
            assert payload["input_type"] == "query"
