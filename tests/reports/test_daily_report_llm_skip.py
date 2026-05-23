"""SPEC-TRADING-026 (c) — accurate daily-report LLM-skip reason.

The daily report previously always appended "(Anthropic API 미구성 또는 호출
실패로 LLM 요약 생략)" whenever the LLM summary was unavailable. In cli_only_mode
the direct API call is blocked *by design*, so that message wrongly implied a
broken system. SPEC-026 surfaces the real reason.
"""

from __future__ import annotations

from trading.reports import daily_report as dr


def _min_data() -> dict:
    return {"today": "2026-05-22", "orders": [], "runs": [], "risk": []}


class TestLlmSkipReason:
    def test_cli_only_mode_is_normal(self):
        exc = RuntimeError(
            "cli_only_mode=True but _llm_text attempted a direct Anthropic API call."
        )
        reason = dr._llm_skip_reason(exc)
        assert "CLI 전용 모드" in reason
        assert "정상" in reason

    def test_missing_key(self):
        reason = dr._llm_skip_reason(RuntimeError("ANTHROPIC_API_KEY missing"))
        assert "ANTHROPIC_API_KEY" in reason
        assert "미설정" in reason

    def test_generic_failure(self):
        reason = dr._llm_skip_reason(RuntimeError("429 rate limit"))
        assert "실패" in reason
        assert "429" in reason


class TestFallbackUsesReason:
    def test_skip_reason_rendered_and_old_message_gone(self):
        reason = "CLI 전용 모드 — 직접 API 호출 차단(정상), 결정형 요약 사용"
        text = dr._fallback_text(_min_data(), skip_reason=reason)
        assert reason in text
        # The misleading legacy phrasing must no longer appear for cli-only.
        assert "미구성" not in text

    def test_default_when_no_reason(self):
        text = dr._fallback_text(_min_data())
        assert "LLM 요약 생략" in text
