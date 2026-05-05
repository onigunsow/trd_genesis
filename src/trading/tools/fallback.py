"""Tool Fallback — consecutive failure detection and bulk injection fallback.

REQ-COMPAT-04-4: After 3 consecutive tool failures within a single persona
invocation, trigger fallback to bulk injection.

Usage:
    tracker = FallbackTracker()
    # After each tool call:
    tracker.record(success=False)
    if tracker.should_fallback():
        # Switch to bulk injection for this invocation
"""

from __future__ import annotations

import logging
from typing import Any

from trading.db.session import audit

LOG = logging.getLogger(__name__)

# Number of consecutive failures before triggering fallback (REQ-COMPAT-04-4)
CONSECUTIVE_FAILURE_THRESHOLD: int = 3


class FallbackTracker:
    """Track consecutive tool failures within a single persona invocation.

    Per-invocation tracker: create a new instance for each persona call.
    Resets on any successful tool call (non-consecutive failures).
    """

    def __init__(self, persona_name: str = "", persona_run_id: int | None = None) -> None:
        self._consecutive_failures: int = 0
        self._total_failures: int = 0
        self._total_calls: int = 0
        self._fallback_triggered: bool = False
        self._persona_name = persona_name
        self._persona_run_id = persona_run_id

    @property
    def consecutive_failures(self) -> int:
        """Current count of consecutive failures."""
        return self._consecutive_failures

    @property
    def total_failures(self) -> int:
        """Total number of failures recorded."""
        return self._total_failures

    @property
    def total_calls(self) -> int:
        """Total tool calls tracked."""
        return self._total_calls

    @property
    def fallback_triggered(self) -> bool:
        """Whether fallback has been triggered."""
        return self._fallback_triggered

    def record(self, success: bool) -> None:
        """Record a tool call result.

        Args:
            success: True if tool call succeeded, False if it failed.
        """
        self._total_calls += 1
        if success:
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1
            self._total_failures += 1

    def should_fallback(self) -> bool:
        """Check if fallback should be triggered.

        Returns:
            True if 3+ consecutive failures have occurred and fallback
            has not already been triggered for this invocation.
        """
        if self._fallback_triggered:
            return True  # Already triggered, stay in fallback
        if self._consecutive_failures >= CONSECUTIVE_FAILURE_THRESHOLD:
            self._trigger_fallback()
            return True
        return False

    def _trigger_fallback(self) -> None:
        """Record fallback activation in audit log."""
        self._fallback_triggered = True
        LOG.warning(
            "Tool fallback triggered for persona=%s run_id=%s after %d consecutive failures",
            self._persona_name,
            self._persona_run_id,
            self._consecutive_failures,
        )
        try:
            audit(
                "TOOL_FALLBACK_TRIGGERED",
                actor="tool_executor",
                details={
                    "persona_name": self._persona_name,
                    "persona_run_id": self._persona_run_id,
                    "consecutive_failures": self._consecutive_failures,
                    "total_failures": self._total_failures,
                    "total_calls": self._total_calls,
                },
            )
        except Exception as e:  # noqa: BLE001
            LOG.warning("Failed to write TOOL_FALLBACK_TRIGGERED audit: %s", e)

    def reset(self) -> None:
        """Reset tracker state. Used when starting a fresh invocation."""
        self._consecutive_failures = 0
        self._total_failures = 0
        self._total_calls = 0
        self._fallback_triggered = False
