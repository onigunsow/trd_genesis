"""Embedding model configuration — SPEC-010 REQ-PGVEC-02-4.

Supports:
- voyage-3 (Anthropic/Voyage AI, 1024 dimensions, $0.06/M tokens)
- text-embedding-3-small (OpenAI, 1536 dimensions, $0.02/M tokens)

Configuration via environment variables:
- EMBEDDING_MODEL: Model identifier (default: voyage-3)
- VOYAGE_API_KEY: API key for Voyage AI
- OPENAI_API_KEY: API key for OpenAI embeddings (separate from main key)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

# REQ-PGVEC-02-4: Supported embedding models
SUPPORTED_MODELS: Final[dict[str, dict[str, int | str | float]]] = {
    "voyage-3": {
        "dimensions": 1024,
        "api_url": "https://api.voyageai.com/v1/embeddings",
        "env_key": "VOYAGE_API_KEY",
        "price_per_mtok": 0.06,
        "max_batch_size": 50,
        "rate_limit_rps": 100,
    },
    "text-embedding-3-small": {
        "dimensions": 1536,
        "api_url": "https://api.openai.com/v1/embeddings",
        "env_key": "OPENAI_API_KEY",
        "price_per_mtok": 0.02,
        "max_batch_size": 50,
        "rate_limit_rps": 500,
    },
}

# Chunking configuration (REQ-PGVEC-02-6)
CHUNK_MIN_TOKENS: Final[int] = 200
CHUNK_TARGET_TOKENS: Final[int] = 400
CHUNK_MAX_TOKENS: Final[int] = 500
CHUNK_OVERLAP_TOKENS: Final[int] = 50

# Token estimation heuristic: 4 chars per token (consistent with base.py)
CHARS_PER_TOKEN: Final[int] = 4


@dataclass(frozen=True)
class EmbeddingConfig:
    """Resolved embedding model configuration."""

    model_name: str
    dimensions: int
    api_url: str
    api_key: str
    price_per_mtok: float
    max_batch_size: int
    rate_limit_rps: int


def get_embedding_config() -> EmbeddingConfig:
    """Resolve embedding configuration from environment.

    Returns:
        EmbeddingConfig with all necessary fields.

    Raises:
        ValueError: If EMBEDDING_MODEL is unsupported.
        RuntimeError: If required API key is missing.
    """
    model_name = os.environ.get("EMBEDDING_MODEL", "voyage-3")

    if model_name not in SUPPORTED_MODELS:
        raise ValueError(
            f"Unsupported EMBEDDING_MODEL='{model_name}'. "
            f"Supported: {list(SUPPORTED_MODELS.keys())}"
        )

    spec = SUPPORTED_MODELS[model_name]
    env_key = str(spec["env_key"])
    api_key = os.environ.get(env_key, "")

    if not api_key:
        raise RuntimeError(
            f"Embedding API key missing: set {env_key} environment variable. "
            f"REQ-PGVEC-02-8: API keys must be in env vars only."
        )

    return EmbeddingConfig(
        model_name=model_name,
        dimensions=int(spec["dimensions"]),
        api_url=str(spec["api_url"]),
        api_key=api_key,
        price_per_mtok=float(spec["price_per_mtok"]),
        max_batch_size=int(spec["max_batch_size"]),
        rate_limit_rps=int(spec["rate_limit_rps"]),
    )


def estimate_tokens(text: str) -> int:
    """Estimate token count using 4-chars-per-token heuristic."""
    return max(1, len(text) // CHARS_PER_TOKEN)
