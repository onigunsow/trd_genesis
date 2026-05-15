"""SPEC-TRADING-024 REQ-024-2 — ATR-based price threshold watcher tests.

@MX:SPEC: SPEC-TRADING-024
"""

from __future__ import annotations

from unittest.mock import patch


def _atr_payload(atr_14: float, close_price: float) -> dict:
    """Mimic compute_atr() return shape."""
    return {
        "atr_14": atr_14,
        "atr_pct": (atr_14 / close_price) * 100.0,
        "close_price": close_price,
        "date": "2026-05-15",
    }


class TestPriceThresholdPoll:
    """REQ-024-2: when price change exceeds 1.5x ATR, fire event."""

    def test_price_change_below_threshold_no_event(self):
        """ATR=1000 on 100000 close => threshold = 1.5%. -1% move = no event."""
        from trading.watchers import price_threshold

        target_tickers = ["005930"]
        # current_price 0.5% below last seen
        kis_quote = {"price": 99500, "change_pct": -0.5}

        with (
            patch.object(
                price_threshold,
                "_get_target_tickers",
                return_value=target_tickers,
            ),
            patch.object(
                price_threshold, "compute_atr", return_value=_atr_payload(1000.0, 100000.0)
            ),
            patch.object(
                price_threshold,
                "_get_kis_quote",
                return_value=kis_quote,
            ),
            patch.object(price_threshold, "_fire_trigger_event") as fire,
        ):
            metrics = price_threshold.poll_price_threshold()

        assert fire.call_count == 0
        assert metrics["checked"] == 1
        assert metrics["fired"] == 0

    def test_price_change_above_atr_threshold_fires_event(self):
        """-3% move on threshold 1.5% (ATR-based) fires event."""
        from trading.watchers import price_threshold

        target_tickers = ["005930"]
        # ATR=1000 on 100000 close => 1.5x ATR ratio = 1.5%. -3% > 1.5% => fire.
        kis_quote = {"price": 97000, "change_pct": -3.0}

        with (
            patch.object(
                price_threshold,
                "_get_target_tickers",
                return_value=target_tickers,
            ),
            patch.object(
                price_threshold, "compute_atr", return_value=_atr_payload(1000.0, 100000.0)
            ),
            patch.object(
                price_threshold,
                "_get_kis_quote",
                return_value=kis_quote,
            ),
            patch.object(price_threshold, "_fire_trigger_event") as fire,
        ):
            metrics = price_threshold.poll_price_threshold()

        assert fire.call_count == 1
        args, _ = fire.call_args
        assert args[0] == "005930"
        assert args[1] == "price_threshold"
        # metadata contains atr context
        metadata = args[2]
        assert "atr_14" in metadata
        assert "price_change_pct" in metadata
        assert metrics["fired"] == 1

    def test_same_ticker_throttled_within_cooldown(self):
        """Two consecutive polls for same ticker fire only once."""
        from trading.watchers import price_threshold
        from trading.watchers.throttle import TickerThrottle

        target_tickers = ["005930"]
        kis_quote = {"price": 97000, "change_pct": -3.0}
        # Shared throttle to simulate continued state across polls
        throttle = TickerThrottle(min_interval_sec=300, daily_cap=20)

        with (
            patch.object(price_threshold, "_get_target_tickers", return_value=target_tickers),
            patch.object(
                price_threshold, "compute_atr", return_value=_atr_payload(1000.0, 100000.0)
            ),
            patch.object(price_threshold, "_get_kis_quote", return_value=kis_quote),
            patch.object(price_threshold, "_get_shared_throttle", return_value=throttle),
            patch.object(price_threshold, "_fire_trigger_event") as fire,
        ):
            price_threshold.poll_price_threshold()
            price_threshold.poll_price_threshold()

        assert fire.call_count == 1

    def test_daily_cap_limits_firings(self):
        """daily_cap=2 blocks 3rd firing across distinct tickers."""
        from trading.watchers import price_threshold
        from trading.watchers.throttle import TickerThrottle

        target_tickers = ["A", "B", "C"]
        kis_quote = {"price": 97000, "change_pct": -3.0}
        throttle = TickerThrottle(min_interval_sec=300, daily_cap=2)

        with (
            patch.object(price_threshold, "_get_target_tickers", return_value=target_tickers),
            patch.object(
                price_threshold, "compute_atr", return_value=_atr_payload(1000.0, 100000.0)
            ),
            patch.object(price_threshold, "_get_kis_quote", return_value=kis_quote),
            patch.object(price_threshold, "_get_shared_throttle", return_value=throttle),
            patch.object(price_threshold, "_fire_trigger_event") as fire,
        ):
            price_threshold.poll_price_threshold()

        # Only 2 firings due to daily_cap
        assert fire.call_count == 2

    def test_atr_unavailable_skips_ticker_silently(self):
        """compute_atr returning None means insufficient data → skip silently."""
        from trading.watchers import price_threshold

        target_tickers = ["005930"]
        kis_quote = {"price": 97000, "change_pct": -3.0}

        with (
            patch.object(price_threshold, "_get_target_tickers", return_value=target_tickers),
            patch.object(price_threshold, "compute_atr", return_value=None),
            patch.object(price_threshold, "_get_kis_quote", return_value=kis_quote),
            patch.object(price_threshold, "_fire_trigger_event") as fire,
        ):
            metrics = price_threshold.poll_price_threshold()

        assert fire.call_count == 0
        assert metrics["skipped_no_atr"] == 1
