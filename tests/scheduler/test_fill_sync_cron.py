"""SPEC-TRADING-029 Phase C — APScheduler integration for fill_sync.

REQ-029-4: fill_sync runs every 60s on Mon-Fri 09:00-15:30 KST via APScheduler,
wrapped in the existing trading-day guard so failures log but never crash the
scheduler process.

These tests assert:

1. ``runner.main()`` registers a job with id ``"fill_sync"`` and a CronTrigger
   matching the spec (Mon-Fri, hour 9-15, minute *, second 0).
2. ``runner._run_fill_sync()`` constructs a ``KisClient`` from settings and
   invokes ``trading.kis.fills.fill_sync`` with ``dry_run=False``.
3. The result counters (queried / transitioned / errors) are emitted at INFO
   so operators can grep the container logs for cycle health.

The tests deliberately mock ``BlockingScheduler`` and the KIS client so the
suite stays fully offline.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _capture_jobs():
    """Return ``(captured, FakeScheduler)`` for use inside a ``patch.object``.

    Mirrors the pattern from ``tests/scheduler/test_adaptive_cron.py`` so the
    test reads like the existing scheduler tests.
    """
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


# ---------------------------------------------------------------------------
# Job registration (REQ-029-4)
# ---------------------------------------------------------------------------


class TestFillSyncCronRegistration:
    """REQ-029-4: scheduler registers a ``fill_sync`` job during main()."""

    def test_fill_sync_job_registered_in_main(self):
        """``main()`` adds a job with id ``fill_sync`` and the spec CronTrigger."""
        captured, fake_sched = _capture_jobs()
        from trading.scheduler import runner

        with (
            patch.object(runner, "BlockingScheduler", fake_sched),
            patch.object(runner, "refresh_market_data") as _refresh_mod,
        ):
            _refresh_mod.bootstrap_backfill_if_empty = MagicMock()
            runner.main()

        ids = {j["id"] for j in captured}
        assert "fill_sync" in ids, (
            f"Expected job id 'fill_sync' to be registered; got: {sorted(ids)}"
        )

        job = next(j for j in captured if j["id"] == "fill_sync")
        trigger = job["trigger"]

        # CronTrigger stringifies as e.g.
        #   cron[day_of_week='mon-fri', hour='9-15', minute='*', second='0', timezone='Asia/Seoul']
        # Inspect the fields directly so we are not sensitive to repr format.
        fields_by_name = {f.name: str(f) for f in trigger.fields}
        assert fields_by_name["day_of_week"] == "mon-fri"
        assert fields_by_name["hour"] == "9-15"
        assert fields_by_name["minute"] == "*"
        assert fields_by_name["second"] == "0"

    def test_fill_sync_job_uses_wrap_guard(self):
        """REQ-029-4: the job must be wrapped so non-trading days are skipped.

        The cleanest proxy for "wrapped in _wrap" is to call the registered
        lambda with ``is_trading_day`` returning False and confirm the inner
        ``_run_fill_sync`` is not invoked.
        """
        captured, fake_sched = _capture_jobs()
        from trading.scheduler import runner

        with (
            patch.object(runner, "BlockingScheduler", fake_sched),
            patch.object(runner, "refresh_market_data") as _refresh_mod,
        ):
            _refresh_mod.bootstrap_backfill_if_empty = MagicMock()
            runner.main()

        job = next(j for j in captured if j["id"] == "fill_sync")

        with (
            patch.object(runner, "is_trading_day", return_value=False),
            patch.object(runner, "_run_fill_sync") as inner,
        ):
            job["fn"]()

        inner.assert_not_called()


# ---------------------------------------------------------------------------
# _run_fill_sync wrapper
# ---------------------------------------------------------------------------


class TestRunFillSyncWrapper:
    """REQ-029-4: ``_run_fill_sync`` wires KisClient + fill_sync + logging."""

    def test_run_fill_sync_invokes_fills_module(self):
        """Helper builds KisClient(trading_mode) and calls fill_sync(client, dry_run=False)."""
        from trading.scheduler import runner

        fake_settings = MagicMock()
        fake_settings.trading_mode = "PAPER"
        fake_client = MagicMock(name="KisClient instance")

        with (
            patch("trading.config.get_settings", return_value=fake_settings),
            patch(
                "trading.kis.client.KisClient", return_value=fake_client
            ) as client_cls,
            patch(
                "trading.kis.fills.fill_sync",
                return_value={"queried": 0, "transitioned": 0, "errors": 0, "dry_run": False},
            ) as fill_sync_fn,
        ):
            runner._run_fill_sync()

        client_cls.assert_called_once_with(fake_settings.trading_mode)
        fill_sync_fn.assert_called_once_with(fake_client, dry_run=False)

    def test_run_fill_sync_drives_balance_reconcile(self):
        """SPEC-029 v0.2.0: the cron path resolves to balance reconcile.

        ``fill_sync`` now delegates to ``reconcile_from_balance``; patch the
        latter to prove the scheduler's data source is inquire-balance, not the
        deprecated inquire-daily-ccld.
        """
        from trading.scheduler import runner

        with (
            patch("trading.config.get_settings", return_value=MagicMock(trading_mode="PAPER")),
            patch("trading.kis.client.KisClient", return_value=MagicMock()),
            patch(
                "trading.kis.fills.reconcile_from_balance",
                return_value={
                    "queried": 0, "transitioned": 0, "errors": 0, "dry_run": False,
                },
            ) as reconcile,
        ):
            runner._run_fill_sync()

        reconcile.assert_called_once()
        _, kwargs = reconcile.call_args
        assert kwargs.get("dry_run") is False

    def test_run_fill_sync_logs_result_counts(self, caplog):
        """Logged INFO line must contain queried / transitioned / errors values."""
        from trading.scheduler import runner

        with (
            patch("trading.config.get_settings", return_value=MagicMock(trading_mode="PAPER")),
            patch("trading.kis.client.KisClient"),
            patch(
                "trading.kis.fills.fill_sync",
                return_value={
                    "queried": 3,
                    "transitioned": 2,
                    "errors": 0,
                    "dry_run": False,
                },
            ),
        ):
            with caplog.at_level("INFO", logger=runner.LOG.name):
                runner._run_fill_sync()

        msgs = " | ".join(r.getMessage() for r in caplog.records)
        assert "queried=3" in msgs, f"expected queried=3 in logs; got: {msgs}"
        assert "transitioned=2" in msgs, f"expected transitioned=2 in logs; got: {msgs}"
        assert "errors=0" in msgs, f"expected errors=0 in logs; got: {msgs}"
