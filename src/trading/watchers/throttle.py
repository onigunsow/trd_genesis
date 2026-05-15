"""SPEC-TRADING-024 Stage 1 — per-ticker token-bucket throttle.

Lightweight in-memory throttle shared across watcher pollers. Two limits:

1. Per-ticker cooldown (default 300s): same ticker may not fire more than
   once within the cooldown window.
2. Daily cap (default 20): total ticker-trigger firings per KST trading day.

The day boundary is calendar-day KST (00:00 KST), matching SPEC-024 Q-3 /
acceptance scenario AC-024-2 (same-day cap reset).

State is purely in-process — sufficient for Stage 1 single-process scheduler.
If we ever move to multi-process, swap for Redis. (YAGNI per plan.md
ADR-024-2.)

@MX:SPEC: SPEC-TRADING-024
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


# @MX:ANCHOR: SPEC-TRADING-024 shared throttle for all Stage 1 watchers
# @MX:REASON: fan_in >= 3 (price_threshold, volume_anomaly, blocked_release
#             pollers + future Stage 2 multi-tier dispatcher)
# @MX:SPEC: SPEC-TRADING-024
class TickerThrottle:
    """Token-bucket throttle keyed by ticker, with a daily total cap.

    Args:
        min_interval_sec: Minimum seconds between firings for the same ticker.
        daily_cap: Maximum total firings per KST calendar day.
        now_provider: Test seam — returns the current time (KST-aware datetime).
    """

    def __init__(
        self,
        min_interval_sec: int = 300,
        daily_cap: int = 20,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.min_interval_sec = int(min_interval_sec)
        self.daily_cap = int(daily_cap)
        self._now: Callable[[], datetime] = now_provider or (lambda: datetime.now(KST))
        self._last_fired_at: dict[str, datetime] = {}
        self._daily_count: int = 0
        self._daily_anchor: date = self._now().astimezone(KST).date()

    def _maybe_roll_day(self) -> None:
        today = self._now().astimezone(KST).date()
        if today != self._daily_anchor:
            self._daily_anchor = today
            self._daily_count = 0
            # Last-fired memory persists; the cooldown window is far shorter
            # than a day so stale entries are harmless.

    def daily_count(self) -> int:
        """Current day's firing count (rolls automatically at midnight KST)."""
        self._maybe_roll_day()
        return self._daily_count

    def can_fire(self, ticker: str) -> bool:
        """True if `ticker` is allowed to fire under both throttle rules."""
        self._maybe_roll_day()
        if self._daily_count >= self.daily_cap:
            return False
        last = self._last_fired_at.get(ticker)
        if last is None:
            return True
        elapsed = (self._now() - last).total_seconds()
        return elapsed >= self.min_interval_sec

    def record(self, ticker: str) -> None:
        """Record a firing for `ticker` (caller-side; gate on can_fire first)."""
        self._maybe_roll_day()
        self._last_fired_at[ticker] = self._now()
        self._daily_count += 1
