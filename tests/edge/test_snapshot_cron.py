"""Edge Validation Phase 0 — 스케줄러 15:40 KST 스냅샷 잡 등록.

tests/scheduler/test_fill_sync_cron.py 의 패턴을 차용: BlockingScheduler 를 페이크로 바꿔
add_job 호출을 캡처하고, 거래일 가드(_wrap)를 검증한다.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _capture_jobs():
    captured: list[dict] = []

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


def _run_main():
    captured, fake_sched = _capture_jobs()
    from trading.scheduler import runner

    with (
        patch.object(runner, "BlockingScheduler", fake_sched),
        patch.object(runner, "refresh_market_data") as _refresh_mod,
    ):
        _refresh_mod.bootstrap_backfill_if_empty = MagicMock()
        runner.main()
    return captured


class TestSnapshotCron:
    def test_equity_snapshot_job_registered(self):
        captured = _run_main()
        ids = {j["id"] for j in captured}
        assert "equity_snapshot" in ids, f"got: {sorted(ids)}"

        job = next(j for j in captured if j["id"] == "equity_snapshot")
        fields = {f.name: str(f) for f in job["trigger"].fields}
        assert fields["day_of_week"] == "mon-fri"
        assert fields["hour"] == "15"
        assert fields["minute"] == "40"

    def test_job_uses_trading_day_guard(self):
        captured = _run_main()
        from trading.scheduler import runner

        job = next(j for j in captured if j["id"] == "equity_snapshot")
        with (
            patch.object(runner, "is_trading_day", return_value=False),
            patch.object(runner, "_run_equity_snapshot") as inner,
        ):
            job["fn"]()
        inner.assert_not_called()

    def test_run_equity_snapshot_calls_record(self):
        from trading.scheduler import runner

        with patch(
            "trading.edge.snapshot.record_snapshot",
            return_value={"trading_day": "2026-05-29", "total_assets": 1_000_000},
        ) as rec:
            runner._run_equity_snapshot()
        rec.assert_called_once()
