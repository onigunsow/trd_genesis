"""SPEC-TRADING-024 Stage 1 — TickerThrottle unit tests.

@MX:SPEC: SPEC-TRADING-024
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


class TestTickerThrottleCanFire:
    """REQ-024-2/3: per-ticker cooldown and daily cap throttling."""

    def test_can_fire_first_time_true(self):
        from trading.watchers.throttle import TickerThrottle

        throttle = TickerThrottle(min_interval_sec=300, daily_cap=20)
        assert throttle.can_fire("005930") is True

    def test_can_fire_within_cooldown_false(self):
        from trading.watchers.throttle import TickerThrottle

        now = datetime(2026, 5, 15, 10, 0, 0, tzinfo=KST)
        throttle = TickerThrottle(min_interval_sec=300, daily_cap=20, now_provider=lambda: now)
        throttle.record("005930")
        # Within 5-minute cooldown
        assert throttle.can_fire("005930") is False

    def test_can_fire_after_cooldown_true(self):
        from trading.watchers.throttle import TickerThrottle

        current = {"t": datetime(2026, 5, 15, 10, 0, 0, tzinfo=KST)}
        throttle = TickerThrottle(
            min_interval_sec=300, daily_cap=20, now_provider=lambda: current["t"]
        )
        throttle.record("005930")
        # Advance 6 minutes
        current["t"] = current["t"] + timedelta(minutes=6)
        assert throttle.can_fire("005930") is True

    def test_daily_cap_blocks_after_n_fires(self):
        from trading.watchers.throttle import TickerThrottle

        current = {"t": datetime(2026, 5, 15, 10, 0, 0, tzinfo=KST)}
        # daily_cap=3, no per-ticker cooldown blocker for distinct tickers
        throttle = TickerThrottle(
            min_interval_sec=1, daily_cap=3, now_provider=lambda: current["t"]
        )
        throttle.record("A")
        throttle.record("B")
        throttle.record("C")
        # 4th distinct ticker should be blocked by daily cap
        assert throttle.can_fire("D") is False
        assert throttle.daily_count() == 3

    def test_daily_counter_resets_next_day(self):
        from trading.watchers.throttle import TickerThrottle

        current = {"t": datetime(2026, 5, 15, 23, 0, 0, tzinfo=KST)}
        throttle = TickerThrottle(
            min_interval_sec=1, daily_cap=2, now_provider=lambda: current["t"]
        )
        throttle.record("A")
        throttle.record("B")
        assert throttle.can_fire("C") is False

        # Advance past midnight KST
        current["t"] = datetime(2026, 5, 16, 0, 0, 1, tzinfo=KST)
        # daily counter should reset
        assert throttle.daily_count() == 0
        assert throttle.can_fire("C") is True


class TestTickerThrottleRecord:
    """record() updates internal state."""

    def test_record_marks_ticker_fired(self):
        from trading.watchers.throttle import TickerThrottle

        throttle = TickerThrottle(min_interval_sec=300, daily_cap=20)
        assert throttle.can_fire("005930") is True
        throttle.record("005930")
        assert throttle.can_fire("005930") is False
