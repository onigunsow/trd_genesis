"""SPEC-TRADING-024 REQ-024-4 — Blocked-release watcher tests.

@MX:SPEC: SPEC-TRADING-024
"""

from __future__ import annotations

from unittest.mock import patch


class TestBlockedReleasePoll:
    """REQ-024-4: blocked (stat_cls=55) -> released (stat_cls != 55) fires event."""

    def test_blocked_to_released_fires_event(self):
        """Previously blocked ticker now stat_cls=00 fires an event."""
        from trading.watchers import blocked_release

        previous = {"005930": {"stat_cls": "55", "reason": "단기과열"}}
        # Current KIS quote — released
        current_quotes = {"005930": {"stat_cls": "00", "is_normal": True}}

        with (
            patch.object(blocked_release, "_load_previous_blocked", return_value=previous),
            patch.object(
                blocked_release,
                "_get_current_stat_cls",
                side_effect=lambda t: current_quotes.get(t),
            ),
            patch.object(blocked_release, "_get_universe", return_value=["005930"]),
            patch.object(blocked_release, "_persist_blocked_state"),
            patch.object(blocked_release, "_fire_trigger_event") as fire,
        ):
            metrics = blocked_release.poll_blocked_release()

        assert fire.call_count == 1
        args, _ = fire.call_args
        assert args[0] == "005930"
        assert args[1] == "blocked_release"
        metadata = args[2]
        assert metadata["previous_stat_cls"] == "55"
        assert metadata["current_stat_cls"] == "00"
        assert metrics["released"] == 1

    def test_released_to_released_no_event(self):
        """Ticker not previously blocked and currently released → no event."""
        from trading.watchers import blocked_release

        previous: dict[str, dict] = {}  # nothing previously blocked
        current_quotes = {"005930": {"stat_cls": "00", "is_normal": True}}

        with (
            patch.object(blocked_release, "_load_previous_blocked", return_value=previous),
            patch.object(
                blocked_release,
                "_get_current_stat_cls",
                side_effect=lambda t: current_quotes.get(t),
            ),
            patch.object(blocked_release, "_get_universe", return_value=["005930"]),
            patch.object(blocked_release, "_persist_blocked_state"),
            patch.object(blocked_release, "_fire_trigger_event") as fire,
        ):
            metrics = blocked_release.poll_blocked_release()

        assert fire.call_count == 0
        assert metrics["released"] == 0

    def test_blocked_to_blocked_no_event(self):
        """Ticker still blocked → no event."""
        from trading.watchers import blocked_release

        previous = {"005930": {"stat_cls": "55", "reason": "단기과열"}}
        current_quotes = {"005930": {"stat_cls": "55", "is_normal": False}}

        with (
            patch.object(blocked_release, "_load_previous_blocked", return_value=previous),
            patch.object(
                blocked_release,
                "_get_current_stat_cls",
                side_effect=lambda t: current_quotes.get(t),
            ),
            patch.object(blocked_release, "_get_universe", return_value=["005930"]),
            patch.object(blocked_release, "_persist_blocked_state"),
            patch.object(blocked_release, "_fire_trigger_event") as fire,
        ):
            metrics = blocked_release.poll_blocked_release()

        assert fire.call_count == 0
        assert metrics["released"] == 0
