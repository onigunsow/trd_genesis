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

    def test_live_confirm_fills_is_guarded_seam_not_fabricated(self):
        """REQ-042-A3/A5: live fill-inquiry is a guarded seam, NEVER fabricated.

        The 주식일별주문체결조회 TR id is not yet wired; confirm_fills(live) must
        raise the typed seam error and must NOT fall back to balance reconcile
        (no fabricated live fills).
        """
        from trading.kis import broker_truth

        client = _live_client()
        with patch.object(
            broker_truth, "reconcile_from_balance",
        ) as reconcile:
            try:
                broker_truth.confirm_fills(client)
            except broker_truth.BrokerFillInquiryNotImplemented:
                pass
            else:  # pragma: no cover - explicit failure path
                raise AssertionError(
                    "live confirm_fills must raise the guarded seam error"
                )

        reconcile.assert_not_called()

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
        """REQ-042-A5: live path must not reach the paper reconcile fallback."""
        from trading.kis import broker_truth

        client = _live_client()
        with patch.object(broker_truth, "reconcile_from_balance") as reconcile:
            try:
                broker_truth.confirm_fills(client)
            except broker_truth.BrokerFillInquiryNotImplemented:
                pass
        reconcile.assert_not_called()
