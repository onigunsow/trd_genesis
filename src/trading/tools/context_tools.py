"""Context tool functions — static .md files with semantic search and dynamic memory.

REQ-TOOL-01-3: Standalone functions wrapping existing context.py logic.
REQ-TOOL-01-8: No Anthropic API or persona calls. Data-only.
REQ-SCTX-03-1: Enhanced get_static_context with mode/query/top_k parameters.
"""

from __future__ import annotations

import logging
from typing import Any

from trading.db.session import audit, connection
from trading.personas.context import _format_memory, _load_memory, _read_md

LOG = logging.getLogger(__name__)


def get_static_context(
    name: str,
    mode: str = "full",
    query: str | None = None,
    top_k: int = 7,
) -> dict[str, Any]:
    """Load a static context .md file from data/contexts/.

    REQ-SCTX-03-1: Enhanced with optional semantic search mode.

    Args:
        name: Context file identifier. One of:
            macro_context, micro_context, macro_news, micro_news,
            intelligence_macro, intelligence_micro
        mode: Retrieval mode - "full" (entire file) or "semantic" (top-K chunks).
        query: Search query for semantic mode (required when mode=semantic).
        top_k: Number of chunks to return in semantic mode (default 7, max 20).

    Returns:
        Dict with 'content' (full mode) or semantic search results.
    """
    # Validate name
    filename_map = {
        "macro_context": "macro_context.md",
        "micro_context": "micro_context.md",
        "macro_news": "macro_news.md",
        "micro_news": "micro_news.md",
        "intelligence_macro": "intelligence_macro.md",
        "intelligence_micro": "intelligence_micro.md",
    }
    filename = filename_map.get(name)
    if not filename:
        return {"error": "invalid_name", "valid_names": list(filename_map.keys())}

    # REQ-SCTX-03-3: Default to full mode when no query provided
    if mode == "semantic" and not query:
        mode = "full"

    # REQ-SCTX-03-5: Check feature flag
    if mode == "semantic":
        try:
            semantic_enabled = _check_semantic_enabled()
        except Exception:  # noqa: BLE001
            semantic_enabled = False

        if not semantic_enabled:
            # Feature disabled — force full mode, no embedding queries
            mode = "full"

    # Full mode — backward compatible (REQ-SCTX-03-3)
    if mode != "semantic":
        content = _read_md(filename)
        return {"name": name, "content": content}

    # Semantic mode (REQ-SCTX-03-2)
    return _semantic_search(name, query, top_k)  # type: ignore[arg-type]


def _check_semantic_enabled() -> bool:
    """Check if semantic_retrieval_enabled feature flag is true."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT semantic_retrieval_enabled FROM system_state WHERE id = 1")
        row = cur.fetchone()
        return bool(row and row.get("semantic_retrieval_enabled"))


def _semantic_search(name: str, query: str, top_k: int) -> dict[str, Any]:
    """Execute semantic search via embeddings searcher.

    REQ-SCTX-03-4: Cold start fallback to full mode.
    """
    from trading.embeddings.searcher import (
        SEARCH_LATENCY_LIMIT_MS,
        has_embeddings,
        search,
    )

    # REQ-SCTX-03-4: Check for cold start
    if not has_embeddings(name):
        audit(
            "SEMANTIC_FALLBACK_NO_EMBEDDINGS",
            actor="context_tools",
            details={"source_file": name, "query": query},
        )
        LOG.info("No embeddings for %s, falling back to full mode", name)
        from trading.personas.context import _read_md

        filename_map = {
            "macro_context": "macro_context.md",
            "micro_context": "micro_context.md",
            "macro_news": "macro_news.md",
            "micro_news": "micro_news.md",
            "intelligence_macro": "intelligence_macro.md",
            "intelligence_micro": "intelligence_micro.md",
        }
        content = _read_md(filename_map[name])
        return {"name": name, "content": content, "fallback": "no_embeddings"}

    try:
        response = search(source_file=name, query=query, top_k=top_k)

        # REQ-NFR-10-2: Latency SLA — fallback if too slow
        if response.latency_ms > SEARCH_LATENCY_LIMIT_MS:
            audit(
                "SEMANTIC_SEARCH_SLOW",
                actor="context_tools",
                details={
                    "source_file": name,
                    "query": query,
                    "latency_ms": response.latency_ms,
                },
            )
            # Fall back to full mode on latency violation
            from trading.personas.context import _read_md

            filename_map = {
                "macro_context": "macro_context.md",
                "micro_context": "micro_context.md",
                "macro_news": "macro_news.md",
                "micro_news": "micro_news.md",
            }
            content = _read_md(filename_map[name])
            return {"name": name, "content": content, "fallback": "latency_exceeded"}

        # REQ-SCTX-03-6: Semantic search response format
        return {
            "source": response.source,
            "mode": "semantic",
            "query": response.query,
            "results": [
                {
                    "chunk_index": r.chunk_index,
                    "text": r.text,
                    "similarity": round(r.similarity, 4),
                    "metadata": r.metadata,
                }
                for r in response.results
            ],
            "total_chunks": response.total_chunks,
            "returned_chunks": response.returned_chunks,
            "estimated_tokens": response.estimated_tokens,
        }

    except Exception as e:  # noqa: BLE001
        LOG.warning("Semantic search failed for %s: %s, falling back to full", name, e)
        from trading.personas.context import _read_md

        filename_map = {
            "macro_context": "macro_context.md",
            "micro_context": "micro_context.md",
            "macro_news": "macro_news.md",
            "micro_news": "micro_news.md",
            "intelligence_macro": "intelligence_macro.md",
            "intelligence_micro": "intelligence_micro.md",
        }
        content = _read_md(filename_map[name])
        return {"name": name, "content": content, "fallback": "search_error"}


def get_active_memory(
    table: str,
    limit: int = 20,
    scope_filter: list[str] | None = None,
) -> dict[str, Any]:
    """Query dynamic memory rows from macro_memory or micro_memory.

    Args:
        table: Memory table name ('macro_memory' or 'micro_memory').
        limit: Maximum rows to return.
        scope_filter: Optional list of scope_id values to filter by.

    Returns:
        Dict with 'rows' (list of memory entries) and 'formatted' (display string).
    """
    # Validate table name to prevent SQL injection
    allowed_tables = {"macro_memory", "micro_memory"}
    if table not in allowed_tables:
        return {"error": "invalid_table", "valid_tables": list(allowed_tables)}

    rows = _load_memory(table, limit=limit, scope_filter=scope_filter)
    formatted = _format_memory(rows)

    # Serialize date fields for JSON
    cleaned_rows = []
    for r in rows:
        cleaned = {}
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                cleaned[k] = v.isoformat()
            else:
                cleaned[k] = v
        cleaned_rows.append(cleaned)

    return {"table": table, "count": len(cleaned_rows), "rows": cleaned_rows, "formatted": formatted}
