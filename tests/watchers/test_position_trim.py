"""SPEC-TRADING-040 — concentration-cap auto-trim (M2) + stagnation rotation
trim (M1c). Reproduction-first money/risk tests.

Both trims are RISK/REBALANCE-motivated (EV-exempt — see SPEC ADR-1), code-enforced
in the position watchdog (the decision persona effectively never sells), idempotent
per (KST day, ticker) via ``position_action_markers`` action='trim', and never
over-sell / short (over-sell clamp, REQ-040-2d).

The watchdog's DB-backed marker is replaced by the set-backed double already used
by the SPEC-033/038 watchdog tests (imported from this package's existing tests).

@MX:SPEC: SPEC-TRADING-040
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Any
from unittest.mock import patch

import pytest


# Action-aware marker double (the SPEC-038 double hardcodes 'take_profit'; the
# trim path uses action='trim', so we need to honour the real action param).
class _MarkerCursor:
    def __init__(self, store: set[tuple[date, str, str]]) -> None:
        self._store = store
        self._result: Any = None

    def execute(self, sql: str, params: Any = None) -> None:
        text = sql.strip().upper()
        day, ticker, action = params[0], params[1], params[2]
        if text.startswith("SELECT"):
            self._result = (1,) if (day, ticker, action) in self._store else None
        elif "INSERT" in text:
            self._store.add((day, ticker, action))

    def fetchone(self) -> Any:
        return self._result

    def __enter__(self) -> _MarkerCursor:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class _MarkerConn:
    def __init__(self, store: set[tuple[date, str, str]]) -> None:
        self._store = store

    def cursor(self) -> _MarkerCursor:
        return _MarkerCursor(self._store)

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None

    def __enter__(self) -> _MarkerConn:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


def _holding(ticker: str, qty: int, pnl_pct: float, eval_amount: int) -> dict:
    return {
        "ticker": ticker,
        "name": ticker,
        "qty": qty,
        "avg_cost": eval_amount // max(qty, 1),
        "current_price": eval_amount // max(qty, 1),
        "eval_amount": eval_amount,
        "pnl_amount": 0,
        "pnl_pct": pnl_pct,
    }


@pytest.fixture(autouse=True)
def _marker_store():
    """Fresh set-backed position_action_markers double per test (offline)."""
    from trading.watchers import position_watchdog

    store: set[tuple[date, str, str]] = set()

    @contextmanager
    def _factory(*_a: Any, **_k: Any):
        yield _MarkerConn(store)

    with patch.object(position_watchdog, "connection", side_effect=_factory):
        yield store


# --------------------------------------------------------------------------- #
# classify_concentration — pure helper (M2)
# --------------------------------------------------------------------------- #
class TestClassifyConcentration:
    def test_over_cap_returns_trim_qty_back_to_cap(self):
        """REQ-040-2a: a ticker at 40% of a 1,000,000 book trims down to ~25%.

        eval=400,000 (qty 40 @ 10,000); cap 25% → target 250,000 → excess
        150,000 → trim 15 shares (eval/share = 10,000). Result ~250,000 = 25%.
        """
        from trading.watchers.position_watchdog import classify_concentration

        action, trim_qty = classify_concentration(
            eval_amount=400_000,
            qty=40,
            total_portfolio_value=1_000_000,
            cap_pct=0.25,
        )
        assert action == "trim"
        assert trim_qty == 15

    def test_under_cap_returns_skip(self):
        from trading.watchers.position_watchdog import classify_concentration

        action, trim_qty = classify_concentration(
            eval_amount=200_000, qty=20, total_portfolio_value=1_000_000, cap_pct=0.25
        )
        assert action == "skip"
        assert trim_qty == 0

    def test_at_cap_exactly_returns_skip(self):
        from trading.watchers.position_watchdog import classify_concentration

        action, _ = classify_concentration(
            eval_amount=250_000, qty=25, total_portfolio_value=1_000_000, cap_pct=0.25
        )
        assert action == "skip"

    def test_over_sell_clamp_never_exceeds_qty(self):
        """REQ-040-2d: trim qty is clamped to the held qty (never short)."""
        from trading.watchers.position_watchdog import classify_concentration

        # Pathological: tiny qty but huge eval/share — excess > position.
        action, trim_qty = classify_concentration(
            eval_amount=900_000, qty=3, total_portfolio_value=1_000_000, cap_pct=0.25
        )
        assert action == "trim"
        assert trim_qty <= 3

    def test_zero_portfolio_value_returns_skip_no_crash(self):
        from trading.watchers.position_watchdog import classify_concentration

        action, trim_qty = classify_concentration(
            eval_amount=100_000, qty=10, total_portfolio_value=0, cap_pct=0.25
        )
        assert action == "skip"
        assert trim_qty == 0

    def test_late_cycle_tighter_cap_trims_more(self):
        """REQ-040-2c: a tighter cap (late-cycle) trims a position that the
        normal cap would leave alone."""
        from trading.watchers.position_watchdog import classify_concentration

        # 22% position: under the 25% normal cap, over a 20% late-cycle cap.
        normal, _ = classify_concentration(
            eval_amount=220_000, qty=22, total_portfolio_value=1_000_000, cap_pct=0.25
        )
        tight, tight_qty = classify_concentration(
            eval_amount=220_000, qty=22, total_portfolio_value=1_000_000, cap_pct=0.20
        )
        assert normal == "skip"
        assert tight == "trim"
        assert tight_qty >= 1


# --------------------------------------------------------------------------- #
# is_stagnant — pure helper (M1c)
# --------------------------------------------------------------------------- #
class TestIsStagnant:
    def test_long_hold_flat_neutral_rsi_is_stagnant(self):
        from trading.watchers.position_watchdog import is_stagnant

        assert is_stagnant(holding_days=25, pnl_pct=0.5, rsi=50.0) is True

    def test_short_hold_not_stagnant(self):
        from trading.watchers.position_watchdog import is_stagnant

        assert is_stagnant(holding_days=3, pnl_pct=0.5, rsi=50.0) is False

    def test_big_move_not_stagnant(self):
        from trading.watchers.position_watchdog import is_stagnant

        assert is_stagnant(holding_days=25, pnl_pct=8.0, rsi=50.0) is False

    def test_extreme_rsi_not_stagnant(self):
        """RSI outside the neutral band is handled by the extreme exit rules."""
        from trading.watchers.position_watchdog import is_stagnant

        assert is_stagnant(holding_days=25, pnl_pct=0.5, rsi=88.0) is False

    def test_missing_data_not_stagnant(self):
        """Defensive: missing holding_days / rsi → not stagnant (never crash)."""
        from trading.watchers.position_watchdog import is_stagnant

        assert is_stagnant(holding_days=None, pnl_pct=0.5, rsi=None) is False


# --------------------------------------------------------------------------- #
# poll_position_watchdog — concentration trim integration (M2)
# --------------------------------------------------------------------------- #
class TestConcentrationTrimIntegration:
    def _run(self, holdings, total_value, *, late_cycle=False, today=date(2026, 6, 3)):
        from trading.watchers import position_watchdog

        with (
            patch.object(position_watchdog, "_today_kst", return_value=today),
            patch.object(position_watchdog, "_build_client", return_value=object()),
            patch.object(position_watchdog, "_read_holdings", return_value=holdings),
            patch.object(
                position_watchdog, "_portfolio_value", return_value=total_value
            ),
            patch.object(
                position_watchdog, "_late_cycle_active", return_value=late_cycle
            ),
            patch.object(
                position_watchdog,
                "get_dynamic_thresholds",
                return_value={"effective_stop": -8.5, "effective_take": 12.0},
            ),
            patch.object(
                position_watchdog,
                "_confirm_qty",
                side_effect=lambda _c, t: next(
                    (h["qty"] for h in holdings if h["ticker"] == t), 0
                ),
            ),
            patch.object(position_watchdog, "kis_sell") as sell,
            patch.object(position_watchdog, "system_briefing"),
            patch.object(position_watchdog, "audit") as audit,
        ):
            metrics = position_watchdog.poll_position_watchdog()
        return metrics, sell, audit

    def test_concentrated_ticker_trimmed(self):
        """AC-2 (reproduction): a ticker over the 25% cap is auto-trimmed by a
        direct kis_sell of the excess qty, with a trim audit event.

        086790 at 40% of a 1,000,000 book (normal-range pnl, RSI<85) is left
        untouched by the extreme stop/take rules, but trimmed by the cap.
        """
        holdings = [_holding("086790", qty=40, pnl_pct=2.0, eval_amount=400_000)]
        metrics, sell, audit = self._run(holdings, 1_000_000)

        assert sell.call_count == 1
        _, kwargs = sell.call_args
        assert kwargs["ticker"] == "086790"
        assert kwargs["qty"] == 15  # trim back to 25%
        assert metrics["trim_exits"] == 1
        kinds = [a.kwargs["details"].get("kind") for a in audit.call_args_list]
        assert "trim" in kinds

    def test_under_cap_not_trimmed(self):
        holdings = [_holding("000660", qty=10, pnl_pct=2.0, eval_amount=150_000)]
        metrics, sell, _ = self._run(holdings, 1_000_000)
        assert sell.call_count == 0
        assert metrics["trim_exits"] == 0

    def test_late_cycle_tightens_trigger(self):
        """REQ-040-2c: a 22% position is skipped normally but trimmed in
        late-cycle defence (tighter cap)."""
        holdings = [_holding("086790", qty=22, pnl_pct=2.0, eval_amount=220_000)]

        _normal, sell_n, _ = self._run(holdings, 1_000_000, late_cycle=False)
        _late, sell_l, _ = self._run(holdings, 1_000_000, late_cycle=True)

        assert sell_n.call_count == 0
        assert sell_l.call_count == 1


class TestConcentrationTrimIdempotentSameStore:
    def test_marker_blocks_second_poll_same_store(self):
        """REQ-040-2b: with a shared marker store, a 2nd same-day poll is a skip."""
        from trading.watchers import position_watchdog

        store: set[tuple[date, str, str]] = set()

        @contextmanager
        def _factory(*_a: Any, **_k: Any):
            yield _MarkerConn(store)

        holdings = [_holding("086790", qty=40, pnl_pct=2.0, eval_amount=400_000)]

        def _poll():
            with (
                patch.object(position_watchdog, "connection", side_effect=_factory),
                patch.object(
                    position_watchdog, "_today_kst", return_value=date(2026, 6, 3)
                ),
                patch.object(position_watchdog, "_build_client", return_value=object()),
                patch.object(
                    position_watchdog, "_read_holdings", return_value=holdings
                ),
                patch.object(
                    position_watchdog, "_portfolio_value", return_value=1_000_000
                ),
                patch.object(
                    position_watchdog, "_late_cycle_active", return_value=False
                ),
                patch.object(
                    position_watchdog,
                    "get_dynamic_thresholds",
                    return_value={"effective_stop": -8.5, "effective_take": 12.0},
                ),
                patch.object(position_watchdog, "_confirm_qty", return_value=40),
                patch.object(position_watchdog, "kis_sell") as sell,
                patch.object(position_watchdog, "system_briefing"),
                patch.object(position_watchdog, "audit"),
            ):
                position_watchdog.poll_position_watchdog()
                return sell.call_count

        assert _poll() == 1  # first trim
        assert _poll() == 0  # idempotent — marker blocks the repeat


# --------------------------------------------------------------------------- #
# holding_days + rsi data sources (M1c wiring) — helpers
# --------------------------------------------------------------------------- #
class _OrdersCursor:
    """매수 체결 행(최신순)을 돌려주는 커서 더블.

    2026-08-15 변경: _holding_days 가 MIN(첫 매수) 한 값을 받는 대신, 매수
    행 목록을 최신순으로 훑어 held_qty 를 채우는 지점을 찾는다.
    맵 값은 [(진입시각, 수량), ...] 를 최신순으로 준다.
    """

    def __init__(self, buys_by_ticker: dict[str, Any]) -> None:
        self._map = buys_by_ticker
        self._result: Any = []

    def execute(self, sql: str, params: Any = None) -> None:
        ticker = params[0] if params else None
        rows = self._map.get(ticker) or []
        self._result = [{"t": t, "q": q} for t, q in rows]

    def fetchall(self) -> Any:
        return self._result

    def fetchone(self) -> Any:
        return self._result[0] if self._result else None

    def __enter__(self) -> _OrdersCursor:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class _OrdersConn:
    def __init__(self, buys_by_ticker: dict[str, Any]) -> None:
        self._map = buys_by_ticker

    def cursor(self) -> _OrdersCursor:
        return _OrdersCursor(self._map)

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None

    def __enter__(self) -> _OrdersConn:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class TestHoldingDaysHelper:
    @staticmethod
    @contextmanager
    def _conn(buys):
        yield _OrdersConn(buys)

    def _days(self, buys, ticker, held_qty, today=date(2026, 6, 3)):
        from trading.watchers import position_watchdog

        with (
            patch.object(position_watchdog, "connection",
                         side_effect=lambda *a, **k: self._conn(buys)),
            patch.object(position_watchdog, "_today_kst", return_value=today),
        ):
            return position_watchdog._holding_days(ticker, held_qty=held_qty)

    def test_days_since_current_lot_entry(self):
        """보유 1주는 가장 최근 매수 1건이 채운다 — 그 진입일 기준."""
        from datetime import datetime

        buys = {"064350": [(datetime(2026, 5, 24), 1), (datetime(2026, 5, 4), 1)]}
        assert self._days(buys, "064350", 1) == 10  # 5/24 진입

    def test_older_lot_included_when_held_qty_spans_multiple_buys(self):
        """보유 2주면 매수 2건이 필요 — 그중 오래된 진입일이 기준."""
        from datetime import datetime

        buys = {"064350": [(datetime(2026, 5, 24), 1), (datetime(2026, 5, 4), 1)]}
        assert self._days(buys, "064350", 2) == 30  # 5/4 까지 거슬러감

    def test_reentry_after_full_exit_resets_holding_days(self):
        """2026-08-14 재현: 완전 청산 후 재매수하면 보유일이 리셋된다.

        055550 을 5/26 부터 여러 번 사고팔았지만 8/14 재매수분만 보유 중이었다.
        종전 구현은 MIN(첫 매수)=5/26 → 80일로 계산해 STAGNATION_DAYS(20)를
        넘겨 매수 3분 뒤 rotate 매도했다. 현재 보유수량(1주)을 채우는 매수는
        가장 최근 1건뿐이므로 보유일은 0 이어야 한다.
        """
        from datetime import datetime

        buys = {"055550": [
            (datetime(2026, 6, 3), 1),   # 재매수 (오늘)
            (datetime(2026, 5, 26), 3),  # 과거 매수 — 이미 청산됨
        ]}
        assert self._days(buys, "055550", 1) == 0

    def test_no_rows_returns_none(self):
        """매수 기록이 없으면 None (정체 판정 포기)."""
        assert self._days({}, "XXXXX", 1) is None

    def test_held_qty_zero_or_none_returns_none(self):
        """미보유는 정체 판정 대상이 아니다."""
        from datetime import datetime

        buys = {"064350": [(datetime(2026, 5, 4), 5)]}
        assert self._days(buys, "064350", 0) is None
        assert self._days(buys, "064350", None) is None

    def test_ledger_short_of_held_qty_returns_none(self):
        """매수 기록이 보유수량을 설명 못 하면 날짜를 추정하지 않는다."""
        from datetime import datetime

        buys = {"064350": [(datetime(2026, 5, 24), 1)]}
        assert self._days(buys, "064350", 5) is None

    def test_date_typed_entry(self):
        """date(datetime 아님) 값도 처리한다."""
        buys = {"064350": [(date(2026, 5, 24), 1)]}
        assert self._days(buys, "064350", 1) == 10


class TestTickerRsiHelper:
    def test_rsi_reuses_compute_rsi(self):
        """REQ-040-1c wiring: _ticker_rsi reuses the shared compute_rsi."""
        from trading.watchers import position_watchdog

        with patch.object(position_watchdog, "compute_rsi", return_value=52.0):
            assert position_watchdog._ticker_rsi("064350") == 52.0

    def test_rsi_unavailable_returns_none(self):
        """compute_rsi None (insufficient data) → None (defensive skip)."""
        from trading.watchers import position_watchdog

        with patch.object(position_watchdog, "compute_rsi", return_value=None):
            assert position_watchdog._ticker_rsi("064350") is None

    def test_rsi_error_returns_none(self):
        """A compute_rsi exception is absorbed → None (never aborts the sweep)."""
        from trading.watchers import position_watchdog

        with patch.object(position_watchdog, "compute_rsi", side_effect=RuntimeError("boom")):
            assert position_watchdog._ticker_rsi("064350") is None


# --------------------------------------------------------------------------- #
# poll_position_watchdog — stagnation rotation trim integration (M1c)
# --------------------------------------------------------------------------- #
class TestStagnationTrimIntegration:
    def _run(
        self,
        holdings,
        *,
        holding_days,
        rsi,
        total_value=10_000_000,
        late_cycle=False,
        today=date(2026, 6, 3),
    ):
        """Run a poll with stagnation seams patched; concentration left inert
        (huge portfolio so no holding is over the 25% cap)."""
        from trading.watchers import position_watchdog

        with (
            patch.object(position_watchdog, "_today_kst", return_value=today),
            patch.object(position_watchdog, "_build_client", return_value=object()),
            patch.object(position_watchdog, "_read_holdings", return_value=holdings),
            patch.object(position_watchdog, "_portfolio_value", return_value=total_value),
            patch.object(position_watchdog, "_late_cycle_active", return_value=late_cycle),
            patch.object(
                position_watchdog,
                "get_dynamic_thresholds",
                return_value={"effective_stop": -8.5, "effective_take": 12.0},
            ),
            patch.object(position_watchdog, "_holding_days", return_value=holding_days),
            patch.object(position_watchdog, "_ticker_rsi", return_value=rsi),
            patch.object(
                position_watchdog,
                "_confirm_qty",
                side_effect=lambda _c, t: next(
                    (h["qty"] for h in holdings if h["ticker"] == t), 0
                ),
            ),
            patch.object(position_watchdog, "kis_sell") as sell,
            patch.object(position_watchdog, "system_briefing"),
            patch.object(position_watchdog, "audit") as audit,
        ):
            metrics = position_watchdog.poll_position_watchdog()
        return metrics, sell, audit

    def test_stagnant_holding_rotated_reproduction(self):
        """REQ-040-1c reproduction: a long-held flat-pnl neutral-RSI holding
        (the current 064350-like shape) is partial-trimmed (rotated).

        BEFORE this wiring holding_days/rsi were never fed → is_stagnant always
        False → no rotation. This proves the wiring now fires the first exit.
        """
        holdings = [_holding("064350", qty=10, pnl_pct=-2.37, eval_amount=500_000)]
        metrics, sell, audit = self._run(holdings, holding_days=30, rsi=50.0)

        assert sell.call_count == 1
        _, kwargs = sell.call_args
        assert kwargs["ticker"] == "064350"
        assert 1 <= kwargs["qty"] < 10  # partial rotation, not full
        assert metrics["rotate_exits"] == 1
        kinds = [a.kwargs["details"].get("kind") for a in audit.call_args_list]
        assert "rotate" in kinds

    def test_non_stagnant_rsi_high_no_rotation(self):
        """RSI 70 (outside neutral band) → no rotation (extreme rules govern)."""
        holdings = [_holding("064350", qty=10, pnl_pct=-2.37, eval_amount=500_000)]
        metrics, sell, _ = self._run(holdings, holding_days=30, rsi=70.0)
        assert sell.call_count == 0
        assert metrics["rotate_exits"] == 0

    def test_non_stagnant_big_pnl_no_rotation(self):
        """|pnl| 5% (> band) → not stagnant → no rotation."""
        holdings = [_holding("064350", qty=10, pnl_pct=5.0, eval_amount=500_000)]
        metrics, sell, _ = self._run(holdings, holding_days=30, rsi=50.0)
        assert sell.call_count == 0
        assert metrics["rotate_exits"] == 0

    def test_short_hold_no_rotation(self):
        """Held < STAGNATION_DAYS → not stagnant → no rotation."""
        holdings = [_holding("064350", qty=10, pnl_pct=-1.0, eval_amount=500_000)]
        metrics, sell, _ = self._run(holdings, holding_days=3, rsi=50.0)
        assert sell.call_count == 0
        assert metrics["rotate_exits"] == 0

    def test_rsi_absent_skips_rotation(self):
        """RSI unavailable (None) → defensive skip (is_stagnant False)."""
        holdings = [_holding("064350", qty=10, pnl_pct=-2.0, eval_amount=500_000)]
        metrics, sell, _ = self._run(holdings, holding_days=30, rsi=None)
        assert sell.call_count == 0
        assert metrics["rotate_exits"] == 0


class TestTrimSingleFireBothEligible:
    """When BOTH concentration and stagnation apply, only ONE trim fires
    (shared action='trim' marker → no double-sell)."""

    def test_both_eligible_single_trim(self):
        from trading.watchers import position_watchdog

        store: set[tuple[date, str, str]] = set()

        @contextmanager
        def _factory(*_a: Any, **_k: Any):
            yield _MarkerConn(store)

        # Over the 25% cap (400k of 1M) AND long-held flat neutral-RSI.
        holdings = [_holding("086790", qty=40, pnl_pct=1.0, eval_amount=400_000)]

        with (
            patch.object(position_watchdog, "connection", side_effect=_factory),
            patch.object(position_watchdog, "_today_kst", return_value=date(2026, 6, 3)),
            patch.object(position_watchdog, "_build_client", return_value=object()),
            patch.object(position_watchdog, "_read_holdings", return_value=holdings),
            patch.object(position_watchdog, "_portfolio_value", return_value=1_000_000),
            patch.object(position_watchdog, "_late_cycle_active", return_value=False),
            patch.object(
                position_watchdog,
                "get_dynamic_thresholds",
                return_value={"effective_stop": -8.5, "effective_take": 12.0},
            ),
            patch.object(position_watchdog, "_holding_days", return_value=30),
            patch.object(position_watchdog, "_ticker_rsi", return_value=50.0),
            patch.object(position_watchdog, "_confirm_qty", return_value=40),
            patch.object(position_watchdog, "kis_sell") as sell,
            patch.object(position_watchdog, "system_briefing"),
            patch.object(position_watchdog, "audit"),
        ):
            metrics = position_watchdog.poll_position_watchdog()

        # exactly one trim (concentration wins, evaluated first), not two sells
        assert sell.call_count == 1
        assert metrics["trim_exits"] + metrics["rotate_exits"] == 1
