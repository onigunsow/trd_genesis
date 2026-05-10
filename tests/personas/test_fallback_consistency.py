"""Tests for SPEC-TRADING-016 REQ-016-1-3 / REQ-016-1-4.

REQ-016-1-3: block_if_cli_only_mode decorator prevents direct API calls
when system_state.cli_only_mode is True.

REQ-016-1-4: fallback_to_haiku rejects non-Haiku models with ValueError,
log message uses the actual model name (consistency between log and API call).
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# REQ-016-1-4 — fallback model whitelist (cli_bridge.assert_fallback_model)
# ---------------------------------------------------------------------------

class TestFallbackModelWhitelist:
    """Verify fallback path only accepts allowed Haiku models."""

    def test_allowed_haiku_model_passes(self):
        """Valid Haiku model name does not raise."""
        from trading.personas.cli_bridge import assert_fallback_model
        # Should not raise
        assert_fallback_model("claude-haiku-4-5")

    def test_allowed_haiku_dated_variant_passes(self):
        """Dated Haiku variant is also allowed."""
        from trading.personas.cli_bridge import assert_fallback_model
        assert_fallback_model("claude-haiku-4-5-20251001")

    def test_rejects_sonnet_model(self):
        """Sonnet model is rejected with informative ValueError."""
        from trading.personas.cli_bridge import assert_fallback_model
        with pytest.raises(ValueError, match="non-Haiku|Allowed"):
            assert_fallback_model("claude-sonnet-4-6")

    def test_rejects_opus_model(self):
        """Opus model is rejected."""
        from trading.personas.cli_bridge import assert_fallback_model
        with pytest.raises(ValueError):
            assert_fallback_model("claude-opus-4-7")

    def test_rejects_arbitrary_string(self):
        """Arbitrary garbage model name is rejected."""
        from trading.personas.cli_bridge import assert_fallback_model
        with pytest.raises(ValueError):
            assert_fallback_model("not-a-real-model")


class TestFallbackLogConsistency:
    """REQ-016-1-4(c): Log message must reference the actual model used."""

    def test_log_message_uses_actual_model_name(self, caplog):
        """When fallback runs, the WARNING log includes the resolved model name.

        We exercise call_persona_via_cli with a forced CLI failure path and
        capture the log output. The fallback branch should log the actual model
        being called (Haiku), not a hardcoded string that could drift.
        """
        from trading.personas import base as base_module

        # Force CLI to fail so the fallback fires.
        def _raise_cli_error(**_kwargs):
            from trading.personas.cli_bridge import CLICallError
            raise CLICallError("simulated CLI failure for log-consistency test")

        # Capture call_persona invocation to short-circuit before DB.
        captured: dict = {}

        def _fake_call_persona(**kwargs):
            captured.update(kwargs)
            from trading.personas.base import PersonaResult
            return PersonaResult(
                persona_run_id=1,
                response_text="ok",
                response_json=None,
                input_tokens=0,
                output_tokens=0,
                cost_krw=0.0,
                latency_ms=1,
                tool_calls_count=0,
                tool_input_tokens=0,
                tool_output_tokens=0,
            )

        with patch.object(base_module, "call_persona_cli", side_effect=_raise_cli_error), \
             patch.object(base_module, "build_cli_prompt", return_value="prompt"), \
             patch.object(base_module, "call_persona", side_effect=_fake_call_persona), \
             patch.object(base_module, "_record_cli_failure", lambda *a, **kw: None):
            caplog.set_level(logging.WARNING, logger="trading.personas.base")
            base_module.call_persona_via_cli(
                persona_name="test_persona",
                model="claude-sonnet-4-6",  # original model — should NOT appear in fallback log as the API call model
                cycle_kind="pre_market",
                system_prompt="sys",
                user_message="msg",
            )

        # The fallback was actually called with the Haiku model
        assert captured.get("model") == "claude-haiku-4-5", \
            f"Fallback must invoke Haiku, got {captured.get('model')!r}"

        # The log message must mention the actual fallback model (Haiku),
        # so operators don't see a misleading message.
        joined_logs = "\n".join(r.getMessage() for r in caplog.records)
        assert "claude-haiku-4-5" in joined_logs, (
            "Fallback log message must include the actual model name "
            f"(claude-haiku-4-5), got: {joined_logs}"
        )


# ---------------------------------------------------------------------------
# REQ-016-1-3 — block_if_cli_only_mode decorator
# ---------------------------------------------------------------------------

class TestBlockIfCliOnlyMode:
    """Decorator must raise when cli_only_mode is True, pass otherwise."""

    def test_raises_when_cli_only_mode_is_true(self):
        """Decorated function raises RuntimeError under cli_only_mode."""
        from trading.personas.base import block_if_cli_only_mode

        @block_if_cli_only_mode
        def fake_direct_api_caller():
            return "called API"

        with patch(
            "trading.personas.base.get_system_state",
            return_value={"cli_personas_enabled": True, "cli_only_mode": True},
        ):
            with pytest.raises(RuntimeError, match="cli_only_mode"):
                fake_direct_api_caller()

    def test_allows_when_cli_only_mode_is_false(self):
        """Decorated function passes through when cli_only_mode is False."""
        from trading.personas.base import block_if_cli_only_mode

        @block_if_cli_only_mode
        def fake_direct_api_caller():
            return "called API"

        with patch(
            "trading.personas.base.get_system_state",
            return_value={"cli_personas_enabled": False, "cli_only_mode": False},
        ):
            assert fake_direct_api_caller() == "called API"

    def test_alias_cli_personas_enabled_when_only_legacy_key_set(self):
        """If only legacy column name is present, decorator still blocks."""
        from trading.personas.base import block_if_cli_only_mode

        @block_if_cli_only_mode
        def fake_direct_api_caller():
            return "called API"

        # Legacy column name only (no cli_only_mode key in dict)
        with patch(
            "trading.personas.base.get_system_state",
            return_value={"cli_personas_enabled": True},
        ):
            with pytest.raises(RuntimeError):
                fake_direct_api_caller()

    def test_does_not_block_when_get_system_state_raises(self):
        """Decorator is fail-open: if DB read fails, function still runs.

        This avoids a DB outage taking down direct-API code paths that
        might be the only working path during recovery.
        """
        from trading.personas.base import block_if_cli_only_mode

        @block_if_cli_only_mode
        def fake_direct_api_caller():
            return "called API"

        with patch(
            "trading.personas.base.get_system_state",
            side_effect=RuntimeError("DB down"),
        ):
            # Fail-open: should not raise, should call through
            assert fake_direct_api_caller() == "called API"


# ---------------------------------------------------------------------------
# Internal-consistency invariant: the constant used by base.py MUST appear
# in the whitelist defined by cli_bridge.py. Catches accidental drift if a
# future change updates one without the other.
# ---------------------------------------------------------------------------

class TestFallbackConstantConsistency:
    """The fallback model used at runtime must be in the whitelist."""

    def test_haiku_fallback_constant_is_whitelisted(self):
        from trading.personas.base import _HAIKU_FALLBACK_MODEL
        from trading.personas.cli_bridge import ALLOWED_FALLBACK_MODELS

        assert _HAIKU_FALLBACK_MODEL in ALLOWED_FALLBACK_MODELS, (
            f"_HAIKU_FALLBACK_MODEL={_HAIKU_FALLBACK_MODEL!r} is not in the "
            f"whitelist; the fallback would always raise ValueError. "
            f"Allowed: {sorted(ALLOWED_FALLBACK_MODELS)}"
        )
