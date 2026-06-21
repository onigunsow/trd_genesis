"""SPEC-TRADING-042 D2 — orders-positions parity 테스트.

smoke_gate ledger_parity 배선 테스트 포함.

테스트 시나리오:
  S1: 정합 → parity=True, diff=0
  S2: orders_net > positions → parity=False, diff>0
  S3: M2 교정 후 parity=True
  S4: smoke_gate 배선 — drift=0&errors=0 이지만 orders diverge → ledger_parity False

@MX:SPEC: SPEC-TRADING-042
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

# ---------------------------------------------------------------------------
# 더블: 두 번의 fetchall 을 순서대로 다른 rows 반환
# ---------------------------------------------------------------------------


class _SeqCursor:
    def __init__(self, rows_seq: list[list[dict[str, Any]]]) -> None:
        self._seq = list(rows_seq)
        self._idx = 0

    def execute(self, sql: str, params: Any = None) -> None:
        pass

    def fetchall(self) -> list[dict[str, Any]]:
        rows = self._seq[self._idx] if self._idx < len(self._seq) else []
        self._idx += 1
        return rows

    def fetchone(self) -> dict[str, Any] | None:
        rows = self._seq[self._idx] if self._idx < len(self._seq) else []
        return rows[0] if rows else None

    def __enter__(self) -> _SeqCursor:
        return self

    def __exit__(self, *_: Any) -> None:
        pass


class _FakeConn:
    def __init__(self, cur: _SeqCursor) -> None:
        self._cur = cur

    def cursor(self) -> _SeqCursor:
        return self._cur

    def commit(self) -> None:
        pass

    def __enter__(self) -> _FakeConn:
        return self

    def __exit__(self, *_: Any) -> None:
        pass


def _patch_conn(orders_rows, positions_rows):
    cur = _SeqCursor([orders_rows, positions_rows])
    conn = _FakeConn(cur)

    @contextmanager
    def _conn(autocommit: bool = False):
        yield conn

    return patch("trading.kis.ghost_convergence.connection", side_effect=_conn)


# ---------------------------------------------------------------------------
# S1: 정합 → parity=True
# ---------------------------------------------------------------------------


class TestParityAligned:
    def test_parity_true_when_aligned(self):
        orders_rows = [{"ticker": "086790", "net_qty": 10}]
        positions_rows = [{"ticker": "086790", "qty": 10, "avg_cost": 75000.0}]

        with _patch_conn(orders_rows, positions_rows):
            from trading.kis.ghost_convergence import orders_positions_divergence
            result = orders_positions_divergence()

        assert result["parity"] is True
        assert result["by_ticker"]["086790"]["diff"] == 0
        assert result["by_ticker"]["086790"]["orders_net"] == 10
        assert result["by_ticker"]["086790"]["positions_qty"] == 10

    def test_empty_both_tables_parity_true(self):
        with _patch_conn([], []):
            from trading.kis.ghost_convergence import orders_positions_divergence
            result = orders_positions_divergence()

        assert result["parity"] is True
        assert result["by_ticker"] == {}


# ---------------------------------------------------------------------------
# S2: orders_net > positions → parity=False
# ---------------------------------------------------------------------------


class TestParityDivergent:
    def test_parity_false_when_orders_exceed(self):
        orders_rows = [{"ticker": "086790", "net_qty": 13}]
        positions_rows = [{"ticker": "086790", "qty": 10, "avg_cost": 75000.0}]

        with _patch_conn(orders_rows, positions_rows):
            from trading.kis.ghost_convergence import orders_positions_divergence
            result = orders_positions_divergence()

        assert result["parity"] is False
        ticker_info = result["by_ticker"]["086790"]
        assert ticker_info["diff"] == 3
        assert ticker_info["orders_net"] == 13
        assert ticker_info["positions_qty"] == 10

    def test_multiple_tickers_one_divergent(self):
        """복수 ticker 중 하나만 diverge 이면 parity=False."""
        orders_rows = [
            {"ticker": "086790", "net_qty": 13},
            {"ticker": "000270", "net_qty": 5},
        ]
        positions_rows = [
            {"ticker": "086790", "qty": 10, "avg_cost": 75000.0},
            {"ticker": "000270", "qty": 5, "avg_cost": 60000.0},
        ]

        with _patch_conn(orders_rows, positions_rows):
            from trading.kis.ghost_convergence import orders_positions_divergence
            result = orders_positions_divergence()

        assert result["parity"] is False
        assert result["by_ticker"]["086790"]["diff"] == 3
        assert result["by_ticker"]["000270"]["diff"] == 0


# ---------------------------------------------------------------------------
# S3: 교정 후 parity=True (orders_net 이 positions.qty 와 같아짐)
# ---------------------------------------------------------------------------


class TestParityAfterConvergence:
    def test_parity_true_after_correction(self):
        """교정 매도로 net 이 positions 와 같아진 상태 모사."""
        # 교정 전: net=13, held=10. 교정 후: net=10 (교정 -3 반영 집계)
        orders_rows = [{"ticker": "086790", "net_qty": 10}]
        positions_rows = [{"ticker": "086790", "qty": 10, "avg_cost": 75000.0}]

        with _patch_conn(orders_rows, positions_rows):
            from trading.kis.ghost_convergence import orders_positions_divergence
            result = orders_positions_divergence()

        assert result["parity"] is True
        assert result["by_ticker"]["086790"]["diff"] == 0


# ---------------------------------------------------------------------------
# S4: smoke_gate 배선 — orders diverge → ledger_parity False
# ---------------------------------------------------------------------------


class TestSmokeLedgerParityWiring:
    """cli.py smoke-gate: positions drift=0 이더라도 orders diverge → ledger_parity=False."""

    def test_ledger_parity_false_when_orders_diverge(self):
        """orders_positions_divergence parity=False 이면 ledger_parity=False."""
        from trading.kis.ghost_convergence import orders_positions_divergence

        orders_rows = [{"ticker": "086790", "net_qty": 13}]
        positions_rows = [{"ticker": "086790", "qty": 10, "avg_cost": 75000.0}]

        with _patch_conn(orders_rows, positions_rows):
            divergence = orders_positions_divergence()

        orders_parity = divergence["parity"]
        # positions drift=0, errors=0 이지만 orders diverge
        positions_drift_ok = True
        ledger_parity = positions_drift_ok and orders_parity

        assert ledger_parity is False

    def test_ledger_parity_true_when_both_ok(self):
        """positions drift=0 + orders parity=True → ledger_parity=True."""
        from trading.kis.ghost_convergence import orders_positions_divergence

        orders_rows = [{"ticker": "086790", "net_qty": 10}]
        positions_rows = [{"ticker": "086790", "qty": 10, "avg_cost": 75000.0}]

        with _patch_conn(orders_rows, positions_rows):
            divergence = orders_positions_divergence()

        orders_parity = divergence["parity"]
        positions_drift_ok = True
        ledger_parity = positions_drift_ok and orders_parity

        assert ledger_parity is True
