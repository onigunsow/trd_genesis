"""T-009 GREEN — 결정 단위 postmortem 분류 코어 순수 함수.

SPEC-TRADING-048 REQ-048-M3-1/2/3, REQ-048-CORE-1/2/3.
AC: AC-M3-1(4분류·귀인), AC-M3-2(20표본), AC-CORE-1/2.

순수 함수 — 외부 I/O / 전역 상태 / now() / DB 접근 없음.
KRX/KOSPI 특유 상수(confidence 임계·relative 임계)는 모두 파라미터로 주입.

# @MX:NOTE: [AUTO] 시장 중립 postmortem 코어 — KRX 상수 하드코딩 없음.
# @MX:SPEC: SPEC-TRADING-048 REQ-048-M3-1/2/3
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# 분류 라벨
# ---------------------------------------------------------------------------

LABEL_TRUE_POSITIVE = "TRUE_POSITIVE"
LABEL_FALSE_POSITIVE = "FALSE_POSITIVE"
LABEL_REGIME_MISMATCH = "REGIME_MISMATCH"
LABEL_MISSED = "MISSED"

# 진입 경로 우선순위 (높은 값 = 높은 우선순위)
_PRIORITY = {
    LABEL_REGIME_MISMATCH: 3,
    LABEL_FALSE_POSITIVE: 2,
    LABEL_TRUE_POSITIVE: 1,
}


# ---------------------------------------------------------------------------
# 결과 타입
# ---------------------------------------------------------------------------

@dataclass
class DecisionOutcome:
    """단일 결정의 분류 결과."""

    label: str
    persona: str | None = None
    reason: str = ""


@dataclass
class PersonaStats:
    """페르소나별 집계 통계."""

    persona: str
    n_total: int = 0
    n_true_positive: int = 0
    n_false_positive: int = 0
    n_regime_mismatch: int = 0
    n_missed: int = 0


@dataclass
class WeightProposal:
    """weight 조정 제안 (자동 적용 금지)."""

    persona: str
    current_weight: float
    proposed_weight: float
    reason: str


# ---------------------------------------------------------------------------
# 기본 임계 (주입용 — 하드코딩은 여기 default 값에만, 코어 로직에는 없음)
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLDS: dict[str, float] = {
    "confidence_threshold": 0.6,    # FALSE_POSITIVE 판정용 confidence 임계
    "relative_threshold": 0.0,      # MISSED 판정용 relative 임계 (0 초과면 MISSED)
}


# ---------------------------------------------------------------------------
# 코어 분류 함수
# ---------------------------------------------------------------------------


# @MX:ANCHOR: [AUTO] classify_decision_outcome — postmortem 분류의 단일 진입점.
# @MX:REASON: SPEC-048 REQ-048-M3-1: 대시보드·일일 배치·테스트 모두 이 함수를 소비.
def classify_decision_outcome(
    decision: dict[str, Any],
    roundtrip_or_none: dict[str, Any] | None,
    relative_5d: float,
    relative_20d: float,
    regime: str,
    *,
    thresholds: dict[str, float] | None = None,
) -> DecisionOutcome:
    """결정(decision) 단위 postmortem 분류.

    두 경로:
    - 진입 경로 (roundtrip_or_none 존재): TP/FP/REGIME_MISMATCH (우선순위 REGIME>FP>TP).
    - 미진입 경로 (roundtrip_or_none=None, hold/REJECT/HOLD): rel_20d>0 → MISSED.

    Args:
        decision:           persona_decisions 행 (side, confidence, regime 등 포함).
        roundtrip_or_none:  해당 결정의 라운드트립 dict (진입·종료 시) 또는 None (미진입).
        relative_5d:        결정 이후 5일 KOSPI 상대수익률.
        relative_20d:       결정 이후 20일 KOSPI 상대수익률.
        regime:             결정 시점 macro regime 문자열.
        thresholds:         {'confidence_threshold': float, 'relative_threshold': float}.

    Returns:
        DecisionOutcome(label, persona, reason).

    Notes:
        - 순수 함수: I/O 없음 (AC-CORE-2).
        - KRX/KOSPI 상수 하드코딩 없음 — thresholds 로 주입 (AC-CORE-1).
    """
    t = thresholds or DEFAULT_THRESHOLDS
    conf_threshold: float = float(t.get("confidence_threshold", 0.6))
    rel_threshold: float = float(t.get("relative_threshold", 0.0))

    side = str(decision.get("side", "buy")).lower()
    confidence = float(decision.get("confidence") or 0.0)
    signal_dir = str(decision.get("signal_dir", side)).lower()

    # --- 미진입 경로 ---
    if roundtrip_or_none is None:
        if relative_20d > rel_threshold:
            return DecisionOutcome(
                label=LABEL_MISSED,
                persona=decision.get("persona"),
                reason=f"미진입 결정, 이후 20일 상대수익 {relative_20d:.2%} > {rel_threshold}",
            )
        return DecisionOutcome(
            label=LABEL_MISSED,
            persona=decision.get("persona"),
            reason=f"미진입 결정, 이후 20일 상대수익 {relative_20d:.2%} ≤ {rel_threshold} (MISSED 아님)",
        )

    # --- 진입 경로 (roundtrip 존재) ---
    rt = roundtrip_or_none
    realized_return = float(rt.get("net_pnl", 0.0))

    candidates: list[tuple[int, str, str]] = []  # (priority, label, reason)

    # REGIME_MISMATCH: 신호 방향과 regime 불일치 (bearish regime + buy signal)
    bearish_regimes = {"bearish", "bear", "conservative", "defensive"}
    if signal_dir == "buy" and regime.lower() in bearish_regimes:
        candidates.append((
            _PRIORITY[LABEL_REGIME_MISMATCH],
            LABEL_REGIME_MISMATCH,
            f"매수 신호가 {regime} regime 에서 발생",
        ))

    # FALSE_POSITIVE: confidence >= threshold 이었으나 relative_20d < 0
    if confidence >= conf_threshold and relative_20d < 0:
        candidates.append((
            _PRIORITY[LABEL_FALSE_POSITIVE],
            LABEL_FALSE_POSITIVE,
            f"confidence={confidence:.2f} ≥ {conf_threshold} 이지만 20일 상대수익 {relative_20d:.2%}",
        ))

    # TRUE_POSITIVE: 수익 실현 + 시장 대비 우위
    if realized_return > 0 and (relative_5d > rel_threshold or relative_20d > rel_threshold):
        candidates.append((
            _PRIORITY[LABEL_TRUE_POSITIVE],
            LABEL_TRUE_POSITIVE,
            f"실현손익 {realized_return:,.0f} > 0, 상대수익 {relative_20d:.2%}",
        ))

    if not candidates:
        # 진입했으나 TP/REGIME 우위가 없는 경우:
        # confidence 가 임계 이상이었으면 FALSE_POSITIVE(확신했으나 실패),
        # 임계 미만이면 확신하지 않은 진입이므로 confidence 실패가 아님 → TRUE_POSITIVE.
        # (주입 thresholds 가 catch-all 경로에도 반영되도록 confidence-gated 분기.)
        if confidence >= conf_threshold:
            return DecisionOutcome(
                label=LABEL_FALSE_POSITIVE,
                persona=decision.get("persona"),
                reason=f"confidence={confidence:.2f} ≥ {conf_threshold} 이나 TP/REGIME 우위 없음",
            )
        return DecisionOutcome(
            label=LABEL_TRUE_POSITIVE,
            persona=decision.get("persona"),
            reason=f"confidence={confidence:.2f} < {conf_threshold} (저확신 진입 — confidence 실패 아님)",
        )

    # 우선순위 최상위 라벨 선택
    candidates.sort(key=lambda x: -x[0])
    _, best_label, best_reason = candidates[0]
    return DecisionOutcome(
        label=best_label,
        persona=decision.get("persona"),
        reason=best_reason,
    )


def attribute_to_persona(
    outcome: DecisionOutcome,
    decision_record: dict[str, Any],
) -> str:
    """결정 결과를 페르소나에 귀인.

    Args:
        outcome:          classify_decision_outcome() 반환값.
        decision_record:  persona_decisions 행.

    Returns:
        페르소나 이름 문자열.

    Notes:
        - 순수 함수: I/O 없음.
    """
    return str(
        outcome.persona
        or decision_record.get("persona")
        or decision_record.get("persona_name")
        or "unknown"
    )


def propose_persona_weights(
    per_persona_stats: dict[str, PersonaStats],
    *,
    min_sample: int = 20,
    current_weights: dict[str, float] | None = None,
) -> list[WeightProposal]:
    """페르소나별 weight 조정 제안 (자동 적용 금지).

    Args:
        per_persona_stats: {persona_name: PersonaStats}.
        min_sample:        제안 산출 최소 표본 (기본 20).
        current_weights:   현재 weight 딕셔너리 (없으면 1.0 기본).

    Returns:
        WeightProposal 목록. 표본 부족 페르소나는 제외.

    Notes:
        - 순수 함수: I/O 없음.
        - 제안만 반환, 자동 적용 없음 (REQ-048-M3-3).
    """
    proposals: list[WeightProposal] = []
    cw = current_weights or {}

    for persona, stats in per_persona_stats.items():
        if stats.n_total < min_sample:
            continue  # REQ-048-M3-3: 20표본 미만이면 제안 없음

        current = float(cw.get(persona, 1.0))
        # 단순 비율 기반 제안: TP 비율이 낮으면 weight 감소
        tp_rate = stats.n_true_positive / stats.n_total if stats.n_total > 0 else 0.0
        fp_rate = (stats.n_false_positive + stats.n_regime_mismatch) / stats.n_total

        # 제안 공식: current * max(0.5, min(1.5, 1 + tp_rate - fp_rate))
        adjustment = max(0.5, min(1.5, 1.0 + tp_rate - fp_rate))
        proposed = round(current * adjustment, 4)

        proposals.append(WeightProposal(
            persona=persona,
            current_weight=current,
            proposed_weight=proposed,
            reason=(
                f"TP율={tp_rate:.1%}, FP+MISMATCH율={fp_rate:.1%}, "
                f"조정계수={adjustment:.3f} (n={stats.n_total})"
            ),
        ))

    return proposals
