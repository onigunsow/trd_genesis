"""SPEC-TRADING-033 — auto stop-loss / take-profit position watchdog tests.

Covers acceptance.md AC-1..AC-11. All KIS / Telegram / audit I/O is mocked;
no network, no DB.

@MX:SPEC: SPEC-TRADING-033
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest


def _holding(ticker: str, qty: int, pnl_pct: float) -> dict:
    """Mimic one item of balance()['holdings']."""
    return {
        "ticker": ticker,
        "name": ticker,
        "qty": qty,
        "avg_cost": 1000,
        "current_price": 1000,
        "eval_amount": qty * 1000,
        "pnl_amount": 0,
        "pnl_pct": pnl_pct,
    }


def _thresholds(eff_stop: float | None, eff_take: float | None, source: str = "dynamic") -> dict:
    """Mimic get_dynamic_thresholds() return shape (subset)."""
    return {
        "ticker": "X",
        "effective_stop": eff_stop,
        "effective_take": eff_take,
        "source": source,
    }


@pytest.fixture(autouse=True)
def _reset_take_profit_marker():
    """Reset the in-memory per-ticker take-profit guard between tests."""
    from trading.watchers import position_watchdog

    position_watchdog._reset_took_profit()
    yield
    position_watchdog._reset_took_profit()


# --------------------------------------------------------------------------- #
# classify_holding — pure decision helper (all branches)
# --------------------------------------------------------------------------- #
class TestClassifyHolding:
    def test_below_stop_returns_stop_full_qty(self):
        from trading.watchers.position_watchdog import classify_holding

        action, qty = classify_holding(
            pnl_pct=-10.0, eff_stop=-8.5, eff_take=12.0, took_profit_today=False, qty=7
        )
        assert action == "stop"
        assert qty == 7  # full

    def test_at_or_above_take_not_took_returns_take_half_qty(self):
        from trading.watchers.position_watchdog import classify_holding

        action, qty = classify_holding(
            pnl_pct=14.0, eff_stop=-8.5, eff_take=12.0, took_profit_today=False, qty=6
        )
        assert action == "take"
        assert qty == 3  # max(1, 6 // 2)

    def test_at_or_above_take_already_took_today_returns_skip(self):
        from trading.watchers.position_watchdog import classify_holding

        action, qty = classify_holding(
            pnl_pct=13.0, eff_stop=-8.5, eff_take=12.0, took_profit_today=True, qty=6
        )
        assert action == "skip"
        assert qty == 0

    def test_within_band_returns_skip(self):
        from trading.watchers.position_watchdog import classify_holding

        action, qty = classify_holding(
            pnl_pct=2.0, eff_stop=-8.5, eff_take=12.0, took_profit_today=False, qty=6
        )
        assert action == "skip"
        assert qty == 0

    def test_qty_one_take_sells_one_full_exit_edge(self):
        from trading.watchers.position_watchdog import classify_holding

        action, qty = classify_holding(
            pnl_pct=14.0, eff_stop=-8.5, eff_take=12.0, took_profit_today=False, qty=1
        )
        assert action == "take"
        assert qty == 1  # max(1, 1 // 2) == 1 → full exit edge

    def test_stop_evaluated_before_take(self):
        """A holding can't satisfy both, but stop is checked first defensively."""
        from trading.watchers.position_watchdog import classify_holding

        # Pathological inputs (stop >= take) — stop must win since it is first.
        action, _qty = classify_holding(
            pnl_pct=-20.0, eff_stop=-8.5, eff_take=12.0, took_profit_today=False, qty=4
        )
        assert action == "stop"

    def test_none_thresholds_returns_skip_no_crash(self):
        from trading.watchers.position_watchdog import classify_holding

        action, qty = classify_holding(
            pnl_pct=-10.0, eff_stop=None, eff_take=None, took_profit_today=False, qty=5
        )
        assert action == "skip"
        assert qty == 0


# --------------------------------------------------------------------------- #
# poll_position_watchdog — orchestration
# --------------------------------------------------------------------------- #
class TestPollPositionWatchdog:
    def test_stop_full_qty_sell_briefing_audit(self):
        """AC-1: pnl <= stop → full-qty kis_sell + '자동 손절' + audit."""
        from trading.watchers import position_watchdog

        holdings = [_holding("X", qty=7, pnl_pct=-10.0)]

        with (
            patch.object(position_watchdog, "_build_client", return_value=object()),
            patch.object(position_watchdog, "_read_holdings", return_value=holdings),
            patch.object(
                position_watchdog,
                "get_dynamic_thresholds",
                return_value=_thresholds(-8.5, 12.0),
            ),
            patch.object(position_watchdog, "_confirm_qty", return_value=7),
            patch.object(position_watchdog, "kis_sell") as sell,
            patch.object(position_watchdog, "system_briefing") as briefing,
            patch.object(position_watchdog, "audit") as audit,
        ):
            metrics = position_watchdog.poll_position_watchdog()

        assert sell.call_count == 1
        _, kwargs = sell.call_args
        assert kwargs["ticker"] == "X"
        assert kwargs["qty"] == 7

        assert briefing.call_count == 1
        cat, msg = briefing.call_args[0]
        assert cat == "자동 손절"
        assert "X" in msg

        assert audit.call_count == 1
        a_args, a_kwargs = audit.call_args
        assert a_args[0] == "POSITION_WATCHDOG_EXIT"
        assert a_kwargs["actor"] == "position_watchdog"
        details = a_kwargs["details"]
        assert details["kind"] == "stop"
        assert details["ticker"] == "X"
        assert details["pnl_pct"] == -10.0
        assert details["threshold"] == -8.5
        assert details["qty"] == 7

        assert metrics["stop_exits"] == 1
        assert metrics["checked"] == 1

    def test_take_half_qty_sell_briefing_audit_marked(self):
        """AC-2: pnl >= take → half-qty + '자동 익절' + audit + marked."""
        from trading.watchers import position_watchdog

        holdings = [_holding("Y", qty=6, pnl_pct=14.0)]

        with (
            patch.object(position_watchdog, "_build_client", return_value=object()),
            patch.object(position_watchdog, "_read_holdings", return_value=holdings),
            patch.object(
                position_watchdog,
                "get_dynamic_thresholds",
                return_value=_thresholds(-8.5, 12.0),
            ),
            patch.object(position_watchdog, "_confirm_qty", return_value=6),
            patch.object(position_watchdog, "kis_sell") as sell,
            patch.object(position_watchdog, "system_briefing") as briefing,
            patch.object(position_watchdog, "audit") as audit,
        ):
            metrics = position_watchdog.poll_position_watchdog()

        assert sell.call_count == 1
        _, kwargs = sell.call_args
        assert kwargs["ticker"] == "Y"
        assert kwargs["qty"] == 3  # max(1, 6 // 2)

        cat, _msg = briefing.call_args[0]
        assert cat == "자동 익절"

        details = audit.call_args[1]["details"]
        assert details["kind"] == "take"
        assert details["qty"] == 3
        assert details["threshold"] == 12.0

        assert metrics["take_exits"] == 1
        # marked as taken today
        assert position_watchdog._took_profit_today("Y") is True

    def test_same_day_second_poll_no_second_take(self):
        """AC-3: a ticker taken today is not re-sold on the same KST day."""
        from trading.watchers import position_watchdog

        holdings = [_holding("Y", qty=6, pnl_pct=14.0)]

        common = dict(
            holdings=holdings,
            thr=_thresholds(-8.5, 12.0),
        )

        def _run():
            with (
                patch.object(position_watchdog, "_build_client", return_value=object()),
                patch.object(position_watchdog, "_read_holdings", return_value=common["holdings"]),
                patch.object(
                    position_watchdog, "get_dynamic_thresholds", return_value=common["thr"]
                ),
                patch.object(position_watchdog, "_confirm_qty", return_value=6),
                patch.object(position_watchdog, "kis_sell") as sell,
                patch.object(position_watchdog, "system_briefing"),
                patch.object(position_watchdog, "audit"),
            ):
                position_watchdog.poll_position_watchdog()
                return sell.call_count

        # fix "today" so both polls share the same KST date
        with patch.object(position_watchdog, "_today_kst", return_value=date(2026, 5, 28)):
            first = _run()
            second = _run()

        assert first == 1
        assert second == 0  # guard blocks the repeat

    def test_new_day_resets_take_guard(self):
        """AC-3 (date boundary): guard resets on a new KST trading day."""
        from trading.watchers import position_watchdog

        holdings = [_holding("Y", qty=6, pnl_pct=14.0)]

        def _run(today):
            with (
                patch.object(position_watchdog, "_today_kst", return_value=today),
                patch.object(position_watchdog, "_build_client", return_value=object()),
                patch.object(position_watchdog, "_read_holdings", return_value=holdings),
                patch.object(
                    position_watchdog,
                    "get_dynamic_thresholds",
                    return_value=_thresholds(-8.5, 12.0),
                ),
                patch.object(position_watchdog, "_confirm_qty", return_value=6),
                patch.object(position_watchdog, "kis_sell") as sell,
                patch.object(position_watchdog, "system_briefing"),
                patch.object(position_watchdog, "audit"),
            ):
                position_watchdog.poll_position_watchdog()
                return sell.call_count

        assert _run(date(2026, 5, 28)) == 1
        assert _run(date(2026, 5, 28)) == 0  # same day blocked
        assert _run(date(2026, 5, 29)) == 1  # next day allowed again

    def test_within_band_no_sell(self):
        """AC-6: stop < pnl < take → no kis_sell, only skipped increments."""
        from trading.watchers import position_watchdog

        holdings = [_holding("Z", qty=5, pnl_pct=2.0)]

        with (
            patch.object(position_watchdog, "_build_client", return_value=object()),
            patch.object(position_watchdog, "_read_holdings", return_value=holdings),
            patch.object(
                position_watchdog, "get_dynamic_thresholds", return_value=_thresholds(-8.5, 12.0)
            ),
            patch.object(position_watchdog, "_confirm_qty", return_value=5),
            patch.object(position_watchdog, "kis_sell") as sell,
            patch.object(position_watchdog, "system_briefing") as briefing,
            patch.object(position_watchdog, "audit") as audit,
        ):
            metrics = position_watchdog.poll_position_watchdog()

        assert sell.call_count == 0
        assert briefing.call_count == 0
        assert audit.call_count == 0
        assert metrics["skipped"] == 1

    def test_per_ticker_error_isolated_others_processed(self):
        """AC-9: one ticker raising is isolated; others still processed."""
        from trading.watchers import position_watchdog

        holdings = [
            _holding("A", qty=4, pnl_pct=-10.0),  # stop
            _holding("B", qty=4, pnl_pct=5.0),  # raises in get_dynamic_thresholds
            _holding("C", qty=8, pnl_pct=14.0),  # take
        ]

        def _thr(ticker):
            if ticker == "B":
                raise RuntimeError("boom B")
            return _thresholds(-8.5, 12.0)

        with (
            patch.object(position_watchdog, "_build_client", return_value=object()),
            patch.object(position_watchdog, "_read_holdings", return_value=holdings),
            patch.object(position_watchdog, "get_dynamic_thresholds", side_effect=_thr),
            patch.object(
                position_watchdog,
                "_confirm_qty",
                side_effect=lambda _client, ticker: {"A": 4, "C": 8}.get(ticker, 0),
            ),
            patch.object(position_watchdog, "kis_sell") as sell,
            patch.object(position_watchdog, "system_briefing"),
            patch.object(position_watchdog, "audit"),
        ):
            metrics = position_watchdog.poll_position_watchdog()

        sold_tickers = {kw["ticker"] for _, kw in sell.call_args_list}
        assert sold_tickers == {"A", "C"}
        assert metrics["stop_exits"] == 1
        assert metrics["take_exits"] == 1
        assert metrics["errors"] == 1

    def test_telegram_failure_swallowed(self):
        """AC-9: a telegram send failure does not abort the exit/sweep."""
        from trading.watchers import position_watchdog

        holdings = [_holding("X", qty=7, pnl_pct=-10.0)]

        with (
            patch.object(position_watchdog, "_build_client", return_value=object()),
            patch.object(position_watchdog, "_read_holdings", return_value=holdings),
            patch.object(
                position_watchdog, "get_dynamic_thresholds", return_value=_thresholds(-8.5, 12.0)
            ),
            patch.object(position_watchdog, "_confirm_qty", return_value=7),
            patch.object(position_watchdog, "kis_sell") as sell,
            patch.object(position_watchdog, "system_briefing", side_effect=RuntimeError("tg down")),
            patch.object(position_watchdog, "audit") as audit,
        ):
            metrics = position_watchdog.poll_position_watchdog()

        # sell + audit still happen despite telegram failure
        assert sell.call_count == 1
        assert audit.call_count == 1
        assert metrics["stop_exits"] == 1
        assert metrics["errors"] == 0

    def test_atr_fallback_source_still_classified(self):
        """AC-7: source='fixed_fallback' (with thresholds) classifies, no crash."""
        from trading.watchers import position_watchdog

        holdings = [_holding("W", qty=3, pnl_pct=-9.0)]

        with (
            patch.object(position_watchdog, "_build_client", return_value=object()),
            patch.object(position_watchdog, "_read_holdings", return_value=holdings),
            patch.object(
                position_watchdog,
                "get_dynamic_thresholds",
                return_value=_thresholds(-7.0, 30.0, source="fixed_fallback"),
            ),
            patch.object(position_watchdog, "_confirm_qty", return_value=3),
            patch.object(position_watchdog, "kis_sell") as sell,
            patch.object(position_watchdog, "system_briefing"),
            patch.object(position_watchdog, "audit"),
        ):
            metrics = position_watchdog.poll_position_watchdog()

        assert sell.call_count == 1
        assert metrics["stop_exits"] == 1

    def test_fallback_none_thresholds_no_crash_skips(self):
        """AC-7 (real fallback): effective_stop/take None → skip, no crash."""
        from trading.watchers import position_watchdog

        holdings = [_holding("W", qty=3, pnl_pct=-9.0)]

        with (
            patch.object(position_watchdog, "_build_client", return_value=object()),
            patch.object(position_watchdog, "_read_holdings", return_value=holdings),
            patch.object(
                position_watchdog,
                "get_dynamic_thresholds",
                return_value=_thresholds(None, None, source="fixed_fallback"),
            ),
            patch.object(position_watchdog, "_confirm_qty", return_value=3),
            patch.object(position_watchdog, "kis_sell") as sell,
            patch.object(position_watchdog, "system_briefing"),
            patch.object(position_watchdog, "audit"),
        ):
            metrics = position_watchdog.poll_position_watchdog()

        assert sell.call_count == 0
        assert metrics["skipped"] == 1

    def test_qty_one_take_sells_one(self):
        """AC-8: qty==1 take → kis_sell qty=1 (full exit)."""
        from trading.watchers import position_watchdog

        holdings = [_holding("V", qty=1, pnl_pct=14.0)]

        with (
            patch.object(position_watchdog, "_build_client", return_value=object()),
            patch.object(position_watchdog, "_read_holdings", return_value=holdings),
            patch.object(
                position_watchdog, "get_dynamic_thresholds", return_value=_thresholds(-8.5, 12.0)
            ),
            patch.object(position_watchdog, "_confirm_qty", return_value=1),
            patch.object(position_watchdog, "kis_sell") as sell,
            patch.object(position_watchdog, "system_briefing"),
            patch.object(position_watchdog, "audit"),
        ):
            position_watchdog.poll_position_watchdog()

        _, kwargs = sell.call_args
        assert kwargs["qty"] == 1

    def test_reread_qty_zero_skips_double_sell(self):
        """AC-5/Q-4: fresh balance qty==0 → skip (double-sell guard)."""
        from trading.watchers import position_watchdog

        holdings = [_holding("X", qty=7, pnl_pct=-10.0)]

        with (
            patch.object(position_watchdog, "_build_client", return_value=object()),
            patch.object(position_watchdog, "_read_holdings", return_value=holdings),
            patch.object(
                position_watchdog, "get_dynamic_thresholds", return_value=_thresholds(-8.5, 12.0)
            ),
            patch.object(position_watchdog, "_confirm_qty", return_value=0),  # already sold
            patch.object(position_watchdog, "kis_sell") as sell,
            patch.object(position_watchdog, "system_briefing") as briefing,
            patch.object(position_watchdog, "audit") as audit,
        ):
            metrics = position_watchdog.poll_position_watchdog()

        assert sell.call_count == 0
        assert briefing.call_count == 0
        assert audit.call_count == 0
        assert metrics["skipped"] == 1

    def test_sell_uses_direct_kis_sell_market_order(self):
        """AC-4/AC-5: exit uses kis_sell directly (bypasses cycle halt/limit).

        The watchdog must NOT import or call the orchestrator cycle, its halt
        gate, or check_pre_order. We assert it calls kis_sell with a market
        order and persona_decision_id=None (a direct, gate-free exit path).
        """
        from trading.watchers import position_watchdog

        holdings = [_holding("X", qty=7, pnl_pct=-10.0)]

        with (
            patch.object(position_watchdog, "_build_client", return_value=object()),
            patch.object(position_watchdog, "_read_holdings", return_value=holdings),
            patch.object(
                position_watchdog, "get_dynamic_thresholds", return_value=_thresholds(-8.5, 12.0)
            ),
            patch.object(position_watchdog, "_confirm_qty", return_value=7),
            patch.object(position_watchdog, "kis_sell") as sell,
            patch.object(position_watchdog, "system_briefing"),
            patch.object(position_watchdog, "audit"),
        ):
            position_watchdog.poll_position_watchdog()

        _, kwargs = sell.call_args
        assert kwargs["order_type"] == "market"
        assert kwargs.get("persona_decision_id") is None

    def test_module_does_not_reference_cycle_gates(self):
        """AC-11 (static): watchdog does not import orchestrator/limits/check_pre_order."""
        import inspect

        from trading.watchers import position_watchdog

        src = inspect.getsource(position_watchdog)
        assert "check_pre_order" not in src
        assert "run_intraday_cycle" not in src
        assert "from trading.risk.limits" not in src
        assert "from trading.personas.orchestrator" not in src
