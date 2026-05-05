"""Tests for JIT data models."""

from __future__ import annotations

from datetime import datetime

from trading.jit.models import DeltaEvent, MarketSummary, MergedState, TickerState


class TestDeltaEvent:
    """Test DeltaEvent dataclass."""

    def test_create_price_update(self):
        event = DeltaEvent(
            event_type="price_update",
            source="kis_ws",
            ticker="005930",
            payload={"price": 78500, "volume": 1000},
            event_ts=datetime(2026, 5, 5, 10, 30),
        )
        assert event.event_type == "price_update"
        assert event.source == "kis_ws"
        assert event.ticker == "005930"
        assert event.payload["price"] == 78500
        assert event.snapshot_id is None

    def test_create_disclosure(self):
        event = DeltaEvent(
            event_type="disclosure",
            source="dart_api",
            ticker="005930",
            payload={"title": "Earnings", "report_type": "earnings"},
            event_ts=datetime(2026, 5, 5, 9, 45),
        )
        assert event.event_type == "disclosure"
        assert event.payload["report_type"] == "earnings"

    def test_create_market_wide_news(self):
        event = DeltaEvent(
            event_type="news",
            source="news_rss",
            ticker=None,
            payload={"headline": "Fed holds rates"},
            event_ts=datetime(2026, 5, 5, 11, 0),
        )
        assert event.ticker is None


class TestTickerState:
    """Test TickerState dataclass."""

    def test_default_values(self):
        ts = TickerState(ticker="005930")
        assert ts.ticker == "005930"
        assert ts.price is None
        assert ts.disclosures_today == []
        assert ts.news_today == []
        assert ts.deltas_applied == 0

    def test_mutable_fields(self):
        ts = TickerState(ticker="005930")
        ts.price = 78500
        ts.volume = 1000
        ts.disclosures_today.append({"title": "test"})
        assert ts.price == 78500
        assert len(ts.disclosures_today) == 1


class TestMergedState:
    """Test MergedState dataclass."""

    def test_default_values(self):
        state = MergedState(snapshot_type="micro")
        assert state.snapshot_type == "micro"
        assert state.tickers == {}
        assert state.deltas_applied == 0
        assert state.cached is False

    def test_add_ticker(self):
        state = MergedState(snapshot_type="micro")
        state.tickers["005930"] = TickerState(ticker="005930", price=78500)
        assert "005930" in state.tickers
        assert state.tickers["005930"].price == 78500


class TestMarketSummary:
    """Test MarketSummary dataclass."""

    def test_default_values(self):
        summary = MarketSummary()
        assert summary.active_tickers == 0
        assert summary.total_deltas_today == 0
        assert summary.kospi_change_pct is None
