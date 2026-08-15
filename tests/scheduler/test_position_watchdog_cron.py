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

    def test_position_watchdog_fires_every_5min_inside_session_only(self):
        """정규장 안에서만 5분 주기로 발사한다.

        2026-08-15 변경: 기존 hour="9-15" + minute="*/5" 는 hour×minute 곱이라
        15:30~15:55 에도 발사됐다. 마감이 15:30 이므로 그 구간의 워치독 매도는
        KIS 가 '장종료' 로 거부한다. 트리거 내부 필드가 아니라 실제 발사 시각을
        검증한다 — 필드 모양을 못 박으면 이 버그를 테스트가 다시 고착시킨다.
        """
        from datetime import datetime, timedelta

        import pytz

        captured = self._run_main()
        job = next(j for j in captured if j["id"] == "position_watchdog")
        trigger = job["trigger"]

        kst = pytz.timezone("Asia/Seoul")
        # 2026-08-14 (금) 하루치 발사 시각 열거
        cur = kst.localize(datetime(2026, 8, 14, 0, 0))
        end = kst.localize(datetime(2026, 8, 15, 0, 0))
        fires = []
        for _ in range(500):
            nxt = trigger.get_next_fire_time(None, cur)
            if nxt is None or nxt >= end:
                break
            fires.append(nxt)
            cur = nxt + timedelta(seconds=1)

        hhmm = [f.strftime("%H:%M") for f in fires]
        assert hhmm[0] == "09:00"
        assert hhmm[-1] == "15:25", "마감(15:30) 이후로는 발사하지 않아야 한다"
        assert "15:30" not in hhmm
        assert "15:55" not in hhmm
        # 5분 간격 유지
        assert "09:05" in hhmm and "12:35" in hhmm
        assert all(f.tzinfo is not None for f in fires)
        assert "Asia/Seoul" in str(fires[0].tzinfo)

    def test_position_watchdog_does_not_fire_on_weekend(self):
        """주말에는 발사하지 않는다 (2026-08-16 은 일요일)."""
        from datetime import datetime

        import pytz

        captured = self._run_main()
        job = next(j for j in captured if j["id"] == "position_watchdog")
        kst = pytz.timezone("Asia/Seoul")
        sunday = kst.localize(datetime(2026, 8, 16, 0, 0))
        nxt = job["trigger"].get_next_fire_time(None, sunday)
        assert nxt is None or nxt.weekday() < 5

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
