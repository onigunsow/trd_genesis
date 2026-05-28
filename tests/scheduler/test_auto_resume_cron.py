"""SPEC-TRADING-032 REQ-032-1 — pre-market auto-resume cron registration tests.

REQ-032-1 / AC-8: a job id ``premarket_auto_resume`` is registered with a
CronTrigger of day_of_week=mon-fri, hour=7, minute=25 (KST), running BEFORE the
07:30 pre_market job and wrapped in the existing trading-day guard.

Mirrors the FakeScheduler capture pattern from
``tests/scheduler/test_adaptive_cron.py`` so the suite stays fully offline.

@MX:SPEC: SPEC-TRADING-032
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _capture_jobs():
    """Return ``(captured, FakeScheduler)`` capturing add_job calls in order."""
    captured: list[dict] = []

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


class TestAutoResumeCronRegistration:
    """REQ-032-1 / AC-8."""

    def _run_main(self):
        captured, fake_sched = _capture_jobs()
        from trading.scheduler import runner

        with (
            patch.object(runner, "BlockingScheduler", fake_sched),
            patch.object(runner, "refresh_market_data") as _refresh_mod,
        ):
            _refresh_mod.bootstrap_backfill_if_empty = MagicMock()
            runner.main()
        return captured

    def test_premarket_auto_resume_job_registered(self):
        """A job id 'premarket_auto_resume' exists with the 07:25 mon-fri trigger."""
        captured = self._run_main()
        ids = {j["id"] for j in captured}
        assert "premarket_auto_resume" in ids, (
            f"Expected 'premarket_auto_resume' to be registered; got: {sorted(ids)}"
        )

        job = next(j for j in captured if j["id"] == "premarket_auto_resume")
        fields_by_name = {f.name: str(f) for f in job["trigger"].fields}
        assert fields_by_name["day_of_week"] == "mon-fri"
        assert fields_by_name["hour"] == "7"
        assert fields_by_name["minute"] == "25"

    def test_auto_resume_runs_before_pre_market(self):
        """AC-8: the auto-resume job is registered before the 07:30 pre_market job."""
        captured = self._run_main()
        order = [j["id"] for j in captured]
        assert "premarket_auto_resume" in order
        assert "pre_market" in order
        assert order.index("premarket_auto_resume") < order.index("pre_market")

    def test_auto_resume_job_uses_wrap_guard(self):
        """AC-8 / Q-3: the job is wrapped so non-trading days are skipped.

        Calling the registered lambda with is_trading_day() False must not invoke
        run_premarket_auto_resume.
        """
        captured = self._run_main()
        job = next(j for j in captured if j["id"] == "premarket_auto_resume")

        from trading.scheduler import runner

        with (
            patch.object(runner, "is_trading_day", return_value=False),
            patch.object(runner, "run_premarket_auto_resume") as inner,
        ):
            job["fn"]()

        inner.assert_not_called()

    def test_auto_resume_job_calls_entry_on_trading_day(self):
        """On a trading day the wrapped job invokes run_premarket_auto_resume."""
        captured = self._run_main()
        job = next(j for j in captured if j["id"] == "premarket_auto_resume")

        from trading.scheduler import runner

        with (
            patch.object(runner, "is_trading_day", return_value=True),
            patch.object(runner, "run_premarket_auto_resume") as inner,
        ):
            job["fn"]()

        inner.assert_called_once()
