"""T-009 RED→GREEN — postmortem 분류 코어 순수 함수.

SPEC-TRADING-048 REQ-048-M3-1/2/3, REQ-048-CORE-1/2.
AC: AC-M3-1(4분류·귀인), AC-M3-2(20표본), AC-CORE-1/2.
"""

from __future__ import annotations

import pytest


def _decision(
    side: str = "buy",
    confidence: float = 0.5,
    persona: str = "decision",
    signal_dir: str | None = None,
) -> dict:
    d: dict = {"side": side, "confidence": confidence, "persona": persona}
    if signal_dir is not None:
        d["signal_dir"] = signal_dir
    return d


def _roundtrip(net_pnl: float = 1000.0) -> dict:
    return {"net_pnl": net_pnl}


class TestClassifyDecisionOutcome:
    """classify_decision_outcome 4분류 + 우선순위."""

    def test_ac_m3_1_case_a_true_positive(self) -> None:
        """케이스 A: realized_return>0, relative_20d>0 → TRUE_POSITIVE."""
        from trading.edge.postmortem import classify_decision_outcome, LABEL_TRUE_POSITIVE
        outcome = classify_decision_outcome(
            _decision(confidence=0.5),
            _roundtrip(net_pnl=5000.0),
            relative_5d=0.02,
            relative_20d=0.02,
            regime="neutral",
        )
        assert outcome.label == LABEL_TRUE_POSITIVE

    def test_ac_m3_1_case_b_false_positive(self) -> None:
        """케이스 B: entry_confidence>=0.6, relative_20d<0 → FALSE_POSITIVE."""
        from trading.edge.postmortem import classify_decision_outcome, LABEL_FALSE_POSITIVE
        outcome = classify_decision_outcome(
            _decision(confidence=0.8),
            _roundtrip(net_pnl=-1000.0),
            relative_5d=-0.01,
            relative_20d=-0.03,
            regime="neutral",
        )
        assert outcome.label == LABEL_FALSE_POSITIVE

    def test_ac_m3_1_case_c_missed(self) -> None:
        """케이스 C: roundtrip=None, relative_20d>0 → MISSED."""
        from trading.edge.postmortem import classify_decision_outcome, LABEL_MISSED
        outcome = classify_decision_outcome(
            _decision(),
            None,  # 미진입
            relative_5d=0.01,
            relative_20d=0.04,
            regime="neutral",
        )
        assert outcome.label == LABEL_MISSED

    def test_ac_m3_1_case_d_regime_mismatch(self) -> None:
        """케이스 D: buy signal + bearish regime → REGIME_MISMATCH (최우선)."""
        from trading.edge.postmortem import classify_decision_outcome, LABEL_REGIME_MISMATCH
        outcome = classify_decision_outcome(
            _decision(confidence=0.8, signal_dir="buy"),
            _roundtrip(net_pnl=-2000.0),
            relative_5d=-0.02,
            relative_20d=-0.05,
            regime="bearish",
        )
        assert outcome.label == LABEL_REGIME_MISMATCH

    def test_regime_mismatch_wins_over_false_positive(self) -> None:
        """진입 경로: REGIME_MISMATCH > FALSE_POSITIVE 우선순위."""
        from trading.edge.postmortem import classify_decision_outcome, LABEL_REGIME_MISMATCH
        # confidence>=0.6 + relative_20d<0 (FP 조건) 동시 + bearish regime (REGIME 조건)
        outcome = classify_decision_outcome(
            _decision(confidence=0.9, signal_dir="buy"),
            _roundtrip(net_pnl=-500.0),
            relative_5d=-0.01,
            relative_20d=-0.03,
            regime="bearish",
        )
        assert outcome.label == LABEL_REGIME_MISMATCH

    def test_missed_non_entry_negative_relative(self) -> None:
        """미진입 경로, relative_20d<=0 → MISSED (label=MISSED but with 'not MISSED' reason)."""
        from trading.edge.postmortem import classify_decision_outcome, LABEL_MISSED
        outcome = classify_decision_outcome(
            _decision(),
            None,
            relative_5d=-0.01,
            relative_20d=-0.02,
            regime="neutral",
        )
        # 분류 라벨은 MISSED 이지만 reason 에 "MISSED 아님" 또는 유사 문구
        assert outcome.label == LABEL_MISSED

    def test_injected_thresholds_change_outcome(self) -> None:
        """임계 주입: confidence_threshold=0.9 → confidence=0.8 이면 FP 아님."""
        from trading.edge.postmortem import classify_decision_outcome, LABEL_FALSE_POSITIVE
        # 기본(0.6) 이면 FP, 주입(0.9) 이면 FP 조건 미충족
        outcome_default = classify_decision_outcome(
            _decision(confidence=0.8),
            _roundtrip(net_pnl=-500.0),
            relative_5d=-0.01,
            relative_20d=-0.02,
            regime="neutral",
        )
        outcome_strict = classify_decision_outcome(
            _decision(confidence=0.8),
            _roundtrip(net_pnl=-500.0),
            relative_5d=-0.01,
            relative_20d=-0.02,
            regime="neutral",
            thresholds={"confidence_threshold": 0.9, "relative_threshold": 0.0},
        )
        assert outcome_default.label == LABEL_FALSE_POSITIVE
        assert outcome_strict.label != LABEL_FALSE_POSITIVE

    def test_persona_attributed(self) -> None:
        """페르소나 귀속이 출력에 포함된다."""
        from trading.edge.postmortem import classify_decision_outcome
        outcome = classify_decision_outcome(
            _decision(persona="macro"),
            _roundtrip(1000.0),
            relative_5d=0.02,
            relative_20d=0.02,
            regime="neutral",
        )
        assert outcome.persona == "macro"


class TestAttributeToPersona:
    """attribute_to_persona() 귀인 로직."""

    def test_returns_outcome_persona(self) -> None:
        from trading.edge.postmortem import classify_decision_outcome, attribute_to_persona
        outcome = classify_decision_outcome(
            _decision(persona="micro"), _roundtrip(), 0.01, 0.01, "neutral"
        )
        persona = attribute_to_persona(outcome, _decision(persona="micro"))
        assert persona == "micro"

    def test_falls_back_to_decision_record(self) -> None:
        from trading.edge.postmortem import DecisionOutcome, attribute_to_persona
        outcome = DecisionOutcome(label="TRUE_POSITIVE", persona=None)
        persona = attribute_to_persona(outcome, {"persona": "portfolio"})
        assert persona == "portfolio"


class TestProposePersonaWeights:
    """AC-M3-2: 20표본 미만이면 제안 없음."""

    def _make_stats(self, persona: str, n: int, n_tp: int = 5):
        from trading.edge.postmortem import PersonaStats
        return PersonaStats(
            persona=persona,
            n_total=n,
            n_true_positive=n_tp,
            n_false_positive=n - n_tp,
        )

    def test_below_min_sample_no_proposal(self) -> None:
        """n=19 < 20 → 제안 없음."""
        from trading.edge.postmortem import propose_persona_weights
        stats = {"macro": self._make_stats("macro", 19)}
        proposals = propose_persona_weights(stats, min_sample=20)
        assert len(proposals) == 0

    def test_at_min_sample_gives_proposal(self) -> None:
        """n=20 ≥ 20 → 제안 산출 (자동 적용 없음)."""
        from trading.edge.postmortem import propose_persona_weights
        stats = {"macro": self._make_stats("macro", 20)}
        proposals = propose_persona_weights(stats, min_sample=20)
        assert len(proposals) == 1
        assert proposals[0].persona == "macro"
        # proposed_weight 는 float 숫자 (자동 적용 아님 — 반환만)
        assert isinstance(proposals[0].proposed_weight, float)

    def test_mixed_sample_sizes(self) -> None:
        """n=30(제안), n=10(제외) — 혼합."""
        from trading.edge.postmortem import propose_persona_weights
        stats = {
            "micro": self._make_stats("micro", 30),
            "portfolio": self._make_stats("portfolio", 10),
        }
        proposals = propose_persona_weights(stats, min_sample=20)
        personas = [p.persona for p in proposals]
        assert "micro" in personas
        assert "portfolio" not in personas


class TestPostmortemMarketNeutral:
    """AC-CORE-1: postmortem.py 본문에 KRX 상수 하드코딩 없음."""

    def test_no_hardcoded_krx_constants(self) -> None:
        import ast
        import inspect
        from trading.edge import postmortem as m

        src = inspect.getsource(m)
        tree = ast.parse(src)
        krx_constants = {0.0018, 0.18, 0.0015, 0.15, 0.00215}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                assert node.value not in krx_constants, (
                    f"KRX 하드코딩 상수 발견: {node.value}"
                )

    def test_korean_and_us_params_both_work(self) -> None:
        """한국/미국 파라미터 세트 모두 동작."""
        from trading.edge.postmortem import classify_decision_outcome

        # 한국 파라미터 세트 (KOSPI 상대수익, KRX confidence 임계)
        out_kr = classify_decision_outcome(
            _decision(confidence=0.7),
            _roundtrip(1000.0),
            relative_5d=0.01,
            relative_20d=0.02,
            regime="neutral",
            thresholds={"confidence_threshold": 0.6, "relative_threshold": 0.0},
        )

        # 가상 미국 파라미터 세트 (SPY 상대수익, 다른 임계)
        out_us = classify_decision_outcome(
            _decision(confidence=0.7),
            _roundtrip(500.0),
            relative_5d=0.005,
            relative_20d=0.015,
            regime="neutral",
            thresholds={"confidence_threshold": 0.65, "relative_threshold": 0.0},
        )

        assert out_kr.label is not None
        assert out_us.label is not None
