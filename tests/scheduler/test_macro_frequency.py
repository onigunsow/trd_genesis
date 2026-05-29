"""SPEC-TRADING-035 REQ-035-3 — daily macro persona cron registration tests.

The macro persona (the regime producer) previously ran only Friday 17:00 KST,
so the cached regime could be up to 7 days stale. REQ-035-3 adds a weekday
06:10 KST run that REUSES ``orchestrator.run_weekly_macro``'s existing CLI path
(zero added cost), while keeping the Friday weekly job.

Verified with the same FakeScheduler capture pattern as the other cron tests
(test_position_watchdog_cron.py) — no live scheduler, no network.

@MX:SPEC: SPEC-TRADING-035
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestDailyMacroCronRegistration:
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

    def test_two_macro_persona_jobs_exist(self):
        """AC: weekday 06:10 (new) + Friday 17:00 (existing) macro persona jobs."""
        captured = self._run_main()
        macro_ids = {
            j["id"] for j in captured
            if j["id"] in ("weekly_macro", "daily_macro")
        }
        assert "weekly_macro" in macro_ids  # existing Friday job retained
        assert "daily_macro" in macro_ids   # new daily job

    def test_daily_macro_trigger_is_0610_kst_weekdays(self):
        captured = self._run_main()
        job = next(j for j in captured if j["id"] == "daily_macro")
        fields = {f.name: str(f) for f in job["trigger"].fields}
        # Q-4 resolution: 06:10 (after the 06:00 build_macro_context data job).
        assert fields["day_of_week"] == "mon-fri"
        assert fields["hour"] == "6"
        assert fields["minute"] == "10"
        assert "Asia/Seoul" in repr(job["trigger"])

    def test_weekly_macro_trigger_unchanged(self):
        captured = self._run_main()
        job = next(j for j in captured if j["id"] == "weekly_macro")
        fields = {f.name: str(f) for f in job["trigger"].fields}
        assert fields["day_of_week"] == "fri"
        assert fields["hour"] == "17"
        assert fields["minute"] == "0"

    def test_daily_macro_callback_reuses_run_weekly_macro_cli_path(self):
        """REQ-035-3(b): the 06:10 job routes through run_weekly_macro (the
        existing CLI path) — it does NOT call a paid bare persona path."""
        captured = self._run_main()
        job = next(j for j in captured if j["id"] == "daily_macro")

        from trading.scheduler import runner

        with (
            patch.object(runner, "is_trading_day", return_value=True),
            patch.object(runner.orchestrator, "run_weekly_macro") as rwm,
        ):
            job["fn"]()

        rwm.assert_called_once()

    def test_no_impact5_macro_news_trigger_job_added(self):
        """REQ-035-3(d): adaptive impact-5 macro news trigger is explicitly
        deferred — no such job id is registered by this SPEC."""
        captured = self._run_main()
        ids = {j["id"] for j in captured}
        assert "macro_impact5_trigger" not in ids
