"""Semantic searcher — cosine similarity search against pgvector.

REQ-SCTX-03-2: Cosine similarity search with top-K retrieval.
REQ-NFR-10-2: Total latency <= 500ms per call.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from trading.db.session import connection
from trading.embeddings.config import estimate_tokens, get_embedding_config
from trading.embeddings.embedder import embed_query

LOG = logging.getLogger(__name__)

# REQ-NFR-10-2: Maximum acceptable latency for semantic search
SEARCH_LATENCY_LIMIT_MS: int = 500


@dataclass
class SearchResult:
    """A single search result from semantic search."""

    chunk_index: int
    text: str
    similarity: float
    tokens: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SemanticSearchResponse:
    """Full response from semantic search operation."""

    source: str
    mode: str
    query: str
    results: list[SearchResult]
    total_chunks: int
    returned_chunks: int
    estimated_tokens: int
    latency_ms: int


def search(
    source_file: str,
    query: str,
    top_k: int = 7,
) -> SemanticSearchResponse:
    """Execute semantic search against context_embeddings.

    REQ-SCTX-03-2 Flow:
    1. Generate embedding for query text
    2. Execute cosine similarity search filtered by source_file
    3. Return top-K chunks ordered by similarity descending

    Args:
        source_file: Filter by source_file (e.g., "macro_context").
        query: Search query text.
        top_k: Number of results to return (default 7, max 20).

    Returns:
        SemanticSearchResponse with results and metadata.

    Raises:
        SemanticSearchError: If search fails or exceeds latency SLA.
    """
    start = time.time()
    top_k = min(max(1, top_k), 20)

    # Step 1: Generate query embedding
    config = get_embedding_config()
    query_embedding = embed_query(query, config=config)

    # Step 2: Execute pgvector cosine similarity search
    # PostgreSQL <=> operator = cosine distance (1 - cosine_similarity)
    # Lower distance = higher similarity
    sql = """
        SELECT
            chunk_index,
            chunk_text,
            chunk_tokens,
            metadata,
            1 - (embedding <=> %s::vector) AS similarity
        FROM context_embeddings
        WHERE source_file = %s
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """

    # Format embedding as PostgreSQL vector literal
    vec_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (vec_str, source_file, vec_str, top_k))
        rows = cur.fetchall()

        # Get total chunks count for this source
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM context_embeddings WHERE source_file = %s",
            (source_file,),
        )
        count_row = cur.fetchone()
        total_chunks = int(count_row["cnt"]) if count_row else 0

    latency_ms = int((time.time() - start) * 1000)

    # Step 3: Build results
    results: list[SearchResult] = []
    total_tokens = 0

    for row in rows:
        tokens = int(row["chunk_tokens"])
        total_tokens += tokens
        results.append(SearchResult(
            chunk_index=int(row["chunk_index"]),
            text=row["chunk_text"],
            similarity=float(row["similarity"]),
            tokens=tokens,
            metadata=row["metadata"] or {},
        ))

    # REQ-NFR-10-2: Log if latency exceeds SLA
    if latency_ms > SEARCH_LATENCY_LIMIT_MS:
        LOG.warning(
            "SEMANTIC_SEARCH_SLOW: source=%s latency=%dms (limit=%dms)",
            source_file, latency_ms, SEARCH_LATENCY_LIMIT_MS,
        )

    return SemanticSearchResponse(
        source=source_file,
        mode="semantic",
        query=query,
        results=results,
        total_chunks=total_chunks,
        returned_chunks=len(results),
        estimated_tokens=total_tokens,
        latency_ms=latency_ms,
    )


def has_embeddings(source_file: str) -> bool:
    """Check if embeddings exist for a source file (cold start detection).

    REQ-SCTX-03-4: Used for fallback logic when no embeddings available.
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM context_embeddings WHERE source_file = %s",
                (source_file,),
            )
            row = cur.fetchone()
            return bool(row and int(row["cnt"]) > 0)
    except Exception as e:  # noqa: BLE001
        LOG.warning("Failed to check embeddings for %s: %s", source_file, e)
        return False
