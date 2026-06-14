"""T-011 GREEN — COOL_DOWN 리스크 상태 (수동 해제 전용).

SPEC-TRADING-048 REQ-048-M3-5, REQ-048-CORE-3.
AC: AC-M3-4(3회/-5% 발동·2회 미발동·수동 해제 전용·독립 레이어).

기존 halt_state/일일한도와 독립된 레이어.
발동: 규칙위반 누적 3회 OR 드로다운 <= -5%.
해제: 운영자 수동 /resume 만 (SPEC-032 자동재개 제외).

# @MX:NOTE: [AUTO] COOL_DOWN 은 halt_state 위 독립 레이어 — circuit_breaker 와 별도.
# @MX:SPEC: SPEC-TRADING-048 REQ-048-M3-5
"""

from __future__ import annotations

import logging
from typing import Any

LOG = logging.getLogger(__name__)

# 발동 임계 (config 으로 재정의 가능)
DEFAULT_VIOLATION_THRESHOLD = 3       # 규칙위반 누적 횟수
DEFAULT_DRAWDOWN_THRESHOLD = -0.05    # 자기자본 드로다운 임계 (-5%)

# COOL_DOWN 유발 원인 (auto_resume.classify_halt 에서 제외할 접두어)
COOL_DOWN_REASON_PREFIX = "cool_down"


def check_cool_down_trigger(
    violation_count: int,
    drawdown_pct: float,
    *,
    violation_threshold: int = DEFAULT_VIOLATION_THRESHOLD,
    drawdown_threshold: float = DEFAULT_DRAWDOWN_THRESHOLD,
) -> tuple[bool, str]:
    """COOL_DOWN 발동 조건 확인 (순수 함수).

    Args:
        violation_count:        누적 규칙위반 횟수.
        drawdown_pct:           현재 자기자본 드로다운 비율 (음수; 예: -0.06).
        violation_threshold:    위반 누적 임계 (기본 3).
        drawdown_threshold:     드로다운 임계 (기본 -0.05).

    Returns:
        (should_trigger: bool, reason: str).

    Notes:
        - 순수 함수: I/O 없음 (AC-CORE-2).
        - 임계는 모두 파라미터 주입 (AC-CORE-1).
    """
    if violation_count >= violation_threshold:
        return True, (
            f"{COOL_DOWN_REASON_PREFIX}: 규칙위반 {violation_count}회 누적 "
            f"(임계: {violation_threshold}회)"
        )
    if drawdown_pct <= drawdown_threshold:
        return True, (
            f"{COOL_DOWN_REASON_PREFIX}: 드로다운 {drawdown_pct:.1%} "
            f"≤ {drawdown_threshold:.1%}"
        )
    return False, ""


def is_cool_down_halt(reason: str) -> bool:
    """auto_resume.classify_halt 에서 COOL_DOWN 원인인지 판별 (순수 함수).

    REQ-048-M3-5: COOL_DOWN 은 수동 해제 전용 — 자동재개 제외.
    auto_resume.py 에서 이 함수를 호출해 자동재개 대상에서 제외한다.
    """
    return str(reason).startswith(COOL_DOWN_REASON_PREFIX)


def record_violation(
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    """규칙위반 이벤트를 DB cool_down_events 에 기록.

    Args:
        reason:  위반 사유.
        details: 추가 정보 (JSONB 저장).
    """
    from trading.db.session import connection
    sql = """
        INSERT INTO cool_down_events (event_type, reason, details)
        VALUES ('violation', %s, %s::jsonb)
    """
    import json
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (reason, json.dumps(details or {})))
    LOG.info("COOL_DOWN 위반 기록: %s", reason)


def get_violation_count() -> int:
    """오늘(리셋 기준 없음 — 누적) cool_down_events 의 violation 행 수 반환."""
    from trading.db.session import connection
    sql = """
        SELECT COUNT(*) AS n FROM cool_down_events
        WHERE event_type = 'violation'
          AND NOT EXISTS (
              SELECT 1 FROM cool_down_events c2
              WHERE c2.event_type = 'cleared'
                AND c2.ts > cool_down_events.ts
          )
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
        return int(row["n"] or 0) if row else 0


def activate_cool_down(reason: str) -> None:
    """COOL_DOWN 상태 활성화 (system_state.cool_down_active=TRUE)."""
    from trading.db.session import audit, connection
    sql = "UPDATE system_state SET cool_down_active = TRUE WHERE id = 1"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
    # cool_down_events 에 triggered 기록
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO cool_down_events (event_type, reason) VALUES ('triggered', %s)",
            (reason,),
        )
    audit("COOL_DOWN_ACTIVATED", actor="cool_down", details={"reason": reason})
    LOG.warning("COOL_DOWN 활성화: %s", reason)


def deactivate_cool_down() -> None:
    """COOL_DOWN 상태 해제 (운영자 /resume 호출 시).

    REQ-048-M3-5: 수동 해제만 허용.
    """
    from trading.db.session import audit, connection
    sql = "UPDATE system_state SET cool_down_active = FALSE WHERE id = 1"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO cool_down_events (event_type, reason) VALUES ('cleared', 'manual_resume')",
        )
    audit("COOL_DOWN_CLEARED", actor="cool_down", details={"reason": "manual_resume"})
    LOG.info("COOL_DOWN 해제 (수동 resume)")


def is_cool_down_active() -> bool:
    """현재 COOL_DOWN 상태 확인."""
    from trading.db.session import connection
    sql = "SELECT cool_down_active FROM system_state WHERE id = 1"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
        return bool(row["cool_down_active"]) if row else False
