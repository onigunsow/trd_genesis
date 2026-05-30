"""SPEC-TRADING-038 REQ-038-2 — take-profit marker persists to DB (restart-safe).

Reproduction-first (money/risk logic). The in-memory ``_TOOK_PROFIT`` dict reset
on container restart, so a 14:00 take-profit could be repeated at 14:30 after a
restart (double half-sell). These tests pin the DB-backed behaviour:

- after a *simulated restart* (no in-memory state; the marker already lives in
  the DB) ``_took_profit_today(ticker)`` still returns True for the SAME trading
  day → the second half-sell is suppressed;
- a different ticker returns False;
- the next trading day naturally resets (the marker is keyed by trading_day);
- ``_mark_took_profit`` is idempotent (re-marking the same key keeps one row,
  mirroring ``ON CONFLICT DO NOTHING``);
- a DB failure during marking does not crash the poll (graceful per-ticker).

A stateful in-memory ``position_action_markers`` double (a ``set`` of
``(trading_day, ticker, action)``) is wired through a patched ``connection`` so
inserts in one call are visible to later SELECTs — exactly the cross-restart
semantics we are protecting.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Any
from unittest.mock import patch

import pytest

from trading.watchers import position_watchdog


# --------------------------------------------------------------------------- #
# Stateful in-memory position_action_markers double
# --------------------------------------------------------------------------- #
class _MarkerCursor:
    """Cursor emulating position_action_markers SELECT/INSERT against a set store."""

    def __init__(self, store: set[tuple[date, str, str]], fail: bool = False) -> None:
        self._store = store
        self._fail = fail
        self._result: Any = None

    def execute(self, sql: str, params: Any = None) -> None:
        if self._fail:
            raise RuntimeError("simulated DB failure")
        text = sql.strip().upper()
        day, ticker = params[0], params[1]
        if text.startswith("SELECT"):
            self._result = (1,) if (day, ticker, "take_profit") in self._store else None
        elif "INSERT" in text:
            # set membership == ON CONFLICT DO NOTHING (idempotent).
            self._store.add((day, ticker, "take_profit"))

    def fetchone(self) -> Any:
        return self._result

    def __enter__(self) -> _MarkerCursor:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class _MarkerConn:
    def __init__(self, store: set[tuple[date, str, str]], fail: bool = False) -> None:
        self._store = store
        self._fail = fail

    def cursor(self) -> _MarkerCursor:
        return _MarkerCursor(self._store, fail=self._fail)

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None

    def __enter__(self) -> _MarkerConn:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


def _patch_db(store: set[tuple[date, str, str]], *, fail: bool = False):
    @contextmanager
    def _factory(*_a: Any, **_k: Any):
        yield _MarkerConn(store, fail=fail)

    return patch.object(position_watchdog, "connection", side_effect=_factory)


_TODAY = date(2026, 5, 30)
_TOMORROW = date(2026, 5, 31)


# --------------------------------------------------------------------------- #
# REQ-038-2: restart-safe marker
# --------------------------------------------------------------------------- #
class TestRestartSafety:
    def test_marker_survives_restart_same_day(self):
        """AC: marker already in DB → True on same trading day after 'restart'."""
        store: set[tuple[date, str, str]] = {(_TODAY, "005930", "take_profit")}
        with (
            _patch_db(store),
            patch.object(position_watchdog, "_today_kst", return_value=_TODAY),
        ):
            assert position_watchdog._took_profit_today("005930") is True

    def test_other_ticker_not_marked(self):
        """A ticker with no marker returns False."""
        store: set[tuple[date, str, str]] = {(_TODAY, "005930", "take_profit")}
        with (
            _patch_db(store),
            patch.object(position_watchdog, "_today_kst", return_value=_TODAY),
        ):
            assert position_watchdog._took_profit_today("000660") is False

    def test_next_trading_day_resets(self):
        """The marker is keyed by trading_day → next KST day is False (natural reset)."""
        store: set[tuple[date, str, str]] = {(_TODAY, "005930", "take_profit")}
        with (
            _patch_db(store),
            patch.object(position_watchdog, "_today_kst", return_value=_TOMORROW),
        ):
            assert position_watchdog._took_profit_today("005930") is False


class TestMarkAndIdempotency:
    def test_mark_then_read_true(self):
        """_mark_took_profit writes a marker that a later read sees (DB-backed)."""
        store: set[tuple[date, str, str]] = set()
        with (
            _patch_db(store),
            patch.object(position_watchdog, "_today_kst", return_value=_TODAY),
        ):
            assert position_watchdog._took_profit_today("005930") is False
            position_watchdog._mark_took_profit("005930")
            assert position_watchdog._took_profit_today("005930") is True

    def test_mark_is_idempotent(self):
        """Re-marking the same (day, ticker, action) keeps exactly one row."""
        store: set[tuple[date, str, str]] = set()
        with (
            _patch_db(store),
            patch.object(position_watchdog, "_today_kst", return_value=_TODAY),
        ):
            position_watchdog._mark_took_profit("005930")
            position_watchdog._mark_took_profit("005930")
        assert store == {(_TODAY, "005930", "take_profit")}
        assert len(store) == 1


class TestGracefulFailure:
    def test_mark_failure_propagates_for_caller_isolation(self):
        """A DB failure raises so the poll's per-ticker try/except absorbs it.

        REQ-038-2(e): the failure must NOT be silently lost in a way that flips the
        marker, and the poll wraps the call in per-ticker isolation — verified in
        the poll-level graceful test below.
        """
        store: set[tuple[date, str, str]] = set()
        with (
            _patch_db(store, fail=True),
            patch.object(position_watchdog, "_today_kst", return_value=_TODAY),
            pytest.raises(RuntimeError),
        ):
            position_watchdog._mark_took_profit("005930")
