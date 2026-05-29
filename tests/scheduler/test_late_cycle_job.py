"""SPEC-TRADING-036 REQ-036-3(h) — late-cycle 16:05 cron registration tests.

@MX:SPEC: SPEC-TRADING-036
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class _Capture:
    def _capture_jobs(self):
        captured = []

        class _FakeScheduler:
            def __init__(self, *_a, **_k):
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


class TestLateCycleCronRegistration(_Capture):
    def test_late_cycle_job_registered(self):
        captured = self._run_main()
        ids = {j["id"] for j in captured}
        assert "late_cycle" in ids

    def test_trigger_is_1605_weekdays_kst(self):
        captured = self._run_main()
        job = next(j for j in captured if j["id"] == "late_cycle")
        fields = {f.name: str(f) for f in job["trigger"].fields}
        assert fields["day_of_week"] == "mon-fri"
        assert fields["hour"] == "16"
        assert fields["minute"] == "5"
        assert "Asia/Seoul" in repr(job["trigger"])

    def test_callback_invokes_run_via_wrap(self):
        captured = self._run_main()
        job = next(j for j in captured if j["id"] == "late_cycle")

        from trading.scheduler import runner

        with (
            patch.object(runner, "is_trading_day", return_value=True),
            patch.object(runner._late_cycle, "run_late_cycle_evaluation") as run,
        ):
            job["fn"]()

        assert run.call_count == 1
