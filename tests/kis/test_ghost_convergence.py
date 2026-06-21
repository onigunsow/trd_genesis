"""SPEC-TRADING-042 D1/D6 — ghost_convergence 단위 테스트.

거짓그린 방지(감사 C1/메모리 교훈):
- 집계 SQL 은 dict 직접 주입이 아니라 실 cursor/conn 더블로 SQL 실행 경로를 탄다.
- MultiCursor 패턴으로 복수 execute 호출 순서대로 rows 를 주입.

테스트 시나리오:
  S1: excess>0 → 교정 SELL 행 INSERT (필드 검증)
  S2: paper-only — live 클라이언트이면 no-op (INSERT 없음)
  S3: 멱등 — 교정 후 재실행 시 excess=0 → INSERT 없음
  S4: excess<=0 → no-op (mis-detection 없음)
  S5: orders_positions_divergence — 정합/비정합/교정후 parity
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# 커서 더블 (실 SQL 실행 경로 검증용)
# ---------------------------------------------------------------------------


class MultiCursor:
    """execute() 호출마다 순서대로 다른 rows 를 반환하는 커서 더블.

    거짓그린 방지: dict 직접주입이 아니라 SQL 실행 경로를 탐.
    """

    def __init__(self, rows_sequence: list[list[dict[str, Any]]]) -> None:
        self._seq = list(rows_sequence)
        self._idx = 0
        self.inserted_sqls: list[str] = []
        self.inserted_params: list[Any] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.inserted_sqls.append(sql)
        self.inserted_params.append(params)

    def fetchone(self) -> dict[str, Any] | None:
        rows = self._current_rows()
        return rows[0] if rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        rows = self._current_rows()
        self._idx += 1
        return rows

    def _current_rows(self) -> list[dict[str, Any]]:
        if self._idx < len(self._seq):
            return self._seq[self._idx]
        return self._seq[-1] if self._seq else []

    def __enter__(self) -> MultiCursor:
        return self

    def __exit__(self, *_: Any) -> None:
        pass


class FakeConn:
    def __init__(self, cursor: MultiCursor) -> None:
        self._cur = cursor
        self.committed = False

    def cursor(self) -> MultiCursor:
        return self._cur

    def commit(self) -> None:
        self.committed = True

    def __enter__(self) -> FakeConn:
        return self

    def __exit__(self, *_: Any) -> None:
        pass


def _make_conn_patch(rows_sequence: list[list[dict[str, Any]]]):
    """connection() 컨텍스트 매니저를 MultiCursor 로 교체."""
    cursor = MultiCursor(rows_sequence)
    conn = FakeConn(cursor)

    @contextmanager
    def _conn(autocommit: bool = False):
        yield conn

    return patch("trading.kis.ghost_convergence.connection", side_effect=_conn), conn, cursor


def _paper_client() -> MagicMock:
    from trading.config import TradingMode
    c = MagicMock()
    c.mode = TradingMode.PAPER
    return c


def _live_client() -> MagicMock:
    from trading.config import TradingMode
    c = MagicMock()
    c.mode = TradingMode.LIVE
    return c


# ---------------------------------------------------------------------------
# S1: excess > 0 → 교정 SELL 1행 INSERT
# ---------------------------------------------------------------------------

class TestConvergeGhostBuysExcessInsert:
    """excess > 0 케이스: 교정 SELL 행 1개 INSERT, 필드 검증."""

    def test_inserts_correction_sell_when_excess(self):
        # orders_net = 13 (086790 buy 13 filled), positions.qty = 10 → excess 3
        orders_rows = [{"ticker": "086790", "net_qty": 13}]
        positions_rows = [{"ticker": "086790", "qty": 10, "avg_cost": 75000.0}]

        patch_conn, fake_conn, _cur = _make_conn_patch([orders_rows, positions_rows])
        with patch_conn:
            from trading.kis.ghost_convergence import converge_ghost_buys
            result = converge_ghost_buys(_paper_client(), dry_run=False)

        assert result["converged"] == 1
        assert result["total_excess"] == 3
        assert result["scanned_tickers"] == 1
        assert result["dry_run"] is False
        assert result["skipped_live"] is False
        # commit 호출 확인
        assert fake_conn.committed is True

    def test_correction_sell_insert_sql_fields(self):
        """INSERT SQL 에 correction=TRUE, synthetic=TRUE, side='sell', mode='paper' 포함."""
        orders_rows = [{"ticker": "086790", "net_qty": 5}]
        positions_rows = [{"ticker": "086790", "qty": 2, "avg_cost": 80000.0}]

        patch_conn, _fconn, cur = _make_conn_patch([orders_rows, positions_rows])
        with patch_conn:
            from trading.kis.ghost_convergence import converge_ghost_buys
            converge_ghost_buys(_paper_client(), dry_run=False)

        # INSERT SQL 이 실행됐는지 확인
        insert_sqls = [s for s in cur.inserted_sqls if "INSERT INTO orders" in s]
        assert len(insert_sqls) >= 1, "교정 SELL INSERT SQL 이 실행돼야 한다"
        insert_sql = insert_sqls[0]
        assert "correction" in insert_sql
        assert "synthetic" in insert_sql
        assert "'sell'" in insert_sql
        assert "'paper'" in insert_sql

        # INSERT params: ticker, qty, fill_qty, fill_price, ...
        insert_params = next(
            p for s, p in zip(cur.inserted_sqls, cur.inserted_params, strict=False)
            if "INSERT INTO orders" in s
        )
        assert insert_params[0] == "086790"
        # qty = fill_qty = excess = 3
        assert insert_params[1] == 3
        assert insert_params[2] == 3
        # fill_price = avg_cost = 80000.0
        assert insert_params[3] == 80000.0

    def test_audit_log_inserted(self):
        """GHOST_BUY_CONVERGED 감사 행이 audit_log 에 INSERT 된다."""
        orders_rows = [{"ticker": "000270", "net_qty": 8}]
        positions_rows = [{"ticker": "000270", "qty": 0, "avg_cost": 0.0}]

        patch_conn, _conn, cur = _make_conn_patch(
            [orders_rows, positions_rows, [{"vwap": 60000.0}]]
        )
        with patch_conn:
            from trading.kis.ghost_convergence import converge_ghost_buys
            converge_ghost_buys(_paper_client(), dry_run=False)

        audit_sqls = [s for s in cur.inserted_sqls if "audit_log" in s]
        assert len(audit_sqls) >= 1, "audit_log INSERT 가 있어야 한다"
        # 이벤트 타입 파라미터 확인
        audit_params = next(
            p for s, p in zip(cur.inserted_sqls, cur.inserted_params, strict=False)
            if "audit_log" in s
        )
        assert audit_params[0] == "GHOST_BUY_CONVERGED"


# ---------------------------------------------------------------------------
# S2: paper-only — live 클라이언트는 no-op
# ---------------------------------------------------------------------------

class TestConvergeGhostBuysLiveNoOp:
    """live 모드이면 DB 접근 없이 no-op 요약 반환."""

    def test_live_client_returns_no_op(self):
        # connection 이 호출돼선 안 됨
        with patch("trading.kis.ghost_convergence.connection") as mock_conn:
            from trading.kis.ghost_convergence import converge_ghost_buys
            result = converge_ghost_buys(_live_client(), dry_run=False)

        mock_conn.assert_not_called()
        assert result["skipped_live"] is True
        assert result["converged"] == 0
        assert result["scanned_tickers"] == 0


# ---------------------------------------------------------------------------
# S3: 멱등 — 교정 후 재실행 시 excess=0 → INSERT 없음
# ---------------------------------------------------------------------------

class TestConvergeGhostBuysIdempotent:
    """교정 후 재실행: orders_net == positions_qty → excess=0 → INSERT 없음."""

    def test_no_insert_when_already_converged(self):
        # 교정 매도 포함 후 net=10, positions=10 → excess 0
        orders_rows = [{"ticker": "086790", "net_qty": 10}]
        positions_rows = [{"ticker": "086790", "qty": 10, "avg_cost": 75000.0}]

        patch_conn, _c, cur = _make_conn_patch([orders_rows, positions_rows])
        with patch_conn:
            from trading.kis.ghost_convergence import converge_ghost_buys
            result = converge_ghost_buys(_paper_client(), dry_run=False)

        assert result["converged"] == 0
        assert result["total_excess"] == 0
        # INSERT INTO orders 가 없어야 함
        insert_sqls = [s for s in cur.inserted_sqls if "INSERT INTO orders" in s]
        assert len(insert_sqls) == 0


# ---------------------------------------------------------------------------
# S4: excess <= 0 → no-op
# ---------------------------------------------------------------------------

class TestConvergeGhostBuysNoExcess:
    """orders_net <= positions_qty 이면 INSERT 없음."""

    def test_no_excess_no_insert(self):
        # net=5, held=7 → excess=-2 (orders 부족, D2 parity 에서 감지할 케이스)
        orders_rows = [{"ticker": "071050", "net_qty": 5}]
        positions_rows = [{"ticker": "071050", "qty": 7, "avg_cost": 50000.0}]

        patch_conn, _c, cur = _make_conn_patch([orders_rows, positions_rows])
        with patch_conn:
            from trading.kis.ghost_convergence import converge_ghost_buys
            result = converge_ghost_buys(_paper_client(), dry_run=False)

        assert result["converged"] == 0
        insert_sqls = [s for s in cur.inserted_sqls if "INSERT INTO orders" in s]
        assert len(insert_sqls) == 0

    def test_dry_run_does_not_insert(self):
        """dry_run=True 이면 INSERT 없음."""
        orders_rows = [{"ticker": "086790", "net_qty": 13}]
        positions_rows = [{"ticker": "086790", "qty": 10, "avg_cost": 75000.0}]

        patch_conn, dry_conn, cur = _make_conn_patch([orders_rows, positions_rows])
        with patch_conn:
            from trading.kis.ghost_convergence import converge_ghost_buys
            result = converge_ghost_buys(_paper_client(), dry_run=True)

        assert result["dry_run"] is True
        # dry_run 이므로 commit 없음
        assert dry_conn.committed is False
        # INSERT INTO orders 없음
        insert_sqls = [s for s in cur.inserted_sqls if "INSERT INTO orders" in s]
        assert len(insert_sqls) == 0


# ---------------------------------------------------------------------------
# S5: orders_positions_divergence — parity 검증
# ---------------------------------------------------------------------------

class TestOrdersPositionsDivergence:
    """D2: orders-agg net vs positions.qty parity."""

    def _patch(self, orders_rows, positions_rows):
        patch_conn, _c, _cur = _make_conn_patch([orders_rows, positions_rows])
        return patch_conn

    def test_aligned_parity_true(self):
        """orders_net == positions_qty → parity=True, diff=0."""
        orders_rows = [{"ticker": "086790", "net_qty": 10}]
        positions_rows = [{"ticker": "086790", "qty": 10, "avg_cost": 75000.0}]

        with self._patch(orders_rows, positions_rows):
            from trading.kis.ghost_convergence import orders_positions_divergence
            result = orders_positions_divergence()

        assert result["parity"] is True
        assert result["by_ticker"]["086790"]["diff"] == 0

    def test_orders_excess_parity_false(self):
        """orders_net > positions_qty → parity=False, diff>0."""
        orders_rows = [{"ticker": "086790", "net_qty": 13}]
        positions_rows = [{"ticker": "086790", "qty": 10, "avg_cost": 75000.0}]

        with self._patch(orders_rows, positions_rows):
            from trading.kis.ghost_convergence import orders_positions_divergence
            result = orders_positions_divergence()

        assert result["parity"] is False
        assert result["by_ticker"]["086790"]["diff"] == 3

    def test_after_convergence_parity_true(self):
        """교정 매도 포함 후 net==positions → parity=True."""
        # 교정 매도가 net 을 10 으로 줄인 결과를 모사
        orders_rows = [{"ticker": "086790", "net_qty": 10}]
        positions_rows = [{"ticker": "086790", "qty": 10, "avg_cost": 75000.0}]

        with self._patch(orders_rows, positions_rows):
            from trading.kis.ghost_convergence import orders_positions_divergence
            result = orders_positions_divergence()

        assert result["parity"] is True
