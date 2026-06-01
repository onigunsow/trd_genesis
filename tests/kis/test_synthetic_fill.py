"""SPEC-TRADING-039 — paper synthetic fill (reproduction-first).

Covers the 2026-06-01 regression: a paper SELL submitted to KIS stays in
``status='submitted'`` forever (no fill path), so round-trips never complete and
``daily_pnl_pct`` reports a phantom loss.

AC-1  paper SELL (and BUY) become 'filled' with fill_qty/fill_price at submit time.
AC-2  over-sell is clamped to held qty (the dropped excess is audited; never short).
AC-3  live mode is never synthetically filled (paper-only hard gate, byte-for-byte
      unchanged live path).
AC-5  reference-price quote failure → audit + graceful skip (order stays submitted,
      no crash, reconcile takes over).

All tests are offline: ``client.post`` returns a scripted KisResponse, the DB
connection is a ``ScriptedCursor`` and ``current_price`` / ``balance`` are patched.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

from trading.config import TradingMode
from trading.kis.client import KisResponse

# ---------------------------------------------------------------------------
# Scripted in-memory DB doubles (mirrors tests/kis/test_fills_balance_reconcile.py)
# ---------------------------------------------------------------------------


class ScriptedCursor:
    """Cursor that records executes and returns scripted fetch results."""

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


@contextmanager
def _conn_factory(cursor: ScriptedCursor):
    yield ScriptedConn(cursor)


class _AuditSink:
    """Captures ``order.audit(event, actor, details)`` calls (db.session.audit
    opens its own connection, so it is patched separately from order.connection)."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def __call__(self, event_type: str, actor: str = "system",
                 details: Any = None) -> None:
        self.events.append(event_type)


@contextmanager
def _patched(order_mod, cursor: ScriptedCursor, *,
             current_price=None, balance=None):
    """Patch order.connection + order.audit (+ optional current_price/balance)."""
    sink = _AuditSink()
    patches = [
        patch.object(order_mod, "connection",
                     side_effect=lambda *a, **k: _conn_factory(cursor)),
        patch.object(order_mod, "audit", sink),
    ]
    if current_price is not None:
        patches.append(patch.object(order_mod, "current_price", current_price))
    if balance is not None:
        patches.append(patch.object(order_mod, "balance", balance))
    for p in patches:
        p.start()
    try:
        yield sink
    finally:
        for p in reversed(patches):
            p.stop()


def _all_events(cursor: ScriptedCursor, sink: _AuditSink) -> list[str]:
    """Union of cursor-recorded audit_log inserts and order.audit() calls."""
    return _audit_events(cursor) + sink.events


def _ok_response(odno: str = "0000117057") -> KisResponse:
    return KisResponse(
        status_code=200,
        rt_cd="0",
        msg_cd="APBK0013",
        msg="주문 전송 완료",
        output={"ODNO": odno, "KRX_FWDG_ORD_ORGNO": "00950"},
        raw={"rt_cd": "0", "output": {"ODNO": odno}},
    )


def _audit_events(cursor: ScriptedCursor) -> list[str]:
    events: list[str] = []
    for sql, params in cursor.calls:
        if "audit_log" in sql.lower() and params:
            events.append(str(params[0]))
    return events


def _order_updates(cursor: ScriptedCursor) -> list[tuple[str, Any]]:
    """All synthetic-fill `UPDATE orders ... SET status='filled' ... fill_qty=...` calls."""
    out = []
    for sql, params in cursor.calls:
        up = sql.upper()
        if "UPDATE ORDERS" in up and "FILL_QTY" in up and "SYNTHETIC" in up:
            out.append((sql, params))
    return out


def _paper_client() -> MagicMock:
    client = MagicMock()
    client.mode = TradingMode.PAPER
    client.account_prefix = "50000000"
    client.account_suffix = "01"
    client.tr_id.side_effect = lambda paper_id, live_id: paper_id
    client.post.return_value = _ok_response()
    return client


def _live_client() -> MagicMock:
    client = MagicMock()
    client.mode = TradingMode.LIVE
    client.account_prefix = "50000000"
    client.account_suffix = "01"
    client.tr_id.side_effect = lambda paper_id, live_id: live_id
    client.post.return_value = _ok_response()
    return client


def _quote(price: int) -> dict[str, Any]:
    return {"ticker": "X", "price": price, "is_normal": True, "raw": {}}


def _bal(holdings: list[dict]) -> dict:
    return {"holdings": holdings, "raw": {}}


def _held(ticker: str, qty: int, avg_cost: int = 50_000) -> dict:
    return {"ticker": ticker, "qty": qty, "avg_cost": avg_cost,
            "current_price": avg_cost, "eval_amount": qty * avg_cost,
            "pnl_amount": 0, "pnl_pct": 0.0, "name": ""}


# ---------------------------------------------------------------------------
# AC-1 — paper SELL/BUY synthetic fill
# ---------------------------------------------------------------------------


class TestPaperSyntheticFill:
    def test_paper_market_sell_becomes_filled(self):
        """AC-1: paper market SELL of 3 @ inquire-price 55,900 → status=filled."""
        from trading.kis import order

        client = _paper_client()
        # INSERT RETURNING id (pre-create) → 42; positions SELECT inside synthetic
        # fill; final status re-read returns 'filled'.
        cursor = ScriptedCursor(
            fetchone_queue=[{"id": 42}, {"qty": 3, "avg_cost": 55000},
                            {"status": "filled"}],
        )
        with _patched(order, cursor,
                      current_price=MagicMock(return_value=_quote(55_900)),
                      balance=MagicMock(return_value=_bal([_held("086790", 3, 55000)]))) as sink:
            result = order.submit_order(
                client, ticker="086790", qty=3, side="sell", order_type="market",
            )

        ups = _order_updates(cursor)
        assert ups, "expected a synthetic fill UPDATE on the sell order"
        sql, params = ups[-1]
        assert "filled" in sql.lower()       # status='filled' literal in SQL
        assert 55_900 in params              # fill_price = inquire-price current price
        assert 3 in params                   # fill_qty = order qty
        assert "ORDER_FILLED_SYNTHETIC" in _all_events(cursor, sink)
        assert result["status"] == "filled"

    def test_paper_market_buy_becomes_filled(self):
        """AC-1 symmetry: paper market BUY is filled synthetically too."""
        from trading.kis import order

        client = _paper_client()
        cursor = ScriptedCursor(
            fetchone_queue=[{"id": 7}, {"qty": 0, "avg_cost": 0}, {"status": "filled"}],
        )
        with _patched(order, cursor,
                      current_price=MagicMock(return_value=_quote(168_675)),
                      balance=MagicMock(return_value=_bal([]))) as sink:
            result = order.submit_order(
                client, ticker="000270", qty=1, side="buy", order_type="market",
            )

        ups = _order_updates(cursor)
        assert ups
        assert "filled" in ups[-1][0].lower()
        assert 168_675 in ups[-1][1]
        assert "ORDER_FILLED_SYNTHETIC" in _all_events(cursor, sink)
        assert result["status"] == "filled"

    def test_paper_limit_order_uses_limit_price(self):
        """AC-1/REQ-039-3: a limit order fills at limit_price (no quote call)."""
        from trading.kis import order

        client = _paper_client()
        cursor = ScriptedCursor(
            fetchone_queue=[{"id": 9}, {"qty": 2, "avg_cost": 80000}, {"status": "filled"}],
        )
        cp = MagicMock(side_effect=AssertionError("current_price must not be called for limit"))
        with _patched(order, cursor, current_price=cp,
                      balance=MagicMock(return_value=_bal([_held("055550", 2, 80000)]))):
            result = order.submit_order(
                client, ticker="055550", qty=2, side="sell",
                order_type="limit", limit_price=84_000,
            )

        ups = _order_updates(cursor)
        assert ups
        assert 84_000 in ups[-1][1]   # fill_price = limit_price
        assert result["status"] == "filled"

    def test_synthetic_marker_set_true(self):
        """AC-1: the synthetic column is set TRUE on the fill UPDATE."""
        from trading.kis import order

        client = _paper_client()
        cursor = ScriptedCursor(
            fetchone_queue=[{"id": 11}, {"qty": 1, "avg_cost": 50000}, {"status": "filled"}],
        )
        with _patched(order, cursor,
                      current_price=MagicMock(return_value=_quote(50_000)),
                      balance=MagicMock(return_value=_bal([_held("005930", 1, 50000)]))):
            order.submit_order(client, ticker="005930", qty=1, side="sell")

        ups = _order_updates(cursor)
        assert ups
        assert "synthetic" in ups[-1][0].lower()   # synthetic = TRUE literal in SQL


# ---------------------------------------------------------------------------
# AC-2 — over-sell clamp
# ---------------------------------------------------------------------------


class TestOverSellClamp:
    def test_oversell_clamped_to_held(self):
        """AC-2: sell 2 of a held 1 → fill_qty clamped to 1, excess audited."""
        from trading.kis import order

        client = _paper_client()
        cursor = ScriptedCursor(
            fetchone_queue=[{"id": 5}, {"qty": 1, "avg_cost": 84000}, {"status": "filled"}],
        )
        with _patched(order, cursor,
                      current_price=MagicMock(return_value=_quote(84_000)),
                      balance=MagicMock(return_value=_bal([_held("055550", 1, 84000)]))) as sink:
            order.submit_order(client, ticker="055550", qty=2, side="sell")

        ups = _order_updates(cursor)
        assert ups, "held portion (1 share) must still be exited"
        # fill_qty must be 1 (held), never 2 (over-sell forbidden).
        assert 1 in ups[-1][1]
        assert 2 not in ups[-1][1]
        assert "OVERSELL_CLAMPED" in _all_events(cursor, sink)

    def test_sell_with_zero_held_skips_fill(self):
        """AC-2 edge: sell something not held → no fill, audited, never short."""
        from trading.kis import order

        client = _paper_client()
        cursor = ScriptedCursor(fetchone_queue=[{"id": 6}, {"status": "submitted"}])
        with _patched(order, cursor,
                      current_price=MagicMock(return_value=_quote(10_000)),
                      balance=MagicMock(return_value=_bal([]))) as sink:
            order.submit_order(client, ticker="999999", qty=2, side="sell")

        # No synthetic fill UPDATE at all (nothing held to exit).
        assert _order_updates(cursor) == []
        assert "OVERSELL_CLAMPED" in _all_events(cursor, sink)


# ---------------------------------------------------------------------------
# AC-3 — live mode untouched (paper-only hard gate)
# ---------------------------------------------------------------------------


class TestLiveHardGate:
    def test_live_order_never_synthetically_filled(self):
        """AC-3: live mode must never set status=filled at submit (synthetic off).

        live_unlocked is forced true so the live gate passes and we exercise the
        normal live submission path; it must remain 'submitted' with no synthetic
        fill UPDATE and no ORDER_FILLED_SYNTHETIC audit. current_price/balance must
        never be touched on the live path.
        """
        from trading.kis import order

        client = _live_client()
        cursor = ScriptedCursor(fetchone_queue=[{"id": 99}, {"status": "submitted"}])
        cp = MagicMock(side_effect=AssertionError("current_price must not be called in live"))
        bal = MagicMock(side_effect=AssertionError("balance must not be called in live"))

        with patch.object(order, "get_system_state",
                          return_value={"live_unlocked": True}):
            with _patched(order, cursor, current_price=cp, balance=bal) as sink:
                result = order.submit_order(client, ticker="005930", qty=1, side="sell")

        assert result["status"] == "submitted"
        assert _order_updates(cursor) == [], "live must not perform a synthetic fill UPDATE"
        assert "ORDER_FILLED_SYNTHETIC" not in _all_events(cursor, sink)

    def test_synthetic_fill_helper_is_noop_in_live(self):
        """AC-3: the synthetic-fill helper guards mode != PAPER directly."""
        from trading.kis import order

        client = _live_client()
        cursor = ScriptedCursor()
        cp = MagicMock(side_effect=AssertionError("no quote in live"))
        with _patched(order, cursor, current_price=cp) as sink:
            order._synthetic_fill(
                client, order_id=1, ticker="005930", qty=1, side="sell",
                order_type="market", limit_price=None,
            )

        assert _order_updates(cursor) == []
        assert "ORDER_FILLED_SYNTHETIC" not in _all_events(cursor, sink)
        # A live mis-invocation is surfaced for visibility.
        assert "ORDER_SYNTHETIC_BLOCKED_LIVE" in _all_events(cursor, sink)


# ---------------------------------------------------------------------------
# AC-5 — quote-failure graceful skip
# ---------------------------------------------------------------------------


class TestQuoteFailureGraceful:
    def test_quote_failure_skips_fill_no_crash(self):
        """AC-5: inquire-price raises → audit + skip, order stays submitted, no crash."""
        from trading.kis import order
        from trading.kis.client import KisError

        client = _paper_client()
        cursor = ScriptedCursor(fetchone_queue=[{"id": 21}, {"status": "submitted"}])
        err = KisError(KisResponse(500, "1", "X", "quote down", {}, {}))
        with _patched(order, cursor,
                      current_price=MagicMock(side_effect=err),
                      balance=MagicMock(return_value=_bal([_held("000270", 1)]))) as sink:
            result = order.submit_order(
                client, ticker="000270", qty=1, side="buy", order_type="market",
            )

        # Order remains 'submitted' (reconcile will pick it up); no exception.
        assert result["status"] == "submitted"
        assert _order_updates(cursor) == [], "no synthetic fill when reference price unavailable"
        assert "ORDER_SYNTHETIC_SKIPPED" in _all_events(cursor, sink)

    def test_zero_reference_price_skips(self):
        """REQ-039-3: a non-positive reference price → skip (audited), no crash."""
        from trading.kis import order

        client = _paper_client()
        cursor = ScriptedCursor(fetchone_queue=[{"id": 31}, {"status": "submitted"}])
        with _patched(order, cursor,
                      current_price=MagicMock(return_value=_quote(0)),
                      balance=MagicMock(return_value=_bal([_held("000270", 1)]))) as sink:
            result = order.submit_order(client, ticker="000270", qty=1, side="buy")

        assert result["status"] == "submitted"
        assert _order_updates(cursor) == []
        assert "ORDER_SYNTHETIC_SKIPPED" in _all_events(cursor, sink)


class TestSyntheticFillContained:
    def test_synthetic_fill_db_error_does_not_break_submission(self):
        """Synthetic-fill failure is audited (ORDER_SYNTHETIC_ERROR), never raised.

        submit_order must still return success — the order was accepted by KIS and
        SPEC-029 reconcile remains the fallback fill path.
        """
        from trading.kis import order

        client = _paper_client()

        # The pre-create + Step-3 persistence connections succeed; the synthetic
        # fill connection raises (e.g. positions FOR UPDATE deadlock).
        cursor = ScriptedCursor(fetchone_queue=[{"id": 51}])

        calls = {"n": 0}

        @contextmanager
        def _flaky_connection(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 3:  # the synthetic-fill transaction
                raise RuntimeError("simulated positions deadlock")
            yield ScriptedConn(cursor)

        with (
            patch.object(order, "connection", _flaky_connection),
            patch.object(order, "audit", _AuditSink()) as _sink,
            patch.object(order, "current_price", MagicMock(return_value=_quote(50_000))),
            patch.object(order, "balance",
                         MagicMock(return_value=_bal([_held("005930", 1, 50000)]))),
        ):
            result = order.submit_order(client, ticker="005930", qty=1, side="buy")

        # Submission still succeeds; the failure was contained + audited.
        assert result["rt_cd"] == "0"
        assert "ORDER_SYNTHETIC_ERROR" in _sink.events
