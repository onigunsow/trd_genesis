"""T-010 GREEN — confidence 시나리오 확률 저장 경로 (스키마-only).

SPEC-TRADING-048 REQ-048-M3-4.
AC: AC-M3-3(저장·합 검증·NULL 허용).

페르소나 프롬프트 변경 없음 — DB 스키마 + 저장 경로만.
컬럼은 nullable; 프롬프트가 채우기 전까지 NULL 허용.

# @MX:NOTE: [AUTO] prob_bull/base/bear 저장 경로 — 프롬프트는 후속 SPEC.
# @MX:SPEC: SPEC-TRADING-048 REQ-048-M3-4
"""

from __future__ import annotations


def validate_probs(
    prob_bull: float,
    prob_base: float,
    prob_bear: float,
    *,
    tol: float = 1e-6,
) -> None:
    """세 확률의 합이 1.0 인지 검증.

    Args:
        prob_bull: 강세 확률.
        prob_base: 기준 확률.
        prob_bear: 약세 확률.
        tol:       허용 오차 (기본 1e-6).

    Raises:
        ValueError: |sum - 1| > tol 이면.

    Notes:
        - 순수 함수: I/O 없음.
    """
    total = prob_bull + prob_base + prob_bear
    if abs(total - 1.0) > tol:
        raise ValueError(
            f"prob_bull+prob_base+prob_bear 합 = {total:.8f} ≠ 1.0 "
            f"(허용오차 {tol})"
        )


def store_decision_probs(
    decision_id: int,
    prob_bull: float | None,
    prob_base: float | None,
    prob_bear: float | None,
) -> None:
    """persona_decisions 에 prob_bull/base/bear 저장.

    세 값이 모두 존재하면 합 검증(|sum-1|<=1e-6) 후 저장.
    일부/전부 None 이면 검증 없이 NULL 저장.

    Args:
        decision_id: persona_decisions.id.
        prob_bull:   강세 확률 (None 허용).
        prob_base:   기준 확률 (None 허용).
        prob_bear:   약세 확률 (None 허용).
    """
    all_present = all(v is not None for v in [prob_bull, prob_base, prob_bear])
    if all_present:
        assert prob_bull is not None
        assert prob_base is not None
        assert prob_bear is not None
        validate_probs(prob_bull, prob_base, prob_bear)

    from trading.db.session import connection

    sql = """
        UPDATE persona_decisions
           SET prob_bull = %s,
               prob_base = %s,
               prob_bear = %s
         WHERE id = %s
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (prob_bull, prob_base, prob_bear, decision_id))
