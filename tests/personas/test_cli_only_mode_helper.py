"""SPEC-TRADING-043 REQ-043-A5 — shared ``is_cli_only_mode()`` predicate.

The news-import fallback guard (REQ-043-A1) must reuse the SAME mode-detection
mechanism as ``block_if_cli_only_mode`` — no second source of truth. We extract a
``is_cli_only_mode() -> bool`` helper from the decorator's L100-115 logic,
preserving its fall-open behaviour on ``get_system_state()`` failure.
"""

from __future__ import annotations

from unittest.mock import patch


class TestIsCliOnlyMode:
    def test_true_when_cli_only_mode_set(self):
        from trading.personas.base import is_cli_only_mode

        with patch(
            "trading.personas.base.get_system_state",
            return_value={"cli_only_mode": True, "cli_personas_enabled": False},
        ):
            assert is_cli_only_mode() is True

    def test_true_when_only_legacy_key_set(self):
        """REQ-043-A5: legacy SPEC-015 column name is treated as equivalent."""
        from trading.personas.base import is_cli_only_mode

        with patch(
            "trading.personas.base.get_system_state",
            return_value={"cli_personas_enabled": True},
        ):
            assert is_cli_only_mode() is True

    def test_false_when_both_off(self):
        from trading.personas.base import is_cli_only_mode

        with patch(
            "trading.personas.base.get_system_state",
            return_value={"cli_only_mode": False, "cli_personas_enabled": False},
        ):
            assert is_cli_only_mode() is False

    def test_falls_open_to_false_when_get_system_state_raises(self):
        """REQ-043-A5: a DB outage must not wedge the only working path.

        For the decorator, fall-open means "run the wrapped direct-API fn".
        For this predicate the equivalent is to report NOT cli-only (False), so
        the news Haiku fallback can still proceed when the DB is down.
        """
        from trading.personas.base import is_cli_only_mode

        with patch(
            "trading.personas.base.get_system_state",
            side_effect=RuntimeError("DB down"),
        ):
            assert is_cli_only_mode() is False
