"""SPEC-TRADING-019 REQ-019-1/2/3/4/7/8: refresh job and scheduler tests.

Pre-RED Discovery:
- pykrx adapter: `pykrx_adapter.fetch_ohlcv|fetch_fundamentals|fetch_flows(symbol,
  start, end) -> int` (rows upserted). All use idempotent upsert keyed by
  (source, symbol, ts).
- DART: `dart_adapter.list_recent(start, end, page_count=100) -> list[dict]`.
- Cache helper: `cache.cached_range(source, symbol)` returns (min_ts, max_ts)
  or None.
- Scheduler: APScheduler BlockingScheduler with CronTrigger, KST timezone.
  Existing _wrap()/_safe_call() helpers in scheduler/runner.py.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# REQ-019-1 / REQ-019-2 / REQ-019-3: OHLCV / flows / fundamentals refresh
# ---------------------------------------------------------------------------


class TestRefreshOhlcv:
    """REQ-019-1: Daily OHLCV refresh entrypoint."""

    def test_refresh_ohlcv_calls_universe_and_pykrx_per_ticker(self):
        """refresh_ohlcv() fetches OHLCV for every universe ticker."""
        from trading.scripts import refresh_market_data as mod

        universe = ["005930", "005380"]
        with (
            patch.object(mod, "get_data_universe", return_value=universe),
            patch.object(mod, "_fetch_ohlcv_for_ticker", return_value=10) as fetcher,
        ):
            metrics = mod.refresh_ohlcv()

        assert fetcher.call_count == 2
        called_tickers = sorted(c.args[0] for c in fetcher.call_args_list)
        assert called_tickers == ["005380", "005930"]
        assert metrics["total_tickers"] == 2
        assert metrics["success_count"] == 2
        assert metrics["error_count"] == 0
        assert metrics["total_rows_upserted"] == 20
        assert "duration_seconds" in metrics

    def test_refresh_ohlcv_isolates_per_ticker_failures(self):
        """REQ-019-1 (d): one ticker failure must not abort the batch."""
        from trading.scripts import refresh_market_data as mod

        universe = ["A", "B", "Z", "C", "D"]

        def _fetch(ticker, **_kw):
            if ticker == "Z":
                raise RuntimeError("network error")
            return 5

        with (
            patch.object(mod, "get_data_universe", return_value=universe),
            patch.object(mod, "_fetch_ohlcv_for_ticker", side_effect=_fetch),
        ):
            metrics = mod.refresh_ohlcv()

        # 4 succeeded, 1 failed; batch did not abort
        assert metrics["success_count"] == 4
        assert metrics["error_count"] == 1
        assert metrics["total_rows_upserted"] == 20

    def test_refresh_ohlcv_logs_metrics_at_info(self, caplog):
        """REQ-019-1 (e): metrics emitted as INFO log."""
        from trading.scripts import refresh_market_data as mod

        with (
            patch.object(mod, "get_data_universe", return_value=["005930"]),
            patch.object(mod, "_fetch_ohlcv_for_ticker", return_value=3),
        ):
            with caplog.at_level("INFO"):
                mod.refresh_ohlcv()

        assert any(
            "success_count" in r.message or "refresh_ohlcv" in r.message.lower()
            for r in caplog.records
        )

    def test_refresh_ohlcv_incremental_vs_backfill_window(self):
        """REQ-019-1 (c): cache miss -> 90d backfill, hit -> (last_ts+1d, today)."""
        from trading.scripts import refresh_market_data as mod

        today = date.today()

        # Cache miss
        with (
            patch.object(mod, "_get_latest_ohlcv_ts", return_value=None),
            patch.object(mod, "_pykrx_fetch_ohlcv", return_value=90) as fetcher,
        ):
            rows = mod._fetch_ohlcv_for_ticker("005930", today_override=today)
            assert rows == 90
            _, start_arg, end_arg = fetcher.call_args.args
            assert start_arg == today - timedelta(days=90)
            assert end_arg == today

        # Cache hit -> incremental
        with (
            patch.object(mod, "_get_latest_ohlcv_ts", return_value=today - timedelta(days=3)),
            patch.object(mod, "_pykrx_fetch_ohlcv", return_value=3) as fetcher,
        ):
            mod._fetch_ohlcv_for_ticker("005930", today_override=today)
            _, start_arg, end_arg = fetcher.call_args.args
            assert start_arg == today - timedelta(days=2)
            assert end_arg == today


class TestRefreshFlows:
    """REQ-019-2: Daily flows refresh entrypoint."""

    def test_refresh_flows_calls_universe_and_per_ticker(self):
        from trading.scripts import refresh_market_data as mod

        with (
            patch.object(mod, "get_data_universe", return_value=["005930", "005380"]),
            patch.object(mod, "_fetch_flows_for_ticker", return_value=4) as fetcher,
        ):
            metrics = mod.refresh_flows()

        assert fetcher.call_count == 2
        assert metrics["success_count"] == 2
        assert metrics["total_rows_upserted"] == 8

    def test_refresh_flows_isolates_failures(self):
        from trading.scripts import refresh_market_data as mod

        def _fetch(ticker, **_kw):
            if ticker == "BAD":
                raise ValueError("flows fetch err")
            return 2

        with (
            patch.object(mod, "get_data_universe", return_value=["005930", "BAD", "005380"]),
            patch.object(mod, "_fetch_flows_for_ticker", side_effect=_fetch),
        ):
            metrics = mod.refresh_flows()

        assert metrics["success_count"] == 2
        assert metrics["error_count"] == 1


class TestFlowsLatestTsHelper:
    """SPEC-022 REQ-022-1 (a): _get_latest_flows_ts helper queries flows table."""

    def test_get_latest_flows_ts_returns_max_ts_when_rows_present(self):
        """_get_latest_flows_ts queries SELECT MAX(ts) FROM flows for given ticker."""
        from contextlib import contextmanager

        from tests.conftest import FakeConnection, FakeCursor
        from trading.scripts import refresh_market_data as mod

        sentinel_ts = date(2026, 5, 13)
        cursor = FakeCursor(rows=[{"hi": sentinel_ts}])

        @contextmanager
        def _fake_conn(*_a, **_kw):
            yield FakeConnection(cursor)

        with patch.object(mod, "connection", _fake_conn):
            result = mod._get_latest_flows_ts("005930")

        assert result == sentinel_ts
        assert "flows" in cursor.last_sql.lower()
        assert "max(ts)" in cursor.last_sql.lower().replace(" ", "")
        assert cursor.last_params == ("005930",)

    def test_get_latest_flows_ts_returns_none_when_empty(self):
        """_get_latest_flows_ts returns None when no rows for the ticker."""
        from contextlib import contextmanager

        from tests.conftest import FakeConnection, FakeCursor
        from trading.scripts import refresh_market_data as mod

        cursor = FakeCursor(rows=[{"hi": None}])

        @contextmanager
        def _fake_conn(*_a, **_kw):
            yield FakeConnection(cursor)

        with patch.object(mod, "connection", _fake_conn):
            result = mod._get_latest_flows_ts("999999")

        assert result is None


class TestFlowsSilentSkipRegression:
    """SPEC-022 REQ-022-1 (b, d): flows refresh uses flows table ts, not ohlcv."""

    def test_flows_refresh_uses_flows_table_ts_not_ohlcv(self):
        """REQ-022-1 (b): _fetch_flows_for_ticker calls _get_latest_flows_ts.

        With OHLCV ts=today and flows ts=today-5 (5d stale), the silent-skip
        bug WAS that flows refresh saw today's ohlcv ts and short-circuited.
        After fix, flows refresh must use its own latest_ts (today-5) and
        fetch (today-4, today) — NOT return 0.
        """
        from trading.scripts import refresh_market_data as mod

        today = date.today()
        flows_last_ts = today - timedelta(days=5)

        with (
            # OHLCV is fresh (today). If _fetch_flows_for_ticker still depended
            # on this helper, it would short-circuit -> 0 (the bug).
            patch.object(mod, "_get_latest_ohlcv_ts", return_value=today),
            # Flows latest_ts is 5 days stale -> fix must use this.
            patch.object(mod, "_get_latest_flows_ts", return_value=flows_last_ts) as flows_ts_mock,
            patch.object(mod, "_pykrx_fetch_flows", return_value=5) as fetcher,
        ):
            rows = mod._fetch_flows_for_ticker("005930", today_override=today)

        # Must use _get_latest_flows_ts (NOT _get_latest_ohlcv_ts).
        flows_ts_mock.assert_called_once_with("005930")
        # Fetch must NOT be skipped — flows is stale, so we pull (last+1d, today).
        assert rows == 5
        ticker_arg, start_arg, end_arg = fetcher.call_args.args
        assert ticker_arg == "005930"
        assert start_arg == flows_last_ts + timedelta(days=1)
        assert end_arg == today

    def test_flows_refresh_full_backfill_when_flows_empty(self):
        """REQ-022-1 (c) + Scenario 5: new ticker with no flows rows triggers
        full BACKFILL_WINDOW_DAYS backfill."""
        from trading.scripts import refresh_market_data as mod

        today = date.today()

        with (
            # New ticker — flows has no rows.
            patch.object(mod, "_get_latest_flows_ts", return_value=None),
            patch.object(mod, "_pykrx_fetch_flows", return_value=90) as fetcher,
        ):
            rows = mod._fetch_flows_for_ticker("281820", today_override=today)

        assert rows == 90
        _, start_arg, end_arg = fetcher.call_args.args
        assert start_arg == today - timedelta(days=mod.BACKFILL_WINDOW_DAYS)
        assert end_arg == today


class TestRefreshFundamentals:
    """REQ-019-3: Weekly fundamentals refresh."""

    def test_refresh_fundamentals_calls_per_ticker(self):
        from trading.scripts import refresh_market_data as mod

        with (
            patch.object(mod, "get_data_universe", return_value=["005930", "005380"]),
            patch.object(mod, "_fetch_fundamentals_for_ticker", return_value=7) as fetcher,
        ):
            metrics = mod.refresh_fundamentals()

        assert fetcher.call_count == 2
        assert metrics["total_rows_upserted"] == 14


# ---------------------------------------------------------------------------
# REQ-019-4: DART disclosures refresh with gap auto-detection
# ---------------------------------------------------------------------------


class TestRefreshDisclosures:
    """REQ-019-4: DART disclosure refresh with gap auto-backfill."""

    def test_refresh_disclosures_normal_one_day(self):
        """Default mode: fetch (today-1, today)."""
        from trading.scripts import refresh_market_data as mod

        today = date(2026, 5, 11)
        # latest disclosure is fresh (today - 1)
        with (
            patch.object(mod, "_get_latest_disclosure_ts", return_value=today - timedelta(days=1)),
            patch.object(mod, "_dart_list_recent", return_value=[{"a": 1}]) as fetcher,
        ):
            metrics = mod.refresh_disclosures(today_override=today)

        fetcher.assert_called_once_with(today - timedelta(days=1), today)
        assert metrics["total_rows_upserted"] == 1

    def test_refresh_disclosures_auto_gap_backfill_12days(self):
        """REQ-019-4 (c): when latest is older than today-2, switch to 12d backfill."""
        from trading.scripts import refresh_market_data as mod

        today = date(2026, 5, 11)
        # Gap: latest is 11 days stale (2026-04-30)
        gap_latest = today - timedelta(days=11)
        with (
            patch.object(mod, "_get_latest_disclosure_ts", return_value=gap_latest),
            patch.object(mod, "_dart_list_recent", return_value=[{"x": 1}] * 50) as fetcher,
        ):
            metrics = mod.refresh_disclosures(today_override=today)

        # Should call with (today - 12, today)
        fetcher.assert_called_once_with(today - timedelta(days=12), today)
        assert metrics["backfill_mode"] is True

    def test_refresh_disclosures_first_run_empty_cache_triggers_backfill(self):
        """REQ-019-4 (d): first run with no cached rows triggers gap mode."""
        from trading.scripts import refresh_market_data as mod

        today = date(2026, 5, 11)
        with (
            patch.object(mod, "_get_latest_disclosure_ts", return_value=None),
            patch.object(mod, "_dart_list_recent", return_value=[]) as fetcher,
        ):
            metrics = mod.refresh_disclosures(today_override=today)

        # Empty cache also treated as backfill
        fetcher.assert_called_once_with(today - timedelta(days=12), today)
        assert metrics["backfill_mode"] is True


# ---------------------------------------------------------------------------
# REQ-019-7 (P0 escalated): Bootstrap backfill on empty tables
# ---------------------------------------------------------------------------


class TestBootstrapBackfill:
    """REQ-019-7: bootstrap backfill when any data table is empty."""

    def test_bootstrap_triggers_when_ohlcv_empty(self):
        """When ohlcv row count is 0, bootstrap_backfill_if_empty triggers full refresh."""
        from trading.scripts import refresh_market_data as mod

        with (
            patch.object(mod, "_count_rows", side_effect=lambda tbl: 0 if tbl == "ohlcv" else 100),
            patch.object(mod, "refresh_ohlcv") as ro,
            patch.object(mod, "refresh_flows"),
            patch.object(mod, "refresh_fundamentals"),
            patch.object(mod, "refresh_disclosures"),
        ):
            result = mod.bootstrap_backfill_if_empty()

        # At least OHLCV refresh fired
        ro.assert_called_once()
        # `result` reports the bootstrap was triggered
        assert result["bootstrapped"] is True

    def test_bootstrap_skips_when_all_tables_have_data(self):
        """REQ-019-7 (d): non-empty tables should NOT trigger bootstrap."""
        from trading.scripts import refresh_market_data as mod

        with (
            patch.object(mod, "_count_rows", return_value=100),
            patch.object(mod, "refresh_ohlcv") as ro,
            patch.object(mod, "refresh_flows") as rf,
            patch.object(mod, "refresh_fundamentals") as rfu,
            patch.object(mod, "refresh_disclosures") as rd,
        ):
            result = mod.bootstrap_backfill_if_empty()

        ro.assert_not_called()
        rf.assert_not_called()
        rfu.assert_not_called()
        rd.assert_not_called()
        assert result["bootstrapped"] is False


# ---------------------------------------------------------------------------
# REQ-019-8: Per-ticker timeout budget
# ---------------------------------------------------------------------------


class TestPerTickerTimeout:
    """REQ-019-8: per-ticker fetch timeout (default 10s)."""

    def test_timeout_is_recorded_in_metrics(self):
        """When a ticker fetch exceeds budget, ticker is skipped + counted."""
        from trading.scripts import refresh_market_data as mod

        def _slow(ticker, **_kw):
            if ticker == "SLOW":
                raise mod.TickerTimeout("exceeded budget")
            return 3

        with (
            patch.object(mod, "get_data_universe", return_value=["A", "SLOW", "B"]),
            patch.object(mod, "_fetch_ohlcv_for_ticker", side_effect=_slow),
        ):
            metrics = mod.refresh_ohlcv()

        assert metrics["timeout_count"] == 1
        assert metrics["success_count"] == 2


# ---------------------------------------------------------------------------
# Scheduler registration tests (5 new cron jobs)
# ---------------------------------------------------------------------------


class TestSchedulerRegistration:
    """REQ-019-1/2/3/4/5 wiring: 5 new cron jobs in scheduler/runner.py."""

    def test_main_registers_five_new_data_jobs(self):
        """`runner.main()` registers 5 SPEC-019 cron jobs with correct ids."""
        from trading.scheduler import runner

        fake_sched = MagicMock()
        fake_sched.add_job = MagicMock()

        with (
            patch("trading.scheduler.runner.BlockingScheduler", return_value=fake_sched),
            patch("trading.scheduler.runner.signal.signal"),
            patch.object(fake_sched, "start"),
        ):
            runner.main()

        # Inspect all add_job calls; check for SPEC-019 job ids
        registered_ids = []
        for c in fake_sched.add_job.call_args_list:
            jid = c.kwargs.get("id")
            if jid is None and len(c.args) >= 3:
                # positional id (3rd arg) — fallback
                jid = c.args[2]
            if jid:
                registered_ids.append(jid)

        expected = {
            "data_refresh_ohlcv",
            "data_refresh_flows",
            "data_refresh_fundamentals",
            "data_refresh_disclosures",
            "data_freshness_check",
        }
        missing = expected - set(registered_ids)
        assert not missing, f"Missing SPEC-019 cron jobs: {missing}"

    def test_run_batch_enforces_per_ticker_timeout(self, monkeypatch):
        """REQ-019-8: _run_batch가 종목별 타임아웃을 실제로 강제해야 한다.

        REFRESH_PER_TICKER_TIMEOUT=0.2s, 종목 A는 5초 블로킹, 종목 B는 즉시 반환.
        _run_batch가 몇 초 내에 반환하고 timeout_count=1, error_count=1, success_count=1.

        OLD CODE: fetcher(ticker)를 직접 호출 → A가 5초 동안 블로킹 → 전체 배치 hang.
        NEW CODE: _call_with_timeout으로 감싸서 0.2s 후 TickerTimeout 발생 → 빠른 반환.
        """
        import threading
        import time

        from trading.scripts import refresh_market_data as mod

        monkeypatch.setenv("REFRESH_PER_TICKER_TIMEOUT", "0.2")

        def _blocking_fetcher(ticker: str) -> int:
            if ticker == "A":
                # 5초 블로킹 — 타임아웃이 없으면 배치 전체가 hang
                threading.Event().wait(5)
                return 1
            return 1  # B: 즉시 반환

        start = time.monotonic()
        metrics = mod._run_batch("test_timeout", _blocking_fetcher, ["A", "B"])
        elapsed = time.monotonic() - start

        assert elapsed < 3.0, (
            f"_run_batch가 {elapsed:.1f}s 걸림 — 타임아웃이 작동하지 않음 (OLD CODE: hang)"
        )
        assert metrics["timeout_count"] == 1, (
            f"timeout_count={metrics['timeout_count']} — A가 타임아웃으로 잡혀야 함"
        )
        assert metrics["error_count"] == 1, (
            f"error_count={metrics['error_count']} — 타임아웃은 error로도 집계되어야 함"
        )
        assert metrics["success_count"] == 1, (
            f"success_count={metrics['success_count']} — B는 성공해야 함"
        )

    def test_scheduler_main_invokes_bootstrap_backfill(self):
        """REQ-019-7: container start triggers bootstrap_backfill_if_empty."""
        from trading.scheduler import runner

        fake_sched = MagicMock()
        fake_sched.start = MagicMock()
        with (
            patch("trading.scheduler.runner.BlockingScheduler", return_value=fake_sched),
            patch("trading.scheduler.runner.signal.signal"),
            patch("trading.scripts.refresh_market_data.bootstrap_backfill_if_empty") as boot,
        ):
            runner.main()

        boot.assert_called_once()
