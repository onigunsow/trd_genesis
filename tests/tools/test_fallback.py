"""Tests for tools/fallback.py — consecutive failure detection."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from trading.tools.fallback import CONSECUTIVE_FAILURE_THRESHOLD, FallbackTracker


class TestFallbackTracker:
    """Verify per-invocation failure tracking and fallback logic."""

    def test_initial_state(self):
        """Fresh tracker has zero counts and no fallback."""
        tracker = FallbackTracker(persona_name="micro")
        assert tracker.consecutive_failures == 0
        assert tracker.total_failures == 0
        assert tracker.total_calls == 0
        assert tracker.fallback_triggered is False
        assert tracker.should_fallback() is False

    def test_single_failure_no_fallback(self):
        """One failure does not trigger fallback."""
        tracker = FallbackTracker()
        tracker.record(success=False)
        assert tracker.consecutive_failures == 1
        assert tracker.should_fallback() is False

    def test_two_failures_no_fallback(self):
        """Two consecutive failures still below threshold."""
        tracker = FallbackTracker()
        tracker.record(success=False)
        tracker.record(success=False)
        assert tracker.consecutive_failures == 2
        assert tracker.should_fallback() is False

    def test_three_consecutive_failures_triggers_fallback(self):
        """REQ-COMPAT-04-4: 3 consecutive failures triggers fallback."""
        tracker = FallbackTracker(persona_name="test", persona_run_id=42)
        with patch("trading.tools.fallback.audit"):
            tracker.record(success=False)
            tracker.record(success=False)
            tracker.record(success=False)
            assert tracker.should_fallback() is True

    def test_success_resets_consecutive_count(self):
        """A successful call between failures resets the counter."""
        tracker = FallbackTracker()
        tracker.record(success=False)
        tracker.record(success=False)
        tracker.record(success=True)  # Reset
        tracker.record(success=False)
        assert tracker.consecutive_failures == 1
        assert tracker.should_fallback() is False

    def test_non_consecutive_failures_no_fallback(self):
        """Failures separated by success never reach threshold."""
        tracker = FallbackTracker()
        for _ in range(10):
            tracker.record(success=False)
            tracker.record(success=False)
            tracker.record(success=True)  # Reset each time
        assert tracker.total_failures == 20
        assert tracker.should_fallback() is False

    def test_fallback_persists_once_triggered(self):
        """Once triggered, should_fallback() stays True."""
        tracker = FallbackTracker(persona_name="test")
        with patch("trading.tools.fallback.audit"):
            for _ in range(CONSECUTIVE_FAILURE_THRESHOLD):
                tracker.record(success=False)
            assert tracker.should_fallback() is True
            # Even after a success, fallback stays triggered for this invocation
            tracker.record(success=True)
            assert tracker.should_fallback() is True

    def test_audit_event_written_on_fallback(self):
        """TOOL_FALLBACK_TRIGGERED audit event is written."""
        tracker = FallbackTracker(persona_name="micro", persona_run_id=99)
        with patch("trading.tools.fallback.audit") as mock_audit:
            for _ in range(CONSECUTIVE_FAILURE_THRESHOLD):
                tracker.record(success=False)
            tracker.should_fallback()

        mock_audit.assert_called_once_with(
            "TOOL_FALLBACK_TRIGGERED",
            actor="tool_executor",
            details={
                "persona_name": "micro",
                "persona_run_id": 99,
                "consecutive_failures": CONSECUTIVE_FAILURE_THRESHOLD,
                "total_failures": CONSECUTIVE_FAILURE_THRESHOLD,
                "total_calls": CONSECUTIVE_FAILURE_THRESHOLD,
            },
        )

    def test_reset_clears_all_state(self):
        """reset() restores tracker to initial state."""
        tracker = FallbackTracker(persona_name="test")
        with patch("trading.tools.fallback.audit"):
            for _ in range(CONSECUTIVE_FAILURE_THRESHOLD):
                tracker.record(success=False)
            tracker.should_fallback()

        tracker.reset()
        assert tracker.consecutive_failures == 0
        assert tracker.total_failures == 0
        assert tracker.total_calls == 0
        assert tracker.fallback_triggered is False
        assert tracker.should_fallback() is False

    def test_total_calls_tracking(self):
        """total_calls counts all calls regardless of success."""
        tracker = FallbackTracker()
        tracker.record(success=True)
        tracker.record(success=False)
        tracker.record(success=True)
        assert tracker.total_calls == 3
        assert tracker.total_failures == 1

    def test_threshold_constant_is_three(self):
        """Verify the threshold constant matches SPEC requirement."""
        assert CONSECUTIVE_FAILURE_THRESHOLD == 3
