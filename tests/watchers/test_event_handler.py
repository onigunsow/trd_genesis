"""SPEC-TRADING-024 Stage 1 — event_handler unit tests.

@MX:SPEC: SPEC-TRADING-024
"""

from __future__ import annotations

from unittest.mock import patch


class TestHandleTriggerEvent:
    """Stage 1 entry point invoked by all 3 watchers."""

    def test_invokes_run_intraday_cycle_and_persists(self):
        from trading.watchers import event_handler

        with (
            patch.object(event_handler, "_persist_trigger_event") as persist,
            patch.object(event_handler, "_maybe_warn_budget"),
            patch("trading.personas.orchestrator.run_intraday_cycle") as cycle,
        ):
            event_handler.handle_trigger_event("005930", "price_threshold", {"atr_14": 1000.0})

        assert cycle.call_count == 1
        assert persist.call_count == 1
        args, _ = persist.call_args
        assert args[0] == "005930"
        assert args[1] == "price_threshold"
        assert args[2]["atr_14"] == 1000.0

    def test_handles_orchestrator_exception_gracefully(self):
        """If run_intraday_cycle raises, handler must not propagate.

        Watcher poll iteration must continue across other tickers/triggers.
        """
        from trading.watchers import event_handler

        with (
            patch.object(event_handler, "_persist_trigger_event"),
            patch.object(event_handler, "_maybe_warn_budget"),
            patch(
                "trading.personas.orchestrator.run_intraday_cycle",
                side_effect=RuntimeError("boom"),
            ),
        ):
            # Should not raise
            event_handler.handle_trigger_event("005930", "volume_anomaly", {"volume_ratio": 2.5})

    def test_concurrent_cycles_drop_overlap(self):
        """Second handler invocation while first holds the lock should skip."""
        from trading.watchers import event_handler

        # Pre-acquire the in-process lock to simulate a cycle in flight
        assert event_handler._CYCLE_LOCK.acquire(blocking=False) is True
        try:
            with (
                patch.object(event_handler, "_persist_trigger_event"),
                patch.object(event_handler, "_maybe_warn_budget"),
                patch("trading.personas.orchestrator.run_intraday_cycle") as cycle,
            ):
                event_handler.handle_trigger_event("005930", "price_threshold", {})
                # Cycle should be skipped because lock is held
                assert cycle.call_count == 0
        finally:
            event_handler._CYCLE_LOCK.release()

    def test_persist_swallows_db_failure(self):
        """_persist_trigger_event must not raise on DB error."""
        from trading.watchers import event_handler

        with patch("trading.db.session.connection", side_effect=RuntimeError("db down")):
            # Should return None, not raise
            event_handler._persist_trigger_event("005930", "price_threshold", {})

    def test_today_llm_cost_returns_zero_when_table_missing(self):
        """Stage 1: llm_cost_log table doesn't exist yet — silent 0."""
        from trading.watchers import event_handler

        with patch(
            "trading.db.session.connection",
            side_effect=RuntimeError("relation llm_cost_log does not exist"),
        ):
            assert event_handler._today_llm_cost_krw() == 0.0
