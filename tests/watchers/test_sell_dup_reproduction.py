"""SPEC-TRADING-042 Module C — RC-3 reproduction: duplicate/racing sell path.

This is the AC-3 reproduction. On 2026-06-08 033780 (KT&G -7.3%) fired a SELL
**4 times in 5 minutes** (09:04 / 09:31 / 09:32 / 09:34) because the position
watchdog (``*/5``) and the persona orchestrator BOTH evaluate the same stop-loss
and neither knew the other had already fired.

These tests drive the TWO REAL firing paths against a SHARED in-flight lock store:

- ``position_watchdog.poll_position_watchdog`` (the watchdog ``*/5`` path that
  sells DIRECTLY via ``kis_sell``), and
- ``orchestrator._execute_signal`` (the persona sell branch).

Reproduction-first: WITHOUT the shared lock both paths fire on every evaluation
(4 sells). WITH the shared lock the first fire takes the lock and every later
evaluation of the same ticker in the in-flight window is SUPPRESSED → exactly ONE
``kis_sell``. The same store is shared by both modules so the test proves both
firing paths respect the SAME lock (a single helper, not duplicated logic).

All KIS / Telegram / DB / audit I/O is faked; no network, no real DB.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import patch

from tests.watchers.test_sell_inflight_lock import _Conn, _Store

_TODAY = date(2026, 6, 8)
_T0 = datetime(2026, 6, 8, 0, 4, 0, tzinfo=UTC)  # 09:04 KST (first fire)

_TICKER = "033780"


def _holding(qty: int, pnl_pct: float) -> dict[str, Any]:
    return {
        "ticker": _TICKER,
        "name": "KT&G",
        "qty": qty,
        "avg_cost": 1000,
        "current_price": 900,
        "eval_amount": qty * 900,
        "pnl_amount": -qty * 100,
        "pnl_pct": pnl_pct,
    }


def _thresholds() -> dict[str, Any]:
    return {"ticker": _TICKER, "effective_stop": -5.0, "effective_take": 12.0, "source": "dynamic"}


@contextmanager
def _patch_sell_lock(store: _Store, *, now: datetime):
    """Patch sell_lock internals against the shared store (single source of truth)."""
    from trading.kis import sell_lock

    @contextmanager
    def _factory(*_a: Any, **_k: Any):
        yield _Conn(store, now)

    def _audit(event_type: str, actor: str, details: dict[str, Any] | None = None) -> None:
        store.audits.append((event_type, details or {}))

    patches = [
        patch.object(sell_lock, "connection", side_effect=_factory),
        patch.object(sell_lock, "audit", side_effect=_audit),
        patch.object(sell_lock, "_now", return_value=now),
        patch.object(sell_lock, "_today_kst", return_value=_TODAY),
    ]
    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in reversed(patches):
            p.stop()


class _WatchdogMarkerCursor:
    """Tolerant double for the watchdog's OWN markers (take_profit / trim).

    These are unrelated to the sell_inflight lock — they only need to not crash
    the poll. Always reports 'no marker' (so the stop fires) and accepts inserts.
    """

    def execute(self, sql: str, params: Any = None) -> None:
        return None

    def fetchone(self) -> Any:
        return None

    def __enter__(self) -> _WatchdogMarkerCursor:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class _WatchdogMarkerConn:
    def cursor(self) -> _WatchdogMarkerCursor:
        return _WatchdogMarkerCursor()

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None

    def __enter__(self) -> _WatchdogMarkerConn:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


def _fire_watchdog(sell_counter: list[str], *, qty: int = 7) -> None:
    """Run one watchdog poll for 033780 below its stop (a stop-loss evaluation)."""
    from trading.watchers import position_watchdog

    @contextmanager
    def _wd_conn(*_a: Any, **_k: Any):
        yield _WatchdogMarkerConn()

    with (
        patch.object(position_watchdog, "connection", side_effect=_wd_conn),
        patch.object(position_watchdog, "_build_client", return_value=object()),
        patch.object(position_watchdog, "_read_holdings", return_value=[_holding(qty, -7.3)]),
        patch.object(position_watchdog, "get_dynamic_thresholds", return_value=_thresholds()),
        patch.object(position_watchdog, "_confirm_qty", return_value=qty),
        patch.object(position_watchdog, "_portfolio_value", return_value=10_000_000),
        patch.object(position_watchdog, "system_briefing"),
        patch.object(position_watchdog, "audit"),
        patch.object(
            position_watchdog, "kis_sell",
            side_effect=lambda *a, **k: sell_counter.append("wd"),
        ),
    ):
        position_watchdog.poll_position_watchdog()


def _fire_persona(sell_counter: list[str], *, qty: int = 7) -> None:
    """Run one persona _execute_signal SELL for 033780 (the other firing path)."""
    from trading.personas import orchestrator

    sig = {"side": "sell", "ticker": _TICKER, "qty": qty}

    class _Client:
        class mode:
            value = "paper"

    with (
        patch.object(orchestrator, "resolve_stuck_orders", lambda *a, **k: None),
        patch.object(orchestrator, "intraday_reconcile", lambda *a, **k: None),
        patch.object(orchestrator, "clamp_sell_to_confirmed", lambda *a, **k: qty),
        patch.object(orchestrator, "audit"),
        patch.object(
            orchestrator, "kis_sell",
            side_effect=lambda *a, **k: (sell_counter.append("persona"), {"order_id": 1})[1],
        ),
    ):
        orchestrator._execute_signal(_Client(), sig, decision_id=99)


# --------------------------------------------------------------------------- #
# RC-3 reproduction — exactly ONE sell despite 4 evaluations
# --------------------------------------------------------------------------- #
class TestRC3DuplicateSellReproduction:
    def test_four_evaluations_fire_only_one_sell(self):
        """RC-3: watchdog (09:04) + persona (09:31) + watchdog (09:32/09:34) all
        evaluate the same stop in the in-flight window → ONLY ONE kis_sell."""
        store = _Store()
        sell_counter: list[str] = []

        # 09:04 — watchdog fires first (takes the lock).
        with _patch_sell_lock(store, now=_T0):
            _fire_watchdog(sell_counter)
        # Simulate the in-flight order persisting (RC-2 leak: 'submitted').
        store.orders.append({"ticker": _TICKER, "side": "sell", "status": "submitted"})

        # 09:31 — persona evaluates the SAME stop (different path) → suppressed.
        with _patch_sell_lock(store, now=_T0 + timedelta(minutes=27)):
            _fire_persona(sell_counter)
        # 09:32, 09:34 — watchdog re-evaluates → suppressed.
        with _patch_sell_lock(store, now=_T0 + timedelta(minutes=28)):
            _fire_watchdog(sell_counter)
        with _patch_sell_lock(store, now=_T0 + timedelta(minutes=30)):
            _fire_watchdog(sell_counter)

        assert sell_counter == ["wd"], f"expected ONE sell, got {sell_counter}"

    def test_both_paths_respect_same_lock_persona_first(self):
        """The persona can take the lock and the watchdog then suppresses (the
        symmetric case — proves a SINGLE shared lock, not per-path logic)."""
        store = _Store()
        sell_counter: list[str] = []

        with _patch_sell_lock(store, now=_T0):
            _fire_persona(sell_counter)
        store.orders.append({"ticker": _TICKER, "side": "sell", "status": "submitted"})
        with _patch_sell_lock(store, now=_T0 + timedelta(minutes=1)):
            _fire_watchdog(sell_counter)

        assert sell_counter == ["persona"], f"watchdog should suppress, got {sell_counter}"


# --------------------------------------------------------------------------- #
# REQ-042-C2 — a genuine NEW signal after resolution + cooldown is NOT blocked
# --------------------------------------------------------------------------- #
class TestGenuineNewSignalNotBlocked:
    def test_new_sell_after_resolution_and_cooldown_fires(self):
        """After Module B resolves the order (no 'submitted') AND the cooldown
        elapses, a fresh stop-loss fires again (REQ-042-C2 capital-preservation)."""
        from trading.kis import sell_lock

        store = _Store()
        sell_counter: list[str] = []

        # First fire takes the lock, order goes in-flight.
        with _patch_sell_lock(store, now=_T0):
            _fire_watchdog(sell_counter)
        store.orders.append({"ticker": _TICKER, "side": "sell", "status": "submitted"})

        # Module B resolver converges the order (no longer submitted) ...
        store.orders.clear()
        # ... and the cooldown window elapses.
        later = _T0 + timedelta(seconds=sell_lock.SELL_INFLIGHT_COOLDOWN_SECONDS + 60)

        # A genuine NEW exit signal now arrives → it must NOT be blocked.
        with _patch_sell_lock(store, now=later):
            _fire_watchdog(sell_counter)

        assert sell_counter == ["wd", "wd"], (
            f"a genuine new exit after resolution must fire, got {sell_counter}"
        )
