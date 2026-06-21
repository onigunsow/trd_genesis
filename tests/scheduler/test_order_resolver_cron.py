"""SPEC-TRADING-042 D3 — order_resolver cron 등록 테스트.

REQ-042-B1: submitted 주문을 5분 주기로 resolver 가 수렴.
감사 m3 반영: minute="2-59/5" 로 position_watchdog 과 desync.

test_position_watchdog_cron.py 패턴 미러.

@MX:SPEC: SPEC-TRADING-042
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestOrderResolverCronRegistration:
    """AC: order_resolver 잡이 2-59/5 09-15 KST mon-fri 로 등록됨."""

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

    def test_order_resolver_job_registered(self):
        """잡이 id='order_resolver' 로 등록돼야 한다."""
        captured = self._run_main()
        ids = {j["id"] for j in captured}
        assert "order_resolver" in ids

    def test_order_resolver_trigger_is_offset_09_15_kst_weekdays(self):
        """감사 m3: minute='2-59/5' 로 position_watchdog 와 desync."""
        captured = self._run_main()
        job = next(j for j in captured if j["id"] == "order_resolver")
        fields = {f.name: str(f) for f in job["trigger"].fields}
        assert fields["day_of_week"] == "mon-fri"
        assert fields["hour"] == "9-15"
        # 오프셋: 2,7,12,… (watchdog=0,5,10,… 와 desync)
        assert fields["minute"] == "2-59/5"
        assert "Asia/Seoul" in repr(job["trigger"])

    def test_callback_invokes_resolve_stuck_orders(self):
        """잡 콜백이 _wrap → _run_resolver → resolve_stuck_orders 경로를 탄다."""
        captured = self._run_main()
        job = next(j for j in captured if j["id"] == "order_resolver")

        from trading.scheduler import runner

        with (
            patch.object(runner, "is_trading_day", return_value=True),
            patch.object(runner, "_run_resolver") as mock_resolver,
        ):
            job["fn"]()

        assert mock_resolver.call_count == 1

    def test_order_resolver_and_position_watchdog_both_registered(self):
        """두 잡이 독립적으로 공존한다."""
        captured = self._run_main()
        ids = {j["id"] for j in captured}
        assert "order_resolver" in ids
        assert "position_watchdog" in ids
