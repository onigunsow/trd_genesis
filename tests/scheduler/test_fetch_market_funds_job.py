"""SPEC-TRADING-036 REQ-036-1 — fetch_market_funds daily cron registration.

The ECOS 901Y056 S23E/S23A series (신용융자/예탁금) must stay fresh so the
late-cycle defense signals can fire. A weekday 05:50 KST job (before the 06:00
ctx_macro build) refreshes them over a wide window. Monthly series -> daily
refresh is cheap and harmless.

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


class TestFetchMarketFundsCron(_Capture):
    def test_job_registered(self):
        captured = self._run_main()
        ids = {j["id"] for j in captured}
        assert "fetch_market_funds" in ids

    def test_trigger_is_0550_weekdays_kst(self):
        captured = self._run_main()
        job = next(j for j in captured if j["id"] == "fetch_market_funds")
        fields = {f.name: str(f) for f in job["trigger"].fields}
        assert fields["day_of_week"] == "mon-fri"
        assert fields["hour"] == "5"
        assert fields["minute"] == "50"
        assert "Asia/Seoul" in repr(job["trigger"])

    def test_runs_before_ctx_macro(self):
        # 05:50 must be earlier than the 06:00 ctx_macro data build.
        captured = self._run_main()
        funds = next(j for j in captured if j["id"] == "fetch_market_funds")
        macro = next(j for j in captured if j["id"] == "ctx_macro")
        f_fields = {f.name: str(f) for f in funds["trigger"].fields}
        m_fields = {f.name: str(f) for f in macro["trigger"].fields}
        funds_minutes = int(f_fields["hour"]) * 60 + int(f_fields["minute"])
        macro_minutes = int(m_fields["hour"]) * 60 + int(m_fields["minute"])
        assert funds_minutes < macro_minutes

    def test_callback_invokes_fetch_market_funds(self):
        captured = self._run_main()
        job = next(j for j in captured if j["id"] == "fetch_market_funds")

        from trading.scheduler import runner

        with (
            patch.object(runner, "is_trading_day", return_value=True),
            patch.object(runner, "_run_fetch_market_funds") as run,
        ):
            job["fn"]()

        assert run.call_count == 1
