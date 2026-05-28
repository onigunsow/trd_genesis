"""SPEC-TRADING-029 v0.2.0 — balance-reconcile fill tracking.

Covers REQ-029-6 (data source = inquire-balance, NOT inquire-daily-ccld) and
REQ-029-7 (FIFO attribution of held qty to submitted/partial BUY orders).

All tests are offline: ``balance()`` is patched and the DB connection is a
``ScriptedCursor`` that returns pre-programmed SELECT results in order.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Scripted in-memory DB doubles
# ---------------------------------------------------------------------------


class ScriptedCursor:
    """Cursor that records executes and returns scripted fetch results.

    ``fetchone_queue`` and ``fetchall_queue`` are consumed in order. fetchone()
    pops from fetchone_queue; fetchall() pops from fetchall_queue.
    """

    def __init__(
        self,
        *,
        fetchone_queue: list[Any] | None = None,
        fetchall_queue: list[Any] | None = None,
    ) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._one = list(fetchone_queue or [])
        self._all = list(fetchall_queue or [])

    def execute(self, sql: str, params: Any = None) -> None:
        self.calls.append((sql, params))

    def fetchone(self) -> Any:
        return self._one.pop(0) if self._one else None

    def fetchall(self) -> Any:
        return self._all.pop(0) if self._all else []

    def __enter__(self) -> ScriptedCursor:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class ScriptedConn:
    def __init__(self, cursor: ScriptedCursor) -> None:
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> ScriptedCursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        return None

    def __enter__(self) -> ScriptedConn:
        return self

    def __exit__(self, exc_type: Any, *_: Any) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()


def _holding(ticker: str, qty: int, avg_cost: int = 70_000, name: str = "") -> dict:
    return {
        "ticker": ticker,
        "name": name,
        "qty": qty,
        "avg_cost": avg_cost,
        "current_price": avg_cost,
        "eval_amount": qty * avg_cost,
        "pnl_amount": 0,
        "pnl_pct": 0.0,
    }


def _bal(holdings: list[dict]) -> dict:
    return {
        "cash_d2": 1_000_000,
        "buyable": 1_000_000,
        "buyable_effective": 1_000_000,
        "nrcvb_buy_amt": 0,
        "total_assets": 5_000_000,
        "stock_eval": 1_000_000,
        "invest_basis": 2_000_000,
        "pnl_total": 0,
        "holdings": holdings,
        "raw": {},
    }


def _order(order_id: int, qty: int, *, fill_qty: int = 0, status: str = "submitted") -> dict:
    return {"id": order_id, "qty": qty, "fill_qty": fill_qty, "status": status}


@contextmanager
def _conn_factory(cursor: ScriptedCursor):
    yield ScriptedConn(cursor)


def _audit_events(cursor: ScriptedCursor) -> list[str]:
    """Return the list of audit_log event_type values recorded by the cursor."""
    events: list[str] = []
    for sql, params in cursor.calls:
        if "audit_log" in sql.lower() and params:
            events.append(str(params[0]))
    return events


# ---------------------------------------------------------------------------
# REQ-029-6: data source
# ---------------------------------------------------------------------------


class TestDataSource:
    def test_uses_inquire_balance_not_daily_ccld(self):
        """REQ-029-6 / AC-029-9: balance() is the data source; no inquire-daily-ccld."""
        from trading.kis import fills

        client = MagicMock()
        bal = _bal([])  # no holdings → nothing to do

        with (
            patch.object(fills, "balance", return_value=bal) as balance_fn,
            patch.object(
                fills, "connection",
                side_effect=lambda *a, **k: _conn_factory(ScriptedCursor()),
            ),
        ):
            fills.reconcile_from_balance(client, dry_run=False)

        balance_fn.assert_called_once_with(client)
        # The deprecated entry point must be gone entirely.
        assert not hasattr(fills, "inquire_fills_today")

    def test_fill_sync_delegates_to_reconcile_from_balance(self):
        """fill_sync keeps the scheduler/CLI contract by delegating to reconcile."""
        from trading.kis import fills

        client = MagicMock()
        with patch.object(
            fills, "reconcile_from_balance",
            return_value={"queried": 0, "transitioned": 0, "errors": 0, "dry_run": False},
        ) as reconcile:
            result = fills.fill_sync(client, dry_run=False)

        reconcile.assert_called_once_with(client, dry_run=False)
        assert set(result) >= {"queried", "transitioned", "errors", "dry_run"}


# ---------------------------------------------------------------------------
# REQ-029-7: FIFO orders transition
# ---------------------------------------------------------------------------


class TestFifoTransition:
    def test_single_submitted_buy_fills_when_held_qty_matches(self):
        """AC-029-9: held_qty=1 matches one submitted qty=1 BUY → filled."""
        from trading.kis import fills

        client = MagicMock()
        bal = _bal([_holding("005930", qty=1, avg_cost=70_000)])

        cursor = ScriptedCursor(
            fetchone_queue=[{"accounted": 0}],          # already_accounted SELECT
            fetchall_queue=[
                [_order(10, qty=1)],                    # submitted orders for 005930
                [],                                     # positions-to-zero SELECT
            ],
        )

        with (
            patch.object(fills, "balance", return_value=bal),
            patch.object(
                fills, "connection",
                side_effect=lambda *a, **k: _conn_factory(cursor),
            ),
        ):
            result = fills.reconcile_from_balance(client, dry_run=False)

        # orders row 10 updated to filled with fill_qty=1, fill_price=70000
        updates = [
            (s, p) for s, p in cursor.calls if "UPDATE ORDERS" in s.upper()
        ]
        assert len(updates) == 1
        assert "filled" in str(updates[0][1])
        assert 70_000 in updates[0][1]
        assert "ORDER_FILLED" in _audit_events(cursor)
        assert result["transitioned"] == 1

    def test_fifo_allocation_across_two_submitted_orders(self):
        """AC-029-10: newly_filled=5 → order A(qty3)=filled, order B(qty5)=partial(2)."""
        from trading.kis import fills

        client = MagicMock()
        bal = _bal([_holding("000660", qty=5, avg_cost=150_000)])

        cursor = ScriptedCursor(
            fetchone_queue=[{"accounted": 0}],
            fetchall_queue=[
                [_order(1, qty=3), _order(2, qty=5)],   # oldest-first
                [],
            ],
        )

        with (
            patch.object(fills, "balance", return_value=bal),
            patch.object(
                fills, "connection",
                side_effect=lambda *a, **k: _conn_factory(cursor),
            ),
        ):
            result = fills.reconcile_from_balance(client, dry_run=False)

        updates = [p for s, p in cursor.calls if "UPDATE ORDERS" in s.upper()]
        assert len(updates) == 2
        # Order A: filled, fill_qty=3
        assert "filled" in str(updates[0])
        assert 3 in updates[0]
        # Order B: partial, fill_qty=2
        assert "partial" in str(updates[1])
        assert 2 in updates[1]
        events = _audit_events(cursor)
        assert "ORDER_FILLED" in events
        assert "ORDER_PARTIAL" in events
        assert result["transitioned"] == 2

    def test_partial_order_advances_to_filled_next_cycle(self):
        """AC-029-10 second cycle: held=8, accounted=5 → partial B(2/5) fills."""
        from trading.kis import fills

        client = MagicMock()
        bal = _bal([_holding("000660", qty=8, avg_cost=150_000)])

        # already_accounted=5; one partial order B (fill_qty=2 of qty=5) remains.
        cursor = ScriptedCursor(
            fetchone_queue=[{"accounted": 5}],
            fetchall_queue=[
                [_order(2, qty=5, fill_qty=2, status="partial")],
                [],
            ],
        )

        with (
            patch.object(fills, "balance", return_value=bal),
            patch.object(
                fills, "connection",
                side_effect=lambda *a, **k: _conn_factory(cursor),
            ),
        ):
            result = fills.reconcile_from_balance(client, dry_run=False)

        updates = [p for s, p in cursor.calls if "UPDATE ORDERS" in s.upper()]
        assert len(updates) == 1
        # newly_filled = 8 - 5 = 3; capacity of B = 5 - 2 = 3 → fully fills → filled, fill_qty=5
        assert "filled" in str(updates[0])
        assert 5 in updates[0]
        assert result["transitioned"] == 1

    def test_partial_when_allocation_less_than_order_qty(self):
        """AC-029-10: newly_filled=2 < submitted qty=10 → partial(2)."""
        from trading.kis import fills

        client = MagicMock()
        bal = _bal([_holding("281820", qty=2, avg_cost=200_000)])

        cursor = ScriptedCursor(
            fetchone_queue=[{"accounted": 0}],
            fetchall_queue=[[_order(7, qty=10)], []],
        )

        with (
            patch.object(fills, "balance", return_value=bal),
            patch.object(
                fills, "connection",
                side_effect=lambda *a, **k: _conn_factory(cursor),
            ),
        ):
            result = fills.reconcile_from_balance(client, dry_run=False)

        updates = [p for s, p in cursor.calls if "UPDATE ORDERS" in s.upper()]
        assert len(updates) == 1
        assert "partial" in str(updates[0])
        assert 2 in updates[0]
        assert "ORDER_PARTIAL" in _audit_events(cursor)
        assert result["transitioned"] == 1

    def test_noop_when_newly_filled_zero(self):
        """AC-029-11: held_qty == already_accounted → no orders UPDATE, no audit."""
        from trading.kis import fills

        client = MagicMock()
        bal = _bal([_holding("035420", qty=4, avg_cost=200_000)])

        cursor = ScriptedCursor(
            fetchone_queue=[{"accounted": 4}],
            fetchall_queue=[[_order(9, qty=4)], []],
        )

        with (
            patch.object(fills, "balance", return_value=bal),
            patch.object(
                fills, "connection",
                side_effect=lambda *a, **k: _conn_factory(cursor),
            ),
        ):
            result = fills.reconcile_from_balance(client, dry_run=False)

        order_updates = [s for s, _ in cursor.calls if "UPDATE ORDERS" in s.upper()]
        assert order_updates == []
        assert "ORDER_FILLED" not in _audit_events(cursor)
        assert "ORDER_PARTIAL" not in _audit_events(cursor)
        assert result["transitioned"] == 0

    def test_held_qty_less_than_accounted_clamps_to_zero(self):
        """EC-029-7: held_qty(2) < accounted(4) → newly_filled clamped to 0."""
        from trading.kis import fills

        client = MagicMock()
        bal = _bal([_holding("005930", qty=2, avg_cost=70_000)])

        cursor = ScriptedCursor(
            fetchone_queue=[{"accounted": 4}],
            fetchall_queue=[[_order(3, qty=4, fill_qty=4, status="partial")], []],
        )

        with (
            patch.object(fills, "balance", return_value=bal),
            patch.object(
                fills, "connection",
                side_effect=lambda *a, **k: _conn_factory(cursor),
            ),
        ):
            result = fills.reconcile_from_balance(client, dry_run=False)

        order_updates = [s for s, _ in cursor.calls if "UPDATE ORDERS" in s.upper()]
        assert order_updates == []
        assert result["transitioned"] == 0

    def test_no_cancel_or_reject_autotransition(self):
        """REQ-029-7: balance-only scope never sets cancelled/rejected."""
        from trading.kis import fills

        client = MagicMock()
        # Ticker held; one submitted order fully fills — but never cancel/reject.
        bal = _bal([_holding("005930", qty=1, avg_cost=70_000)])
        cursor = ScriptedCursor(
            fetchone_queue=[{"accounted": 0}],
            fetchall_queue=[[_order(10, qty=1)], []],
        )

        with (
            patch.object(fills, "balance", return_value=bal),
            patch.object(
                fills, "connection",
                side_effect=lambda *a, **k: _conn_factory(cursor),
            ),
        ):
            fills.reconcile_from_balance(client, dry_run=False)

        events = _audit_events(cursor)
        assert "ORDER_CANCELLED" not in events
        assert "ORDER_REJECTED_BY_KIS" not in events
        updates_text = " ".join(
            str(p) for s, p in cursor.calls if "UPDATE ORDERS" in s.upper()
        )
        assert "cancelled" not in updates_text
        assert "rejected" not in updates_text

    def test_held_ticker_without_local_order_only_mirrors(self):
        """EC-029-8: ticker held in balance but no submitted order → no transition."""
        from trading.kis import fills

        client = MagicMock()
        bal = _bal([_holding("078930", qty=10, avg_cost=30_000)])

        cursor = ScriptedCursor(
            fetchone_queue=[{"accounted": 0}],
            fetchall_queue=[
                [],   # no submitted/partial orders for 078930
                [],   # positions-to-zero SELECT
            ],
        )

        with (
            patch.object(fills, "balance", return_value=bal),
            patch.object(
                fills, "connection",
                side_effect=lambda *a, **k: _conn_factory(cursor),
            ),
        ):
            result = fills.reconcile_from_balance(client, dry_run=False)

        order_updates = [s for s, _ in cursor.calls if "UPDATE ORDERS" in s.upper()]
        assert order_updates == []
        assert result["transitioned"] == 0
        # positions still mirrored for 078930
        upserts = [s for s, _ in cursor.calls if "INSERT INTO POSITIONS" in s.upper()]
        assert upserts, "positions must still be mirrored even without a local order"

    def test_for_update_row_lock_used(self):
        """REQ-029-7: submitted-orders SELECT uses FOR UPDATE (cron+CLI race)."""
        from trading.kis import fills

        client = MagicMock()
        bal = _bal([_holding("005930", qty=1, avg_cost=70_000)])
        cursor = ScriptedCursor(
            fetchone_queue=[{"accounted": 0}],
            fetchall_queue=[[_order(10, qty=1)], []],
        )

        with (
            patch.object(fills, "balance", return_value=bal),
            patch.object(
                fills, "connection",
                side_effect=lambda *a, **k: _conn_factory(cursor),
            ),
        ):
            fills.reconcile_from_balance(client, dry_run=False)

        # The order-selection SELECT (the one that orders by ts) must lock rows.
        locking_selects = [
            s for s, _ in cursor.calls
            if "SELECT" in s.upper() and "FOR UPDATE" in s.upper()
        ]
        assert locking_selects, "expected a SELECT ... FOR UPDATE on submitted orders"


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_makes_no_writes(self):
        """AC-029-6 carried over: --dry-run performs zero UPDATE/INSERT."""
        from trading.kis import fills

        client = MagicMock()
        bal = _bal([_holding("005930", qty=1, avg_cost=70_000)])
        cursor = ScriptedCursor(
            fetchone_queue=[{"accounted": 0}],
            fetchall_queue=[[_order(10, qty=1)], []],
        )

        with (
            patch.object(fills, "balance", return_value=bal),
            patch.object(
                fills, "connection",
                side_effect=lambda *a, **k: _conn_factory(cursor),
            ),
        ):
            result = fills.reconcile_from_balance(client, dry_run=True)

        writes = [
            s for s, _ in cursor.calls
            if any(kw in s.upper() for kw in (
                "UPDATE ORDERS", "UPDATE POSITIONS",
                "INSERT INTO POSITIONS", "INSERT INTO AUDIT_LOG",
            ))
        ]
        assert writes == [], f"dry_run made writes: {writes}"
        assert result["dry_run"] is True


class TestErrorIsolation:
    def test_db_error_counted_not_raised(self):
        """A DB failure mid-cycle is logged + counted, never crashes the cron."""
        from trading.kis import fills

        client = MagicMock()
        bal = _bal([_holding("005930", qty=1, avg_cost=70_000)])

        @contextmanager
        def _boom(*_: Any, **__: Any):
            raise RuntimeError("simulated DB outage")
            yield  # pragma: no cover

        with (
            patch.object(fills, "balance", return_value=bal),
            patch.object(fills, "connection", side_effect=_boom),
        ):
            result = fills.reconcile_from_balance(client, dry_run=False)

        assert result["errors"] == 1
        assert result["transitioned"] == 0
