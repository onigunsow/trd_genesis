"""SPEC-TRADING-042 Module A — broker-truth single ledger (reproduction-first).

Covers the 2026-06-08 폭락일 RC-1: ``_synthetic_fill`` fabricated a LOCAL
``positions`` row for a paper BUY, but the KIS paper account never held that
balance. A subsequent SELL then routed a REAL KIS order which KIS rejected with
``40240000:모의투자 잔고내역이 없습니다`` (000270 기아 -10.8% stop-loss). Local ledger
diverged from the KIS ledger and reconcile only ran once daily (15:59).

Module A makes the KIS account balance the authoritative position source and
reconciles the local cache INTRADAY — before a sell decision and after each
order — clamping every sell to the KIS-confirmed held quantity so a phantom
position can never drive a real KIS sell.

AC-1 (RC-1 reproduction)  phantom sell is blocked BEFORE a real KIS POST.
AC-1 (paper/live parity)  fill confirmation is ONE code path, source-branched.
AC-1 (drift-0)            paper fallback reconcile reports drift 0 + audits it.
AC-1 (live safety)        live never fabricates; live fill-inquiry is a guarded
                          seam (NotImplemented), never a fabricated fill.

All tests are offline: ``balance`` / ``reconcile_from_balance`` are patched and
``audit`` is captured by a sink. No DB, no network.
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

from trading.config import TradingMode

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _AuditSink:
    """Captures ``broker_truth.audit(event, actor, details)`` calls."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.details: list[dict[str, Any]] = []

    def __call__(
        self, event_type: str, actor: str = "system", details: Any = None
    ) -> None:
        self.events.append(event_type)
        self.details.append(details or {})


def _held(ticker: str, qty: int, avg_cost: int = 50_000) -> dict[str, Any]:
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


def _bal(holdings: list[dict[str, Any]]) -> dict[str, Any]:
    return {"holdings": holdings, "raw": {}}


def _paper_client() -> MagicMock:
    client = MagicMock()
    client.mode = TradingMode.PAPER
    return client


def _live_client() -> MagicMock:
    client = MagicMock()
    client.mode = TradingMode.LIVE
    return client


# ---------------------------------------------------------------------------
# AC-1 (RC-1 reproduction) — phantom sell blocked before a real KIS POST
# ---------------------------------------------------------------------------


class TestPhantomSellBlocked:
    def test_phantom_sell_clamps_to_zero_and_is_audited(self):
        """RC-1: a ticker absent from KIS balance must clamp to 0 (no KIS sell).

        2026-06-08 000270: local positions held a phantom row; KIS balance had
        no such holding. ``clamp_sell_to_confirmed`` must return 0 so the
        caller never POSTs a real KIS sell that KIS would reject with
        '잔고내역이 없습니다', and audit PHANTOM_SELL_BLOCKED.
        """
        from trading.kis import broker_truth

        client = _paper_client()
        sink = _AuditSink()
        with (
            patch.object(broker_truth, "balance", return_value=_bal([])),
            patch.object(broker_truth, "audit", sink),
        ):
            confirmed = broker_truth.clamp_sell_to_confirmed(
                client, "000270", 1
            )

        assert confirmed == 0, "phantom sell must clamp to 0 — never POST to KIS"
        assert "PHANTOM_SELL_BLOCKED" in sink.events

    def test_oversell_clamped_to_confirmed_qty_presubmit(self):
        """RC-1 over-sell: confirmed=1, requested=3 → clamp to 1, audit (pre-POST).

        This is a PRE-submission clamp (distinct from SPEC-039's post-POST
        synthetic clamp) so an over-sized real KIS sell is never issued.
        """
        from trading.kis import broker_truth

        client = _paper_client()
        sink = _AuditSink()
        with (
            patch.object(
                broker_truth, "balance",
                return_value=_bal([_held("000270", 1)]),
            ),
            patch.object(broker_truth, "audit", sink),
        ):
            confirmed = broker_truth.clamp_sell_to_confirmed(
                client, "000270", 3
            )

        assert confirmed == 1
        assert "OVERSELL_CLAMPED_PRESUBMIT" in sink.events

    def test_confirmed_sell_passes_through_unchanged(self):
        """A genuine held position is NOT blocked (capital-preservation hard rule).

        Confirmed qty >= requested → returns the requested qty, no clamp audit.
        A real stop-loss on a real holding must always go through.
        """
        from trading.kis import broker_truth

        client = _paper_client()
        sink = _AuditSink()
        with (
            patch.object(
                broker_truth, "balance",
                return_value=_bal([_held("000270", 5)]),
            ),
            patch.object(broker_truth, "audit", sink),
        ):
            confirmed = broker_truth.clamp_sell_to_confirmed(
                client, "000270", 5
            )

        assert confirmed == 5
        assert "PHANTOM_SELL_BLOCKED" not in sink.events
        assert "OVERSELL_CLAMPED_PRESUBMIT" not in sink.events

    def test_confirm_held_qty_reads_kis_balance(self):
        """KIS balance is the single source of confirmed held qty (REQ-042-A1)."""
        from trading.kis import broker_truth

        client = _paper_client()
        with patch.object(
            broker_truth, "balance",
            return_value=_bal([_held("005930", 7), _held("000660", 2)]),
        ):
            assert broker_truth.confirm_held_qty(client, "005930") == 7
            assert broker_truth.confirm_held_qty(client, "000660") == 2
            assert broker_truth.confirm_held_qty(client, "999999") == 0


# ---------------------------------------------------------------------------
# AC-1 (paper/live parity) — one fill-confirmation code path, source-branched
# ---------------------------------------------------------------------------


class TestFillConfirmationParity:
    def test_paper_confirm_fills_uses_balance_reconcile(self):
        """REQ-042-A3: paper fill confirmation delegates to balance reconcile.

        Same entry point ``confirm_fills`` regardless of mode — only the source
        branches. Paper → reconcile_from_balance.
        """
        from trading.kis import broker_truth

        client = _paper_client()
        with patch.object(
            broker_truth, "reconcile_from_balance",
            return_value={"queried": 0, "transitioned": 0,
                          "positions_synced": 0, "errors": 0, "dry_run": False},
        ) as reconcile:
            out = broker_truth.confirm_fills(client)

        reconcile.assert_called_once_with(client, dry_run=False)
        assert out["source"] == "balance_reconcile"

    def test_live_confirm_fills_uses_execution_inquiry_not_balance_reconcile(self):
        """REQ-042-A3/A5 + SPEC-045-M2: live fill-inquiry uses KIS execution inquiry.

        SPEC-045 M2 wires the live seam: confirm_fills(live) now calls
        inquire-daily-ccld via client.get() and returns source='execution_inquiry'.
        It must NOT fall back to balance reconcile (no fabricated live fills).
        """
        from trading.kis import broker_truth

        client = _live_client()
        with (
            patch.object(broker_truth, "reconcile_from_balance") as reconcile,
            patch.object(broker_truth, "_inquire_daily_ccld",
                         return_value=[]) as _inq,
            patch.object(broker_truth, "_apply_live_fills",
                         return_value={"filled_count": 0, "partial_count": 0,
                                       "unmatched_kis": 0, "skipped_terminal": 0,
                                       "errors": 0}),
        ):
            result = broker_truth.confirm_fills(client)

        # Paper reconcile must NOT be called (no fabricated live fill)
        reconcile.assert_not_called()
        # Live seam now returns execution_inquiry result
        assert result["source"] == "execution_inquiry"
        # client.get() is invoked inside _inquire_daily_ccld (tested in SPEC-045 suite)
        _inq.assert_called_once_with(client)

    def test_confirm_fills_single_signature_both_modes(self):
        """Parity: the same callable + signature serves paper and live."""
        from trading.kis import broker_truth

        sig = inspect.signature(broker_truth.confirm_fills)
        # (client, *, source=None) — source is the only branch knob.
        assert "client" in sig.parameters
        assert "source" in sig.parameters


# ---------------------------------------------------------------------------
# AC-1 (drift-0) — paper fallback reconcile reports drift 0 + drift audit
# ---------------------------------------------------------------------------


class TestDriftZero:
    def test_intraday_reconcile_emits_drift_audit(self):
        """REQ-042-A4: reconcile emits a drift audit (drift logging requirement)."""
        from trading.kis import broker_truth

        client = _paper_client()
        sink = _AuditSink()
        with (
            patch.object(
                broker_truth, "reconcile_from_balance",
                return_value={"queried": 1, "transitioned": 0,
                              "positions_synced": 1, "errors": 0, "dry_run": False},
            ),
            patch.object(broker_truth, "audit", sink),
        ):
            broker_truth.intraday_reconcile(
                client, reason="post_submit", force=True
            )

        assert "INTRADAY_RECONCILE" in sink.events
        # REQ-064-B8: account-wide reconcile — TIER 5 permanent exemption.
        idx = sink.events.index("INTRADAY_RECONCILE")
        assert sink.details[idx]["decision_scope"] == "account"
        assert "decision_id" not in sink.details[idx]

    def test_intraday_reconcile_ttl_throttle(self):
        """REQ-042-A2/ADR-1: within TTL the reconcile is throttled (rate-limit)."""
        from trading.kis import broker_truth

        client = _paper_client()
        broker_truth.reset_reconcile_throttle()
        with (
            patch.object(
                broker_truth, "reconcile_from_balance",
                return_value={"queried": 0, "transitioned": 0,
                              "positions_synced": 0, "errors": 0, "dry_run": False},
            ) as reconcile,
            patch.object(broker_truth, "audit", _AuditSink()),
        ):
            first = broker_truth.intraday_reconcile(client, reason="pre_sell")
            second = broker_truth.intraday_reconcile(client, reason="pre_sell")

        assert reconcile.call_count == 1, "second call within TTL must be throttled"
        assert first["reconciled"] is True
        assert second["throttled"] is True

    def test_intraday_reconcile_force_bypasses_ttl(self):
        """force=True (post-submission) always reconciles regardless of TTL."""
        from trading.kis import broker_truth

        client = _paper_client()
        broker_truth.reset_reconcile_throttle()
        with (
            patch.object(
                broker_truth, "reconcile_from_balance",
                return_value={"queried": 0, "transitioned": 0,
                              "positions_synced": 0, "errors": 0, "dry_run": False},
            ) as reconcile,
            patch.object(broker_truth, "audit", _AuditSink()),
        ):
            broker_truth.intraday_reconcile(client, reason="pre_sell")
            broker_truth.intraday_reconcile(
                client, reason="post_submit", force=True
            )

        assert reconcile.call_count == 2, "force must bypass the TTL throttle"

    def test_ttl_constant_is_named_and_in_range(self):
        """ADR-1: the reconcile TTL is a named constant in the 30-60s range."""
        from trading.kis import broker_truth

        assert isinstance(
            broker_truth.INTRADAY_RECONCILE_TTL_SECONDS, (int, float)
        )
        assert 30 <= broker_truth.INTRADAY_RECONCILE_TTL_SECONDS <= 60


# ---------------------------------------------------------------------------
# AC-1 (live safety) — no fabrication on live, phantom sell never issued
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# SPEC-TRADING-064 REQ-064-B4 — Optional decision_id/decision_scope threading
# ---------------------------------------------------------------------------


class TestClampDecisionIdThreading:
    def test_phantom_sell_records_decision_id_when_provided(self):
        """The orchestrator sell path passes decision_id; it lands at the
        details top level alongside the existing keys (no regression)."""
        from trading.kis import broker_truth

        client = _paper_client()
        sink = _AuditSink()
        with (
            patch.object(broker_truth, "balance", return_value=_bal([])),
            patch.object(broker_truth, "audit", sink),
        ):
            broker_truth.clamp_sell_to_confirmed(
                client, "000270", 1, decision_id=42
            )

        idx = sink.events.index("PHANTOM_SELL_BLOCKED")
        assert sink.details[idx]["decision_id"] == 42
        assert sink.details[idx]["decision_scope"] is None
        # existing keys preserved (observability regression 0)
        assert sink.details[idx]["ticker"] == "000270"
        assert sink.details[idx]["requested_qty"] == 1

    def test_phantom_sell_records_null_decision_id_and_scope_when_absent(self):
        """Neither decision_id nor decision_scope supplied → both null, never
        inferred (a wiring gap must never look like an intentional watchdog path)."""
        from trading.kis import broker_truth

        client = _paper_client()
        sink = _AuditSink()
        with (
            patch.object(broker_truth, "balance", return_value=_bal([])),
            patch.object(broker_truth, "audit", sink),
        ):
            broker_truth.clamp_sell_to_confirmed(client, "000270", 1)

        idx = sink.events.index("PHANTOM_SELL_BLOCKED")
        assert sink.details[idx]["decision_id"] is None
        assert sink.details[idx]["decision_scope"] is None

    def test_oversell_clamped_records_decision_id(self):
        from trading.kis import broker_truth

        client = _paper_client()
        sink = _AuditSink()
        with (
            patch.object(
                broker_truth, "balance",
                return_value=_bal([_held("000270", 1)]),
            ),
            patch.object(broker_truth, "audit", sink),
        ):
            broker_truth.clamp_sell_to_confirmed(
                client, "000270", 3, decision_id=7
            )

        idx = sink.events.index("OVERSELL_CLAMPED_PRESUBMIT")
        assert sink.details[idx]["decision_id"] == 7
        assert sink.details[idx]["decision_scope"] is None

    def test_explicit_decision_scope_is_recorded_verbatim(self):
        """decision_scope is never inferred from decision_id — it is recorded
        exactly as the caller supplies it."""
        from trading.kis import broker_truth

        client = _paper_client()
        sink = _AuditSink()
        with (
            patch.object(broker_truth, "balance", return_value=_bal([])),
            patch.object(broker_truth, "audit", sink),
        ):
            broker_truth.clamp_sell_to_confirmed(
                client, "000270", 1, decision_scope="watchdog"
            )

        idx = sink.events.index("PHANTOM_SELL_BLOCKED")
        assert sink.details[idx]["decision_id"] is None
        assert sink.details[idx]["decision_scope"] == "watchdog"


# ---------------------------------------------------------------------------
# SPEC-TRADING-064 REQ-064-B6 — TIER 4 fill-audit decision_id threading
# ---------------------------------------------------------------------------


class _FillCursor:
    def __init__(self, orders_rows: list[dict[str, Any]]) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._orders_rows = orders_rows

    def execute(self, sql: str, params: Any = None) -> None:
        self.calls.append((sql, params))

    def fetchall(self) -> Any:
        return self._orders_rows

    def fetchone(self) -> Any:
        return None

    def __enter__(self) -> _FillCursor:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class _FillConn:
    def __init__(self, cursor: _FillCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _FillCursor:
        return self._cursor

    def __enter__(self) -> _FillConn:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


def _fill_audit_details(cursor: _FillCursor) -> dict[str, Any]:
    import json

    for sql, params in cursor.calls:
        if "audit_log" in sql.lower():
            return json.loads(params[2])
    raise AssertionError("no audit_log INSERT was executed")


class TestFillAuditDecisionId:
    def test_select_includes_persona_decision_id_column(self):
        """REQ-064-B6: the driving SELECT must carry persona_decision_id so the
        fill audit can thread it through — no new JOIN, one added column."""
        from trading.kis import broker_truth

        cursor = _FillCursor([])
        conn = _FillConn(cursor)

        @contextmanager
        def _factory(*_a: Any, **_k: Any):
            yield conn

        # A non-empty kis_by_odno is required to reach the SELECT at all (an
        # empty records list short-circuits before any DB access).
        records = [{"ODNO": "0000000001", "CCLD_QTY": 0, "CCLD_AVG_UNPR": 0}]
        with patch.object(broker_truth, "connection", _factory):
            broker_truth._apply_live_fills(MagicMock(), records)

        select_sql = cursor.calls[0][0]
        assert "persona_decision_id" in select_sql

    def test_order_filled_carries_decision_id_when_present(self):
        from trading.kis import broker_truth

        order_row = {
            "id": 1, "qty": 5, "fill_qty": 0, "status": "submitted",
            "kis_order_no": "0000012345", "ticker": "005930", "side": "buy",
            "persona_decision_id": 42,
        }
        cursor = _FillCursor([order_row])
        conn = _FillConn(cursor)

        @contextmanager
        def _factory(*_a: Any, **_k: Any):
            yield conn

        records = [{"ODNO": "0000012345", "CCLD_QTY": 5, "CCLD_AVG_UNPR": 70_000}]
        with patch.object(broker_truth, "connection", _factory):
            broker_truth._apply_live_fills(MagicMock(), records)

        details = _fill_audit_details(cursor)
        assert details["decision_id"] == 42
        assert details["decision_scope"] is None
        # existing keys preserved
        assert details["order_id"] == 1
        assert details["ticker"] == "005930"

    def test_order_filled_null_decision_id_is_rule_based(self):
        """REQ-064-B6/C5: a NULL persona_decision_id order (late_cycle/watchdog/
        ghost_convergence) is a rule-based execution, not a missing record."""
        from trading.kis import broker_truth

        order_row = {
            "id": 2, "qty": 5, "fill_qty": 0, "status": "submitted",
            "kis_order_no": "0000099999", "ticker": "005930", "side": "sell",
            "persona_decision_id": None,
        }
        cursor = _FillCursor([order_row])
        conn = _FillConn(cursor)

        @contextmanager
        def _factory(*_a: Any, **_k: Any):
            yield conn

        records = [{"ODNO": "0000099999", "CCLD_QTY": 5, "CCLD_AVG_UNPR": 70_000}]
        with patch.object(broker_truth, "connection", _factory):
            broker_truth._apply_live_fills(MagicMock(), records)

        details = _fill_audit_details(cursor)
        assert details["decision_id"] is None
        assert details["decision_scope"] == "rule_based"


class TestLiveSafety:
    def test_live_clamp_still_uses_kis_truth(self):
        """REQ-042-A5: clamp works on live too — confirmed qty from KIS balance.

        A live phantom sell (KIS-unconfirmed) must clamp to 0 just like paper;
        capital preservation is mode-independent. (No fabrication is involved —
        clamp only READS balance and never fills.)
        """
        from trading.kis import broker_truth

        client = _live_client()
        sink = _AuditSink()
        with (
            patch.object(broker_truth, "balance", return_value=_bal([])),
            patch.object(broker_truth, "audit", sink),
        ):
            confirmed = broker_truth.clamp_sell_to_confirmed(
                client, "000270", 1
            )

        assert confirmed == 0
        assert "PHANTOM_SELL_BLOCKED" in sink.events

    def test_confirm_fills_live_never_calls_reconcile(self):
        """REQ-042-A5: live path must not reach the paper reconcile fallback.

        SPEC-045 M2: live now uses _inquire_daily_ccld, never reconcile_from_balance.
        """
        from trading.kis import broker_truth

        client = _live_client()
        with (
            patch.object(broker_truth, "reconcile_from_balance") as reconcile,
            patch.object(broker_truth, "_inquire_daily_ccld", return_value=[]),
            patch.object(broker_truth, "_apply_live_fills",
                         return_value={"filled_count": 0, "partial_count": 0,
                                       "unmatched_kis": 0, "skipped_terminal": 0,
                                       "errors": 0}),
        ):
            broker_truth.confirm_fills(client)
        reconcile.assert_not_called()
