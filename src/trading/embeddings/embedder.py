"""Embedding generator — generate embeddings via configured model API.

REQ-PGVEC-02-7:
- Rate limit: 100 requests/second (Voyage AI default)
- Exponential backoff on 429 (1s, 2s, 4s, max 30s, 5 retries)
- Batch size: 50 chunks per API call
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from trading.embeddings.config import EmbeddingConfig, get_embedding_config

LOG = logging.getLogger(__name__)


class EmbeddingError(Exception):
    """Raised when embedding generation fails after retries."""


class RateLimitError(Exception):
    """Raised on 429 responses for tenacity retry logic."""


@retry(
    retry=retry_if_exception_type(RateLimitError),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)
def _call_embedding_api(
    client: httpx.Client,
    config: EmbeddingConfig,
    texts: list[str],
) -> list[list[float]]:
    """Call embedding API with retry on rate limit.

    Args:
        client: httpx client instance.
        config: Embedding configuration.
        texts: List of text strings to embed.

    Returns:
        List of embedding vectors (list of floats).
    """
    headers = {
        "Content-Type": "application/json",
    }

    # Auth header differs by provider
    if config.model_name.startswith("voyage"):
        headers["Authorization"] = f"Bearer {config.api_key}"
        payload: dict[str, Any] = {
            "model": config.model_name,
            "input": texts,
            "input_type": "document",
        }
    else:
        # OpenAI-compatible
        headers["Authorization"] = f"Bearer {config.api_key}"
        payload = {
            "model": config.model_name,
            "input": texts,
        }

    resp = client.post(config.api_url, json=payload, headers=headers, timeout=30.0)

    if resp.status_code == 429:
        LOG.warning("Embedding API rate limited (429), will retry")
        raise RateLimitError("Rate limited by embedding API")

    if resp.status_code != 200:
        raise EmbeddingError(
            f"Embedding API returned {resp.status_code}: {resp.text[:200]}"
        )

    data = resp.json()
    # Both Voyage AI and OpenAI return embeddings in data[].embedding format
    embeddings = [item["embedding"] for item in data["data"]]
    return embeddings


def embed_texts(
    texts: list[str],
    config: EmbeddingConfig | None = None,
) -> list[list[float]]:
    """Generate embeddings for a list of texts.

    Handles batching (max 50 per call) and rate limiting.

    Args:
        texts: List of text strings to embed.
        config: Optional embedding config (auto-resolved if None).

    Returns:
        List of embedding vectors matching input order.

    Raises:
        EmbeddingError: If embedding fails after retries.
    """
    if not texts:
        return []

    if config is None:
        config = get_embedding_config()

    all_embeddings: list[list[float]] = []
    batch_size = config.max_batch_size

    with httpx.Client() as client:
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]

            try:
                embeddings = _call_embedding_api(client, config, batch)
                all_embeddings.extend(embeddings)
            except RateLimitError:
                LOG.error("Embedding rate limit exceeded after 5 retries for batch %d", i)
                raise EmbeddingError(
                    f"Embedding API rate limit exceeded after retries (batch starting at index {i})"
                )
            except Exception as e:
                LOG.error("Embedding generation failed for batch %d: %s", i, e)
                raise EmbeddingError(f"Embedding generation failed: {e}") from e

            # Simple rate limiting between batches
            if i + batch_size < len(texts):
                time.sleep(1.0 / config.rate_limit_rps)

    return all_embeddings


def embed_query(
    query: str,
    config: EmbeddingConfig | None = None,
) -> list[float]:
    """Generate embedding for a single query text.

    Uses 'query' input_type for Voyage AI (optimized for retrieval queries).

    Args:
        query: Query text to embed.
        config: Optional embedding config.

    Returns:
        Embedding vector for the query.
    """
    if config is None:
        config = get_embedding_config()

    headers = {
        "Content-Type": "application/json",
    }

    if config.model_name.startswith("voyage"):
        headers["Authorization"] = f"Bearer {config.api_key}"
        payload: dict[str, Any] = {
            "model": config.model_name,
            "input": [query],
            "input_type": "query",
        }
    else:
        headers["Authorization"] = f"Bearer {config.api_key}"
        payload = {
            "model": config.model_name,
            "input": [query],
        }

    with httpx.Client() as client:
        resp = client.post(config.api_url, json=payload, headers=headers, timeout=30.0)

    if resp.status_code == 429:
        raise EmbeddingError("Embedding API rate limited for query")

    if resp.status_code != 200:
        raise EmbeddingError(
            f"Query embedding API returned {resp.status_code}: {resp.text[:200]}"
        )

    data = resp.json()
    return data["data"][0]["embedding"]
