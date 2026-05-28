"""SPEC-TRADING-029 Phase C — CLI subcommand ``trading fill-sync``.

REQ-029-5: expose a manual / backfill entry point that mirrors the scheduler
cron. The ``--dry-run`` flag must propagate so the first deploy can preview
the intended transitions for today's already-submitted orders without writing
to the DB. ``--start`` is accepted for forward compatibility but not yet
implemented; it must warn and continue rather than error.

These tests stay fully offline by mocking ``KisClient`` and
``trading.kis.fills.fill_sync``.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest


def _stub_settings() -> MagicMock:
    s = MagicMock()
    s.trading_mode = "PAPER"
    return s


@pytest.fixture
def patch_kis_and_fill_sync():
    """Yield ``(client_cls, fill_sync_fn)`` mocks with default success return.

    Default return mirrors the spec orchestrator's contract so tests only need
    to override ``side_effect`` / ``return_value`` for the relevant scenario.
    """
    with (
        patch("trading.config.get_settings", return_value=_stub_settings()),
        patch("trading.kis.client.KisClient") as client_cls,
        patch(
            "trading.kis.fills.fill_sync",
            return_value={
                "queried": 0,
                "transitioned": 0,
                "errors": 0,
                "dry_run": False,
            },
        ) as fill_sync_fn,
    ):
        yield client_cls, fill_sync_fn


# ---------------------------------------------------------------------------
# Dispatch + flag propagation
# ---------------------------------------------------------------------------


class TestFillSyncSubcommand:
    """REQ-029-5: ``trading fill-sync`` invokes the orchestrator."""

    def test_fill_sync_subcommand_calls_fill_sync_module(
        self, patch_kis_and_fill_sync
    ):
        """``trading fill-sync`` (no flags) → fill_sync(client, dry_run=False)."""
        client_cls, fill_sync_fn = patch_kis_and_fill_sync
        from trading.cli import main

        rc = main(["fill-sync"])

        assert rc == 0
        client_cls.assert_called_once()
        fill_sync_fn.assert_called_once()
        # Positional client arg + dry_run keyword
        _, kwargs = fill_sync_fn.call_args
        assert kwargs.get("dry_run") is False

    def test_fill_sync_drives_balance_reconcile(self):
        """SPEC-029 v0.2.0: ``trading fill-sync`` resolves to balance reconcile."""
        with (
            patch("trading.config.get_settings", return_value=_stub_settings()),
            patch("trading.kis.client.KisClient"),
            patch(
                "trading.kis.fills.reconcile_from_balance",
                return_value={
                    "queried": 0, "transitioned": 0, "errors": 0, "dry_run": False,
                },
            ) as reconcile,
        ):
            from trading.cli import main

            rc = main(["fill-sync"])

        assert rc == 0
        reconcile.assert_called_once()

    def test_fill_sync_dry_run_flag_propagates(self, patch_kis_and_fill_sync):
        """``trading fill-sync --dry-run`` → fill_sync(client, dry_run=True)."""
        _, fill_sync_fn = patch_kis_and_fill_sync
        fill_sync_fn.return_value = {
            "queried": 0,
            "transitioned": 0,
            "errors": 0,
            "dry_run": True,
        }
        from trading.cli import main

        rc = main(["fill-sync", "--dry-run"])

        assert rc == 0
        _, kwargs = fill_sync_fn.call_args
        assert kwargs.get("dry_run") is True


# ---------------------------------------------------------------------------
# stdout summary
# ---------------------------------------------------------------------------


class TestFillSyncStdout:
    """Operator-visible summary line must include the spec counters."""

    def test_fill_sync_prints_summary_to_stdout(
        self, patch_kis_and_fill_sync, capsys
    ):
        _, fill_sync_fn = patch_kis_and_fill_sync
        fill_sync_fn.return_value = {
            "queried": 5,
            "transitioned": 4,
            "errors": 1,
            "dry_run": False,
        }
        from trading.cli import main

        rc = main(["fill-sync"])
        out = capsys.readouterr().out

        assert rc == 0
        assert "queried=5" in out
        assert "transitioned=4" in out
        assert "errors=1" in out
        assert "dry_run=False" in out


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------


class TestFillSyncExitCodes:
    """Exit code 0 on success, 1 on KisError / RuntimeError."""

    def test_fill_sync_returns_zero_on_success(self, patch_kis_and_fill_sync):
        from trading.cli import main

        rc = main(["fill-sync"])

        assert rc == 0

    def test_fill_sync_returns_one_on_kis_error(
        self, patch_kis_and_fill_sync, capsys
    ):
        """KisError → exit code 1 and a stderr diagnostic."""
        _, fill_sync_fn = patch_kis_and_fill_sync
        from trading.kis.client import KisError, KisResponse

        resp = KisResponse(
            status_code=200,
            rt_cd="1",
            msg_cd="EGW00000",
            msg="boom",
            output={},
            raw={},
        )
        fill_sync_fn.side_effect = KisError(resp)
        from trading.cli import main

        rc = main(["fill-sync"])
        captured = capsys.readouterr()

        assert rc == 1
        assert "fill-sync" in captured.err.lower() or "kis" in captured.err.lower()

    def test_fill_sync_returns_one_on_runtime_error(
        self, patch_kis_and_fill_sync, capsys
    ):
        """Any RuntimeError → exit code 1 (no traceback leaked to stderr)."""
        _, fill_sync_fn = patch_kis_and_fill_sync
        fill_sync_fn.side_effect = RuntimeError("DB down")
        from trading.cli import main

        rc = main(["fill-sync"])
        captured = capsys.readouterr()

        assert rc == 1
        assert captured.err  # stderr non-empty


# ---------------------------------------------------------------------------
# Flag handling
# ---------------------------------------------------------------------------


class TestFillSyncFlags:
    """Unknown / not-yet-implemented flags must not break the happy path."""

    def test_unknown_flag_handling(
        self, patch_kis_and_fill_sync, caplog
    ):
        """Unknown flags log a warning but the command still runs."""
        _, fill_sync_fn = patch_kis_and_fill_sync
        from trading.cli import main

        with caplog.at_level(logging.WARNING):
            rc = main(["fill-sync", "--bogus"])

        assert rc == 0
        # fill_sync should still have been invoked
        fill_sync_fn.assert_called_once()
        msgs = " | ".join(r.getMessage() for r in caplog.records)
        assert "--bogus" in msgs or "unknown" in msgs.lower()

    def test_start_flag_warns_but_continues(
        self, patch_kis_and_fill_sync, caplog
    ):
        """``--start YYYYMMDD`` is accepted-but-not-yet-implemented per Phase C."""
        _, fill_sync_fn = patch_kis_and_fill_sync
        from trading.cli import main

        with caplog.at_level(logging.WARNING):
            rc = main(["fill-sync", "--start", "20260520"])

        assert rc == 0
        fill_sync_fn.assert_called_once()
        msgs = " | ".join(r.getMessage() for r in caplog.records)
        assert "--start" in msgs or "start" in msgs.lower()
