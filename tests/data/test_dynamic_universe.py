"""SPEC-TRADING-023 REQ-023-2: dynamic_universe registry tests.

Covers the dynamic_tickers DB table CRUD layer:
- register() inserts new tickers, updates last_used_at for existing
- list_active() returns sorted ticker list
- FIFO eviction at cap (REQ-023-2 (d) / REQ-023-5 (d))
- Cap is configurable via DYNAMIC_UNIVERSE_CAP env var
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Stateful in-memory fake connection that simulates the dynamic_tickers table
# ---------------------------------------------------------------------------


class _InMemoryDynamicTickersDB:
    """Minimal SQL executor that models the dynamic_tickers table.

    Supports the exact statements used by dynamic_universe.py:
      - SELECT COUNT(*) FROM dynamic_tickers
      - SELECT ticker FROM dynamic_tickers ORDER BY ticker
      - SELECT ticker, first_seen_at FROM dynamic_tickers ORDER BY first_seen_at ASC LIMIT 1
      - INSERT ... ON CONFLICT (ticker) DO UPDATE SET last_used_at = NOW() ...
      - DELETE FROM dynamic_tickers WHERE ticker = %s

    The clock is a monotonically increasing integer so that "oldest" is well
    defined under fast test execution.
    """

    def __init__(self) -> None:
        # ticker -> (first_seen_at, last_used_at, source)
        self.rows: dict[str, tuple[int, int, str]] = {}
        self.clock = 0

    def _tick(self) -> int:
        self.clock += 1
        return self.clock


class _Cursor:
    def __init__(self, db: _InMemoryDynamicTickersDB) -> None:
        self.db = db
        self._result: list[dict[str, Any]] = []
        self.last_sql: str = ""

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        self.last_sql = sql
        params = params or ()
        sql_norm = " ".join(sql.split()).lower()

        # SELECT 1 FROM dynamic_tickers WHERE ticker = %s -- existence probe
        if (
            "select 1" in sql_norm
            and "from dynamic_tickers" in sql_norm
            and "where ticker" in sql_norm
        ):
            ticker = params[0] if params else None
            self._result = [{"?column?": 1}] if ticker in self.db.rows else []
            return

        if "select count(*)" in sql_norm and "dynamic_tickers" in sql_norm:
            self._result = [{"n": len(self.db.rows)}]
            return

        if (
            "select ticker" in sql_norm
            and "from dynamic_tickers" in sql_norm
            and "order by ticker" in sql_norm
        ):
            self._result = [{"ticker": t} for t in sorted(self.db.rows.keys())]
            return

        if (
            "select ticker" in sql_norm
            and "from dynamic_tickers" in sql_norm
            and "order by first_seen_at" in sql_norm
        ):
            if not self.db.rows:
                self._result = []
                return
            oldest_ticker = min(
                self.db.rows.keys(), key=lambda t: self.db.rows[t][0]
            )
            first_seen, _, _ = self.db.rows[oldest_ticker]
            self._result = [
                {"ticker": oldest_ticker, "first_seen_at": first_seen}
            ]
            return

        if "insert into dynamic_tickers" in sql_norm:
            # ticker, source params
            ticker, source = params[0], params[1]
            ts = self.db._tick()
            if "on conflict" in sql_norm and ticker in self.db.rows:
                first_seen, _, src = self.db.rows[ticker]
                self.db.rows[ticker] = (first_seen, ts, src)
                # ON CONFLICT path = update
                self._result = [{"inserted": False}]
                self._rowcount = 1
            else:
                self.db.rows[ticker] = (ts, ts, source)
                self._result = [{"inserted": True}]
                self._rowcount = 1
            return

        if "delete from dynamic_tickers" in sql_norm:
            ticker = params[0]
            self.db.rows.pop(ticker, None)
            self._result = []
            self._rowcount = 1
            return

        # Default no-op
        self._result = []

    def fetchone(self) -> dict[str, Any] | None:
        return self._result[0] if self._result else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._result)

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class _Conn:
    def __init__(self, db: _InMemoryDynamicTickersDB) -> None:
        self.db = db

    def cursor(self) -> _Cursor:
        return _Cursor(self.db)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass

    def __enter__(self) -> _Conn:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


@pytest.fixture
def fake_db(monkeypatch):
    """Patch dynamic_universe.connection() with an in-memory fake."""
    db = _InMemoryDynamicTickersDB()

    @contextmanager
    def _factory(*_a, **_kw):
        yield _Conn(db)

    # The module is created in GREEN phase; patch lazily on import.
    import importlib

    mod = importlib.import_module("trading.data.dynamic_universe")
    monkeypatch.setattr(mod, "connection", _factory)
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRegisterAndList:
    """REQ-023-2 (b, c): register + list_active basic CRUD."""

    def test_register_new_ticker_returns_true(self, fake_db):
        from trading.data.dynamic_universe import register

        added = register("281820", source="micro_recommendation")
        assert added is True
        assert "281820" in fake_db.rows
        first_seen, last_used, source = fake_db.rows["281820"]
        assert source == "micro_recommendation"
        assert first_seen == last_used  # same tick on first insert

    def test_register_duplicate_returns_false_and_updates_last_used(
        self, fake_db
    ):
        from trading.data.dynamic_universe import register

        register("281820", source="micro_recommendation")
        first_seen_before = fake_db.rows["281820"][0]

        added = register("281820", source="micro_recommendation")
        assert added is False
        # first_seen_at preserved
        assert fake_db.rows["281820"][0] == first_seen_before
        # last_used_at advanced
        assert fake_db.rows["281820"][1] > first_seen_before

    def test_list_active_returns_empty_when_no_tickers(self, fake_db):
        from trading.data.dynamic_universe import list_active

        assert list_active() == []

    def test_list_active_returns_sorted_tickers(self, fake_db):
        from trading.data.dynamic_universe import list_active, register

        register("281820", source="micro_recommendation")
        register("068270", source="micro_recommendation")
        register("005935", source="micro_recommendation")

        assert list_active() == ["005935", "068270", "281820"]


class TestFifoEviction:
    """REQ-023-2 (d) + REQ-023-5 (d): FIFO eviction when cap is reached."""

    def test_eviction_removes_oldest_when_cap_reached(self, fake_db, monkeypatch):
        """When cap=3 and a 4th ticker arrives, the oldest (first_seen_at)
        must be evicted and the new one inserted."""
        from trading.data import dynamic_universe

        # Shrink cap so the test stays small.
        monkeypatch.setattr(dynamic_universe, "DEFAULT_CAP", 3)

        dynamic_universe.register("OLDEST", source="micro_recommendation")
        dynamic_universe.register("MID1", source="micro_recommendation")
        dynamic_universe.register("MID2", source="micro_recommendation")

        # cap reached — adding NEWEST must evict OLDEST.
        added = dynamic_universe.register("NEWEST", source="micro_recommendation")

        assert added is True
        assert set(fake_db.rows.keys()) == {"MID1", "MID2", "NEWEST"}
        # Size stays at cap.
        assert len(fake_db.rows) == 3
        assert "OLDEST" not in fake_db.rows

    def test_eviction_does_not_fire_below_cap(self, fake_db, monkeypatch):
        from trading.data import dynamic_universe

        monkeypatch.setattr(dynamic_universe, "DEFAULT_CAP", 100)

        for i in range(10):
            dynamic_universe.register(f"T{i:05d}", source="micro_recommendation")

        # 10 < cap=100, no eviction.
        assert len(fake_db.rows) == 10

    def test_eviction_preserves_cap_invariant(self, fake_db, monkeypatch):
        """REQ-023-2 (f): row count never exceeds cap."""
        from trading.data import dynamic_universe

        monkeypatch.setattr(dynamic_universe, "DEFAULT_CAP", 5)

        for i in range(20):
            dynamic_universe.register(f"T{i:05d}", source="micro_recommendation")

        assert len(fake_db.rows) == 5

    def test_duplicate_register_does_not_evict(self, fake_db, monkeypatch):
        """Registering an existing ticker is an UPDATE, not an INSERT, so it
        must not trigger eviction even at cap."""
        from trading.data import dynamic_universe

        monkeypatch.setattr(dynamic_universe, "DEFAULT_CAP", 3)

        dynamic_universe.register("A", source="micro_recommendation")
        dynamic_universe.register("B", source="micro_recommendation")
        dynamic_universe.register("C", source="micro_recommendation")  # cap reached

        # Re-register existing ticker. No new row -> no eviction.
        added = dynamic_universe.register("A", source="micro_recommendation")
        assert added is False
        assert set(fake_db.rows.keys()) == {"A", "B", "C"}
