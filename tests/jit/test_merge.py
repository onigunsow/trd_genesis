"""Tests for JIT merge engine — behavior characterization."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from trading.jit.merge import _apply_delta, _ensure_ticker
from trading.jit.models import MergedState, TickerState


class TestApplyDelta:
    """Test delta application logic in merge engine."""

    def test_price_update_creates_ticker(self):
        state = MergedState(snapshot_type="micro")
        delta_row = {
            "event_type": "price_update",
            "ticker": "005930",
            "payload": {"price": 78500, "volume": 1000, "change_pct": 0.64},
            "event_ts": datetime(2026, 5, 5, 10, 30),
        }
        _apply_delta(state, delta_row)
        assert "005930" in state.tickers
        assert state.tickers["005930"].price == 78500
        assert state.tickers["005930"].volume == 1000
        assert state.tickers["005930"].change_pct == 0.64

    def test_price_update_overrides_previous(self):
        state = MergedState(snapshot_type="micro")
        state.tickers["005930"] = TickerState(ticker="005930", price=78000)

        delta_row = {
            "event_type": "price_update",
            "ticker": "005930",
            "payload": {"price": 78500},
            "event_ts": datetime(2026, 5, 5, 10, 30),
        }
        _apply_delta(state, delta_row)
        assert state.tickers["005930"].price == 78500

    def test_disclosure_appends(self):
        state = MergedState(snapshot_type="micro")
        delta_row = {
            "event_type": "disclosure",
            "ticker": "005930",
            "payload": {"title": "Earnings Report", "report_type": "earnings", "url": "http://dart"},
            "event_ts": datetime(2026, 5, 5, 9, 45),
        }
        _apply_delta(state, delta_row)
        assert len(state.tickers["005930"].disclosures_today) == 1
        assert state.tickers["005930"].disclosures_today[0]["title"] == "Earnings Report"

    def test_news_appends_to_ticker(self):
        state = MergedState(snapshot_type="micro")
        delta_row = {
            "event_type": "news",
            "ticker": "005930",
            "payload": {"headline": "Samsung AI chip", "source_name": "Reuters"},
            "event_ts": datetime(2026, 5, 5, 11, 0),
        }
        _apply_delta(state, delta_row)
        assert len(state.tickers["005930"].news_today) == 1

    def test_multiple_deltas_sequential(self):
        state = MergedState(snapshot_type="micro")
        deltas = [
            {"event_type": "price_update", "ticker": "005930",
             "payload": {"price": 78000}, "event_ts": datetime(2026, 5, 5, 10, 0)},
            {"event_type": "price_update", "ticker": "005930",
             "payload": {"price": 78200}, "event_ts": datetime(2026, 5, 5, 10, 15)},
            {"event_type": "price_update", "ticker": "005930",
             "payload": {"price": 78500}, "event_ts": datetime(2026, 5, 5, 10, 30)},
        ]
        for d in deltas:
            _apply_delta(state, d)

        # Latest price wins (sequential application)
        assert state.tickers["005930"].price == 78500
        assert state.tickers["005930"].deltas_applied == 3

    def test_string_payload_parsed(self):
        """Payload from DB may come as JSON string."""
        import json
        state = MergedState(snapshot_type="micro")
        delta_row = {
            "event_type": "price_update",
            "ticker": "005930",
            "payload": json.dumps({"price": 79000}),
            "event_ts": datetime(2026, 5, 5, 10, 30),
        }
        _apply_delta(state, delta_row)
        assert state.tickers["005930"].price == 79000

    def test_no_ticker_for_news_is_ignored(self):
        """Market-wide news without ticker should not crash."""
        state = MergedState(snapshot_type="micro")
        delta_row = {
            "event_type": "news",
            "ticker": None,
            "payload": {"headline": "Fed decision"},
            "event_ts": datetime(2026, 5, 5, 11, 0),
        }
        # Should not raise
        _apply_delta(state, delta_row)
        assert len(state.tickers) == 0


class TestEnsureTicker:
    """Test ticker state creation/retrieval."""

    def test_creates_new_ticker(self):
        state = MergedState(snapshot_type="micro")
        ts = _ensure_ticker(state, "005930")
        assert ts.ticker == "005930"
        assert "005930" in state.tickers

    def test_returns_existing_ticker(self):
        state = MergedState(snapshot_type="micro")
        state.tickers["005930"] = TickerState(ticker="005930", price=78000)
        ts = _ensure_ticker(state, "005930")
        assert ts.price == 78000
