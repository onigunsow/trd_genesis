"""T-004 GREEN — 검증 게이트 진입점 + M1-8 PASS 상태 read API.

SPEC-TRADING-048 REQ-048-M2-5, REQ-048-M1-8.
AC: AC-M2-2(PASS 미달 시 차단), AC-M1-7(M2 PASS 전 kelly_pct 강제 0).

최소 상태 설계: 단일 플래그 파일 기반. 검증 이력 테이블 신설 없음(과설계 회피).
기본 = False(REJECT 상태) — 현재 마이너스 기대값에 맞는 안전 기본값.

# @MX:NOTE: [AUTO] M1-8 게이트가 소비하는 PASS 상태 공급 API.
# 기본 False → _execute_signal 의 kelly_pct 가 강제 0 이 됨.
# @MX:SPEC: SPEC-TRADING-048 REQ-048-M2-5, REQ-048-M1-8
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from trading.edge.evaluate_backtest import VERDICT_PASS, BacktestScoreCard

# ---------------------------------------------------------------------------
# 게이트 상태 (인메모리 싱글톤, 재시작 시 False 초기화 — 보수적)
# ---------------------------------------------------------------------------

_gate_state: dict[str, Any] = {
    "passed": False,
    "last_card": None,
    "blocking_reasons": [],
}


# @MX:ANCHOR: [AUTO] is_validation_passed — M1 kelly 게이트의 단일 조건부.
# @MX:REASON: SPEC-048 REQ-048-M1-8: _execute_signal 이 이 값을 소비해 kelly_pct 를 0으로 강제함.
def is_validation_passed() -> bool:
    """M2 검증 게이트가 PASS 판정을 산출했는지 확인.

    Returns:
        True → M2 채점기 PASS (kelly_pct 유효).
        False(기본) → PASS 미달, kelly_pct 강제 0.

    Notes:
        - 재시작 시 항상 False 초기화(보수적 안전 기본값).
        - 순수 읽기; 사이드 이펙트 없음.
    """
    return bool(_gate_state["passed"])


def get_blocking_reasons() -> list[str]:
    """PASS 미달 시 차단 사유(미달 차원) 반환."""
    return list(_gate_state["blocking_reasons"])


@dataclass
class GateResult:
    """apply_scorecard 결과."""
    allowed: bool
    blocking_reasons: list[str] = field(default_factory=list)
    card: BacktestScoreCard | None = None


def apply_scorecard(card: BacktestScoreCard) -> GateResult:
    """채점 결과를 게이트 상태에 반영한다.

    PASS 판정이면 is_validation_passed() = True.
    PASS 미달이면 차단 사유(0점 차원)를 기록하고 False 유지.

    Args:
        card: score_backtest() 반환값.

    Returns:
        GateResult(allowed, blocking_reasons, card).
    """
    if card.verdict == VERDICT_PASS:
        _gate_state["passed"] = True
        _gate_state["last_card"] = card
        _gate_state["blocking_reasons"] = []
        return GateResult(allowed=True, blocking_reasons=[], card=card)

    # REVISE / REJECT — 차단 사유 기록
    reasons: list[str] = []
    for dim, score in card.dimension_scores.items():
        if score == 0.0:
            reasons.append(f"{dim}=0점")
    if card.card if hasattr(card, "card") else False:
        pass
    if not reasons:
        reasons.append(f"합계={card.score:.1f} < {70}점 또는 expectancy<=0")

    _gate_state["passed"] = False
    _gate_state["last_card"] = card
    _gate_state["blocking_reasons"] = reasons
    return GateResult(allowed=False, blocking_reasons=reasons, card=card)


def reset_gate() -> None:
    """게이트를 False 초기 상태로 복원 (테스트·운영자 수동 재설정용)."""
    _gate_state["passed"] = False
    _gate_state["last_card"] = None
    _gate_state["blocking_reasons"] = []
