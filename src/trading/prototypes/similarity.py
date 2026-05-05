"""Prototype similarity computation — current market vs historical prototypes.

REQ-DYNRISK-04-2: Construct current state vector, generate embedding, pgvector search.
REQ-NFR-11-3: Similarity search < 500ms.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any

from trading.db.session import audit, connection

LOG = logging.getLogger(__name__)

# Top-K similar prototypes to return
TOP_K: int = 3


def compute_similarity(
    current_state_text: str,
    cycle_kind: str = "intraday",
) -> list[dict[str, Any]]:
    """Compute similarity between current market state and all active prototypes.

    REQ-DYNRISK-04-2: Full similarity pipeline.
    REQ-NFR-11-3: Target < 500ms total latency.

    Args:
        current_state_text: Text representation of current market conditions
            (for embedding generation).
        cycle_kind: 'pre_market', 'intraday', or 'event'.

    Returns:
        List of top-K matches: [{prototype_id, name, category, similarity, ceiling_pct, ...}]
    """
    start = time.time()

    # Step 1: Generate embedding for current state
    embedding = _generate_embedding(current_state_text)
    if not embedding:
        LOG.warning("Failed to generate current state embedding")
        return []

    # Step 2: pgvector cosine similarity search
    matches = _search_similar(embedding)

    # Step 3: Log results
    elapsed_ms = (time.time() - start) * 1000
    _log_similarity_result(cycle_kind, embedding, matches)

    LOG.info(
        "Prototype similarity computed in %.0fms: %d matches",
        elapsed_ms, len(matches),
    )
    return matches


def _generate_embedding(text: str) -> list[float] | None:
    """Generate embedding for a text using SPEC-010 embedding pipeline."""
    try:
        from trading.embeddings.embedder import generate_embeddings
        results = generate_embeddings([text])
        if results and len(results) > 0:
            return results[0]
    except ImportError:
        LOG.debug("embeddings module not available")
    except Exception:
        LOG.exception("Embedding generation failed")
    return None


def _search_similar(embedding: list[float]) -> list[dict[str, Any]]:
    """Execute pgvector cosine similarity search against active prototypes.

    Returns top-K matches ordered by similarity (descending).
    """
    # Format embedding as pgvector literal
    embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

    sql = """
        SELECT
            id,
            name,
            description,
            category,
            risk_recommendation,
            1 - (embedding <=> %s::vector) AS similarity
        FROM market_prototypes
        WHERE is_active = true
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (embedding_str, embedding_str, TOP_K))
            rows = cur.fetchall()

        matches = []
        for row in rows:
            risk_rec = row["risk_recommendation"]
            if isinstance(risk_rec, str):
                risk_rec = json.loads(risk_rec)

            matches.append({
                "prototype_id": row["id"],
                "name": row["name"],
                "category": row["category"],
                "similarity": round(float(row["similarity"]), 4),
                "ceiling_pct": risk_rec.get("max_exposure_pct"),
                "description": row["description"][:100],
                "risk_recommendation": risk_rec,
            })
        return matches

    except Exception:
        LOG.exception("pgvector similarity search failed")
        return []


def _log_similarity_result(
    cycle_kind: str,
    embedding: list[float],
    matches: list[dict[str, Any]],
) -> None:
    """Persist similarity computation to prototype_similarity_log.

    REQ-DYNRISK-04-7: Log all computations.
    """
    from trading.prototypes.exposure import compute_ceiling

    applied_ceiling = compute_ceiling(matches)
    embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

    sql = """
        INSERT INTO prototype_similarity_log
            (cycle_kind, current_state_embedding, top_matches, applied_ceiling_pct)
        VALUES (%s, %s::vector, %s::jsonb, %s)
    """
    try:
        # Sanitize matches for JSON storage (remove non-serializable parts)
        log_matches = [
            {
                "prototype_id": m["prototype_id"],
                "name": m["name"],
                "category": m["category"],
                "similarity": m["similarity"],
                "ceiling_pct": m.get("ceiling_pct"),
            }
            for m in matches
        ]
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (
                cycle_kind,
                embedding_str,
                json.dumps(log_matches),
                applied_ceiling,
            ))
    except Exception:
        LOG.warning("Failed to log similarity result")


def build_current_state_text(market_data: dict[str, Any] | None = None) -> str:
    """Build text representation of current market state for embedding.

    REQ-DYNRISK-04-2: Constructs current state vector from merged state.
    """
    if not market_data:
        market_data = _fetch_current_indicators()

    parts = [
        "Current Korean stock market state:",
        f"KOSPI 5-day change: {market_data.get('kospi_5d_change', 'N/A')}%",
        f"KOSDAQ 5-day change: {market_data.get('kosdaq_5d_change', 'N/A')}%",
        f"VIX level: {market_data.get('vix', 'N/A')}",
        f"USD/KRW: {market_data.get('usd_krw', 'N/A')}",
        f"Foreign net buy/sell (5-day): {market_data.get('foreign_net_5d', 'N/A')}",
        f"Market breadth: {market_data.get('market_breadth_pct', 'N/A')}%",
        f"Volume ratio vs 20-day avg: {market_data.get('volume_ratio_20d', 'N/A')}",
    ]

    # Add any notable events
    if market_data.get("notable_events"):
        parts.append(f"Notable events: {market_data['notable_events']}")

    return "\n".join(parts)


def _fetch_current_indicators() -> dict[str, Any]:
    """Fetch current market indicators from available sources.

    Tries JIT merged state first, falls back to cached data.
    """
    data: dict[str, Any] = {}
    try:
        # Try to get VIX and USD/KRW from DB cache
        with connection() as conn, conn.cursor() as cur:
            # VIX
            cur.execute(
                "SELECT close FROM ohlcv WHERE ticker = '^VIX' ORDER BY dt DESC LIMIT 1"
            )
            row = cur.fetchone()
            if row:
                data["vix"] = row["close"]

            # USD/KRW
            cur.execute(
                "SELECT close FROM ohlcv WHERE ticker = 'KRW=X' ORDER BY dt DESC LIMIT 1"
            )
            row = cur.fetchone()
            if row:
                data["usd_krw"] = row["close"]

    except Exception:
        LOG.debug("Failed to fetch current indicators from DB")

    return data
