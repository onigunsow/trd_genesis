"""SPEC-TRADING-024 REQ-024-1 — Adaptive intraday cron registration tests.

@MX:SPEC: SPEC-TRADING-024
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestAdaptiveCronRegistration:
    """REQ-024-1: adaptive intraday cron registered alongside existing 4 crons."""

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

    def test_existing_four_intraday_crons_preserved(self):
        """REQ-024-1 backward-compat (Q-8): original 09:30/11:00/13:30/14:30 retained."""
        captured, fake_sched = self._capture_jobs()
        from trading.scheduler import runner

        with (
            patch.object(runner, "BlockingScheduler", fake_sched),
            patch.object(runner, "refresh_market_data") as _refresh_mod,
        ):
            _refresh_mod.bootstrap_backfill_if_empty = MagicMock()
            runner.main()

        ids = {j["id"] for j in captured}
        # Original 4 intraday crons remain
        assert "intraday_9_30" in ids
        assert "intraday_11_0" in ids
        assert "intraday_13_30" in ids
        assert "intraday_14_30" in ids

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
