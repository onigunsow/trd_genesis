"""SPEC-TRADING-023 REQ-023-1/3/4: orchestrator auto-expansion hook tests.

These tests validate the public ``expand_universe_for_tickers()`` entrypoint
in refresh_market_data, which is what the orchestrator calls between micro
and decision personas. The orchestrator integration itself is also exercised
end-to-end via _filter_and_expand_candidates() which is the small helper we
introduce in orchestrator.py.

Scenarios covered (from acceptance.md):
- Scenario 1: 281820 universe-out candidate -> fetch + register
- Scenario 3: delisted ticker fetch fails -> dropped, not registered
- Scenario 5: per-ticker timeout -> dropped
- R-1: blocked filter must run AFTER auto-expansion
- R-2: candidates already in universe (recent OHLCV) -> no fetch triggered
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Recorder:
    """Records calls to pykrx wrappers and dynamic_universe.register."""

    def __init__(self) -> None:
        self.fetch_ohlcv_calls: list[tuple[str, date, date]] = []
        self.fetch_flows_calls: list[tuple[str, date, date]] = []
        self.registered: list[tuple[str, str]] = []
        self.raise_for_ticker: dict[str, Exception] = {}
        self.sleep_for_ticker: dict[str, float] = {}

    def fetch_ohlcv(self, ticker: str, start: date, end: date) -> int:
        self.fetch_ohlcv_calls.append((ticker, start, end))
        if ticker in self.raise_for_ticker:
            raise self.raise_for_ticker[ticker]
        if ticker in self.sleep_for_ticker:
            import time

            time.sleep(self.sleep_for_ticker[ticker])
        return 60  # 60 trading days approximated

    def fetch_flows(self, ticker: str, start: date, end: date) -> int:
        self.fetch_flows_calls.append((ticker, start, end))
        if ticker in self.raise_for_ticker:
            raise self.raise_for_ticker[ticker]
        return 60

    def register(self, ticker: str, source: str) -> bool:
        self.registered.append((ticker, source))
        return True

    def list_active(self) -> list[str]:
        return sorted(t for t, _ in self.registered)


def _patch_expansion(rec: _Recorder, latest_ohlcv_returns: dict[str, date | None]):
    """Convenience: returns a stack of patches injecting `rec` everywhere."""

    def _latest(ticker: str) -> date | None:
        return latest_ohlcv_returns.get(ticker)

    return (
        patch(
            "trading.scripts.refresh_market_data._pykrx_fetch_ohlcv",
            side_effect=rec.fetch_ohlcv,
        ),
        patch(
            "trading.scripts.refresh_market_data._pykrx_fetch_flows",
            side_effect=rec.fetch_flows,
        ),
        patch(
            "trading.scripts.refresh_market_data._get_latest_ohlcv_ts",
            side_effect=_latest,
        ),
        patch(
            "trading.scripts.refresh_market_data._register_dynamic_ticker",
            side_effect=rec.register,
        ),
    )


# ---------------------------------------------------------------------------
# expand_universe_for_tickers() — happy path + edge cases
# ---------------------------------------------------------------------------


class TestExpandUniverseForTickersHappyPath:
    """REQ-023-1 (c, d), REQ-023-2 (b): 281820 scenario."""

    def test_universe_out_candidate_triggers_fetch_and_register(self):
        from trading.scripts.refresh_market_data import (
            expand_universe_for_tickers,
        )

        rec = _Recorder()
        patches = _patch_expansion(rec, latest_ohlcv_returns={"281820": None})
        with patches[0], patches[1], patches[2], patches[3]:
            metrics = expand_universe_for_tickers(
                ["281820"], cycle_kind="pre_market"
            )

        # OHLCV + flows fetched.
        tickers_ohlcv = [c[0] for c in rec.fetch_ohlcv_calls]
        tickers_flows = [c[0] for c in rec.fetch_flows_calls]
        assert "281820" in tickers_ohlcv
        assert "281820" in tickers_flows
        # Registered to dynamic_tickers.
        assert ("281820", "micro_recommendation") in rec.registered
        # Metric shape.
        assert metrics["success_count"] == 1
        assert metrics["error_count"] == 0
        assert metrics["timeout_count"] == 0
        assert "281820" in metrics["successful_tickers"]

    def test_recent_ohlcv_skips_fetch(self):
        """REQ-023-1 (b): ticker with recent OHLCV (<7d) must NOT trigger fetch."""
        from trading.scripts.refresh_market_data import (
            expand_universe_for_tickers,
        )

        rec = _Recorder()
        today = date.today()
        patches = _patch_expansion(
            rec,
            latest_ohlcv_returns={"005930": today - timedelta(days=1)},
        )
        with patches[0], patches[1], patches[2], patches[3]:
            metrics = expand_universe_for_tickers(
                ["005930"], cycle_kind="pre_market"
            )

        assert rec.fetch_ohlcv_calls == []
        assert rec.fetch_flows_calls == []
        assert rec.registered == []
        # Already-fresh tickers count as success (no work needed).
        assert "005930" in metrics["successful_tickers"]


# ---------------------------------------------------------------------------
# REQ-023-3 — failure handling
# ---------------------------------------------------------------------------


class TestExpandUniverseFailureHandling:
    """REQ-023-3: delisted / network error -> drop, never register."""

    def test_fetch_failure_drops_ticker_and_does_not_register(self):
        from trading.scripts.refresh_market_data import (
            expand_universe_for_tickers,
        )

        rec = _Recorder()
        rec.raise_for_ticker["XXXXXX"] = KeyError("delisted")
        patches = _patch_expansion(rec, latest_ohlcv_returns={"XXXXXX": None})
        with patches[0], patches[1], patches[2], patches[3]:
            metrics = expand_universe_for_tickers(
                ["XXXXXX"], cycle_kind="intraday"
            )

        # Not registered.
        assert rec.registered == []
        # Metric reflects failure.
        assert metrics["success_count"] == 0
        assert metrics["error_count"] == 1
        assert "XXXXXX" not in metrics["successful_tickers"]

    def test_mixed_batch_isolates_per_ticker_failure(self):
        """REQ-023-3 (d): one failure must NOT abort the whole batch."""
        from trading.scripts.refresh_market_data import (
            expand_universe_for_tickers,
        )

        rec = _Recorder()
        rec.raise_for_ticker["BAD"] = RuntimeError("network")
        patches = _patch_expansion(
            rec,
            latest_ohlcv_returns={
                "GOOD1": None,
                "BAD": None,
                "GOOD2": None,
            },
        )
        with patches[0], patches[1], patches[2], patches[3]:
            metrics = expand_universe_for_tickers(
                ["GOOD1", "BAD", "GOOD2"], cycle_kind="pre_market"
            )

        registered_tickers = {t for t, _ in rec.registered}
        assert "GOOD1" in registered_tickers
        assert "GOOD2" in registered_tickers
        assert "BAD" not in registered_tickers
        assert metrics["success_count"] == 2
        assert metrics["error_count"] == 1


# ---------------------------------------------------------------------------
# REQ-023-4 — timeout
# ---------------------------------------------------------------------------


class TestExpandUniverseTimeout:
    """REQ-023-4: per-ticker + total batch timeout."""

    def test_per_ticker_timeout_drops_slow_ticker(self):
        """Slow ticker is aborted; remaining tickers proceed normally."""
        from trading.scripts.refresh_market_data import (
            expand_universe_for_tickers,
        )

        rec = _Recorder()
        # Use a small per-ticker budget so the test stays fast.
        rec.sleep_for_ticker["SLOW"] = 0.5

        patches = _patch_expansion(
            rec,
            latest_ohlcv_returns={"FAST": None, "SLOW": None},
        )
        with patches[0], patches[1], patches[2], patches[3]:
            metrics = expand_universe_for_tickers(
                ["FAST", "SLOW"],
                cycle_kind="pre_market",
                per_ticker_timeout_s=0.1,  # << shorter than sleep
                total_timeout_s=60,
            )

        # SLOW is dropped (timeout); FAST succeeds.
        registered_tickers = {t for t, _ in rec.registered}
        assert "FAST" in registered_tickers
        assert "SLOW" not in registered_tickers
        assert metrics["timeout_count"] == 1
        assert "SLOW" not in metrics["successful_tickers"]


# ---------------------------------------------------------------------------
# Orchestrator integration — auto-expansion runs BEFORE blocked filter
# ---------------------------------------------------------------------------


class TestOrchestratorIntegration:
    """SPEC-023 R-1 / R-2: orchestrator hook semantics.

    The orchestrator helper ``_filter_and_expand_candidates`` (introduced by
    SPEC-023) takes a candidate-ticker list and the current blocked dict, and
    returns (filtered_candidates, expansion_metrics). It must:
    1. Call expand_universe_for_tickers() for tickers lacking recent OHLCV.
    2. Drop tickers whose expansion failed.
    3. Apply the blocked filter AFTER expansion (so data is still fetched
       for blocked tickers — they may be unblocked later).
    """

    def test_blocked_filter_runs_after_expansion(self):
        """R-1: 281820 is blocked, yet auto-expansion must still fire so the
        OHLCV exists for the next cycle when blocked is cleared."""
        from trading.personas import orchestrator

        calls: list[tuple[list[str], str]] = []

        def _fake_expand(tickers, *, cycle_kind, **kwargs):
            calls.append((list(tickers), cycle_kind))
            return {
                "cycle_kind": cycle_kind,
                "requested_tickers": list(tickers),
                "success_count": len(tickers),
                "error_count": 0,
                "timeout_count": 0,
                "total_rows_upserted": 120 * len(tickers),
                "duration_ms": 0,
                "dynamic_universe_size": 1,
                "successful_tickers": list(tickers),
            }

        with (
            patch.object(orchestrator, "expand_universe_for_tickers", _fake_expand),
            patch.object(
                orchestrator,
                "_has_recent_ohlcv",
                side_effect=lambda t: False,
            ),
        ):
            filtered, _metrics = orchestrator._filter_and_expand_candidates(
                ["281820"],
                cycle_kind="pre_market",
                blocked_tickers={"281820": {"reason": "단기과열"}},
            )

        # 1. expand_universe_for_tickers was called WITH the blocked ticker.
        assert calls == [(["281820"], "pre_market")]
        # 2. blocked filter then removed 281820 from candidates.
        assert "281820" not in filtered

    def test_already_in_universe_no_expansion(self):
        """R-2: when all candidates have recent OHLCV, expansion is skipped."""
        from trading.personas import orchestrator

        called = {"n": 0}

        def _fake_expand(tickers, *, cycle_kind, **kwargs):
            called["n"] += 1
            return {"successful_tickers": list(tickers)}

        with (
            patch.object(orchestrator, "expand_universe_for_tickers", _fake_expand),
            patch.object(
                orchestrator,
                "_has_recent_ohlcv",
                side_effect=lambda t: True,
            ),
        ):
            filtered, metrics = orchestrator._filter_and_expand_candidates(
                ["005930", "000660"],
                cycle_kind="pre_market",
                blocked_tickers={},
            )

        assert called["n"] == 0
        assert filtered == ["005930", "000660"]
        assert metrics is None

    def test_expansion_failure_drops_failed_ticker(self):
        """Tickers whose expansion fetch failed must NOT reach decision."""
        from trading.personas import orchestrator

        def _fake_expand(tickers, *, cycle_kind, **kwargs):
            return {
                "cycle_kind": cycle_kind,
                "requested_tickers": list(tickers),
                "success_count": 1,
                "error_count": 1,
                "timeout_count": 0,
                "successful_tickers": ["GOOD"],
            }

        def _has_recent(t: str) -> bool:
            return False  # all need expansion

        with (
            patch.object(orchestrator, "expand_universe_for_tickers", _fake_expand),
            patch.object(orchestrator, "_has_recent_ohlcv", side_effect=_has_recent),
        ):
            filtered, metrics = orchestrator._filter_and_expand_candidates(
                ["GOOD", "BAD"],
                cycle_kind="intraday",
                blocked_tickers={},
            )

        assert "GOOD" in filtered
        assert "BAD" not in filtered
        assert metrics is not None
        assert metrics["error_count"] == 1
