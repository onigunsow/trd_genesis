"""T-004 RED→GREEN — 검증 게이트 + M1-8 PASS 상태 read API.

SPEC-TRADING-048 REQ-048-M2-5, REQ-048-M1-8.
AC: AC-M2-2(PASS 미달 시 차단), AC-M1-7(M2 PASS 전 kelly_pct 강제 0).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def reset_gate_state():
    """각 테스트 후 게이트 상태 초기화."""
    from trading.edge.validation_gate import reset_gate
    reset_gate()
    yield
    reset_gate()


class TestValidationGateDefault:
    """기본 상태 = False (보수적 안전 기본값)."""

    def test_default_is_not_passed(self) -> None:
        from trading.edge.validation_gate import is_validation_passed
        assert is_validation_passed() is False

    def test_default_no_blocking_reasons(self) -> None:
        from trading.edge.validation_gate import get_blocking_reasons
        # 기본 상태에서는 빈 목록 (아직 채점 없음)
        reasons = get_blocking_reasons()
        assert isinstance(reasons, list)


class TestApplyScorecardPass:
    """PASS 판정 적용 시 게이트 열림."""

    def _make_pass_card(self):
        from trading.edge.evaluate_backtest import BacktestScoreCard, VERDICT_PASS
        return BacktestScoreCard(
            score=90.0,
            verdict=VERDICT_PASS,
            dimension_scores={
                "expectancy": 20.0,
                "profit_factor": 20.0,
                "sample_size": 20.0,
                "mdd_risk": 15.0,
                "robustness": 15.0,
            },
            warnings=[],
        )

    def test_pass_card_opens_gate(self) -> None:
        from trading.edge.validation_gate import apply_scorecard, is_validation_passed
        card = self._make_pass_card()
        result = apply_scorecard(card)
        assert result.allowed is True
        assert is_validation_passed() is True

    def test_pass_card_no_blocking_reasons(self) -> None:
        from trading.edge.validation_gate import apply_scorecard, get_blocking_reasons
        apply_scorecard(self._make_pass_card())
        assert get_blocking_reasons() == []


class TestApplyScorecardReject:
    """AC-M2-2: REJECT 시 차단 사유 반환, 게이트 닫힘."""

    def _make_reject_card(self):
        from trading.edge.evaluate_backtest import BacktestScoreCard, VERDICT_REJECT
        return BacktestScoreCard(
            score=10.0,
            verdict=VERDICT_REJECT,
            dimension_scores={
                "expectancy": 0.0,      # 0점 → 차단 사유
                "profit_factor": 0.0,   # 0점 → 차단 사유
                "sample_size": 0.0,     # 0점 (표본 30 미만)
                "mdd_risk": 5.0,
                "robustness": 5.0,
            },
            warnings=[],
        )

    def test_reject_card_closes_gate(self) -> None:
        from trading.edge.validation_gate import apply_scorecard, is_validation_passed
        result = apply_scorecard(self._make_reject_card())
        assert result.allowed is False
        assert is_validation_passed() is False

    def test_reject_card_has_blocking_reasons(self) -> None:
        from trading.edge.validation_gate import apply_scorecard, get_blocking_reasons
        apply_scorecard(self._make_reject_card())
        reasons = get_blocking_reasons()
        assert len(reasons) > 0

    def test_blocking_reasons_mention_zero_dims(self) -> None:
        from trading.edge.validation_gate import apply_scorecard, get_blocking_reasons
        apply_scorecard(self._make_reject_card())
        reasons = get_blocking_reasons()
        combined = " ".join(reasons)
        # 0점 차원이 사유에 포함되어야 함
        assert "0점" in combined or "=0" in combined


class TestApplyScorecardRevise:
    """REVISE 판정 = 차단 (PASS 아님)."""

    def _make_revise_card(self):
        from trading.edge.evaluate_backtest import BacktestScoreCard, VERDICT_REVISE
        return BacktestScoreCard(
            score=60.0,
            verdict=VERDICT_REVISE,
            dimension_scores={
                "expectancy": 10.0,
                "profit_factor": 10.0,
                "sample_size": 10.0,
                "mdd_risk": 15.0,
                "robustness": 15.0,
            },
            warnings=[],
        )

    def test_revise_card_does_not_open_gate(self) -> None:
        from trading.edge.validation_gate import apply_scorecard, is_validation_passed
        result = apply_scorecard(self._make_revise_card())
        assert result.allowed is False
        assert is_validation_passed() is False


class TestGateResetAfterPass:
    """PASS 후 reset 하면 다시 False."""

    def test_reset_after_pass(self) -> None:
        from trading.edge.validation_gate import (
            apply_scorecard, is_validation_passed, reset_gate
        )
        from trading.edge.evaluate_backtest import BacktestScoreCard, VERDICT_PASS
        card = BacktestScoreCard(
            score=90.0, verdict=VERDICT_PASS,
            dimension_scores={"expectancy": 20.0, "profit_factor": 20.0,
                              "sample_size": 20.0, "mdd_risk": 15.0, "robustness": 15.0},
        )
        apply_scorecard(card)
        assert is_validation_passed() is True
        reset_gate()
        assert is_validation_passed() is False


class TestGateResultContainsCard:
    """GateResult 에 채점 카드가 포함된다."""

    def test_gate_result_has_card(self) -> None:
        from trading.edge.validation_gate import apply_scorecard
        from trading.edge.evaluate_backtest import BacktestScoreCard, VERDICT_REJECT
        card = BacktestScoreCard(
            score=0.0, verdict=VERDICT_REJECT,
            dimension_scores={"expectancy": 0.0, "profit_factor": 0.0,
                              "sample_size": 0.0, "mdd_risk": 0.0, "robustness": 0.0},
        )
        result = apply_scorecard(card)
        assert result.card is card
