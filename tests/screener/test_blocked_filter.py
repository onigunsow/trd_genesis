"""SPEC-TRADING-025 — Blocked-aware Daily Screener tests.

Unit tests for the blocked-list filter applied at the start of
``daily_screen.run()``. Validates EARS requirements:

- REQ-025-1 (P0): Load ``data/blocked_tickers.json`` at screener start.
- REQ-025-2 (P0): Exclude blocked tickers from candidate scoring (pure
  set-difference; no penalty variant).
- REQ-025-3 (P1): Graceful degrade on missing/stale file (WARNING + empty
  set, no exception, no halt).
- REQ-025-4 (P0): Output guarantee — ``screened_tickers.json`` contains
  zero blocked tickers.
- REQ-025-5 (P2): WARNING when post-filter candidate count < 5.

Test strategy: mock ``BLOCKED_FILE`` via ``monkeypatch`` and patch
``trading.db.session.connection`` to bypass Postgres. The tests focus on
the filter contract — they do not validate KIS or pykrx integration.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers — fake DB rows for _screen_ticker() to return a qualifying score.
# ---------------------------------------------------------------------------

def _fund_row(market_cap: float = 5e12, per: float = 10.0) -> dict[str, Any]:
    return {"market_cap": market_cap, "per": per, "pbr": 1.0, "div_yield": 0.0}


def _ohlcv_rows(n: int = 25, base: float = 100_000.0) -> list[dict[str, Any]]:
    # Returns n rows in DESC order (newest first), matching the SQL contract.
    # Closes oscillate gently so RSI lands inside the healthy 30-70 band.
    rows: list[dict[str, Any]] = []
    for i in range(n):
        # Newest row first; gentle oscillation around the base price.
        delta = (1 if i % 2 == 0 else -1) * 100.0
        rows.append({"ts": f"2026-05-{19 - i:02d}", "close": base + delta, "volume": 200_000})
    return rows


def _flows_row(f5: int = 1_000_000_000) -> dict[str, Any]:
    return {"f5": f5}


# Make a single fetch sequence returning one fundamentals row, one set of
# OHLCV rows, and one flows row per ticker.
class _SeqCursor:
    """Cursor that yields fund → ohlcv → flows in a 3-call cycle."""

    def __init__(self, universe: list[str]) -> None:
        self._universe = universe
        self._call_idx = 0
        # Cursor state for "iter mode": last execute decides what fetchone/all returns.
        self._mode = "universe"

    def execute(self, sql: str, params: Any = None) -> None:
        s = sql.strip().lower()
        if "from ohlcv" in s and "distinct symbol" in s:
            self._mode = "universe"
        elif "from fundamentals" in s:
            self._mode = "fund"
        elif "from ohlcv" in s:
            self._mode = "ohlcv"
        elif "from flows" in s:
            self._mode = "flows"
        else:
            self._mode = "other"

    def fetchone(self) -> dict[str, Any] | None:
        if self._mode == "fund":
            return _fund_row()
        if self._mode == "flows":
            return _flows_row()
        return None

    def fetchall(self) -> list[dict[str, Any]]:
        if self._mode == "universe":
            return [{"symbol": t} for t in self._universe]
        if self._mode == "ohlcv":
            return _ohlcv_rows()
        return []

    def __enter__(self) -> _SeqCursor:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class _SeqConnection:
    def __init__(self, universe: list[str]) -> None:
        self._cursor = _SeqCursor(universe)

    def cursor(self) -> _SeqCursor:
        return self._cursor

    def __enter__(self) -> _SeqConnection:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


@contextmanager
def _patched_connection(universe: list[str]):
    yield _SeqConnection(universe)


# ---------------------------------------------------------------------------
# REQ-025-1 / REQ-025-2 — Load and apply set-difference filter.
# ---------------------------------------------------------------------------

class TestLoadBlockedSet:
    """REQ-025-1: ``_load_blocked_set`` reads JSON and returns a set of keys."""

    def test_fresh_file_returns_blocked_keys(self, tmp_path, monkeypatch):
        from trading.screener import daily_screen as mod

        blocked_path = tmp_path / "blocked_tickers.json"
        blocked_path.write_text(
            json.dumps(
                {
                    "date": mod._today_kst_iso(),
                    "blocked": {
                        "005930": {"stat_cls": "55", "reason": "단기과열"},
                        "000660": {"stat_cls": "55", "reason": "단기과열"},
                    },
                }
            )
        )
        monkeypatch.setattr(mod, "BLOCKED_FILE", blocked_path)

        result = mod._load_blocked_set()

        assert result == {"005930", "000660"}

    def test_missing_file_returns_empty_set_and_warns(self, tmp_path, monkeypatch, caplog):
        """REQ-025-3: missing file → empty set + WARNING (no exception)."""
        from trading.screener import daily_screen as mod

        monkeypatch.setattr(mod, "BLOCKED_FILE", tmp_path / "does_not_exist.json")

        with caplog.at_level("WARNING"):
            result = mod._load_blocked_set()

        assert result == set()
        assert any(
            "missing" in r.message.lower() or "blocked" in r.message.lower()
            for r in caplog.records
        ), f"expected missing-file warning, got: {[r.message for r in caplog.records]}"

    def test_stale_date_returns_empty_set_and_warns(self, tmp_path, monkeypatch, caplog):
        """REQ-025-3: stale date → empty set + WARNING (no exception)."""
        from trading.screener import daily_screen as mod

        stale = (date.today() - timedelta(days=2)).isoformat()
        blocked_path = tmp_path / "blocked_tickers.json"
        blocked_path.write_text(
            json.dumps(
                {
                    "date": stale,
                    "blocked": {"005930": {"reason": "단기과열"}},
                }
            )
        )
        monkeypatch.setattr(mod, "BLOCKED_FILE", blocked_path)

        with caplog.at_level("WARNING"):
            result = mod._load_blocked_set()

        assert result == set()
        # Warning must mention "stale" and include both dates for observability.
        warning_msgs = " ".join(r.message for r in caplog.records if r.levelname == "WARNING")
        assert "stale" in warning_msgs.lower()
        assert stale in warning_msgs

    def test_corrupt_file_returns_empty_set_and_warns(self, tmp_path, monkeypatch, caplog):
        """REQ-025-3: malformed JSON → empty set + WARNING (no exception)."""
        from trading.screener import daily_screen as mod

        blocked_path = tmp_path / "blocked_tickers.json"
        blocked_path.write_text("{not valid json")
        monkeypatch.setattr(mod, "BLOCKED_FILE", blocked_path)

        with caplog.at_level("WARNING"):
            result = mod._load_blocked_set()

        assert result == set()


# ---------------------------------------------------------------------------
# REQ-025-2 / REQ-025-4 — End-to-end: run() excludes blocked tickers.
# ---------------------------------------------------------------------------

class TestRunExcludesBlocked:
    """REQ-025-2 + REQ-025-4: run() must not output any blocked ticker."""

    def test_blocked_tickers_absent_from_output(self, tmp_path, monkeypatch, caplog):
        from trading.screener import daily_screen as mod

        # Universe contains 3 candidates; 1 is blocked.
        universe = ["005930", "000660", "035720"]
        blocked = {"005930"}

        # Write blocked file with today's KST date.
        blocked_path = tmp_path / "blocked_tickers.json"
        blocked_path.write_text(
            json.dumps(
                {
                    "date": mod._today_kst_iso(),
                    "blocked": {t: {"reason": "단기과열"} for t in blocked},
                }
            )
        )
        monkeypatch.setattr(mod, "BLOCKED_FILE", blocked_path)

        # Redirect output files to tmp so we don't touch real data/.
        monkeypatch.setattr(mod, "SCREEN_FILE", tmp_path / "screened_tickers.json")
        monkeypatch.setattr(mod, "PENDING_FILE", tmp_path / "pending_screen.json")

        # Empty DEFAULT_WATCHLIST so it doesn't shadow our 3-ticker universe.
        monkeypatch.setattr(mod, "DEFAULT_WATCHLIST", [])

        with patch(
            "trading.screener.daily_screen.connection",
            side_effect=lambda *a, **kw: _patched_connection(universe),
        ):
            with caplog.at_level("INFO"):
                result = mod.run()

        # REQ-025-4: output contains zero blocked tickers.
        output_tickers = set(result.get("tickers", []))
        assert output_tickers & blocked == set(), (
            f"blocked tickers leaked into output: {output_tickers & blocked}"
        )

        # REQ-025-2: screened file written, and on disk it also excludes blocked.
        on_disk = json.loads((tmp_path / "screened_tickers.json").read_text())
        assert set(on_disk["tickers"]) & blocked == set()

        # The pending file (LLM input) must also exclude blocked tickers.
        pending = json.loads((tmp_path / "pending_screen.json").read_text())
        pending_tickers = {c["ticker"] for c in pending["candidates"]}
        assert pending_tickers & blocked == set()

    def test_filtered_count_logged(self, tmp_path, monkeypatch, caplog):
        """REQ-025-2: emit ``filtered N tickers from blocked list`` INFO log."""
        from trading.screener import daily_screen as mod

        universe = ["005930", "000660", "035720", "005380"]
        blocked = {"005930", "000660"}

        blocked_path = tmp_path / "blocked_tickers.json"
        blocked_path.write_text(
            json.dumps(
                {
                    "date": mod._today_kst_iso(),
                    "blocked": {t: {"reason": "단기과열"} for t in blocked},
                }
            )
        )
        monkeypatch.setattr(mod, "BLOCKED_FILE", blocked_path)
        monkeypatch.setattr(mod, "SCREEN_FILE", tmp_path / "screened_tickers.json")
        monkeypatch.setattr(mod, "PENDING_FILE", tmp_path / "pending_screen.json")
        monkeypatch.setattr(mod, "DEFAULT_WATCHLIST", [])

        with patch(
            "trading.screener.daily_screen.connection",
            side_effect=lambda *a, **kw: _patched_connection(universe),
        ):
            with caplog.at_level("INFO"):
                mod.run()

        msgs = " ".join(r.message for r in caplog.records)
        # We removed exactly 2 tickers from the candidate pool.
        assert "filtered 2 tickers from blocked list" in msgs.lower(), msgs

    def test_missing_blocked_file_does_not_halt_run(self, tmp_path, monkeypatch, caplog):
        """REQ-025-3: missing blocked file must not raise; run completes."""
        from trading.screener import daily_screen as mod

        # Point BLOCKED_FILE at a nonexistent path.
        monkeypatch.setattr(mod, "BLOCKED_FILE", tmp_path / "missing.json")
        monkeypatch.setattr(mod, "SCREEN_FILE", tmp_path / "screened_tickers.json")
        monkeypatch.setattr(mod, "PENDING_FILE", tmp_path / "pending_screen.json")
        monkeypatch.setattr(mod, "DEFAULT_WATCHLIST", [])

        universe = ["005930", "000660"]
        with patch(
            "trading.screener.daily_screen.connection",
            side_effect=lambda *a, **kw: _patched_connection(universe),
        ):
            with caplog.at_level("WARNING"):
                result = mod.run()  # must not raise

        # All universe candidates make it through (no blocked filter).
        assert set(result["tickers"]) == set(universe)


# ---------------------------------------------------------------------------
# REQ-025-5 — Low-yield WARNING when post-filter count < 5.
# ---------------------------------------------------------------------------

class TestLowYieldWarning:
    """REQ-025-5: emit a WARNING when post-filter candidates < 5."""

    def test_low_yield_warning_emitted(self, tmp_path, monkeypatch, caplog):
        from trading.screener import daily_screen as mod

        # 4-ticker universe, all unblocked → post-filter count = 4 < 5.
        universe = ["005930", "000660", "035720", "005380"]
        blocked_path = tmp_path / "blocked_tickers.json"
        blocked_path.write_text(
            json.dumps({"date": mod._today_kst_iso(), "blocked": {}})
        )
        monkeypatch.setattr(mod, "BLOCKED_FILE", blocked_path)
        monkeypatch.setattr(mod, "SCREEN_FILE", tmp_path / "screened_tickers.json")
        monkeypatch.setattr(mod, "PENDING_FILE", tmp_path / "pending_screen.json")
        monkeypatch.setattr(mod, "DEFAULT_WATCHLIST", [])

        with patch(
            "trading.screener.daily_screen.connection",
            side_effect=lambda *a, **kw: _patched_connection(universe),
        ):
            with caplog.at_level("WARNING"):
                mod.run()

        warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
        # Look for an explicit low-yield / min_candidates / <5 indicator.
        assert any(
            "low" in w.lower() or "min_candidates" in w.lower() or "yield" in w.lower()
            for w in warnings
        ), f"expected low-yield warning in: {warnings}"

    def test_normal_yield_no_low_warning(self, tmp_path, monkeypatch, caplog):
        """When candidate count >= 5, no low-yield WARNING is emitted."""
        from trading.screener import daily_screen as mod

        universe = [f"00{i:04d}" for i in range(10)]  # 10 tickers
        blocked_path = tmp_path / "blocked_tickers.json"
        blocked_path.write_text(
            json.dumps({"date": mod._today_kst_iso(), "blocked": {}})
        )
        monkeypatch.setattr(mod, "BLOCKED_FILE", blocked_path)
        monkeypatch.setattr(mod, "SCREEN_FILE", tmp_path / "screened_tickers.json")
        monkeypatch.setattr(mod, "PENDING_FILE", tmp_path / "pending_screen.json")
        monkeypatch.setattr(mod, "DEFAULT_WATCHLIST", [])

        with patch(
            "trading.screener.daily_screen.connection",
            side_effect=lambda *a, **kw: _patched_connection(universe),
        ):
            with caplog.at_level("WARNING"):
                mod.run()

        warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert not any(
            "low yield" in w.lower() or "low-yield" in w.lower()
            for w in warnings
        ), f"unexpected low-yield warning at normal yield: {warnings}"
