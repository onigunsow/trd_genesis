"""SPEC-TRADING-042 Module B — order-state resolver (reproduction-first).

Covers RC-2 (2026-06-01..06-08): an order accepted by KIS (rt_cd=0 → 'submitted')
whose synthetic/fill step throws is left in 'submitted' forever — no resolver, no
timeout. 5 SELL orders leaked this way (086790/055550/064350/000270/071050).

AC-2 (RC-2 reproduction)  a stuck 'submitted' order has no in-line resolver in the
                          submit path; the dedicated resolver converges it.
AC-2 (resolver)           an order in 'submitted' beyond the window → deterministic
                          terminal state (filled if confirmed, else expired).
AC-2 (idempotency)        an already-terminal order is never re-transitioned, and a
                          second resolve run is a no-op.
AC-2 (no arbitrary fill)  an unconfirmable order is NEVER marked 'filled' — it is
                          'expired'. Live (confirm_fills raises) also expires, never
                          fabricates a fill.
AC-2 (cleanup)            the 5 leaked orders are resolved by the one-time cleanup
                          path; a re-run resolves nothing further.

All tests are offline: ``connection`` is a scripted in-memory DB and
``confirm_fills`` is patched. No DB, no network.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

from trading.config import TradingMode

# ---------------------------------------------------------------------------
# Scripted in-memory DB doubles (mirrors tests/kis/test_synthetic_fill.py)
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


def _conn_sequence(cursors: list[ScriptedCursor]):
    """Return a connection() replacement that hands out cursors in order.

    The resolver opens one connection for candidate selection, then one per
    candidate for the row-locked transition. Each call pops the next cursor.
    """
    iterator = iter(cursors)

    @contextmanager
    def _factory(*_a: Any, **_k: Any):
        yield ScriptedConn(next(iterator))

    return _factory


def _audit_events(*cursors: ScriptedCursor) -> list[str]:
    events: list[str] = []
    for cursor in cursors:
        for sql, params in cursor.calls:
            if "audit_log" in sql.lower() and params:
                events.append(str(params[0]))
    return events


def _status_updates(cursor: ScriptedCursor) -> list[tuple[str, Any]]:
    out = []
    for sql, params in cursor.calls:
        up = sql.upper()
        if "UPDATE ORDERS" in up and "STATUS" in up:
            out.append((sql, params))
    return out


def _paper_client() -> MagicMock:
    client = MagicMock()
    client.mode = TradingMode.PAPER
    return client


def _live_client() -> MagicMock:
    client = MagicMock()
    client.mode = TradingMode.LIVE
    return client


def _old_ts(minutes: int = 60) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes)


def _candidate(order_id: int, ticker: str, *, side: str = "sell",
               ts: datetime | None = None, qty: int = 3) -> dict[str, Any]:
    return {
        "id": order_id,
        "ts": ts or _old_ts(),
        "side": side,
        "ticker": ticker,
        "qty": qty,
        "status": "submitted",
    }


# ---------------------------------------------------------------------------
# AC-2 (RC-2 reproduction) — submit path leaves a stuck order, resolver fixes it
# ---------------------------------------------------------------------------


class TestStuckSubmittedReproduction:
    def test_submit_path_has_no_resolver_leaves_order_submitted(self):
        """RC-2 reproduction: KIS accepts, synthetic fill throws → order stays
        'submitted' and submit_order has no in-line resolver (the leak).

        This documents the bug: submission returns success while the order is
        abandoned in 'submitted'. The dedicated resolver (below) is the fix.
        """
        from trading.kis import order

        client = _paper_client()
        client.account_prefix = "50000000"
        client.account_suffix = "01"
        client.tr_id.side_effect = lambda paper_id, live_id: paper_id
        from trading.kis.client import KisResponse
        client.post.return_value = KisResponse(
            status_code=200, rt_cd="0", msg_cd="APBK0013", msg="ok",
            output={"ODNO": "0000029297"}, raw={"rt_cd": "0"},
        )

        # Pre-create returns id; the synthetic-fill connection raises; the final
        # status re-read reports the order is still 'submitted' (the leak).
        cursor = ScriptedCursor(fetchone_queue=[{"id": 69}, {"status": "submitted"}])
        calls = {"n": 0}

        @contextmanager
        def _flaky(*_a: Any, **_k: Any):
            calls["n"] += 1
            if calls["n"] == 3:  # the synthetic-fill transaction
                raise RuntimeError("simulated synthetic-fill crash")
            yield ScriptedConn(cursor)

        with (
            patch.object(order, "connection", _flaky),
            patch.object(order, "audit", MagicMock()),
            patch.object(order, "current_price",
                         MagicMock(return_value={"price": 10_000})),
            patch.object(order, "balance",
                         MagicMock(return_value={"holdings": []})),
        ):
            result = order.submit_order(client, ticker="071050", qty=3, side="sell")

        # Submission "succeeded" but the order is abandoned in 'submitted'.
        assert result["status"] == "submitted"
        # No code path in submit ever transitions it to a terminal state.

    def test_resolver_converges_stuck_sell_to_expired(self):
        """The resolver converges the abandoned 071050 sell to a terminal state.

        With no fill confirmation (paper reconcile does not advance a SELL), the
        order is window-expired — NOT fabricated 'filled' (REQ-042-B3).
        """
        from trading.kis import order_resolver

        client = _paper_client()
        select_cursor = ScriptedCursor(
            fetchall_queue=[[_candidate(69, "071050")]],
        )
        # Row-lock re-read still 'submitted' → expire.
        txn_cursor = ScriptedCursor(fetchone_queue=[{"status": "submitted"}])

        with (
            patch.object(order_resolver, "connection",
                         _conn_sequence([select_cursor, txn_cursor])),
            patch.object(order_resolver, "confirm_fills", MagicMock()),
        ):
            summary = order_resolver.resolve_stuck_orders(client)

        assert summary["resolved_expired"] == 1
        assert summary["resolved_filled"] == 0
        ups = _status_updates(txn_cursor)
        assert ups, "expected an UPDATE to a terminal status"
        assert "expired" in ups[-1][0].lower()
        assert "STUCK_ORDER_EXPIRED" in _audit_events(txn_cursor)


# ---------------------------------------------------------------------------
# AC-2 — window gating
# ---------------------------------------------------------------------------


class TestResolverWindow:
    def test_order_within_window_is_not_resolved(self):
        """A 'submitted' order younger than the window is left alone.

        Modelled by the SELECT (which is window-gated by SQL) returning no rows.
        """
        from trading.kis import order_resolver

        client = _paper_client()
        select_cursor = ScriptedCursor(fetchall_queue=[[]])  # SQL excludes fresh rows

        with (
            patch.object(order_resolver, "connection",
                         _conn_sequence([select_cursor])),
            patch.object(order_resolver, "confirm_fills",
                         MagicMock(side_effect=AssertionError(
                             "confirm_fills must not run when nothing is stuck"))),
        ):
            summary = order_resolver.resolve_stuck_orders(client)

        assert summary["scanned"] == 0
        assert summary["resolved_expired"] == 0
        assert summary["resolved_filled"] == 0

    def test_window_cutoff_passed_to_select(self):
        """The window is converted to a cutoff timestamp bound into the SELECT."""
        from trading.kis import order_resolver

        client = _paper_client()
        select_cursor = ScriptedCursor(fetchall_queue=[[]])
        now = datetime(2026, 6, 8, 5, 0, 0, tzinfo=UTC)

        with (
            patch.object(order_resolver, "connection",
                         _conn_sequence([select_cursor])),
            patch.object(order_resolver, "confirm_fills", MagicMock()),
        ):
            order_resolver.resolve_stuck_orders(client, now=now, window_seconds=900)

        sql, params = select_cursor.calls[0]
        cutoff = params[0]
        assert cutoff == now - timedelta(seconds=900)


# ---------------------------------------------------------------------------
# AC-2 — idempotency / no double-transition (REQ-042-B3)
# ---------------------------------------------------------------------------


class TestResolverIdempotency:
    def test_already_filled_is_not_retransitioned(self):
        """A candidate that another process advanced to 'filled' is recorded as a
        confirmed resolve, not expired and not double-written."""
        from trading.kis import order_resolver

        client = _paper_client()
        select_cursor = ScriptedCursor(fetchall_queue=[[_candidate(60, "064350")]])
        # Under FOR UPDATE the order is already 'filled' (reconcile advanced it).
        txn_cursor = ScriptedCursor(fetchone_queue=[{"status": "filled"}])

        with (
            patch.object(order_resolver, "connection",
                         _conn_sequence([select_cursor, txn_cursor])),
            patch.object(order_resolver, "confirm_fills", MagicMock()),
        ):
            summary = order_resolver.resolve_stuck_orders(client)

        # No expire UPDATE — only an ORDER_RESOLVED audit.
        assert summary["resolved_expired"] == 0
        assert summary["resolved_filled"] == 1
        expire_ups = [u for u in _status_updates(txn_cursor)
                      if "expired" in u[0].lower()]
        assert expire_ups == [], "a filled order must never be expired"
        assert "ORDER_RESOLVED" in _audit_events(txn_cursor)
        assert "STUCK_ORDER_EXPIRED" not in _audit_events(txn_cursor)

    def test_already_cancelled_is_skipped(self):
        """A candidate already in a terminal non-fill state is skipped, untouched."""
        from trading.kis import order_resolver

        client = _paper_client()
        select_cursor = ScriptedCursor(fetchall_queue=[[_candidate(41, "086790")]])
        txn_cursor = ScriptedCursor(fetchone_queue=[{"status": "cancelled"}])

        with (
            patch.object(order_resolver, "connection",
                         _conn_sequence([select_cursor, txn_cursor])),
            patch.object(order_resolver, "confirm_fills", MagicMock()),
        ):
            summary = order_resolver.resolve_stuck_orders(client)

        assert summary["skipped"] == 1
        assert summary["resolved_expired"] == 0
        assert summary["resolved_filled"] == 0
        assert _status_updates(txn_cursor) == []

    def test_second_run_is_noop(self):
        """Re-running the resolver after rows are expired transitions nothing."""
        from trading.kis import order_resolver

        client = _paper_client()
        # Second run: nothing left in 'submitted'.
        select_cursor = ScriptedCursor(fetchall_queue=[[]])

        with (
            patch.object(order_resolver, "connection",
                         _conn_sequence([select_cursor])),
            patch.object(order_resolver, "confirm_fills", MagicMock()),
        ):
            summary = order_resolver.resolve_stuck_orders(client)

        assert summary == {
            "scanned": 0, "resolved_filled": 0, "resolved_expired": 0,
            "skipped": 0, "errors": 0, "dry_run": False,
        }


# ---------------------------------------------------------------------------
# AC-2 — no arbitrary fill / live safety (REQ-042-B3, A5)
# ---------------------------------------------------------------------------


class TestResolverNoArbitraryFill:
    def test_unconfirmed_order_is_expired_never_filled(self):
        """A stuck order with no fill confirmation is expired, never fabricated
        filled (REQ-042-B3 no-arbitrary-fill)."""
        from trading.kis import order_resolver

        client = _paper_client()
        select_cursor = ScriptedCursor(fetchall_queue=[[_candidate(42, "055550")]])
        txn_cursor = ScriptedCursor(fetchone_queue=[{"status": "submitted"}])

        with (
            patch.object(order_resolver, "connection",
                         _conn_sequence([select_cursor, txn_cursor])),
            patch.object(order_resolver, "confirm_fills", MagicMock()),
        ):
            summary = order_resolver.resolve_stuck_orders(client)

        assert summary["resolved_filled"] == 0
        assert summary["resolved_expired"] == 1
        fill_ups = [u for u in _status_updates(txn_cursor)
                    if "'filled'" in u[0].lower() or "filled" in str(u[1] or "")]
        assert fill_ups == [], "resolver must never write status='filled'"

    def test_live_seam_unconfirmable_expires_never_fabricates(self):
        """Live confirm_fills raises BrokerFillInquiryNotImplemented → the order is
        expired (honest), never fabricated filled (REQ-042-A5/B3)."""
        from trading.kis import order_resolver
        from trading.kis.broker_truth import BrokerFillInquiryNotImplemented

        client = _live_client()
        select_cursor = ScriptedCursor(fetchall_queue=[[_candidate(64, "000270")]])
        txn_cursor = ScriptedCursor(fetchone_queue=[{"status": "submitted"}])

        with (
            patch.object(order_resolver, "connection",
                         _conn_sequence([select_cursor, txn_cursor])),
            patch.object(order_resolver, "confirm_fills",
                         MagicMock(side_effect=BrokerFillInquiryNotImplemented("seam"))),
        ):
            summary = order_resolver.resolve_stuck_orders(client)

        # Live cannot confirm a fill → honest 'expired', no fabricated fill.
        assert summary["resolved_expired"] == 1
        assert summary["resolved_filled"] == 0
        assert "STUCK_ORDER_EXPIRED" in _audit_events(txn_cursor)

    def test_resolver_never_posts_a_kis_order(self):
        """The resolver must never POST a KIS order (no submit_order, no client.post)."""
        from trading.kis import order_resolver

        client = _live_client()
        select_cursor = ScriptedCursor(fetchall_queue=[[_candidate(64, "000270")]])
        txn_cursor = ScriptedCursor(fetchone_queue=[{"status": "submitted"}])

        with (
            patch.object(order_resolver, "connection",
                         _conn_sequence([select_cursor, txn_cursor])),
            patch.object(order_resolver, "confirm_fills",
                         MagicMock(side_effect=__import__(
                             "trading.kis.broker_truth", fromlist=["x"]
                         ).BrokerFillInquiryNotImplemented("seam"))),
        ):
            order_resolver.resolve_stuck_orders(client)

        # The resolver module exposes no order-submission surface.
        client.post.assert_not_called()


# ---------------------------------------------------------------------------
# AC-2 — dry-run preview
# ---------------------------------------------------------------------------


class TestResolverDryRun:
    def test_dry_run_writes_nothing(self):
        """--dry-run previews the expire without any UPDATE/audit and without
        calling confirm_fills (no side effects)."""
        from trading.kis import order_resolver

        client = _paper_client()
        select_cursor = ScriptedCursor(fetchall_queue=[[_candidate(69, "071050")]])
        txn_cursor = ScriptedCursor(fetchone_queue=[{"status": "submitted"}])
        cf = MagicMock()

        with (
            patch.object(order_resolver, "connection",
                         _conn_sequence([select_cursor, txn_cursor])),
            patch.object(order_resolver, "confirm_fills", cf),
        ):
            summary = order_resolver.resolve_stuck_orders(client, dry_run=True)

        assert summary["resolved_expired"] == 1
        cf.assert_not_called()
        assert _status_updates(txn_cursor) == []
        assert _audit_events(txn_cursor) == []


# ---------------------------------------------------------------------------
# AC-2 (cleanup) — the 5 leaked orders (REQ-042-B2)
# ---------------------------------------------------------------------------


class TestCleanupFiveOrders:
    def test_cleanup_resolves_all_five_stuck_orders(self):
        """REQ-042-B2: the 5 leaked orders are resolved (here: expired, since no
        fill can be confirmed) by the one-time cleanup path."""
        from trading.kis import order_resolver

        client = _paper_client()
        five = [
            _candidate(41, "086790"),
            _candidate(42, "055550"),
            _candidate(60, "064350"),
            _candidate(64, "000270"),
            _candidate(69, "071050"),
        ]
        select_cursor = ScriptedCursor(fetchall_queue=[five])
        txn_cursors = [
            ScriptedCursor(fetchone_queue=[{"status": "submitted"}]) for _ in five
        ]
        audit_sink = MagicMock()

        with (
            patch.object(order_resolver, "connection",
                         _conn_sequence([select_cursor, *txn_cursors])),
            patch.object(order_resolver, "confirm_fills", MagicMock()),
            patch("trading.db.session.audit", audit_sink),
        ):
            summary = order_resolver.cleanup_stuck_orders(client)

        assert summary["scanned"] == 5
        assert summary["resolved_expired"] == 5
        assert summary["resolved_filled"] == 0
        # Each order got its own expire UPDATE + STUCK_ORDER_EXPIRED audit.
        for tc in txn_cursors:
            assert any("expired" in u[0].lower() for u in _status_updates(tc))
        # The cleanup emits a summary audit (REQ-042-D3).
        assert any(call.args and call.args[0] == "STUCK_ORDER_CLEANUP"
                   for call in audit_sink.call_args_list)

    def test_cleanup_rerun_is_noop(self):
        """A second cleanup run finds nothing in 'submitted' → resolves nothing."""
        from trading.kis import order_resolver

        client = _paper_client()
        select_cursor = ScriptedCursor(fetchall_queue=[[]])

        with (
            patch.object(order_resolver, "connection",
                         _conn_sequence([select_cursor])),
            patch.object(order_resolver, "confirm_fills", MagicMock()),
            patch("trading.db.session.audit", MagicMock()),
        ):
            summary = order_resolver.cleanup_stuck_orders(client)

        assert summary["scanned"] == 0
        assert summary["resolved_expired"] == 0
        assert summary["resolved_filled"] == 0

    def test_cleanup_window_is_zero(self):
        """Cleanup ignores age — the cutoff equals 'now' (window 0) so even a
        freshly-submitted stuck order is targeted."""
        from trading.kis import order_resolver

        client = _paper_client()
        select_cursor = ScriptedCursor(fetchall_queue=[[]])
        now = datetime(2026, 6, 8, 5, 0, 0, tzinfo=UTC)

        with (
            patch.object(order_resolver, "connection",
                         _conn_sequence([select_cursor])),
            patch.object(order_resolver, "confirm_fills", MagicMock()),
            patch("trading.db.session.audit", MagicMock()),
            patch.object(order_resolver, "_now", MagicMock(return_value=now)),
        ):
            order_resolver.cleanup_stuck_orders(client)

        sql, params = select_cursor.calls[0]
        # window=0 → cutoff == now (no age subtracted).
        assert params[0] == now
