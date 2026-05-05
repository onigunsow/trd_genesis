"""Embedding indexer — incremental upsert of chunks into pgvector.

REQ-PGVEC-02-5: Incremental upsert (detect changed/new/stale chunks).
REQ-PGVEC-02-9: Audit logging for pipeline runs.
REQ-NFR-10-3: Complete within 30 seconds per .md file.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from trading.db.session import audit, connection
from trading.embeddings.chunker import Chunk, chunk_markdown
from trading.embeddings.config import EmbeddingConfig, estimate_tokens, get_embedding_config
from trading.embeddings.embedder import EmbeddingError, embed_texts

LOG = logging.getLogger(__name__)


def _content_hash(text: str) -> str:
    """SHA-256 hash of chunk text for change detection."""
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def index_file(
    source_file: str,
    markdown_content: str,
    config: EmbeddingConfig | None = None,
) -> dict[str, Any]:
    """Index a markdown file: chunk, embed, and upsert to pgvector.

    Performs incremental indexing:
    - Detects changed/new chunks via content hash comparison
    - Generates embeddings only for changed/new chunks
    - Upserts changed/new chunks, deletes stale chunks
    - Unchanged chunks are left untouched

    Args:
        source_file: Source identifier (e.g., "macro_context").
        markdown_content: Full markdown file content.
        config: Optional embedding configuration.

    Returns:
        Dict with stats: chunks_processed, embeddings_generated, upserts, deletes,
        total_time_ms, embedding_cost_usd.
    """
    start = time.time()
    if config is None:
        config = get_embedding_config()

    # Step 1: Chunk the markdown
    chunks = chunk_markdown(markdown_content, source_file=source_file)
    chunks_processed = len(chunks)

    # Step 2: Load existing chunks from DB for comparison
    existing: dict[int, str] = {}  # chunk_index -> content_hash
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT chunk_index, md5(chunk_text) AS text_hash FROM context_embeddings "
            "WHERE source_file = %s",
            (source_file,),
        )
        for row in cur.fetchall():
            existing[int(row["chunk_index"])] = row["text_hash"]

    # Step 3: Determine which chunks need embedding
    new_chunk_indices = set(range(len(chunks)))
    existing_indices = set(existing.keys())
    stale_indices = existing_indices - new_chunk_indices

    chunks_to_embed: list[Chunk] = []
    for chunk in chunks:
        current_hash = hashlib.md5(chunk.text.encode()).hexdigest()  # noqa: S324
        if chunk.chunk_index not in existing or existing[chunk.chunk_index] != current_hash:
            chunks_to_embed.append(chunk)

    # Step 4: Generate embeddings for changed/new chunks
    embeddings_generated = 0
    embedding_cost_usd = 0.0
    chunk_embeddings: dict[int, list[float]] = {}

    if chunks_to_embed:
        texts = [c.text for c in chunks_to_embed]
        total_tokens = sum(estimate_tokens(t) for t in texts)

        try:
            embeddings = embed_texts(texts, config=config)
            embeddings_generated = len(embeddings)
            embedding_cost_usd = (total_tokens / 1_000_000) * config.price_per_mtok

            for chunk, embedding in zip(chunks_to_embed, embeddings, strict=True):
                chunk_embeddings[chunk.chunk_index] = embedding

        except EmbeddingError as e:
            LOG.error("Embedding generation failed for %s: %s", source_file, e)
            # Log failure and return partial results
            elapsed_ms = int((time.time() - start) * 1000)
            audit(
                "EMBEDDING_PIPELINE_FAILED",
                actor="indexer",
                details={
                    "source_file": source_file,
                    "error": str(e),
                    "chunks_processed": chunks_processed,
                    "total_time_ms": elapsed_ms,
                },
            )
            return {
                "source_file": source_file,
                "chunks_processed": chunks_processed,
                "embeddings_generated": 0,
                "upserts": 0,
                "deletes": 0,
                "total_time_ms": elapsed_ms,
                "embedding_cost_usd": 0.0,
                "error": str(e),
            }

    # Step 5: Upsert chunks with embeddings + delete stale
    upserts = 0
    deletes = 0

    with connection() as conn, conn.cursor() as cur:
        # Upsert changed/new chunks
        for chunk in chunks_to_embed:
            if chunk.chunk_index not in chunk_embeddings:
                continue

            embedding = chunk_embeddings[chunk.chunk_index]
            vec_str = "[" + ",".join(str(v) for v in embedding) + "]"

            cur.execute(
                """
                INSERT INTO context_embeddings
                    (source_file, chunk_index, chunk_text, chunk_tokens, embedding, metadata, updated_at)
                VALUES (%s, %s, %s, %s, %s::vector, %s::jsonb, NOW())
                ON CONFLICT (source_file, chunk_index) DO UPDATE SET
                    chunk_text = EXCLUDED.chunk_text,
                    chunk_tokens = EXCLUDED.chunk_tokens,
                    embedding = EXCLUDED.embedding,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                """,
                (
                    source_file,
                    chunk.chunk_index,
                    chunk.text,
                    chunk.tokens,
                    vec_str,
                    json.dumps(chunk.metadata),
                ),
            )
            upserts += 1

        # Delete stale chunks
        if stale_indices:
            cur.execute(
                "DELETE FROM context_embeddings WHERE source_file = %s AND chunk_index = ANY(%s)",
                (source_file, list(stale_indices)),
            )
            deletes = len(stale_indices)

    elapsed_ms = int((time.time() - start) * 1000)

    # REQ-PGVEC-02-9: Audit log
    stats = {
        "source_file": source_file,
        "chunks_processed": chunks_processed,
        "embeddings_generated": embeddings_generated,
        "upserts": upserts,
        "deletes": deletes,
        "total_time_ms": elapsed_ms,
        "embedding_cost_usd": embedding_cost_usd,
    }
    audit("EMBEDDING_PIPELINE_RUN", actor="indexer", details=stats)

    LOG.info(
        "Indexed %s: %d chunks, %d embeddings, %d upserts, %d deletes in %dms",
        source_file, chunks_processed, embeddings_generated, upserts, deletes, elapsed_ms,
    )

    return stats
