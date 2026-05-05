"""JIT pipeline and prototype tools — new SPEC-011 tool functions.

REQ-TOOLINT-05-3: New tools for delta events, prototype similarity, intraday prices.
REQ-TOOLINT-05-4: get_market_prototype_similarity for Risk persona.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from trading.db.session import get_system_state

LOG = logging.getLogger(__name__)

# REQ-TOOLINT-05-7: Max token budget per tool response
MAX_RESPONSE_ITEMS: int = 20


def get_delta_events(
    ticker: str,
    event_type: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Fetch recent intraday delta events for a ticker.

    REQ-TOOLINT-05-3: New tool for Risk/Decision personas.

    Args:
        ticker: KRX stock code.
        event_type: Optional filter ('price_update', 'disclosure', 'news').
        limit: Maximum events to return.

    Returns:
        Dict with events list and metadata.
    """
    state = get_system_state()
    if not state.get("jit_pipeline_enabled", False):
        return {"error": "jit_disabled", "message": "JIT pipeline is not enabled"}

    from trading.jit.events import get_recent_deltas_for_ticker

    limit = min(limit, MAX_RESPONSE_ITEMS)
    rows = get_recent_deltas_for_ticker(ticker, limit=limit * 2)  # over-fetch for filtering

    # Apply event_type filter if specified
    if event_type:
        rows = [r for r in rows if r["event_type"] == event_type]

    rows = rows[:limit]

    events = []
    for row in rows:
        events.append({
            "event_type": row["event_type"],
            "source": row["source"],
            "ticker": row["ticker"],
            "payload": row["payload"],
            "event_ts": str(row["event_ts"]),
        })

    total_today = len(get_recent_deltas_for_ticker(ticker, limit=1000))
    truncated = total_today > limit

    return {
        "ticker": ticker,
        "events": events,
        "returned_count": len(events),
        "full_events_count": total_today,
        "truncated": truncated,
    }


def get_market_prototype_similarity() -> dict[str, Any]:
    """Compute and return current market prototype similarity.

    REQ-TOOLINT-05-3: New tool for Risk persona.
    REQ-TOOLINT-05-4: Available when prototype_risk_enabled=true.

    Returns:
        Dict with top matches, applied ceiling, and static limit.
    """
    state = get_system_state()
    if not state.get("prototype_risk_enabled", False):
        return {"error": "prototype_disabled", "message": "Prototype risk is not enabled"}

    from trading.prototypes.exposure import get_risk_advisory
    from trading.prototypes.similarity import build_current_state_text, compute_similarity

    # Build current state text and compute similarity
    state_text = build_current_state_text()
    matches = compute_similarity(state_text, cycle_kind="intraday")

    if not matches:
        return {
            "top_matches": [],
            "applied_ceiling_pct": None,
            "static_limit_pct": 80.0,
            "message": "No prototype matches found",
        }

    advisory = get_risk_advisory(matches)

    return {
        "top_matches": advisory["top_matches"],
        "applied_ceiling_pct": advisory["applied_ceiling_pct"],
        "static_limit_pct": advisory["static_limit_pct"],
        "advisory_text": advisory["text"],
        "has_significant_match": advisory["has_significant_match"],
    }


def get_intraday_price_history(ticker: str) -> dict[str, Any]:
    """Get chronological intraday price movements from delta events.

    REQ-TOOLINT-05-3: New tool for intraday price tracking.

    Args:
        ticker: KRX stock code.

    Returns:
        Dict with chronological price updates for today.
    """
    state = get_system_state()
    if not state.get("jit_pipeline_enabled", False):
        return {"error": "jit_disabled", "message": "JIT pipeline is not enabled"}

    from trading.jit.events import get_recent_deltas_for_ticker

    rows = get_recent_deltas_for_ticker(ticker, limit=200)

    # Filter to price_update only and reverse for chronological order
    price_events = [
        r for r in reversed(rows)
        if r["event_type"] == "price_update"
    ]

    # Limit to avoid token overflow
    if len(price_events) > MAX_RESPONSE_ITEMS:
        # Sample evenly
        step = len(price_events) // MAX_RESPONSE_ITEMS
        price_events = price_events[::step][:MAX_RESPONSE_ITEMS]
        truncated = True
    else:
        truncated = False

    history = []
    for row in price_events:
        payload = row["payload"]
        if isinstance(payload, str):
            import json
            payload = json.loads(payload)
        history.append({
            "time": str(row["event_ts"]),
            "price": payload.get("price"),
            "volume": payload.get("volume"),
            "change_pct": payload.get("change_pct"),
        })

    return {
        "ticker": ticker,
        "price_history": history,
        "data_points": len(history),
        "truncated": truncated,
    }
