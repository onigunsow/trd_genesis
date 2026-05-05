"""Merge Engine — O(1) amortized state reconstruction.

REQ-MERGE-02-1: Combines latest snapshot with un-merged delta events.
REQ-MERGE-02-3: Sequential delta application (price overrides, disclosure/news appends).
REQ-MERGE-02-4: Interface functions: get_merged_state, get_ticker_current, get_market_summary.
REQ-MERGE-02-6: Cached < 1ms, cold merge < 100ms.
REQ-MERGE-02-7: Slow merge alerting (> 200ms, Telegram after 3 consecutive).
REQ-MERGE-02-8: Pure read operation — no modification of source data.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from trading.db.session import audit, connection
from trading.jit.cache import get_cache
from trading.jit.events import get_unmerged_deltas
from trading.jit.models import DeltaEvent, MarketSummary, MergedState, TickerState
from trading.jit.snapshots import get_latest_snapshot

LOG = logging.getLogger(__name__)

# REQ-MERGE-02-7: Slow merge threshold
_SLOW_MERGE_MS: int = 200
_consecutive_slow: int = 0


def get_merged_state(snapshot_type: str) -> MergedState:
    """Full merged state for a snapshot type (macro/micro/news).

    REQ-MERGE-02-4: Primary interface function.
    REQ-MERGE-02-5: Returns cached result if within TTL.
    """
    cache = get_cache()
    cache_key = f"merged:{snapshot_type}"

    # Hot path: cached read (REQ-MERGE-02-5)
    cached = cache.get(cache_key)
    if cached is not None:
        cached.cached = True
        return cached

    # Cold path: full merge
    start = time.time()
    state = _execute_merge(snapshot_type)
    elapsed_ms = (time.time() - start) * 1000
    state.merge_time_ms = elapsed_ms

    # Cache the result
    cache.put(cache_key, state)

    # REQ-MERGE-02-7: Slow merge detection
    _check_slow_merge(elapsed_ms, snapshot_type, state.deltas_applied)

    return state


def get_ticker_current(ticker: str) -> TickerState:
    """Single ticker merged state — price + disclosures + news.

    REQ-MERGE-02-4: Convenience function for per-ticker queries.
    """
    # Use micro snapshot type as primary source for ticker data
    state = get_merged_state("micro")
    return state.tickers.get(ticker, TickerState(ticker=ticker))


def get_market_summary() -> MarketSummary:
    """Aggregate market state from merged data.

    REQ-MERGE-02-4: Market-level summary.
    """
    state = get_merged_state("micro")
    return state.market_summary


def get_deltas_since(snapshot_type: str, since: datetime) -> list[dict[str, Any]]:
    """Raw deltas since a given timestamp.

    REQ-MERGE-02-4: For tools needing raw event access.
    """
    snapshot = get_latest_snapshot(snapshot_type)
    snapshot_id = snapshot["id"] if snapshot else None
    return get_unmerged_deltas(snapshot_id=snapshot_id, since=since)


def invalidate_cache(snapshot_type: str | None = None) -> None:
    """Invalidate merged state cache — called when new delta arrives.

    REQ-MERGE-02-2: Lazy invalidation on new delta event.
    """
    cache = get_cache()
    if snapshot_type:
        cache.invalidate(f"merged:{snapshot_type}")
    else:
        cache.invalidate_all()


def _execute_merge(snapshot_type: str) -> MergedState:
    """Execute the full merge: load snapshot + apply deltas.

    REQ-MERGE-02-3: Deterministic merge algorithm.
    REQ-MERGE-02-8: Pure read — no writes to source data.
    """
    state = MergedState(snapshot_type=snapshot_type)

    # Step 1: Load base snapshot metadata
    snapshot = get_latest_snapshot(snapshot_type)
    if snapshot:
        state.snapshot_id = snapshot["id"]
        state.snapshot_time = snapshot["generated_at"]

    # Step 2: Query un-merged deltas
    deltas = get_unmerged_deltas(snapshot_id=state.snapshot_id)

    # Step 3: Apply deltas sequentially
    for delta_row in deltas:
        _apply_delta(state, delta_row)
        state.deltas_applied += 1

    # Update market summary
    state.market_summary.total_deltas_today = state.deltas_applied
    state.market_summary.active_tickers = len(state.tickers)
    if state.tickers:
        latest_times = [
            t.last_delta_time for t in state.tickers.values() if t.last_delta_time
        ]
        if latest_times:
            state.market_summary.last_update = max(latest_times)

    return state


def _apply_delta(state: MergedState, delta_row: dict[str, Any]) -> None:
    """Apply a single delta event to the merged state.

    REQ-MERGE-02-3: Event-type-specific application logic.
    - price_update: override ticker price/volume/change fields
    - disclosure: append to ticker disclosure list
    - news: append to ticker/market news list
    """
    event_type = delta_row["event_type"]
    ticker = delta_row.get("ticker")
    payload = delta_row["payload"]
    event_ts = delta_row["event_ts"]

    # Ensure payload is a dict (may be string from DB)
    if isinstance(payload, str):
        import json
        payload = json.loads(payload)

    if event_type == "price_update" and ticker:
        ts = _ensure_ticker(state, ticker)
        ts.price = payload.get("price", ts.price)
        ts.volume = payload.get("volume", ts.volume)
        ts.change_pct = payload.get("change_pct", ts.change_pct)
        ts.high = payload.get("high", ts.high)
        ts.low = payload.get("low", ts.low)
        ts.market_cap = payload.get("market_cap", ts.market_cap)
        ts.last_delta_time = event_ts
        ts.deltas_applied += 1

    elif event_type == "disclosure" and ticker:
        ts = _ensure_ticker(state, ticker)
        ts.disclosures_today.append({
            "title": payload.get("title", ""),
            "report_type": payload.get("report_type", ""),
            "url": payload.get("url", ""),
            "event_ts": str(event_ts),
        })
        ts.last_delta_time = event_ts
        ts.deltas_applied += 1

    elif event_type == "news":
        if ticker:
            ts = _ensure_ticker(state, ticker)
            ts.news_today.append({
                "headline": payload.get("headline", ""),
                "source_name": payload.get("source_name", ""),
                "url": payload.get("url", ""),
                "event_ts": str(event_ts),
            })
            ts.last_delta_time = event_ts
            ts.deltas_applied += 1


def _ensure_ticker(state: MergedState, ticker: str) -> TickerState:
    """Get or create a TickerState in the merged state dict."""
    if ticker not in state.tickers:
        state.tickers[ticker] = TickerState(ticker=ticker)
    return state.tickers[ticker]


def _check_slow_merge(elapsed_ms: float, snapshot_type: str, delta_count: int) -> None:
    """Track consecutive slow merges and alert on 3rd occurrence.

    REQ-MERGE-02-7: Telegram alert after 3 consecutive slow merges.
    """
    global _consecutive_slow

    if elapsed_ms > _SLOW_MERGE_MS:
        _consecutive_slow += 1
        audit(
            "MERGE_SLOW",
            actor="jit_merge",
            details={
                "snapshot_type": snapshot_type,
                "duration_ms": round(elapsed_ms, 1),
                "delta_count": delta_count,
                "consecutive": _consecutive_slow,
            },
        )
        LOG.warning(
            "Slow merge: %s took %.1fms (%d deltas, consecutive=%d)",
            snapshot_type, elapsed_ms, delta_count, _consecutive_slow,
        )

        if _consecutive_slow >= 3:
            try:
                from trading.alerts.telegram import send_alert
                send_alert(
                    f"[JIT] Merge slow x{_consecutive_slow}: "
                    f"{snapshot_type} {elapsed_ms:.0f}ms ({delta_count} deltas). "
                    "Consider delta compaction."
                )
            except Exception:
                LOG.warning("Failed to send slow merge Telegram alert")
    else:
        _consecutive_slow = 0
