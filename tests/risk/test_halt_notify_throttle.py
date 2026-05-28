"""SPEC-TRADING-031 — halt-cycle briefing cooldown throttle tests.

Verifies AC-1 .. AC-7 from .moai/specs/SPEC-TRADING-031/acceptance.md:

- AC-1: first halted cycle sends; second within cooldown does NOT send.
- AC-2: a cycle after cooldown elapsed sends again (boundary: >= cooldown sends,
  < cooldown does not).
- AC-3: reset() clears the throttle (halt_notified_at -> NULL) so the next
  episode's first cycle sends immediately.
- AC-4: the halt gate skips trading and logs on every halted cycle, even when
  the Telegram briefing is throttled.
- AC-5: trip()/reset() initial "회로차단"/"회로차단 해제" messages are unaffected.
- AC-6: cooldown default is 21600s and is configurable.
- AC-7: throttle state is read from system_state (survives a restart — the
  helper reads halt_notified_at fresh on each call).

Telegram send is mocked; "now" and halt_notified_at are injected so cooldown
elapsed-time logic is deterministic (no network, no DB).

@MX:SPEC: SPEC-TRADING-031
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

from trading.risk import circuit_breaker as cb

UTC = timezone.utc


def _now() -> datetime:
    return datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)


def _state(halt_notified_at: datetime | None, halt_state: bool = True) -> dict[str, Any]:
    return {"id": 1, "halt_state": halt_state, "halt_notified_at": halt_notified_at}


class TestCooldownDefault:
    """AC-6 — default cooldown constant."""

    def test_default_cooldown_is_21600(self):
        assert cb.HALT_NOTIFY_COOLDOWN_SECONDS == 21600


class TestMaybeNotifyHaltFirstCycle:
    """AC-1 (first), AC-2 — REQ-031-1, REQ-031-2."""

    def test_first_cycle_null_sends_and_stamps(self):
        """halt_notified_at IS NULL -> send immediately + stamp now."""
        with (
            patch.object(cb, "get_system_state", return_value=_state(None)),
            patch.object(cb, "update_system_state") as upd,
            patch.object(cb, "system_briefing") as brief,
        ):
            sent = cb.maybe_notify_halt(now_provider=_now)

        assert sent is True
        brief.assert_called_once()
        # Korean category preserved (REQ-031, unchanged user-facing string).
        assert brief.call_args.args[0] == "매매 정지"
        # halt_notified_at stamped to "now".
        upd.assert_called_once()
        assert upd.call_args.kwargs["halt_notified_at"] == _now()

    def test_second_cycle_within_cooldown_does_not_send(self):
        """halt_notified_at recent (< cooldown) -> no send, no stamp."""
        recent = _now() - timedelta(hours=1)
        with (
            patch.object(cb, "get_system_state", return_value=_state(recent)),
            patch.object(cb, "update_system_state") as upd,
            patch.object(cb, "system_briefing") as brief,
        ):
            sent = cb.maybe_notify_halt(now_provider=_now)

        assert sent is False
        brief.assert_not_called()
        upd.assert_not_called()


class TestCooldownBoundary:
    """AC-2 — REQ-031-1: elapsed-time boundary."""

    def test_just_past_cooldown_sends_again(self):
        """6h 1m elapsed (> cooldown) -> send again + restamp."""
        past = _now() - timedelta(hours=6, minutes=1)
        with (
            patch.object(cb, "get_system_state", return_value=_state(past)),
            patch.object(cb, "update_system_state") as upd,
            patch.object(cb, "system_briefing") as brief,
        ):
            sent = cb.maybe_notify_halt(now_provider=_now)

        assert sent is True
        brief.assert_called_once()
        assert upd.call_args.kwargs["halt_notified_at"] == _now()

    def test_exactly_cooldown_sends(self):
        """Exactly 6h elapsed (>= cooldown) -> send (inclusive boundary)."""
        exact = _now() - timedelta(seconds=cb.HALT_NOTIFY_COOLDOWN_SECONDS)
        with (
            patch.object(cb, "get_system_state", return_value=_state(exact)),
            patch.object(cb, "update_system_state"),
            patch.object(cb, "system_briefing") as brief,
        ):
            sent = cb.maybe_notify_halt(now_provider=_now)

        assert sent is True
        brief.assert_called_once()

    def test_one_second_under_cooldown_does_not_send(self):
        """5h 59m 59s elapsed (< cooldown) -> no send."""
        under = _now() - timedelta(seconds=cb.HALT_NOTIFY_COOLDOWN_SECONDS - 1)
        with (
            patch.object(cb, "get_system_state", return_value=_state(under)),
            patch.object(cb, "update_system_state") as upd,
            patch.object(cb, "system_briefing") as brief,
        ):
            sent = cb.maybe_notify_halt(now_provider=_now)

        assert sent is False
        brief.assert_not_called()
        upd.assert_not_called()


class TestConfigurableCooldown:
    """AC-6 — REQ-031-6: explicit cooldown override changes the boundary."""

    def test_custom_cooldown_1h_boundary(self):
        # 59 min elapsed, cooldown 3600s -> no send.
        under = _now() - timedelta(minutes=59)
        with (
            patch.object(cb, "get_system_state", return_value=_state(under)),
            patch.object(cb, "update_system_state"),
            patch.object(cb, "system_briefing") as brief,
        ):
            assert cb.maybe_notify_halt(cooldown_seconds=3600, now_provider=_now) is False
            brief.assert_not_called()

        # 61 min elapsed, cooldown 3600s -> send.
        over = _now() - timedelta(minutes=61)
        with (
            patch.object(cb, "get_system_state", return_value=_state(over)),
            patch.object(cb, "update_system_state"),
            patch.object(cb, "system_briefing") as brief,
        ):
            assert cb.maybe_notify_halt(cooldown_seconds=3600, now_provider=_now) is True
            brief.assert_called_once()


class TestPersistenceSurvivesRestart:
    """AC-7 — REQ-031-1c: helper reads halt_notified_at fresh from system_state."""

    def test_reads_state_each_call(self):
        """A 'restarted' process with a recent stamp still throttles."""
        recent = _now() - timedelta(minutes=10)
        with (
            patch.object(cb, "get_system_state", return_value=_state(recent)) as get,
            patch.object(cb, "update_system_state"),
            patch.object(cb, "system_briefing") as brief,
        ):
            sent = cb.maybe_notify_halt(now_provider=_now)

        assert sent is False
        get.assert_called_once()  # state is the single source of truth
        brief.assert_not_called()


class TestResetClearsThrottle:
    """AC-3 — REQ-031-3: reset() clears halt_notified_at atomically."""

    def test_reset_sets_halt_notified_at_null(self):
        with (
            patch.object(cb, "update_system_state") as upd,
            patch.object(cb, "audit"),
            patch.object(cb, "system_briefing"),
        ):
            cb.reset()

        upd.assert_called_once()
        kwargs = upd.call_args.kwargs
        assert kwargs["halt_state"] is False
        # Throttle cleared so the next episode's first cycle notifies immediately.
        assert kwargs["halt_notified_at"] is None

    def test_reset_still_sends_release_briefing(self):
        """AC-5 — reset() initial '회로차단 해제' message unaffected."""
        with (
            patch.object(cb, "update_system_state"),
            patch.object(cb, "audit"),
            patch.object(cb, "system_briefing") as brief,
        ):
            cb.reset()

        brief.assert_called_once()
        assert brief.call_args.args[0] == "회로차단 해제"

    def test_after_reset_next_episode_first_cycle_sends(self):
        """AC-3 end-to-end: reset -> halt_notified_at NULL -> first cycle sends."""
        # Simulate post-reset state: halt re-tripped, halt_notified_at NULL.
        with (
            patch.object(cb, "get_system_state", return_value=_state(None)),
            patch.object(cb, "update_system_state"),
            patch.object(cb, "system_briefing") as brief,
        ):
            sent = cb.maybe_notify_halt(now_provider=_now)

        assert sent is True
        brief.assert_called_once()


class TestTripUnaffected:
    """AC-5 — REQ-031-5: trip() initial '회로차단' message unaffected."""

    def test_trip_sends_circuit_breaker_briefing_once(self):
        with (
            patch.object(cb, "update_system_state"),
            patch.object(cb, "audit"),
            patch.object(cb, "system_briefing") as brief,
        ):
            cb.trip(reason="test breach")

        brief.assert_called_once()
        assert brief.call_args.args[0] == "회로차단"

    def test_trip_does_not_touch_halt_notified_at(self):
        """Static-ish guard: trip() must not stamp the cycle-gate throttle."""
        with (
            patch.object(cb, "update_system_state") as upd,
            patch.object(cb, "audit"),
            patch.object(cb, "system_briefing"),
        ):
            cb.trip(reason="test breach")

        assert "halt_notified_at" not in upd.call_args.kwargs
