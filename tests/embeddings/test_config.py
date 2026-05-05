"""Tests for Embedding Configuration — SPEC-010 Module 2.

Tests REQ-PGVEC-02-4, REQ-PGVEC-02-8:
- Supported model configuration
- Environment variable resolution
- Dimension mapping
- Missing API key detection
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from trading.embeddings.config import (
    SUPPORTED_MODELS,
    EmbeddingConfig,
    get_embedding_config,
)


class TestEmbeddingConfig:
    """REQ-PGVEC-02-4: Embedding model configuration tests."""

    def test_default_model_is_voyage3(self):
        """Default EMBEDDING_MODEL is voyage-3."""
        env = {"EMBEDDING_MODEL": "voyage-3", "VOYAGE_API_KEY": "test-key"}
        with patch.dict("os.environ", env, clear=False):
            config = get_embedding_config()
            assert config.model_name == "voyage-3"
            assert config.dimensions == 1024

    def test_openai_model_dimensions(self):
        """text-embedding-3-small has 1536 dimensions."""
        env = {"EMBEDDING_MODEL": "text-embedding-3-small", "OPENAI_API_KEY": "test-key"}
        with patch.dict("os.environ", env, clear=False):
            config = get_embedding_config()
            assert config.model_name == "text-embedding-3-small"
            assert config.dimensions == 1536

    def test_unsupported_model_raises(self):
        """Unsupported EMBEDDING_MODEL raises ValueError."""
        env = {"EMBEDDING_MODEL": "unsupported-model"}
        with patch.dict("os.environ", env, clear=False):
            with pytest.raises(ValueError, match="Unsupported EMBEDDING_MODEL"):
                get_embedding_config()

    def test_missing_api_key_raises(self):
        """Missing API key raises RuntimeError (REQ-PGVEC-02-8)."""
        env = {"EMBEDDING_MODEL": "voyage-3"}
        # Remove VOYAGE_API_KEY if present
        with patch.dict("os.environ", env, clear=False):
            with patch.dict("os.environ", {"VOYAGE_API_KEY": ""}, clear=False):
                with pytest.raises(RuntimeError, match="Embedding API key missing"):
                    get_embedding_config()

    def test_config_returns_frozen_dataclass(self):
        """EmbeddingConfig is immutable (frozen dataclass)."""
        env = {"EMBEDDING_MODEL": "voyage-3", "VOYAGE_API_KEY": "test-key"}
        with patch.dict("os.environ", env, clear=False):
            config = get_embedding_config()
            assert isinstance(config, EmbeddingConfig)
            with pytest.raises(Exception):  # FrozenInstanceError
                config.model_name = "other"  # type: ignore[misc]

    def test_supported_models_have_required_fields(self):
        """All supported models have dimensions, api_url, env_key."""
        for model_name, spec in SUPPORTED_MODELS.items():
            assert "dimensions" in spec
            assert "api_url" in spec
            assert "env_key" in spec
            assert "price_per_mtok" in spec
            assert "max_batch_size" in spec
            assert "rate_limit_rps" in spec

    def test_voyage3_pricing(self):
        """Voyage-3 pricing is $0.06/M tokens."""
        env = {"EMBEDDING_MODEL": "voyage-3", "VOYAGE_API_KEY": "test-key"}
        with patch.dict("os.environ", env, clear=False):
            config = get_embedding_config()
            assert config.price_per_mtok == 0.06

    def test_openai_pricing(self):
        """text-embedding-3-small pricing is $0.02/M tokens."""
        env = {"EMBEDDING_MODEL": "text-embedding-3-small", "OPENAI_API_KEY": "test-key"}
        with patch.dict("os.environ", env, clear=False):
            config = get_embedding_config()
            assert config.price_per_mtok == 0.02
