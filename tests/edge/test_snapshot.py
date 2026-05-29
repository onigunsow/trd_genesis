"""Edge Validation Phase 0 — 일별 자산 스냅샷 기록.

``balance()`` 와 DB connection 을 패치해 오프라인으로 검증한다: balance dict → 컬럼 매핑,
UPSERT(ON CONFLICT) SQL, 멱등(재실행 시 같은 trading_day 로 UPSERT).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Any
from unittest.mock import patch

from trading.edge import snapshot


class _Cursor:
    def __init__(self):
        self.calls: list[tuple[str, Any]] = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None


def _balance(**over):
    base = {
        "total_assets": 11_000_000,
        "stock_eval": 3_000_000,
        "cash_d2": 8_000_000,
        "pnl_total": 250_000,
    }
    base.update(over)
    return base


@contextmanager
def _conn_factory(cursor):
    yield _Conn(cursor)


class TestRecordSnapshot:
    def test_maps_balance_fields(self):
        cur = _Cursor()
        with (
            patch("trading.kis.account.balance", return_value=_balance()),
            patch("trading.edge.snapshot.connection", lambda *a, **k: _conn_factory(cur)),
        ):
            row = snapshot.record_snapshot(client=object(), trading_day=date(2026, 5, 29))

        assert row["trading_day"] == date(2026, 5, 29)
        assert row["total_assets"] == 11_000_000
        assert row["stock_eval"] == 3_000_000
        assert row["cash"] == 8_000_000          # cash_d2 매핑
        assert row["unrealized_pnl"] == 250_000  # pnl_total 매핑

    def test_upsert_sql_is_idempotent(self):
        cur = _Cursor()
        with (
            patch("trading.kis.account.balance", return_value=_balance()),
            patch("trading.edge.snapshot.connection", lambda *a, **k: _conn_factory(cur)),
        ):
            snapshot.record_snapshot(client=object(), trading_day=date(2026, 5, 29))

        assert len(cur.calls) == 1
        sql, params = cur.calls[0]
        assert "INSERT INTO daily_equity_snapshot" in sql
        assert "ON CONFLICT (trading_day) DO UPDATE" in sql
        assert params["trading_day"] == date(2026, 5, 29)
        assert params["cash"] == 8_000_000

    def test_does_not_write_realized_pnl_cum(self):
        # realized_pnl_cum 은 balance() 미제공 → 스냅샷이 건드리지 않음.
        cur = _Cursor()
        with (
            patch("trading.kis.account.balance", return_value=_balance()),
            patch("trading.edge.snapshot.connection", lambda *a, **k: _conn_factory(cur)),
        ):
            snapshot.record_snapshot(client=object(), trading_day=date(2026, 5, 29))
        sql, _ = cur.calls[0]
        assert "realized_pnl_cum" not in sql
