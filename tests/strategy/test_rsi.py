"""SPEC-TRADING-040 — shared RSI(14) extracted from screener.daily_screen.

Pins that ``rsi_from_closes`` reproduces the screener's prior inline formula
(simple-average gains/losses; all-up=100, all-down=0, flat=50) so the watchdog
stagnation rotation and the screener share ONE implementation.

@MX:SPEC: SPEC-TRADING-040
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest

from trading.strategy.volatility.rsi import compute_rsi, rsi_from_closes


def _screener_formula(all_closes: list[float]) -> float:
    """The exact formula the screener used before extraction (oracle)."""
    diffs = [all_closes[i] - all_closes[i - 1] for i in range(1, len(all_closes))]
    gains = [d for d in diffs[-14:] if d > 0]
    losses = [-d for d in diffs[-14:] if d < 0]
    avg_gain = sum(gains) / 14 if gains else 0.0
    avg_loss = sum(losses) / 14 if losses else 0.0
    return 100.0 - (100.0 / (1 + (avg_gain / avg_loss))) if avg_loss > 0 else (
        100.0 if avg_gain > 0 else 50.0
    )


class TestRsiFromCloses:
    def test_matches_screener_formula_mixed(self):
        closes = [100, 101, 99, 102, 98, 103, 97, 104, 96, 105, 95, 106, 94, 107, 93]
        assert rsi_from_closes([float(c) for c in closes]) == pytest.approx(
            _screener_formula([float(c) for c in closes])
        )

    def test_all_up_is_100(self):
        closes = [float(100 + i) for i in range(16)]
        assert rsi_from_closes(closes) == 100.0

    def test_all_down_is_0(self):
        closes = [float(100 - i) for i in range(16)]
        assert rsi_from_closes(closes) == 0.0

    def test_flat_is_50(self):
        closes = [100.0] * 16
        assert rsi_from_closes(closes) == 50.0

    def test_too_few_closes_returns_none(self):
        assert rsi_from_closes([100.0, 101.0]) is None


class _RsiCursor:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def execute(self, *_a: Any, **_k: Any) -> None:
        return None

    def fetchall(self) -> list[dict]:
        return self._rows

    def __enter__(self) -> _RsiCursor:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class _RsiConn:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def cursor(self) -> _RsiCursor:
        return _RsiCursor(self._rows)

    def __enter__(self) -> _RsiConn:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class TestComputeRsi:
    def test_computes_from_db_rows_newest_first(self):
        from trading.strategy.volatility import rsi as rsi_mod

        # newest-first rows (as the SQL ORDER BY ts DESC returns); flat -> 50.
        rows = [{"close": 100.0} for _ in range(16)]

        @contextmanager
        def _factory(*_a: Any, **_k: Any):
            yield _RsiConn(rows)

        with patch.object(rsi_mod, "connection", side_effect=_factory):
            assert compute_rsi("064350") == 50.0

    def test_insufficient_rows_returns_none(self):
        from trading.strategy.volatility import rsi as rsi_mod

        @contextmanager
        def _factory(*_a: Any, **_k: Any):
            yield _RsiConn([{"close": 100.0}, {"close": 101.0}])

        with patch.object(rsi_mod, "connection", side_effect=_factory):
            assert compute_rsi("064350") is None

    def test_db_error_returns_none(self):
        from trading.strategy.volatility import rsi as rsi_mod

        @contextmanager
        def _factory(*_a: Any, **_k: Any):
            raise RuntimeError("db down")
            yield  # pragma: no cover

        with patch.object(rsi_mod, "connection", side_effect=_factory):
            assert compute_rsi("064350") is None
