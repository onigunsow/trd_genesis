"""SPEC-TRADING-024 REQ-024-1 — Adaptive intraday cron registration tests.

@MX:SPEC: SPEC-TRADING-024
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestAdaptiveCronRegistration:
    """REQ-024-1 v0.3.0: adaptive */15 is the single intraday source (legacy 4 slots removed in v0.3.0)."""

    def _capture_jobs(self):
        """Patch BlockingScheduler.add_job and capture (id, trigger) tuples."""
        captured = []

        class _FakeScheduler:
            def __init__(self, *_args, **_kwargs):
                pass

            def add_job(self, fn, trigger, id=None, name=None):
                captured.append({"id": id, "name": name, "trigger": trigger, "fn": fn})

            def start(self):
                pass

            def shutdown(self, wait=False):
                pass

        return captured, _FakeScheduler

    def test_legacy_four_intraday_crons_removed(self):
        """REQ-024-1 v0.3.0: legacy 09:30/11:00/13:30/14:30 slots removed; adaptive is sole source.

        These four slots double-fired run_intraday_cycle on */15 boundaries
        (observed 2026-05-18 09:30 KST), so they were removed in v0.3.0.
        """
        captured, fake_sched = self._capture_jobs()
        from trading.scheduler import runner

        with (
            patch.object(runner, "BlockingScheduler", fake_sched),
            patch.object(runner, "refresh_market_data") as _refresh_mod,
        ):
            _refresh_mod.bootstrap_backfill_if_empty = MagicMock()
            runner.main()

        ids = {j["id"] for j in captured}
        # Legacy 4 intraday slots removed
        assert "intraday_9_30" not in ids
        assert "intraday_11_0" not in ids
        assert "intraday_13_30" not in ids
        assert "intraday_14_30" not in ids
        # Adaptive */15 cron is the single intraday source
        assert "intraday_adaptive" in ids

    def test_adaptive_cron_registered(self):
        """REQ-024-1: a new job id 'intraday_adaptive' is registered."""
        captured, fake_sched = self._capture_jobs()
        from trading.scheduler import runner

        with (
            patch.object(runner, "BlockingScheduler", fake_sched),
            patch.object(runner, "refresh_market_data") as _refresh_mod,
        ):
            _refresh_mod.bootstrap_backfill_if_empty = MagicMock()
            runner.main()

        ids = {j["id"] for j in captured}
        assert "intraday_adaptive" in ids

    def test_watcher_cron_jobs_registered(self):
        """REQ-024-2/3/4: three watcher poller jobs registered."""
        captured, fake_sched = self._capture_jobs()
        from trading.scheduler import runner

        with (
            patch.object(runner, "BlockingScheduler", fake_sched),
            patch.object(runner, "refresh_market_data") as _refresh_mod,
        ):
            _refresh_mod.bootstrap_backfill_if_empty = MagicMock()
            runner.main()

        ids = {j["id"] for j in captured}
        assert "watcher_price_threshold" in ids
        assert "watcher_volume_anomaly" in ids
        assert "watcher_blocked_release" in ids
