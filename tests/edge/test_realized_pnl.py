"""SPEC-TRADING-042 Module D — realized P&L aggregation (REQ-042-D1..D3).

Reproduction-first (RC-4): ``daily_equity_snapshot.realized_pnl_cum`` is NULL for
every row even though round-trips have completed (064350 sells 6/4, 055550
round-trip 6/5). Realized P&L was never aggregated/persisted into the snapshot.

These tests drive:
  * the pure cumulative formula ``realized_pnl_as_of`` (fees deducted, single
    source = edge/roundtrips net_pnl),
  * the reconciliation invariant (REQ-042-D2 / SPEC-039): a pure buy with no
    matching sell contributes 0 — net cash outflow is NOT realized P&L,
  * cumulative correctness across multiple round-trips,
  * synthetic-fill awareness (counted once, never doubled; honesty caveat that a
    paper synthetic fill price is an estimate, not a live execution),
  * the DB aggregator populating realized_pnl_cum (non-NULL, fees deducted) and
    auditing REALIZED_PNL_AGGREGATED (REQ-042-D3),
  * the reproduction that record_snapshot alone leaves realized_pnl_cum NULL.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from typing import Any
from unittest.mock import patch

from trading.edge.roundtrips import RoundTrip, RoundTripResult, build_roundtrips


# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------


def _buy(ticker, qty, price, fee=0, ts="2026-06-01T10:00:00", oid=1):
    return {
        "id": oid, "ts": datetime.fromisoformat(ts), "filled_at": None,
        "side": "buy", "ticker": ticker, "fill_qty": qty, "fill_price": price,
        "fee": fee, "confidence": None, "verdict": None,
    }


def _sell(ticker, qty, price, fee=0, ts="2026-06-04T10:00:00", oid=2):
    return {
        "id": oid, "ts": datetime.fromisoformat(ts), "filled_at": None,
        "side": "sell", "ticker": ticker, "fill_qty": qty, "fill_price": price,
        "fee": fee, "confidence": None, "verdict": None,
    }


class _Cursor:
    """Records executed SQL and serves scripted fetch results."""

    def __init__(self, fetchall_queue: list[list[dict[str, Any]]] | None = None,
                 fetchone_queue: list[dict[str, Any] | None] | None = None):
        self.calls: list[tuple[str, Any]] = []
        self._fetchall_queue = list(fetchall_queue or [])
        self._fetchone_queue = list(fetchone_queue or [])

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return self._fetchall_queue.pop(0) if self._fetchall_queue else []

    def fetchone(self):
        return self._fetchone_queue.pop(0) if self._fetchone_queue else None

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


@contextmanager
def _conn_factory(cursor):
    yield _Conn(cursor)


# ---------------------------------------------------------------------------
# Pure cumulative formula (DB-free)
# ---------------------------------------------------------------------------


class TestRealizedPnlAsOf:
    def test_completed_roundtrip_yields_net_pnl_fees_deducted(self):
        # RC-4 core: a completed round-trip's realized P&L is net_pnl (fees out).
        from trading.edge.realized_pnl import realized_pnl_as_of

        rt = build_roundtrips([
            _buy("064350", 10, 70_000, fee=100, ts="2026-06-01T10:00:00", oid=1),
            _sell("064350", 10, 77_000, fee=110, ts="2026-06-04T10:00:00", oid=2),
        ]).roundtrips
        # gross = (77000-70000)*10 = 70000; net = 70000 - 100 - 110 = 69790
        assert realized_pnl_as_of(rt, date(2026, 6, 4)) == 69_790

    def test_pure_buy_no_sell_is_not_counted_as_pnl(self):
        # REQ-042-D2 / SPEC-039 reconciliation: a buy with NO matching sell is a
        # cash OUTFLOW, NOT realized P&L. It must contribute 0 (never negative).
        from trading.edge.realized_pnl import realized_pnl_as_of

        res = build_roundtrips([
            _buy("055550", 5, 40_000, fee=60, ts="2026-06-01T10:00:00", oid=1),
        ])
        assert res.roundtrips == []  # no round-trip from a pure buy
        assert realized_pnl_as_of(res.roundtrips, date(2026, 6, 30)) == 0

    def test_as_of_excludes_future_exits(self):
        # Cumulative AS OF a day = only round-trips already exited on/before it.
        from trading.edge.realized_pnl import realized_pnl_as_of

        rts = [
            RoundTrip("A", date(2026, 6, 1), date(2026, 6, 4), 10,
                      70_000, 77_000, 100, 110, None, None),  # exit 6/4, net 69790
            RoundTrip("B", date(2026, 6, 2), date(2026, 6, 5), 10,
                      50_000, 45_000, 50, 50, None, None),    # exit 6/5, net -50100
        ]
        assert realized_pnl_as_of(rts, date(2026, 6, 4)) == 69_790
        assert realized_pnl_as_of(rts, date(2026, 6, 5)) == 69_790 - 50_100
        assert realized_pnl_as_of(rts, date(2026, 5, 31)) == 0

    def test_cumulative_multiple_roundtrips(self):
        from trading.edge.realized_pnl import realized_pnl_as_of

        rts = build_roundtrips([
            _buy("A", 10, 100, fee=0, ts="2026-06-01T10:00:00", oid=1),
            _sell("A", 10, 150, fee=0, ts="2026-06-03T10:00:00", oid=2),   # +500
            _buy("B", 10, 200, fee=0, ts="2026-06-02T10:00:00", oid=3),
            _sell("B", 10, 180, fee=0, ts="2026-06-04T10:00:00", oid=4),   # -200
        ]).roundtrips
        assert realized_pnl_as_of(rts, date(2026, 6, 4)) == 300


# ---------------------------------------------------------------------------
# DB aggregator: populate realized_pnl_cum + audit
# ---------------------------------------------------------------------------


class TestAggregateRealizedPnlCum:
    def test_populates_realized_pnl_cum_non_null_per_day(self):
        # RC-4 reproduction → fix: snapshot days exist with realized_pnl_cum NULL;
        # aggregation writes the cumulative realized P&L (fees deducted) per day.
        from trading.edge import realized_pnl

        rt_result = RoundTripResult(roundtrips=build_roundtrips([
            _buy("064350", 10, 70_000, fee=100, ts="2026-06-01T10:00:00", oid=1),
            _sell("064350", 10, 77_000, fee=110, ts="2026-06-04T10:00:00", oid=2),
        ]).roundtrips)

        # Two snapshot rows: 6/3 (pre-exit → 0) and 6/4 (post-exit → 69790).
        cur = _Cursor(
            fetchall_queue=[
                [{"trading_day": date(2026, 6, 3)}, {"trading_day": date(2026, 6, 4)}],
            ],
            fetchone_queue=[{"n": 0}],  # synthetic sell-fill count
        )
        with (
            patch.object(realized_pnl, "compute_roundtrips", return_value=rt_result),
            patch.object(realized_pnl, "connection", lambda *a, **k: _conn_factory(cur)),
        ):
            summary = realized_pnl.aggregate_realized_pnl_cum()

        updates = {
            p["trading_day"]: p["realized_pnl_cum"]
            for sql, p in cur.calls
            if isinstance(p, dict) and "realized_pnl_cum" in p
        }
        assert updates[date(2026, 6, 3)] == 0
        assert updates[date(2026, 6, 4)] == 69_790  # non-NULL, fees deducted
        assert summary["rows_updated"] == 2
        assert summary["roundtrips"] == 1

    def test_emits_audit_event(self):
        # REQ-042-D3: the aggregation action is auditable.
        from trading.edge import realized_pnl

        rt_result = RoundTripResult(roundtrips=[])
        cur = _Cursor(fetchall_queue=[[]], fetchone_queue=[{"n": 0}])
        with (
            patch.object(realized_pnl, "compute_roundtrips", return_value=rt_result),
            patch.object(realized_pnl, "connection", lambda *a, **k: _conn_factory(cur)),
        ):
            realized_pnl.aggregate_realized_pnl_cum()

        audited = [p for sql, p in cur.calls
                   if isinstance(sql, str) and "audit_log" in sql]
        assert audited, "expected a REALIZED_PNL_AGGREGATED audit row"
        assert any("REALIZED_PNL_AGGREGATED" in str(p) for p in audited)

    def test_dry_run_writes_no_update_or_audit(self):
        from trading.edge import realized_pnl

        rt_result = RoundTripResult(roundtrips=build_roundtrips([
            _buy("A", 10, 100, ts="2026-06-01T10:00:00", oid=1),
            _sell("A", 10, 150, ts="2026-06-03T10:00:00", oid=2),
        ]).roundtrips)
        cur = _Cursor(
            fetchall_queue=[[{"trading_day": date(2026, 6, 3)}]],
            fetchone_queue=[{"n": 0}],
        )
        with (
            patch.object(realized_pnl, "compute_roundtrips", return_value=rt_result),
            patch.object(realized_pnl, "connection", lambda *a, **k: _conn_factory(cur)),
        ):
            summary = realized_pnl.aggregate_realized_pnl_cum(dry_run=True)

        writes = [sql for sql, _ in cur.calls
                  if isinstance(sql, str) and ("UPDATE daily_equity_snapshot" in sql
                                               or "audit_log" in sql)]
        assert writes == []
        assert summary["dry_run"] is True
        assert summary["rows_updated"] == 1  # would-update count

    def test_idempotent_recompute_same_values(self):
        # Re-running recomputes the SAME cumulative value → safe to re-run (backfill).
        from trading.edge import realized_pnl

        rt_result = RoundTripResult(roundtrips=build_roundtrips([
            _buy("A", 10, 100, ts="2026-06-01T10:00:00", oid=1),
            _sell("A", 10, 150, ts="2026-06-03T10:00:00", oid=2),
        ]).roundtrips)

        def _run():
            cur = _Cursor(
                fetchall_queue=[[{"trading_day": date(2026, 6, 3)}]],
                fetchone_queue=[{"n": 0}],
            )
            with (
                patch.object(realized_pnl, "compute_roundtrips", return_value=rt_result),
                patch.object(realized_pnl, "connection", lambda *a, **k: _conn_factory(cur)),
            ):
                realized_pnl.aggregate_realized_pnl_cum()
            return {
                p["trading_day"]: p["realized_pnl_cum"]
                for sql, p in cur.calls
                if isinstance(p, dict) and "realized_pnl_cum" in p
            }

        assert _run() == _run() == {date(2026, 6, 3): 500}

    def test_synthetic_sell_fill_counted_once_with_caveat(self):
        # REQ-042-D / honesty: a synthetic paper sell fill is the single source row;
        # it is counted ONCE (FIFO matches each order once — no double count), and
        # the summary surfaces a synthetic count so the report can caveat that the
        # paper synthetic fill price is an estimate, not a live execution price.
        from trading.edge import realized_pnl

        rt_result = RoundTripResult(roundtrips=build_roundtrips([
            _buy("055550", 10, 40_000, ts="2026-06-02T10:00:00", oid=1),
            _sell("055550", 10, 42_000, ts="2026-06-05T10:00:00", oid=2),  # +20000
        ]).roundtrips)
        cur = _Cursor(
            fetchall_queue=[[{"trading_day": date(2026, 6, 5)}]],
            fetchone_queue=[{"n": 1}],  # one synthetic sell fill contributed
        )
        with (
            patch.object(realized_pnl, "compute_roundtrips", return_value=rt_result),
            patch.object(realized_pnl, "connection", lambda *a, **k: _conn_factory(cur)),
        ):
            summary = realized_pnl.aggregate_realized_pnl_cum()

        updates = [p["realized_pnl_cum"] for sql, p in cur.calls
                   if isinstance(p, dict) and "realized_pnl_cum" in p]
        assert updates == [20_000]            # counted exactly once
        assert summary["synthetic_sell_fills"] == 1
        assert summary["synthetic_present"] is True


# ---------------------------------------------------------------------------
# Reproduction: record_snapshot alone leaves realized_pnl_cum NULL
# ---------------------------------------------------------------------------


class TestRc4SnapshotLeavesNull:
    def test_record_snapshot_does_not_populate_realized_pnl_cum(self):
        # The ORIGINAL bug: writing a daily snapshot never touches realized_pnl_cum,
        # so even with completed round-trips the column stays NULL. This asserts the
        # snapshot writer is NOT the populator — aggregation (Module D) is. It must
        # remain true (snapshot.py byte-for-byte unchanged).
        from trading.edge import snapshot

        cur = _Cursor()
        balance = {"total_assets": 9_902_616, "stock_eval": 6_747_716,
                   "cash_d2": 3_154_900, "pnl_total": 0}
        with (
            patch("trading.kis.account.balance", return_value=balance),
            patch("trading.edge.snapshot.connection", lambda *a, **k: _conn_factory(cur)),
        ):
            snapshot.record_snapshot(client=object(), trading_day=date(2026, 6, 5))

        assert all("realized_pnl_cum" not in sql for sql, _ in cur.calls)
