"""T-011 RED→GREEN — COOL_DOWN 리스크 상태 + auto_resume 제외.

SPEC-TRADING-048 REQ-048-M3-5, REQ-048-CORE-3.
AC: AC-M3-4(3회/-5% 발동·2회 미발동·수동 해제 전용·독립 레이어).
"""

from __future__ import annotations

import pytest


class TestCheckCoolDownTrigger:
    """check_cool_down_trigger 순수 함수 — AC-M3-4."""

    def test_two_violations_no_trigger(self) -> None:
        """2회 위반 → 발동 안 함 (임계 3회)."""
        from trading.risk.cool_down import check_cool_down_trigger
        triggered, _ = check_cool_down_trigger(2, -0.01)
        assert triggered is False

    def test_three_violations_triggers(self) -> None:
        """3회 위반 → 발동."""
        from trading.risk.cool_down import check_cool_down_trigger
        triggered, reason = check_cool_down_trigger(3, -0.01)
        assert triggered is True
        assert "위반" in reason or "violation" in reason.lower()

    def test_drawdown_below_threshold_triggers(self) -> None:
        """드로다운 -5% 이하 → 발동."""
        from trading.risk.cool_down import check_cool_down_trigger
        triggered, reason = check_cool_down_trigger(0, -0.05)
        assert triggered is True
        assert "드로다운" in reason or "drawdown" in reason.lower()

    def test_drawdown_above_threshold_no_trigger(self) -> None:
        """드로다운 -4.9% (임계 이상) → 발동 안 함."""
        from trading.risk.cool_down import check_cool_down_trigger
        triggered, _ = check_cool_down_trigger(0, -0.049)
        assert triggered is False

    def test_both_conditions_triggers(self) -> None:
        """위반 + 드로다운 동시 → 발동."""
        from trading.risk.cool_down import check_cool_down_trigger
        triggered, _ = check_cool_down_trigger(5, -0.10)
        assert triggered is True

    def test_custom_thresholds(self) -> None:
        """주입형 임계 — 2회(임계 2)에서 발동."""
        from trading.risk.cool_down import check_cool_down_trigger
        triggered, _ = check_cool_down_trigger(
            2, -0.01,
            violation_threshold=2,
            drawdown_threshold=-0.03,
        )
        assert triggered is True

    def test_reason_has_cool_down_prefix(self) -> None:
        """발동 사유에 cool_down 접두어 포함 (auto_resume 식별용)."""
        from trading.risk.cool_down import check_cool_down_trigger, COOL_DOWN_REASON_PREFIX
        _, reason = check_cool_down_trigger(3, 0.0)
        assert reason.startswith(COOL_DOWN_REASON_PREFIX)


class TestIsCoolDownHalt:
    """auto_resume 에서 COOL_DOWN 원인 판별."""

    def test_cool_down_prefix_detected(self) -> None:
        from trading.risk.cool_down import is_cool_down_halt
        assert is_cool_down_halt("cool_down: 규칙위반 3회") is True

    def test_non_cool_down_not_detected(self) -> None:
        from trading.risk.cool_down import is_cool_down_halt
        assert is_cool_down_halt("pre-order limit breach") is False
        assert is_cool_down_halt("manual /halt") is False
        assert is_cool_down_halt("daily_loss: -2.6%") is False


class TestAutoResumeExcludesCoolDown:
    """REQ-048-M3-5: auto_resume.classify_halt 이 COOL_DOWN 을 자동재개 제외 처리."""

    def test_cool_down_not_auto_resumed(self) -> None:
        from trading.risk.auto_resume import classify_halt

        active_trip = {"reason": "cool_down: 규칙위반 3회 누적"}
        should_resume, cause, _ = classify_halt(True, active_trip)
        assert should_resume is False
        assert cause == "cool_down"

    def test_normal_limit_breach_still_auto_resumed(self) -> None:
        """일반 limit breach 는 기존대로 자동재개 가능."""
        from trading.risk.auto_resume import classify_halt

        active_trip = {
            "reason": "pre-order limit breach",
            "breaches": ["daily_count: 10 orders"],
        }
        should_resume, cause, _ = classify_halt(True, active_trip)
        assert should_resume is True

    def test_daily_loss_not_auto_resumed(self) -> None:
        """daily_loss 는 기존대로 자동재개 불가."""
        from trading.risk.auto_resume import classify_halt

        active_trip = {
            "reason": "pre-order limit breach",
            "breaches": ["daily_loss: -2.6%"],
        }
        should_resume, cause, _ = classify_halt(True, active_trip)
        assert should_resume is False
        assert cause == "daily_loss"
