"""SPEC-TRADING-042 Module C — sell in-flight lock + cooldown (REQ-042-C1..C3).

Reproduction-first (money/risk logic, LIVE imminent). RC-3 (2026-06-08): 033780
(KT&G -7.3%) was decided for SELL **4 times in 5 minutes** (09:04 / 09:31 / 09:32
/ 09:34) because the position watchdog (``*/5``) and the persona orchestrator
BOTH evaluate the same stop-loss and neither knew the other had already fired.

These tests pin a SHARED in-flight lock (a single helper used by both firing
paths). The lock has two legs:

- **submitted leg** — an unresolved ``submitted`` SELL order for the ticker (the
  in-flight order that Module B's resolver converges). While it is open the lock
  holds regardless of elapsed time.
- **cooldown leg** — a recent fire marker (``position_action_markers``
  action='sell_inflight') within ``SELL_INFLIGHT_COOLDOWN_SECONDS``.

``is_sell_locked`` = submitted-leg OR cooldown-leg. A genuine NEW exit signal that
arrives AFTER the prior order resolves (submitted-leg clears via Module B) AND the
cooldown has elapsed is NOT blocked (REQ-042-C2 capital-preservation). The marker
is DB-backed so the lock survives a restart and is idempotent (REQ-042-C3).

All DB/audit I/O is faked with an in-memory double; no network, no real DB.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import patch

# --------------------------------------------------------------------------- #
# In-memory doubles: a position_action_markers store (with created_at) + an
# orders store (for the submitted-sell leg) + an audit sink.
# --------------------------------------------------------------------------- #


class _Marker:
    def __init__(self, trading_day: date, ticker: str, action: str, created_at: datetime) -> None:
        self.trading_day = trading_day
        self.ticker = ticker
        self.action = action
        self.created_at = created_at


class _Store:
    """Shared fake for sell_lock: markers + orders + audit events."""

    def __init__(self) -> None:
        self.markers: list[_Marker] = []
        self.orders: list[dict[str, Any]] = []  # {ticker, side, status}
        self.audits: list[tuple[str, dict[str, Any]]] = []

    # --- helpers used by the cursor double ---
    def find_marker(self, day: date, ticker: str, action: str) -> _Marker | None:
        for m in self.markers:
            if m.trading_day == day and m.ticker == ticker and m.action == action:
                return m
        return None

    def has_submitted_sell(self, ticker: str) -> bool:
        return any(
            o["ticker"] == ticker and o["side"] == "sell" and o["status"] == "submitted"
            for o in self.orders
        )


class _Cursor:
    def __init__(self, store: _Store, now: datetime, *, fail: bool = False) -> None:
        self._store = store
        self._now = now
        self._fail = fail
        self._result: Any = None

    def execute(self, sql: str, params: Any = None) -> None:
        if self._fail:
            raise RuntimeError("simulated DB failure")
        text = " ".join(sql.split()).upper()
        params = params or ()

        if "FROM ORDERS" in text and "SUBMITTED" in text:
            ticker = params[0]
            self._result = (1,) if self._store.has_submitted_sell(ticker) else None
            return

        if text.startswith("SELECT") and "POSITION_ACTION_MARKERS" in text:
            day, ticker, action = params[0], params[1], params[2]
            m = self._store.find_marker(day, ticker, action)
            self._result = (m.created_at,) if m else None
            return

        if "INSERT INTO POSITION_ACTION_MARKERS" in text:
            day, ticker, action = params[0], params[1], params[2]
            existing = self._store.find_marker(day, ticker, action)
            if existing is not None:
                # ON CONFLICT DO UPDATE SET created_at = NOW() — refresh window.
                existing.created_at = self._now
            else:
                self._store.markers.append(_Marker(day, ticker, action, self._now))
            return

        if "DELETE FROM POSITION_ACTION_MARKERS" in text:
            day, ticker, action = params[0], params[1], params[2]
            self._store.markers = [
                m for m in self._store.markers
                if not (m.trading_day == day and m.ticker == ticker and m.action == action)
            ]
            return

    def fetchone(self) -> Any:
        return self._result

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class _Conn:
    def __init__(self, store: _Store, now: datetime, *, fail: bool = False) -> None:
        self._store = store
        self._now = now
        self._fail = fail

    def cursor(self) -> _Cursor:
        return _Cursor(self._store, self._now, fail=self._fail)

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None

    def __enter__(self) -> _Conn:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


_TODAY = date(2026, 6, 8)
_T0 = datetime(2026, 6, 8, 0, 4, 0, tzinfo=UTC)  # ~09:04 KST (RC-3 first fire)


def _patch(store: _Store, *, now: datetime, fail: bool = False):
    """Patch sell_lock's connection + audit + clocks against the shared store."""
    from trading.kis import sell_lock

    @contextmanager
    def _factory(*_a: Any, **_k: Any):
        yield _Conn(store, now, fail=fail)

    def _audit(event_type: str, actor: str, details: dict[str, Any] | None = None) -> None:
        store.audits.append((event_type, details or {}))

    return [
        patch.object(sell_lock, "connection", side_effect=_factory),
        patch.object(sell_lock, "audit", side_effect=_audit),
        patch.object(sell_lock, "_now", return_value=now),
        patch.object(sell_lock, "_today_kst", return_value=_TODAY),
    ]


@contextmanager
def _patched(store: _Store, *, now: datetime, fail: bool = False):
    patches = _patch(store, now=now, fail=fail)
    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in reversed(patches):
            p.stop()


# --------------------------------------------------------------------------- #
# REQ-042-C1 — in-flight lock (cooldown leg + submitted leg)
# --------------------------------------------------------------------------- #
class TestInFlightLock:
    def test_unlocked_initially(self):
        store = _Store()
        with _patched(store, now=_T0):
            from trading.kis import sell_lock

            assert sell_lock.is_sell_locked("033780") is False

    def test_cooldown_leg_locks_after_fire(self):
        """A recent fire marker within the cooldown window holds the lock."""
        store = _Store()
        with _patched(store, now=_T0):
            from trading.kis import sell_lock

            sell_lock.set_sell_inflight("033780")
            assert sell_lock.is_sell_locked("033780") is True

    def test_submitted_leg_locks_without_marker(self):
        """An unresolved 'submitted' SELL holds the lock even with no marker."""
        store = _Store()
        store.orders.append({"ticker": "033780", "side": "sell", "status": "submitted"})
        with _patched(store, now=_T0):
            from trading.kis import sell_lock

            assert sell_lock.is_sell_locked("033780") is True

    def test_other_ticker_not_locked(self):
        store = _Store()
        with _patched(store, now=_T0):
            from trading.kis import sell_lock

            sell_lock.set_sell_inflight("033780")
            assert sell_lock.is_sell_locked("000660") is False


# --------------------------------------------------------------------------- #
# REQ-042-C2 — genuine new signal preserved after resolution + cooldown
# --------------------------------------------------------------------------- #
class TestGenuineSignalPreserved:
    def test_lock_clears_after_cooldown_and_resolution(self):
        """After the order resolves (no submitted) AND cooldown elapses, a NEW
        genuine exit is NOT blocked (capital-preservation, REQ-042-C2)."""
        store = _Store()
        with _patched(store, now=_T0):
            from trading.kis import sell_lock

            sell_lock.set_sell_inflight("033780")
            assert sell_lock.is_sell_locked("033780") is True

        # Module B resolved the order (no submitted row) and the cooldown elapsed.
        later = _T0 + timedelta(seconds=sell_lock.SELL_INFLIGHT_COOLDOWN_SECONDS + 1)
        with _patched(store, now=later):
            assert sell_lock.is_sell_locked("033780") is False
            # a fresh genuine sell may proceed through the shared guard
            assert sell_lock.guard_sell("033780", actor="position_watchdog") is True

    def test_still_locked_if_submitted_open_even_after_cooldown(self):
        """Cooldown elapsed but the order is STILL submitted (RC-2 leak) → locked.
        This is exactly the 09:04→09:31 gap where the cooldown alone would miss."""
        store = _Store()
        store.orders.append({"ticker": "033780", "side": "sell", "status": "submitted"})
        with _patched(store, now=_T0):
            from trading.kis import sell_lock

            sell_lock.set_sell_inflight("033780")
        later = _T0 + timedelta(seconds=sell_lock.SELL_INFLIGHT_COOLDOWN_SECONDS + 600)
        with _patched(store, now=later):
            assert sell_lock.is_sell_locked("033780") is True


# --------------------------------------------------------------------------- #
# REQ-042-C3 — idempotent + restart-surviving marker
# --------------------------------------------------------------------------- #
class TestIdempotentRestartSafe:
    def test_set_is_idempotent_single_row(self):
        store = _Store()
        with _patched(store, now=_T0):
            from trading.kis import sell_lock

            sell_lock.set_sell_inflight("033780")
            sell_lock.set_sell_inflight("033780")
        rows = [m for m in store.markers if m.ticker == "033780" and m.action == "sell_inflight"]
        assert len(rows) == 1

    def test_marker_survives_restart(self):
        """The marker lives in the store (DB) → a 'restarted' process still sees
        the lock within the same window (REQ-042-C3)."""
        store = _Store()
        with _patched(store, now=_T0):
            from trading.kis import sell_lock

            sell_lock.set_sell_inflight("033780")
        # New process (fresh patched scope, same store) within the window.
        soon = _T0 + timedelta(seconds=30)
        with _patched(store, now=soon):
            assert sell_lock.is_sell_locked("033780") is True

    def test_set_refreshes_cooldown_window(self):
        """A re-fire refreshes created_at so the cooldown slides (UPSERT semantics)."""
        store = _Store()
        with _patched(store, now=_T0):
            from trading.kis import sell_lock

            sell_lock.set_sell_inflight("033780")
        mid = _T0 + timedelta(seconds=sell_lock.SELL_INFLIGHT_COOLDOWN_SECONDS - 10)
        with _patched(store, now=mid):
            sell_lock.set_sell_inflight("033780")  # refresh
        # now past the ORIGINAL window but within the refreshed one
        within_refreshed = mid + timedelta(seconds=10)
        with _patched(store, now=within_refreshed):
            assert sell_lock.is_sell_locked("033780") is True


# --------------------------------------------------------------------------- #
# REQ-042-D3 — audit trail for lock lifecycle
# --------------------------------------------------------------------------- #
class TestAuditTrail:
    def test_set_audits_locked(self):
        store = _Store()
        with _patched(store, now=_T0):
            from trading.kis import sell_lock

            sell_lock.set_sell_inflight("033780")
        assert any(e == "SELL_INFLIGHT_LOCKED" for e, _ in store.audits)

    def test_guard_suppress_audits_duplicate(self):
        store = _Store()
        with _patched(store, now=_T0):
            from trading.kis import sell_lock

            sell_lock.set_sell_inflight("033780")
            allowed = sell_lock.guard_sell("033780", actor="position_watchdog")
        assert allowed is False
        assert any(e == "SELL_SUPPRESSED_DUPLICATE" for e, _ in store.audits)

    def test_guard_clears_stale_marker_audits_cleared(self):
        """A stale marker (past cooldown, order resolved) is cleared by the guard
        and the lifecycle end is audited SELL_INFLIGHT_CLEARED."""
        store = _Store()
        with _patched(store, now=_T0):
            from trading.kis import sell_lock

            sell_lock.set_sell_inflight("033780")
        later = _T0 + timedelta(seconds=sell_lock.SELL_INFLIGHT_COOLDOWN_SECONDS + 1)
        with _patched(store, now=later):
            allowed = sell_lock.guard_sell("033780", actor="orchestrator")
        assert allowed is True
        assert any(e == "SELL_INFLIGHT_CLEARED" for e, _ in store.audits)
        # the stale marker row is gone
        assert not [m for m in store.markers if m.ticker == "033780"]


# --------------------------------------------------------------------------- #
# Fail-open safety — capital-preservation: a lock that wrongly BLOCKS a
# stop-loss is worse than a duplicate. On any DB error the guard ALLOWS.
# --------------------------------------------------------------------------- #
class TestFailOpen:
    def test_is_sell_locked_fail_open(self):
        store = _Store()
        with _patched(store, now=_T0, fail=True):
            from trading.kis import sell_lock

            assert sell_lock.is_sell_locked("033780") is False

    def test_guard_sell_fail_open_allows(self):
        store = _Store()
        with _patched(store, now=_T0, fail=True):
            from trading.kis import sell_lock

            assert sell_lock.guard_sell("033780", actor="position_watchdog") is True

    def test_set_sell_inflight_never_raises(self):
        store = _Store()
        with _patched(store, now=_T0, fail=True):
            from trading.kis import sell_lock

            # best-effort: a marker write failure must not crash the sell path
            sell_lock.set_sell_inflight("033780")
