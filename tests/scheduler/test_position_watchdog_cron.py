"""SPEC-TRADING-033 REQ-033-1 — position_watchdog cron registration tests.

@MX:SPEC: SPEC-TRADING-033
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestPositionWatchdogCronRegistration:
    """AC-10: position_watchdog */5 09-15 KST mon-fri job is registered."""

    def _capture_jobs(self):
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

    def _run_main(self):
        captured, fake_sched = self._capture_jobs()
        from trading.scheduler import runner

        with (
            patch.object(runner, "BlockingScheduler", fake_sched),
            patch.object(runner, "refresh_market_data") as _refresh_mod,
        ):
            _refresh_mod.bootstrap_backfill_if_empty = MagicMock()
            runner.main()
        return captured

    def test_position_watchdog_job_registered(self):
        captured = self._run_main()
        ids = {j["id"] for j in captured}
        assert "position_watchdog" in ids

    def test_position_watchdog_trigger_is_5min_09_15_kst_weekdays(self):
        captured = self._run_main()
        job = next(j for j in captured if j["id"] == "position_watchdog")
        fields = {f.name: str(f) for f in job["trigger"].fields}
        assert fields["day_of_week"] == "mon-fri"
        assert fields["hour"] == "9-15"
        assert fields["minute"] == "*/5"
        # KST timezone
        assert "Asia/Seoul" in repr(job["trigger"])

    def test_callback_invokes_poll_via_wrap(self):
        """The job callback routes through _wrap, which calls poll_position_watchdog."""
        captured = self._run_main()
        job = next(j for j in captured if j["id"] == "position_watchdog")

        from trading.scheduler import runner

        # _wrap is gated on is_trading_day(); force it True and assert the
        # watchdog poll fn is invoked through the registered lambda.
        with (
            patch.object(runner, "is_trading_day", return_value=True),
            patch.object(runner._watcher_position_watchdog, "poll_position_watchdog") as poll,
        ):
            job["fn"]()

        assert poll.call_count == 1
