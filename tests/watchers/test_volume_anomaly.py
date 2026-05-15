"""SPEC-TRADING-024 REQ-024-3 — Volume + volatility anomaly watcher tests.

@MX:SPEC: SPEC-TRADING-024
"""

from __future__ import annotations

from unittest.mock import patch


class TestVolumeAnomalyPoll:
    """REQ-024-3: today_volume/avg_20d_volume >= 2.0 AND ATR(today)/ATR(20d_median) >= 1.5."""

    def test_normal_volume_no_event(self):
        """volume 1.0x, atr 1.0x — no event."""
        from trading.watchers import volume_anomaly

        target_tickers = ["005930"]
        stats = {
            "today_volume": 1_000_000,
            "avg_20d_volume": 1_000_000,
            "atr_today": 1000.0,
            "atr_20d_median": 1000.0,
        }
        with (
            patch.object(volume_anomaly, "_get_target_tickers", return_value=target_tickers),
            patch.object(volume_anomaly, "_get_volume_volatility_stats", return_value=stats),
            patch.object(volume_anomaly, "_fire_trigger_event") as fire,
        ):
            metrics = volume_anomaly.poll_volume_anomaly()

        assert fire.call_count == 0
        assert metrics["fired"] == 0

    def test_volume_2x_with_high_atr_fires(self):
        """volume 2.5x, atr 1.6x — both conditions met, fire event."""
        from trading.watchers import volume_anomaly

        target_tickers = ["005930"]
        stats = {
            "today_volume": 2_500_000,
            "avg_20d_volume": 1_000_000,
            "atr_today": 1600.0,
            "atr_20d_median": 1000.0,
        }
        with (
            patch.object(volume_anomaly, "_get_target_tickers", return_value=target_tickers),
            patch.object(volume_anomaly, "_get_volume_volatility_stats", return_value=stats),
            patch.object(volume_anomaly, "_fire_trigger_event") as fire,
        ):
            metrics = volume_anomaly.poll_volume_anomaly()

        assert fire.call_count == 1
        args, _ = fire.call_args
        assert args[0] == "005930"
        assert args[1] == "volume_anomaly"
        metadata = args[2]
        assert metadata["volume_ratio"] >= 2.0
        assert metadata["atr_ratio"] >= 1.5
        assert metrics["fired"] == 1

    def test_high_volume_normal_atr_no_event(self):
        """volume 3x but ATR 1.0x — only one condition; do not fire."""
        from trading.watchers import volume_anomaly

        target_tickers = ["005930"]
        stats = {
            "today_volume": 3_000_000,
            "avg_20d_volume": 1_000_000,
            "atr_today": 1000.0,
            "atr_20d_median": 1000.0,
        }
        with (
            patch.object(volume_anomaly, "_get_target_tickers", return_value=target_tickers),
            patch.object(volume_anomaly, "_get_volume_volatility_stats", return_value=stats),
            patch.object(volume_anomaly, "_fire_trigger_event") as fire,
        ):
            metrics = volume_anomaly.poll_volume_anomaly()

        assert fire.call_count == 0
        assert metrics["fired"] == 0

    def test_missing_stats_skip_silently(self):
        """If stats return None, skip the ticker without raising."""
        from trading.watchers import volume_anomaly

        target_tickers = ["005930"]
        with (
            patch.object(volume_anomaly, "_get_target_tickers", return_value=target_tickers),
            patch.object(volume_anomaly, "_get_volume_volatility_stats", return_value=None),
            patch.object(volume_anomaly, "_fire_trigger_event") as fire,
        ):
            metrics = volume_anomaly.poll_volume_anomaly()

        assert fire.call_count == 0
        assert metrics["skipped_no_stats"] == 1
