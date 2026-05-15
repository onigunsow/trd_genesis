"""SPEC-TRADING-024 Stage 1 — branch coverage tests for watcher utilities.

Covers source-collection branches (holdings/dynamic/micro candidate readers,
KIS quote fallback, blocked_release source loaders, volume_anomaly stats).
These are lightweight tests; they exercise error paths so production failures
in any sub-source degrade gracefully without crashing the scheduler.

@MX:SPEC: SPEC-TRADING-024
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestPriceThresholdHelpers:
    """price_threshold._get_target_tickers + _read_micro_candidate_tickers."""

    def test_get_target_tickers_dedups_sources(self):
        from trading.watchers import price_threshold

        with (
            patch(
                "trading.data.universe._read_active_holdings",
                return_value=["005930", "000660"],
            ),
            patch(
                "trading.data.universe._read_dynamic_tickers",
                return_value=["005930", "035720"],
            ),
            patch.object(
                price_threshold,
                "_read_micro_candidate_tickers",
                return_value=["000660", "066570"],
            ),
        ):
            out = price_threshold._get_target_tickers()

        assert sorted(out) == ["000660", "005930", "035720", "066570"]

    def test_get_target_tickers_swallows_source_failure(self):
        from trading.watchers import price_threshold

        with (
            patch(
                "trading.data.universe._read_active_holdings",
                side_effect=RuntimeError("db blip"),
            ),
            patch(
                "trading.data.universe._read_dynamic_tickers",
                return_value=["005930"],
            ),
            patch.object(
                price_threshold,
                "_read_micro_candidate_tickers",
                return_value=[],
            ),
        ):
            out = price_threshold._get_target_tickers()

        # 005930 still returned despite holdings failure
        assert out == ["005930"]

    def test_read_micro_candidate_tickers_handles_no_cache(self):
        from trading.watchers import price_threshold

        with patch("trading.personas.micro.latest_cached", return_value=None):
            assert price_threshold._read_micro_candidate_tickers() == []

    def test_read_micro_candidate_tickers_extracts_buy(self):
        from trading.watchers import price_threshold

        cached = {
            "response_json": {
                "candidates": {
                    "buy": [
                        {"ticker": "005930"},
                        {"ticker": "035720"},
                        {"no_ticker": True},
                    ]
                }
            }
        }
        with patch("trading.personas.micro.latest_cached", return_value=cached):
            out = price_threshold._read_micro_candidate_tickers()

        assert sorted(out) == ["005930", "035720"]

    def test_get_kis_quote_returns_none_on_failure(self):
        from trading.watchers import price_threshold

        with patch("trading.kis.market.current_price", side_effect=RuntimeError("kis down")):
            assert price_threshold._get_kis_quote("005930") is None

    def test_poll_skips_when_quote_unavailable(self):
        from trading.watchers import price_threshold

        with (
            patch.object(price_threshold, "_get_target_tickers", return_value=["005930"]),
            patch.object(price_threshold, "_get_kis_quote", return_value=None),
            patch.object(price_threshold, "_fire_trigger_event") as fire,
        ):
            metrics = price_threshold.poll_price_threshold()

        assert fire.call_count == 0
        assert metrics["skipped_no_quote"] == 1


class TestBlockedReleaseHelpers:
    """blocked_release source loaders + persistence."""

    def test_get_universe_falls_back_on_failure(self):
        from trading.watchers import blocked_release

        with patch(
            "trading.data.universe.get_data_universe",
            side_effect=RuntimeError("boom"),
        ):
            assert blocked_release._get_universe() == []

    def test_load_previous_blocked_handles_corrupt_cache(self):
        from trading.watchers import blocked_release

        with patch(
            "trading.risk.blocked_cache.get_blocked_tickers",
            side_effect=RuntimeError("io"),
        ):
            assert blocked_release._load_previous_blocked() == {}

    def test_still_blocked_records_in_next_state(self, tmp_path, monkeypatch):
        """Still-blocked ticker must persist into the next-state snapshot."""
        from trading.watchers import blocked_release

        # Redirect project_root() so we don't pollute the real data/ dir.
        monkeypatch.setattr("trading.config.project_root", lambda: tmp_path)

        previous = {"055550": {"stat_cls": "55", "reason": "단기과열"}}
        current_quotes = {
            "055550": {"stat_cls": "55", "is_normal": False},
        }
        with (
            patch.object(blocked_release, "_load_previous_blocked", return_value=previous),
            patch.object(
                blocked_release,
                "_get_current_stat_cls",
                side_effect=lambda t: current_quotes.get(t),
            ),
            patch.object(blocked_release, "_get_universe", return_value=["055550"]),
            patch.object(blocked_release, "_fire_trigger_event") as fire,
        ):
            metrics = blocked_release.poll_blocked_release()

        assert fire.call_count == 0
        assert metrics["still_blocked"] == 1

    def test_skipped_no_quote_preserves_previous(self):
        """When KIS quote is unavailable, keep prior blocked state."""
        from trading.watchers import blocked_release

        previous = {"055550": {"stat_cls": "55", "reason": "단기과열"}}
        with (
            patch.object(blocked_release, "_load_previous_blocked", return_value=previous),
            patch.object(blocked_release, "_get_current_stat_cls", return_value=None),
            patch.object(blocked_release, "_get_universe", return_value=["055550"]),
            patch.object(blocked_release, "_persist_blocked_state") as persist,
            patch.object(blocked_release, "_fire_trigger_event") as fire,
        ):
            metrics = blocked_release.poll_blocked_release()

        assert fire.call_count == 0
        assert metrics["skipped_no_quote"] == 1
        # The persisted snapshot must retain the previously-blocked entry
        persisted = persist.call_args[0][0]
        assert "055550" in persisted

    def test_stat_label_falls_back_on_import_failure(self):
        from trading.watchers import blocked_release

        with patch(
            "trading.kis.market.stat_cls_label",
            side_effect=RuntimeError("nope"),
        ):
            assert blocked_release._stat_label("55") == "stat_cls=55"


class TestVolumeAnomalyHelpers:
    """volume_anomaly statistics helper."""

    def test_get_stats_returns_none_when_too_few_rows(self):
        from trading.watchers import volume_anomaly

        rows = [
            {"ts": f"d{i}", "open": 100, "high": 110, "low": 95, "close": 105, "volume": 1000}
            for i in range(3)  # < 5
        ]
        fake_cur = MagicMock()
        fake_cur.fetchall.return_value = rows
        fake_conn = MagicMock()
        fake_conn.cursor.return_value.__enter__.return_value = fake_cur

        with patch(
            "trading.db.session.connection",
            return_value=MagicMock(__enter__=lambda s: fake_conn, __exit__=lambda *a: False),
        ):
            assert volume_anomaly._get_volume_volatility_stats("005930") is None

    def test_get_stats_handles_db_error(self):
        from trading.watchers import volume_anomaly

        with patch("trading.db.session.connection", side_effect=RuntimeError("db down")):
            assert volume_anomaly._get_volume_volatility_stats("005930") is None

    def test_get_target_tickers_proxies_to_price_threshold(self):
        from trading.watchers import volume_anomaly

        with patch(
            "trading.watchers.price_threshold._get_target_tickers",
            return_value=["005930"],
        ):
            assert volume_anomaly._get_target_tickers() == ["005930"]

    def test_get_shared_throttle_proxies_to_price_threshold(self):
        from trading.watchers import price_threshold, volume_anomaly

        # Both modules must share the same throttle instance.
        t1 = volume_anomaly._get_shared_throttle()
        t2 = price_threshold._get_shared_throttle()
        assert t1 is t2

    def test_poll_throttled_increments_metric(self):
        """When throttle blocks, fired must be 0 and throttled must be 1."""
        from trading.watchers import volume_anomaly
        from trading.watchers.throttle import TickerThrottle

        stats = {
            "today_volume": 2_500_000,
            "avg_20d_volume": 1_000_000,
            "atr_today": 1600.0,
            "atr_20d_median": 1000.0,
        }
        # Pre-record so cooldown blocks
        throttle = TickerThrottle(min_interval_sec=300, daily_cap=20)
        throttle.record("005930")
        with (
            patch.object(volume_anomaly, "_get_target_tickers", return_value=["005930"]),
            patch.object(volume_anomaly, "_get_volume_volatility_stats", return_value=stats),
            patch.object(volume_anomaly, "_get_shared_throttle", return_value=throttle),
            patch.object(volume_anomaly, "_fire_trigger_event") as fire,
        ):
            metrics = volume_anomaly.poll_volume_anomaly()

        assert fire.call_count == 0
        assert metrics["throttled"] == 1

    def test_get_stats_computes_volume_and_atr_ratios(self):
        """Happy path: 6 chronological rows, today is double-volume + wider range."""
        from trading.watchers import volume_anomaly

        # Note: production code does `ORDER BY ts DESC` then `.reverse()`.
        # Construct DESC order rows so reverse() yields chronological.
        rows_desc = [
            # today (high volume + wide range)
            {"ts": "d6", "open": 100, "high": 130, "low": 100, "close": 120, "volume": 3000},
            # 5 prior days
            {"ts": "d5", "open": 100, "high": 110, "low": 100, "close": 105, "volume": 1000},
            {"ts": "d4", "open": 100, "high": 110, "low": 100, "close": 105, "volume": 1000},
            {"ts": "d3", "open": 100, "high": 110, "low": 100, "close": 105, "volume": 1000},
            {"ts": "d2", "open": 100, "high": 110, "low": 100, "close": 105, "volume": 1000},
            {"ts": "d1", "open": 100, "high": 110, "low": 100, "close": 105, "volume": 1000},
        ]
        fake_cur = MagicMock()
        fake_cur.fetchall.return_value = rows_desc
        fake_cursor_ctx = MagicMock()
        fake_cursor_ctx.__enter__.return_value = fake_cur
        fake_cursor_ctx.__exit__.return_value = False
        fake_conn = MagicMock()
        fake_conn.cursor.return_value = fake_cursor_ctx
        fake_conn_ctx = MagicMock()
        fake_conn_ctx.__enter__.return_value = fake_conn
        fake_conn_ctx.__exit__.return_value = False

        with patch("trading.db.session.connection", return_value=fake_conn_ctx):
            stats = volume_anomaly._get_volume_volatility_stats("005930")

        assert stats is not None
        assert stats["today_volume"] == 3000
        assert stats["avg_20d_volume"] == 1000
        assert stats["atr_today"] == 30  # 130 - 100
        assert stats["atr_20d_median"] == 10  # 110 - 100
