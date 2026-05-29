"""SPEC-TRADING-037 REQ-037-5 — risk-reducing SELL bypass of count-halt.

When the daily-order-COUNT circuit breaker trips, the orchestrator halt gate
skips the whole cycle, which also blocks risk-reducing SELLS. This SPEC lets
SELL signals on EXISTING holdings proceed when (and only when) the halt was
caused by the daily-order-count breach. It must NOT bypass:
  - daily-LOSS halt,
  - manual /halt,
  - any other / undeterminable halt reason,
and BUY signals stay blocked in every case.

This mirrors the SPEC-033 position_watchdog direct-sell-bypass philosophy,
extended to the persona sell path. The decision logic lives in pure helpers
(``_count_halt_allows_sells`` + ``_partition_signals_for_count_halt``) so the
four critical scenarios are unit-testable without standing up the full persona
pipeline (precedent: tests/personas/test_halt_gate_throttle.py).

Reproduction-first (LIVE money/risk logic).

@MX:SPEC: SPEC-TRADING-037
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from typing import ClassVar

from trading.personas import orchestrator as orch

# --- active-trip fixtures (shape of audit_log CIRCUIT_BREAKER_TRIP details) ---

_COUNT_BREACH = "daily_count: 오늘 주문 10 → 한도 10"
_LOSS_BREACH = "daily_loss: 오늘 손익 -1.50% ≤ 한도 -1.00%"


def _count_trip() -> dict:
    return {"reason": "pre-order limit breach", "breaches": [_COUNT_BREACH]}


def _loss_trip() -> dict:
    return {"reason": "pre-order limit breach", "breaches": [_LOSS_BREACH]}


def _mixed_trip() -> dict:
    return {
        "reason": "pre-order limit breach",
        "breaches": [_COUNT_BREACH, _LOSS_BREACH],
    }


def _manual_trip() -> dict:
    return {"reason": "manual /halt", "actor": "cli"}


class TestCountHaltAllowsSells:
    """REQ-037-5 (b) — only the count breach (no loss) permits the sell bypass."""

    def test_count_halt_allows(self):
        assert orch._count_halt_allows_sells(_count_trip()) is True

    def test_loss_halt_blocks(self):
        assert orch._count_halt_allows_sells(_loss_trip()) is False

    def test_mixed_count_and_loss_blocks(self):
        # fail-safe: any loss breach in the mix blocks even risk-reducing sells.
        assert orch._count_halt_allows_sells(_mixed_trip()) is False

    def test_manual_halt_blocks(self):
        assert orch._count_halt_allows_sells(_manual_trip()) is False

    def test_undeterminable_blocks(self):
        # fail-safe: no active trip / malformed -> conservative block.
        assert orch._count_halt_allows_sells(None) is False
        assert orch._count_halt_allows_sells({"reason": "pre-order limit breach"}) is False
        assert orch._count_halt_allows_sells({"reason": "weird"}) is False


def _sig(ticker: str, side: str, qty: int = 1) -> dict:
    return {"ticker": ticker, "side": side, "qty": qty}


_HOLDINGS = [{"ticker": "005930", "qty": 10}, {"ticker": "000660", "qty": 5}]


class TestPartitionSignalsForCountHalt:
    """REQ-037-5 (a/c) — keep only risk-reducing SELLs on existing holdings."""

    def test_count_halt_keeps_sell_on_holding(self):
        signals = [_sig("005930", "sell"), _sig("035720", "buy")]
        ids = [101, 102]
        kept_sig, kept_ids = orch._partition_signals_for_count_halt(
            signals, ids, holdings=_HOLDINGS, active_trip=_count_trip()
        )
        assert kept_sig == [_sig("005930", "sell")]
        assert kept_ids == [101]

    def test_count_halt_blocks_buy(self):
        signals = [_sig("035720", "buy")]
        ids = [201]
        kept_sig, kept_ids = orch._partition_signals_for_count_halt(
            signals, ids, holdings=_HOLDINGS, active_trip=_count_trip()
        )
        assert kept_sig == []
        assert kept_ids == []

    def test_count_halt_blocks_sell_on_non_held_ticker(self):
        # selling a non-held ticker is not "risk-reducing" (would be a short).
        signals = [_sig("999999", "sell")]
        ids = [301]
        kept_sig, kept_ids = orch._partition_signals_for_count_halt(
            signals, ids, holdings=_HOLDINGS, active_trip=_count_trip()
        )
        assert kept_sig == []
        assert kept_ids == []

    def test_loss_halt_blocks_sell(self):
        signals = [_sig("005930", "sell")]
        ids = [401]
        kept_sig, kept_ids = orch._partition_signals_for_count_halt(
            signals, ids, holdings=_HOLDINGS, active_trip=_loss_trip()
        )
        assert kept_sig == []
        assert kept_ids == []

    def test_manual_halt_blocks_sell(self):
        signals = [_sig("005930", "sell")]
        ids = [501]
        kept_sig, kept_ids = orch._partition_signals_for_count_halt(
            signals, ids, holdings=_HOLDINGS, active_trip=_manual_trip()
        )
        assert kept_sig == []
        assert kept_ids == []


class TestHaltGatesWireCountBypass:
    """REQ-037-5 (a) — each cycle halt gate references the count-halt bypass.

    Structural (AST) wiring check — the gate's return path must consult the
    bypass helper, not unconditionally ``return res`` (mirrors the existing
    test_halt_gate_throttle structural-wiring precedent).
    """

    _GATE_FUNCS: ClassVar[list] = [
        orch.run_pre_market_cycle,
        orch.run_intraday_cycle,
    ]

    def _names_called(self, func) -> set[str]:
        src = textwrap.dedent(inspect.getsource(func))
        tree = ast.parse(src)
        out: set[str] = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Name):
                    out.add(f.id)
                elif isinstance(f, ast.Attribute):
                    out.add(f.attr)
        return out

    def test_gates_reference_partition_helper(self):
        for func in self._GATE_FUNCS:
            called = self._names_called(func)
            # The gate delegates to the count-halt bypass orchestrator helper,
            # which internally consults _partition_signals_for_count_halt.
            assert "_maybe_count_halt_bypass" in called, (
                f"{func.__name__} must route halted SELLs through "
                f"_maybe_count_halt_bypass; found {sorted(called)}"
            )


class TestMaybeCountHaltBypass:
    """REQ-037-5 (a/b/d) — the gate-body helper: bypass vs skip + audit/telegram."""

    def test_count_halt_returns_sells_and_audits(self):
        from unittest.mock import patch

        with patch(
            "trading.risk.auto_resume._fetch_active_trip", return_value=_count_trip()
        ), patch.object(orch.tg, "system_briefing") as mock_tg, patch.object(
            orch, "audit"
        ) as mock_audit:
            signals = [_sig("005930", "sell", 10), _sig("035720", "buy", 3)]
            kept_sig, kept_ids = orch._maybe_count_halt_bypass(
                signals, [11, 12], holdings=_HOLDINGS, cycle_kind="intraday"
            )
        assert kept_sig == [_sig("005930", "sell", 10)]
        assert kept_ids == [11]
        # REQ-037-5 (d): bypass is audited + telegram-notified.
        mock_audit.assert_called_once()
        assert mock_audit.call_args[0][0] == "COUNT_HALT_BYPASS_SELL"
        mock_tg.assert_called_once()

    def test_loss_halt_returns_empty_and_throttles(self):
        from unittest.mock import patch

        with patch(
            "trading.risk.auto_resume._fetch_active_trip", return_value=_loss_trip()
        ), patch.object(
            orch.circuit_breaker, "maybe_notify_halt", return_value=True
        ) as mock_notify, patch.object(orch, "audit") as mock_audit:
            kept_sig, kept_ids = orch._maybe_count_halt_bypass(
                [_sig("005930", "sell", 10)], [21], holdings=_HOLDINGS, cycle_kind="pre_market"
            )
        assert kept_sig == []
        assert kept_ids == []
        # loss halt -> normal throttled skip path, NOT a bypass audit.
        mock_notify.assert_called_once()
        assert not any(
            c.args and c.args[0] == "COUNT_HALT_BYPASS_SELL" for c in mock_audit.call_args_list
        )
