"""Data models for the JIT pipeline — delta events and merged state.

REQ-DELTA-01-1: DeltaEvent captures intraday market events.
REQ-MERGE-02-4: MergedState/TickerState/MarketSummary provide query interfaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class DeltaEvent:
    """A single intraday market event persisted to delta_events table.

    REQ-DELTA-01-2: Maps to delta_events row structure.
    """

    event_type: str  # 'price_update', 'disclosure', 'news'
    source: str  # 'kis_ws', 'dart_api', 'news_rss'
    ticker: str | None  # stock code (nullable for market-wide events)
    payload: dict[str, Any]  # event-specific data (JSONB)
    event_ts: datetime  # when the event actually occurred
    snapshot_id: int | None = None
    id: int | None = None


@dataclass
class TickerState:
    """Merged state for a single ticker — snapshot + deltas combined.

    REQ-MERGE-02-4: get_ticker_current return type.
    """

    ticker: str
    price: float | None = None
    volume: int | None = None
    change_pct: float | None = None
    high: float | None = None
    low: float | None = None
    market_cap: int | None = None
    disclosures_today: list[dict[str, Any]] = field(default_factory=list)
    news_today: list[dict[str, Any]] = field(default_factory=list)
    last_delta_time: datetime | None = None
    deltas_applied: int = 0


@dataclass
class MarketSummary:
    """Aggregate market state from merged data.

    REQ-MERGE-02-4: get_market_summary return type.
    """

    kospi_change_pct: float | None = None
    kosdaq_change_pct: float | None = None
    market_breadth_pct: float | None = None
    total_volume_ratio: float | None = None
    foreign_net_flow: float | None = None
    active_tickers: int = 0
    total_deltas_today: int = 0
    last_update: datetime | None = None


@dataclass
class MergedState:
    """Full merged state for a snapshot type — base + all pending deltas.

    REQ-MERGE-02-4: get_merged_state return type.
    """

    snapshot_type: str
    snapshot_id: int | None = None
    snapshot_time: datetime | None = None
    tickers: dict[str, TickerState] = field(default_factory=dict)
    market_summary: MarketSummary = field(default_factory=MarketSummary)
    deltas_applied: int = 0
    merge_time_ms: float = 0.0
    cached: bool = False
