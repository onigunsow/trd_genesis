"""SPEC-TRADING-029 v0.2.0 — positions = balance mirror (REQ-029-8).

``_mirror_positions`` UPSERTs every held ticker (avg_cost taken directly from
KIS ``pchs_avg_pric`` — no integer weighted-average reconstruction) and zeroes
out (never DELETEs) positions rows that are no longer held.
"""

from __future__ import annotations

from typing import Any


class ScriptedCursor:
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

    def cursor(self) -> ScriptedCursor:
        return self._cursor

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None


def _holding(ticker: str, qty: int, avg_cost: int) -> dict:
    return {
        "ticker": ticker,
        "name": "",
        "qty": qty,
        "avg_cost": avg_cost,
        "current_price": avg_cost,
        "eval_amount": qty * avg_cost,
        "pnl_amount": 0,
        "pnl_pct": 0.0,
    }


def _audit_events(cursor: ScriptedCursor) -> list[str]:
    return [
        str(params[0])
        for sql, params in cursor.calls
        if "audit_log" in sql.lower() and params
    ]


class TestPositionsMirror:
    def test_upsert_from_balance_holdings(self):
        """AC-029-12: each held ticker UPSERTed via ON CONFLICT (ticker)."""
        from trading.kis import fills

        holdings = [
            _holding("005930", qty=2, avg_cost=71_000),
            _holding("000660", qty=1, avg_cost=150_000),
        ]
        cursor = ScriptedCursor(fetchall_queue=[[]])  # nothing to zero
        fills._mirror_positions(holdings, ScriptedConn(cursor), dry_run=False)

        upserts = [
            (s, p) for s, p in cursor.calls
            if "INSERT INTO POSITIONS" in s.upper() and "ON CONFLICT" in s.upper()
        ]
        assert len(upserts) == 2
        # avg_cost passed straight through from holdings (pchs_avg_pric).
        all_params = [p for _, p in upserts]
        assert any(71_000 in p for p in all_params)
        assert any(150_000 in p for p in all_params)

    def test_avg_cost_taken_from_pchs_avg_pric_not_recomputed(self):
        """AC-029-12: no weighted-average arithmetic — value is verbatim."""
        from trading.kis import fills

        holdings = [_holding("005930", qty=10, avg_cost=12_345)]
        cursor = ScriptedCursor(fetchall_queue=[[]])
        fills._mirror_positions(holdings, ScriptedConn(cursor), dry_run=False)

        upsert = next(
            (p for s, p in cursor.calls
             if "INSERT INTO POSITIONS" in s.upper()),
            None,
        )
        assert upsert is not None
        assert 12_345 in upsert  # exact avg_cost
        assert 10 in upsert      # exact qty

    def test_unheld_ticker_zeroed_not_deleted(self):
        """AC-029-12: a position absent from balance is set qty=0, not DELETEd."""
        from trading.kis import fills

        holdings = [_holding("005930", qty=2, avg_cost=71_000)]
        # zero-out SELECT returns the stale 035720 row.
        cursor = ScriptedCursor(fetchall_queue=[[{"ticker": "035720"}]])
        fills._mirror_positions(holdings, ScriptedConn(cursor), dry_run=False)

        deletes = [s for s, _ in cursor.calls if "DELETE" in s.upper()]
        assert deletes == [], "must not DELETE positions rows (ADR-029-4)"

        zero_updates = [
            (s, p) for s, p in cursor.calls
            if "UPDATE POSITIONS" in s.upper() and p and "035720" in str(p)
        ]
        assert len(zero_updates) == 1
        assert "0" in str(zero_updates[0][1]) or 0 in zero_updates[0][1]

    def test_position_synced_audit_emitted(self):
        """REQ-029-8: every mirror emits POSITION_SYNCED audit."""
        from trading.kis import fills

        holdings = [_holding("005930", qty=2, avg_cost=71_000)]
        cursor = ScriptedCursor(fetchall_queue=[[]])
        fills._mirror_positions(holdings, ScriptedConn(cursor), dry_run=False)

        assert "POSITION_SYNCED" in _audit_events(cursor)

    def test_dry_run_no_writes(self):
        """Dry-run mirror performs zero UPDATE/INSERT."""
        from trading.kis import fills

        holdings = [_holding("005930", qty=2, avg_cost=71_000)]
        cursor = ScriptedCursor(fetchall_queue=[[{"ticker": "035720"}]])
        fills._mirror_positions(holdings, ScriptedConn(cursor), dry_run=True)

        writes = [
            s for s, _ in cursor.calls
            if any(kw in s.upper() for kw in (
                "INSERT INTO POSITIONS", "UPDATE POSITIONS", "INSERT INTO AUDIT_LOG",
            ))
        ]
        assert writes == []
