"""SPEC-TRADING-029 — KIS order lifecycle sync (fills).

Phase A (TDD RED) tests for ``src/trading/kis/fills.py``:

- inquire_fills_today: request shape, response parsing
- apply_fill_to_order: status transition matrix (REQ-029-2)
- apply_fill_to_position: UPSERT + weighted-avg + sell decrement (REQ-029-3)
- fill_sync orchestrator: dry-run, error isolation, unknown-order handling

All tests use mocked KIS responses and in-memory recording cursors / connections.
No real KIS API calls and no live DB connections are made.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytz

from trading.kis.client import KisResponse

# ---------------------------------------------------------------------------
# In-memory DB mocks (richer than the global conftest's FakeCursor)
# ---------------------------------------------------------------------------


class RecordingCursor:
    """psycopg-style cursor that records every execute() call.

    fetch_responses is a list — fetchone() pops the head on each call so a test
    can pre-program a sequence of SELECT results.
    """

    def __init__(self, fetch_responses: list[dict[str, Any] | None] | None = None):
        self.calls: list[tuple[str, Any]] = []
        self._fetch_queue = list(fetch_responses or [])

    def execute(self, sql: str, params: Any = None) -> None:
        self.calls.append((sql, params))

    def fetchone(self) -> dict[str, Any] | None:
        if self._fetch_queue:
            return self._fetch_queue.pop(0)
        return None

    def fetchall(self) -> list[dict[str, Any]]:
        return []

    def __enter__(self) -> RecordingCursor:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class RecordingConn:
    """Mock psycopg connection that yields one RecordingCursor and tracks commits."""

    def __init__(self, cursor: RecordingCursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> RecordingCursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        return None

    def __enter__(self) -> RecordingConn:
        return self

    def __exit__(self, exc_type: Any, *_: Any) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()


def _make_kis_client_mock(*, is_paper: bool = True) -> MagicMock:
    """Build a KisClient look-alike mock with tr_id / account / get / mode."""
    client = MagicMock()
    client.account_prefix = "50123456"
    client.account_suffix = "01"
    client.mode = MagicMock(value="paper" if is_paper else "live")
    # tr_id selects paper_id by default (matches client.tr_id() semantics for paper)
    client.tr_id = MagicMock(
        side_effect=lambda paper_id, live_id: paper_id if is_paper else live_id
    )
    return client


def _kis_response(output1: list[dict[str, Any]], rt_cd: str = "0") -> KisResponse:
    raw = {"rt_cd": rt_cd, "msg_cd": "", "msg1": "", "output1": output1, "output2": [{}]}
    return KisResponse(
        status_code=200,
        rt_cd=rt_cd,
        msg_cd="" if rt_cd == "0" else "ERR",
        msg="OK" if rt_cd == "0" else "error",
        output=output1,
        raw=raw,
    )


def _today_yyyymmdd() -> str:
    return datetime.now(pytz.timezone("Asia/Seoul")).strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# Sample KIS output1 rows (best-guess shape per research.md §3.3)
# ---------------------------------------------------------------------------


def _kis_row(
    *,
    odno: str = "0000001977",
    pdno: str = "086790",
    sll_buy: str = "02",  # 02=buy
    ord_qty: str = "1",
    tot_ccld_qty: str = "1",
    avg_price: str = "50000",
    cncl_yn: str = "N",
    rfus_yn: str = "N",
) -> dict[str, Any]:
    """Synthesise a single KIS inquire-daily-ccld output1 row."""
    return {
        "odno": odno,
        "ord_dt": _today_yyyymmdd(),
        "pdno": pdno,
        "sll_buy_dvsn_cd": sll_buy,
        "ord_qty": ord_qty,
        "tot_ccld_qty": tot_ccld_qty,
        "avg_prvs": avg_price,
        "pchs_avg_pric": avg_price,
        "cncl_yn": cncl_yn,
        "rfus_yn": rfus_yn,
        "rmn_qty": str(int(ord_qty) - int(tot_ccld_qty)),
    }


# ============================================================================
# inquire_fills_today  (REQ-029-1)
# ============================================================================


class TestInquireFillsToday:
    def test_calls_kis_with_correct_path_tr_id_and_today_params(self):
        """REQ-029-1: GET inquire-daily-ccld, paper tr_id=VTTC8001R, today's date."""
        from trading.kis import fills

        client = _make_kis_client_mock(is_paper=True)
        client.get.return_value = _kis_response(output1=[])

        fills.inquire_fills_today(client)

        # tr_id dispatch
        client.tr_id.assert_called_once_with(paper_id="VTTC8001R", live_id="TTTC8001R")

        # path + key params
        assert client.get.call_count == 1
        call = client.get.call_args
        path = call.args[0] if call.args else call.kwargs.get("path")
        assert path == "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"

        params = call.kwargs.get("params") or (call.args[2] if len(call.args) > 2 else {})
        today = _today_yyyymmdd()
        assert params["INQR_STRT_DT"] == today
        assert params["INQR_END_DT"] == today
        assert params["CCLD_DVSN"] == "00"  # 전체 (partial + full)
        assert params["CANO"] == "50123456"
        assert params["ACNT_PRDT_CD"] == "01"
        assert params["SLL_BUY_DVSN_CD"] == "00"

    def test_parses_output1_into_fill_rows(self):
        """REQ-029-1: output1 → list[FillRow] with full / partial / cancelled mix."""
        from trading.kis import fills

        client = _make_kis_client_mock()
        client.get.return_value = _kis_response(
            output1=[
                _kis_row(odno="0001", pdno="086790", ord_qty="1", tot_ccld_qty="1",
                         avg_price="50000"),
                _kis_row(odno="0002", pdno="005930", ord_qty="10", tot_ccld_qty="3",
                         avg_price="70000"),
                _kis_row(odno="0003", pdno="055550", ord_qty="1", tot_ccld_qty="0",
                         cncl_yn="Y"),
            ]
        )

        rows = fills.inquire_fills_today(client)
        assert len(rows) == 3

        assert rows[0].odno == "0001"
        assert rows[0].pdno == "086790"
        assert rows[0].side == "buy"
        assert rows[0].ord_qty == 1
        assert rows[0].tot_ccld_qty == 1
        assert rows[0].avg_fill_price == 50000

        assert rows[1].ord_qty == 10
        assert rows[1].tot_ccld_qty == 3
        assert rows[1].avg_fill_price == 70000

        assert rows[2].cncl_yn is True
        assert rows[2].rfus_yn is False

    def test_handles_empty_output1(self):
        """No fills today → empty list, no crash."""
        from trading.kis import fills

        client = _make_kis_client_mock()
        client.get.return_value = _kis_response(output1=[])
        rows = fills.inquire_fills_today(client)
        assert rows == []

    def test_propagates_kis_error_on_non_success_rt_cd(self):
        """rt_cd != '0' (non-rate-limit) → KisError raised."""
        from trading.kis import fills
        from trading.kis.client import KisError

        client = _make_kis_client_mock()
        client.get.return_value = _kis_response(output1=[], rt_cd="1")
        with pytest.raises(KisError):
            fills.inquire_fills_today(client)

    def test_parser_handles_missing_or_garbage_fields(self):
        """KIS sometimes returns empty strings / floats for numeric fields."""
        from trading.kis import fills

        client = _make_kis_client_mock()
        client.get.return_value = _kis_response(
            output1=[
                {
                    "odno": "0001",
                    "ord_dt": _today_yyyymmdd(),
                    "pdno": "086790",
                    "sll_buy_dvsn_cd": "02",
                    "ord_qty": "",          # empty → 0
                    "tot_ccld_qty": "1.0",  # float-string → 1
                    "avg_prvs": "garbage",  # non-numeric → 0
                    "cncl_yn": None,
                    "rfus_yn": "n",         # lowercase 'n' → False
                },
            ]
        )

        rows = fills.inquire_fills_today(client)
        assert len(rows) == 1
        r = rows[0]
        assert r.ord_qty == 0
        assert r.tot_ccld_qty == 1
        assert r.avg_fill_price == 0
        assert r.cncl_yn is False
        assert r.rfus_yn is False


# ============================================================================
# apply_fill_to_order  (REQ-029-2: status transition matrix)
# ============================================================================


def _existing_order(
    *,
    order_id: int = 100,
    status: str = "submitted",
    side: str = "buy",
    qty: int = 1,
    ticker: str = "086790",
) -> dict[str, Any]:
    return {
        "id": order_id,
        "status": status,
        "side": side,
        "qty": qty,
        "ticker": ticker,
    }


class TestApplyFillToOrder:
    def test_full_fill_transitions_to_filled(self):
        """REQ-029-2: tot_ccld_qty == ord_qty → status='filled'."""
        from trading.kis import fills

        cursor = RecordingCursor(fetch_responses=[_existing_order(qty=1)])
        conn = RecordingConn(cursor)
        fill = fills.FillRow(
            odno="0001", ord_dt=_today_yyyymmdd(), pdno="086790", side="buy",
            ord_qty=1, tot_ccld_qty=1, avg_fill_price=50000,
            cncl_yn=False, rfus_yn=False, raw={},
        )

        new_status = fills.apply_fill_to_order(fill, conn)

        assert new_status == "filled"
        # Verify there is an UPDATE orders ... status='filled' SQL call
        update_calls = [(s, p) for s, p in cursor.calls if "UPDATE ORDERS" in s.upper()]
        assert len(update_calls) >= 1
        sql, params = update_calls[0]
        assert "filled" in str(params)
        # filled_at set via now() so column appears in UPDATE
        assert "filled_at" in sql.lower()
        # audit_log ORDER_FILLED
        audit_calls = [(s, p) for s, p in cursor.calls if "audit_log" in s.lower()]
        assert any("ORDER_FILLED" in str(p) for _, p in audit_calls)

    def test_partial_transitions_to_partial(self):
        """REQ-029-2: 0 < tot_ccld_qty < ord_qty → status='partial'."""
        from trading.kis import fills

        cursor = RecordingCursor(fetch_responses=[_existing_order(qty=10)])
        conn = RecordingConn(cursor)
        fill = fills.FillRow(
            odno="0002", ord_dt=_today_yyyymmdd(), pdno="005930", side="buy",
            ord_qty=10, tot_ccld_qty=3, avg_fill_price=70000,
            cncl_yn=False, rfus_yn=False, raw={},
        )

        new_status = fills.apply_fill_to_order(fill, conn)
        assert new_status == "partial"
        audit_calls = [(s, p) for s, p in cursor.calls if "audit_log" in s.lower()]
        assert any("ORDER_PARTIAL" in str(p) for _, p in audit_calls)

    def test_cancelled_transitions_to_cancelled(self):
        """REQ-029-2: cncl_yn='Y' → status='cancelled'."""
        from trading.kis import fills

        cursor = RecordingCursor(fetch_responses=[_existing_order()])
        conn = RecordingConn(cursor)
        fill = fills.FillRow(
            odno="0003", ord_dt=_today_yyyymmdd(), pdno="055550", side="buy",
            ord_qty=1, tot_ccld_qty=0, avg_fill_price=0,
            cncl_yn=True, rfus_yn=False, raw={},
        )

        new_status = fills.apply_fill_to_order(fill, conn)
        assert new_status == "cancelled"
        audit_calls = [(s, p) for s, p in cursor.calls if "audit_log" in s.lower()]
        assert any("ORDER_CANCELLED" in str(p) for _, p in audit_calls)

    def test_rejected_transitions_to_rejected(self):
        """REQ-029-2: rfus_yn='Y' → status='rejected' with KIS reason."""
        from trading.kis import fills

        cursor = RecordingCursor(fetch_responses=[_existing_order()])
        conn = RecordingConn(cursor)
        fill = fills.FillRow(
            odno="0004", ord_dt=_today_yyyymmdd(), pdno="086790", side="buy",
            ord_qty=1, tot_ccld_qty=0, avg_fill_price=0,
            cncl_yn=False, rfus_yn=True, raw={"reject_reason": "KIS_RFUS"},
        )

        new_status = fills.apply_fill_to_order(fill, conn)
        assert new_status == "rejected"
        audit_calls = [(s, p) for s, p in cursor.calls if "audit_log" in s.lower()]
        assert any("ORDER_REJECTED_BY_KIS" in str(p) for _, p in audit_calls)

    def test_no_fill_keeps_submitted(self):
        """REQ-029-2: tot_ccld_qty=0, not cancelled, not rejected → no-op."""
        from trading.kis import fills

        cursor = RecordingCursor(fetch_responses=[_existing_order()])
        conn = RecordingConn(cursor)
        fill = fills.FillRow(
            odno="0005", ord_dt=_today_yyyymmdd(), pdno="086790", side="buy",
            ord_qty=1, tot_ccld_qty=0, avg_fill_price=0,
            cncl_yn=False, rfus_yn=False, raw={},
        )

        new_status = fills.apply_fill_to_order(fill, conn)
        assert new_status == "submitted"
        # No UPDATE orders SET status='...' nor audit_log INSERT for transition
        update_calls = [s for s, _ in cursor.calls if "UPDATE ORDERS" in s.upper()]
        assert len(update_calls) == 0
        audit_calls = [s for s, _ in cursor.calls if "audit_log" in s.lower()]
        assert len(audit_calls) == 0

    def test_select_uses_for_update_row_lock(self):
        """REQ-029-2 constraint: SELECT FOR UPDATE prevents concurrent transition."""
        from trading.kis import fills

        cursor = RecordingCursor(fetch_responses=[_existing_order()])
        conn = RecordingConn(cursor)
        fill = fills.FillRow(
            odno="0006", ord_dt=_today_yyyymmdd(), pdno="086790", side="buy",
            ord_qty=1, tot_ccld_qty=1, avg_fill_price=50000,
            cncl_yn=False, rfus_yn=False, raw={},
        )

        fills.apply_fill_to_order(fill, conn)
        # First call should be SELECT with FOR UPDATE
        select_call = next((s for s, _ in cursor.calls if "SELECT" in s.upper()), "")
        assert select_call, "expected a SELECT call before UPDATE"
        assert "FOR UPDATE" in select_call.upper()

    def test_unknown_order_returns_none(self):
        """EC-029-3: KIS returns row for order not in local DB → return None."""
        from trading.kis import fills

        cursor = RecordingCursor(fetch_responses=[None])  # SELECT FOR UPDATE finds nothing
        conn = RecordingConn(cursor)
        fill = fills.FillRow(
            odno="UNKNOWN", ord_dt=_today_yyyymmdd(), pdno="086790", side="buy",
            ord_qty=1, tot_ccld_qty=1, avg_fill_price=50000,
            cncl_yn=False, rfus_yn=False, raw={},
        )

        result = fills.apply_fill_to_order(fill, conn)
        assert result is None
        # No UPDATE / INSERT should happen
        write_calls = [
            s for s, _ in cursor.calls
            if "UPDATE ORDERS" in s.upper() or "audit_log" in s.lower()
        ]
        assert write_calls == []

    def test_already_filled_is_idempotent(self):
        """EC-029-4: order already in terminal state → no re-transition, no audit."""
        from trading.kis import fills

        cursor = RecordingCursor(fetch_responses=[_existing_order(status="filled")])
        conn = RecordingConn(cursor)
        fill = fills.FillRow(
            odno="0001", ord_dt=_today_yyyymmdd(), pdno="086790", side="buy",
            ord_qty=1, tot_ccld_qty=1, avg_fill_price=50000,
            cncl_yn=False, rfus_yn=False, raw={},
        )

        result = fills.apply_fill_to_order(fill, conn)
        # Should return existing terminal status, NOT re-UPDATE
        assert result == "filled"
        update_calls = [s for s, _ in cursor.calls if "UPDATE ORDERS" in s.upper()]
        assert update_calls == [], "must not re-update an already-terminal row"
        audit_calls = [s for s, _ in cursor.calls if "audit_log" in s.lower()]
        assert audit_calls == [], "must not emit duplicate audit_log"


# ============================================================================
# apply_fill_to_position  (REQ-029-3)
# ============================================================================


class TestApplyFillToPosition:
    def test_buy_upserts_new_row_uses_on_conflict(self):
        """REQ-029-3: BUY fill on empty positions → INSERT ON CONFLICT pattern."""
        from trading.kis import fills

        cursor = RecordingCursor()
        conn = RecordingConn(cursor)
        fill = fills.FillRow(
            odno="0001", ord_dt=_today_yyyymmdd(), pdno="086790", side="buy",
            ord_qty=1, tot_ccld_qty=1, avg_fill_price=100000,
            cncl_yn=False, rfus_yn=False, raw={},
        )

        fills.apply_fill_to_position(fill, conn, order_id=42)

        # Must contain INSERT ... ON CONFLICT (ticker) DO UPDATE
        upsert = next(
            (s for s, _ in cursor.calls
             if "INSERT" in s.upper() and "ON CONFLICT" in s.upper()),
            None,
        )
        assert upsert is not None, "expected INSERT ... ON CONFLICT (ticker) DO UPDATE"
        assert "(ticker)" in upsert.lower() or "ticker" in upsert.lower()

    def test_buy_audit_log_position_updated(self):
        """REQ-029-3: every position update emits POSITION_UPDATED audit_log."""
        from trading.kis import fills

        cursor = RecordingCursor()
        conn = RecordingConn(cursor)
        fill = fills.FillRow(
            odno="0001", ord_dt=_today_yyyymmdd(), pdno="086790", side="buy",
            ord_qty=1, tot_ccld_qty=1, avg_fill_price=100000,
            cncl_yn=False, rfus_yn=False, raw={},
        )

        fills.apply_fill_to_position(fill, conn, order_id=42)

        audit_calls = [(s, p) for s, p in cursor.calls if "audit_log" in s.lower()]
        assert any("POSITION_UPDATED" in str(p) for _, p in audit_calls)

    def test_sell_updates_with_greatest_clamp(self):
        """REQ-029-3 (SELL): qty = GREATEST(qty - fill_qty, 0), avg_cost unchanged."""
        from trading.kis import fills

        cursor = RecordingCursor()
        conn = RecordingConn(cursor)
        fill = fills.FillRow(
            odno="0009", ord_dt=_today_yyyymmdd(), pdno="086790", side="sell",
            ord_qty=2, tot_ccld_qty=2, avg_fill_price=120000,
            cncl_yn=False, rfus_yn=False, raw={},
        )

        fills.apply_fill_to_position(fill, conn, order_id=99)

        sell_update = next(
            (s for s, _ in cursor.calls
             if "UPDATE POSITIONS" in s.upper() and "GREATEST" in s.upper()),
            None,
        )
        assert sell_update is not None, (
            "expected UPDATE positions ... GREATEST(qty - %s, 0) ... for SELL"
        )

    def test_sell_does_not_use_on_conflict(self):
        """REQ-029-3: SELL is plain UPDATE (existing row), no INSERT/UPSERT."""
        from trading.kis import fills

        cursor = RecordingCursor()
        conn = RecordingConn(cursor)
        fill = fills.FillRow(
            odno="0010", ord_dt=_today_yyyymmdd(), pdno="086790", side="sell",
            ord_qty=1, tot_ccld_qty=1, avg_fill_price=120000,
            cncl_yn=False, rfus_yn=False, raw={},
        )

        fills.apply_fill_to_position(fill, conn, order_id=100)

        insert_upserts = [
            s for s, _ in cursor.calls
            if "INSERT INTO POSITIONS" in s.upper()
        ]
        assert insert_upserts == [], "SELL must not INSERT positions"


# ============================================================================
# fill_sync orchestrator  (REQ-029-4 / REQ-029-5)
# ============================================================================


@contextmanager
def _mock_connection_factory(cursor: RecordingCursor):
    """Yield a recording connection that mimics trading.db.session.connection()."""
    conn = RecordingConn(cursor)
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise


class TestFillSyncOrchestrator:
    def test_dry_run_performs_no_db_writes(self):
        """REQ-029-5 + AC-029-6: --dry-run = zero DB UPDATE/INSERT."""
        from trading.kis import fills

        client = _make_kis_client_mock()
        client.get.return_value = _kis_response(
            output1=[_kis_row(odno="0001", ord_qty="1", tot_ccld_qty="1")]
        )

        cursor = RecordingCursor(fetch_responses=[_existing_order()])

        with patch.object(fills, "connection",
                          side_effect=lambda *a, **kw: _mock_connection_factory(cursor)):
            result = fills.fill_sync(client, dry_run=True)

        assert result["dry_run"] is True
        # Zero UPDATE / INSERT statements recorded
        writes = [
            s for s, _ in cursor.calls
            if any(kw in s.upper() for kw in ("UPDATE ORDERS", "UPDATE POSITIONS",
                                              "INSERT INTO POSITIONS",
                                              "INSERT INTO AUDIT_LOG"))
        ]
        assert writes == [], f"dry_run made writes: {writes}"

    def test_full_cycle_two_orders_one_fill_one_partial(self):
        """End-to-end: KIS returns 2 rows → 2 transitions + 2 position updates."""
        from trading.kis import fills

        client = _make_kis_client_mock()
        client.get.return_value = _kis_response(
            output1=[
                _kis_row(odno="0001", pdno="086790", ord_qty="1", tot_ccld_qty="1",
                         avg_price="50000"),
                _kis_row(odno="0002", pdno="005930", ord_qty="10", tot_ccld_qty="3",
                         avg_price="70000"),
            ]
        )

        # Each call to connection() returns a fresh conn with a fresh cursor
        # whose fetchone() returns an existing order row.
        all_cursors: list[RecordingCursor] = []

        @contextmanager
        def _conn_factory(*_: Any, **__: Any):
            cursor = RecordingCursor(fetch_responses=[
                _existing_order(order_id=10, qty=1, side="buy", ticker="086790"),
                _existing_order(order_id=11, qty=10, side="buy", ticker="005930"),
            ])
            all_cursors.append(cursor)
            yield RecordingConn(cursor)

        with patch.object(fills, "connection", side_effect=_conn_factory):
            result = fills.fill_sync(client, dry_run=False)

        assert result["queried"] == 2
        assert result["transitioned"] == 2
        assert result.get("errors", 0) == 0

        # All cursors combined must contain at least 2 ORDER_* audit events + 2 POSITION_UPDATED
        all_calls = [c for cur in all_cursors for c in cur.calls]
        order_audits = [
            p for s, p in all_calls
            if "audit_log" in s.lower() and any(
                ev in str(p) for ev in ("ORDER_FILLED", "ORDER_PARTIAL"))
        ]
        pos_audits = [
            p for s, p in all_calls
            if "audit_log" in s.lower() and "POSITION_UPDATED" in str(p)
        ]
        assert len(order_audits) == 2
        assert len(pos_audits) == 2

    def test_error_in_one_row_does_not_block_others(self):
        """Error isolation: one bad row writes ORDER_SYNC_ERROR audit, others proceed."""
        from trading.kis import fills

        client = _make_kis_client_mock()
        client.get.return_value = _kis_response(
            output1=[
                _kis_row(odno="BAD", ord_qty="1", tot_ccld_qty="1"),
                _kis_row(odno="GOOD", ord_qty="1", tot_ccld_qty="1"),
            ]
        )

        conn_n = {"i": 0}

        @contextmanager
        def _conn_factory(*_: Any, **__: Any):
            conn_n["i"] += 1
            n = conn_n["i"]
            if n == 1:
                # First (BAD) row: raise on .cursor() to simulate DB error
                bad = MagicMock()
                bad.cursor.side_effect = RuntimeError("simulated DB failure")
                # Make it a working context manager
                bad.__enter__ = MagicMock(return_value=bad)
                bad.__exit__ = MagicMock(return_value=False)
                yield bad
            elif n == 2:
                # Error path: fresh connection used for ORDER_SYNC_ERROR audit
                yield RecordingConn(RecordingCursor())
            else:
                # Second (GOOD) row: normal flow
                yield RecordingConn(RecordingCursor(
                    fetch_responses=[_existing_order(order_id=20)]
                ))

        with patch.object(fills, "connection", side_effect=_conn_factory):
            result = fills.fill_sync(client, dry_run=False)

        assert result["queried"] == 2
        assert result["errors"] == 1
        # The GOOD row should still have transitioned
        assert result["transitioned"] == 1

    def test_unknown_kis_order_is_logged_and_skipped(self):
        """EC-029-3: KIS row whose odno is not in local DB → warning, continue."""
        from trading.kis import fills

        client = _make_kis_client_mock()
        client.get.return_value = _kis_response(
            output1=[
                _kis_row(odno="UNKNOWN", ord_qty="1", tot_ccld_qty="1"),
                _kis_row(odno="0001", ord_qty="1", tot_ccld_qty="1"),
            ]
        )

        # First connection: SELECT returns None (unknown order)
        # Second connection: SELECT returns the existing order
        connection_count = {"n": 0}

        @contextmanager
        def _conn_factory(*_: Any, **__: Any):
            connection_count["n"] += 1
            if connection_count["n"] == 1:
                cur = RecordingCursor(fetch_responses=[None])
            else:
                cur = RecordingCursor(fetch_responses=[_existing_order()])
            yield RecordingConn(cur)

        with patch.object(fills, "connection", side_effect=_conn_factory):
            result = fills.fill_sync(client, dry_run=False)

        # Should not raise; should still process the second row
        assert result["queried"] == 2
        # One transition succeeded for the known order
        assert result["transitioned"] == 1
