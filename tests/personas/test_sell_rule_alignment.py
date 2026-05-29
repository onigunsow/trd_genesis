"""SPEC-TRADING-037 REQ-037-6 — decision persona sell-rule alignment.

The decision prompt's flat "-7% 도달 시 매도" rule was inconsistent with the
dynamic ``effective_stop`` already referenced lower in the prompt (the watchdog
and the persona could disagree on when to exit). This aligns the prompt's
손절 rule to the dynamic ``effective_stop`` (get_dynamic_thresholds), keeping the
flat -7% only as the fallback context. Prompt-only change: entry/buy logic
untouched (C-2).

These are grep/render assertions on the rendered template text.

@MX:SPEC: SPEC-TRADING-037
"""

from __future__ import annotations

from pathlib import Path

PROMPT = (
    Path(__file__).resolve().parents[2]
    / "src" / "trading" / "personas" / "prompts" / "decision.jinja"
).read_text(encoding="utf-8")


def _sell_rule_line() -> str:
    """The 손절 룰 bullet (the one historically anchored on a flat -7%)."""
    for line in PROMPT.splitlines():
        if line.lstrip().startswith("- **손절 룰**"):
            return line
    raise AssertionError("손절 룰 line not found in decision.jinja")


class TestSellRuleAlignedToDynamicStop:
    """REQ-037-6 (a) — primary stop rule references effective_stop, not flat -7%."""

    def test_sell_rule_references_effective_stop(self):
        line = _sell_rule_line()
        assert "effective_stop" in line, (
            "손절 룰 must reference the dynamic effective_stop (REQ-037-6 a); "
            f"got: {line}"
        )

    def test_sell_rule_not_anchored_on_static_minus_seven(self):
        line = _sell_rule_line()
        # The primary rule must not be a flat "-7% 도달 시 매도" any more.
        assert "-7% 도달 시 매도" not in line, (
            f"손절 룰 must no longer hard-code the flat -7% primary rule; got: {line}"
        )
        assert "-7% 도달시 매도" not in line, (
            f"손절 룰 must no longer hard-code the flat -7% primary rule; got: {line}"
        )


class TestFallbackContextRetained:
    """REQ-037-6 (a) — flat -7% / RSI>85 survives ONLY as fallback context."""

    def test_fallback_minus_seven_still_present(self):
        # The fallback guidance (lines ~170/174) must still mention -7% for the
        # source="fixed_fallback" case.
        assert "fixed_fallback" in PROMPT
        assert "-7%" in PROMPT  # retained as fallback, not as the primary rule


class TestEntryLogicUnchanged:
    """REQ-037-6 (b/c) — buy/entry-related prompt sections remain intact (C-2)."""

    def test_buy_signal_obligation_rules_present(self):
        # Sanity that we did not disturb the entry/매수 obligation sections.
        assert "수급 분석 의무 규칙" in PROMPT
        assert "매수 권장 수량은 자본의 10%를 절대 넘지 않는다" in PROMPT
        assert "공매도 금지" in PROMPT
