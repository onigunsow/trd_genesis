"""Characterization tests for personas/base.py — call_persona behavior."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import FakeAnthropicMessage, FakeCursor, FakeConnection


class TestCallPersona:
    """Characterize current behavior of call_persona()."""

    def _patch_all(self, response_text: str = '{"signals": []}', **msg_kwargs):
        """Return context managers patching Anthropic + DB for call_persona."""
        msg = FakeAnthropicMessage(text=response_text, **msg_kwargs)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = msg

        cursor = FakeCursor(rows=[{"id": 42}])
        conn = FakeConnection(cursor)

        patches = {
            "anthropic": patch("trading.personas.base.Anthropic", return_value=mock_client),
            "connection": patch("trading.personas.base.connection", return_value=conn),
            "settings": patch(
                "trading.personas.base.get_settings",
                return_value=MagicMock(
                    anthropic=MagicMock(api_key=MagicMock(get_secret_value=MagicMock(return_value="test")))
                ),
            ),
        }
        return patches, mock_client, conn

    def test_characterize_successful_call(self):
        """call_persona returns PersonaResult with parsed JSON on success."""
        patches, mock_client, conn = self._patch_all(
            response_text='{"signals": [{"ticker": "005930", "side": "buy", "qty": 5}]}'
        )
        with patches["anthropic"], patches["connection"], patches["settings"]:
            from trading.personas.base import call_persona

            result = call_persona(
                persona_name="decision",
                model="claude-sonnet-4-6",
                cycle_kind="pre_market",
                system_prompt="You are a test persona.",
                user_message="Analyze market.",
                expect_json=True,
                apply_memory_ops=False,
            )

        assert result.persona_run_id == 42
        assert result.response_json is not None
        assert result.response_json["signals"][0]["ticker"] == "005930"
        assert result.input_tokens == 1000
        assert result.output_tokens == 500
        assert result.cost_krw > 0

    def test_characterize_non_json_response(self):
        """call_persona with expect_json=True but non-JSON text gives None json."""
        patches, mock_client, conn = self._patch_all(
            response_text="I cannot provide analysis today."
        )
        with patches["anthropic"], patches["connection"], patches["settings"]:
            from trading.personas.base import call_persona

            result = call_persona(
                persona_name="micro",
                model="claude-sonnet-4-6",
                cycle_kind="pre_market",
                system_prompt="Test",
                user_message="Test",
                expect_json=True,
                apply_memory_ops=False,
            )

        assert result.response_json is None
        assert result.response_text == "I cannot provide analysis today."

    def test_characterize_api_error_raises(self):
        """call_persona raises RuntimeError when Anthropic API fails."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API timeout")

        cursor = FakeCursor(rows=[{"id": 99}])
        conn = FakeConnection(cursor)

        with (
            patch("trading.personas.base.Anthropic", return_value=mock_client),
            patch("trading.personas.base.connection", return_value=conn),
            patch(
                "trading.personas.base.get_settings",
                return_value=MagicMock(
                    anthropic=MagicMock(api_key=MagicMock(get_secret_value=MagicMock(return_value="test")))
                ),
            ),
            pytest.raises(RuntimeError, match="Exception: API timeout"),
        ):
            from trading.personas.base import call_persona

            call_persona(
                persona_name="risk",
                model="claude-sonnet-4-6",
                cycle_kind="pre_market",
                system_prompt="Test",
                user_message="Test",
                apply_memory_ops=False,
            )

    def test_characterize_cost_calculation(self):
        """Sonnet 4.6 cost: $3/M in + $15/M out at 1380 KRW/USD."""
        patches, _, _ = self._patch_all(
            response_text="{}",
            input_tokens=1000,
            output_tokens=500,
        )
        with patches["anthropic"], patches["connection"], patches["settings"]:
            from trading.personas.base import call_persona

            result = call_persona(
                persona_name="test",
                model="claude-sonnet-4-6",
                cycle_kind="manual",
                system_prompt="Test",
                user_message="Test",
                apply_memory_ops=False,
            )

        # Expected: (1000/1M * 3 + 500/1M * 15) * 1380
        expected = (1000 / 1_000_000 * 3.0 + 500 / 1_000_000 * 15.0) * 1380
        assert abs(result.cost_krw - expected) < 0.01


class TestExtractJson:
    """Characterize JSON extraction from free-form text."""

    def test_direct_json(self):
        from trading.personas.base import _extract_json
        result = _extract_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_in_code_fence(self):
        from trading.personas.base import _extract_json
        text = 'Here is my analysis:\n```json\n{"signals": []}\n```\nDone.'
        result = _extract_json(text)
        assert result == {"signals": []}

    def test_json_embedded_in_text(self):
        from trading.personas.base import _extract_json
        text = 'Analysis complete. {"verdict": "APPROVE", "rationale": "ok"} end.'
        result = _extract_json(text)
        assert result["verdict"] == "APPROVE"

    def test_no_json_raises(self):
        from trading.personas.base import _extract_json
        with pytest.raises(ValueError, match="no JSON"):
            _extract_json("plain text with no json")
