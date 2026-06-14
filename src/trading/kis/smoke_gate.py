"""SPEC-TRADING-049 — 라이브 스모크 게이트 (REQ-045-C 구현).

운영자가 실거래 전환 전 1회 소액 BUY→SELL round-trip을 실행하고 5가지 증거 항목을
결정론적으로 판정하여 PASS/FAIL 기록을 남기는 실행 경로 검증 게이트.

핵심 원칙: 재사용 우선. live 체결조회 seam(confirm_fills), order_resolver,
sell_lock, broker_truth은 이미 존재한다. 본 게이트는 그것을 **호출**할 뿐이다.

Public surface:
- ``SmokeEvidence``      — 판정 입력(주입형 데이터 클래스)
- ``SmokeItemResult``    — 개별 증거 항목 판정 결과
- ``SmokeVerdict``       — 전체 판정 결과(PASS/FAIL + 항목별 상태 + 사유)
- ``evaluate_smoke_evidence(evidence, *, timestamp)`` — 순수 판정 함수(I/O 없음)
- ``record_smoke_verdict(verdict, snapshot)``         — audit_log 영구 기록
- ``has_valid_smoke_pass()``                          — PASS 기록 존재 여부 조회
- ``check_smoke_gate_precondition()``                 — live 승격 선행 검사(차단 또는 통과)

[확인 필요-3 해소 결정]: 증거 영구 기록은 audit_log(option b)로 확정.
  이유: conftest fake_cursor/fake_conn/patch_db_connection 픽스처와 완전 호환,
  FAIL→PASS 미덮어쓰기(이벤트 추가만, 조회 시 SMOKE_GATE_PASS만 검색),
  신규 마이그레이션 불필요(REQ-049-NFR-3 조건부 충족 = 마이그레이션 생략).

CI 안전: 자동화 테스트는 live POST/inquiry 응답을 mock. CI 실거래 발주 없음(REQ-049-NFR-2).

See ``.moai/specs/SPEC-TRADING-049-live-smoke-gate/`` for the full SPEC.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import UTC, datetime
from typing import Any

from trading.db.session import audit, connection

LOG = logging.getLogger(__name__)

# 정직 고지 문구(REQ-049-M1-4, REQ-045-C4) — 항상 CLI 출력/리포트 머리말에 포함.
HONESTY_DISCLOSURE = (
    "【스모크 게이트 정직 고지】\n"
    "본 게이트는 실행 경로(execution path)를 검증하며 "
    "전략 수익성 검증이 아닙니다.\n"
    "전략 측정은 SPEC-044 + SPEC-046/048 소관입니다."
)

# audit_log 이벤트 타입 상수.
_EVENT_PASS = "SMOKE_GATE_PASS"  # noqa: S105 — audit_log 이벤트 타입, 비밀번호 아님
_EVENT_FAIL = "SMOKE_GATE_FAIL"
_ACTOR = "smoke_gate"


@dataclasses.dataclass(frozen=True)
class SmokeEvidence:
    """판정 입력 — I/O·전역 상태 없이 주입되는 데이터 묶음(REQ-049-M2-2).

    Attributes
    ----------
    buy_fill:
        BUY 주문에 대해 KIS inquire-daily-ccld가 반환한 fill 레코드.
        None이면 (a)BUY확정 미충족.
    buy_order_no:
        BUY 주문의 kis_order_no(ODNO 매칭에 사용).
    sell_fill:
        SELL 주문에 대한 fill 레코드. None이면 (b)SELL확정 미충족.
    sell_order_no:
        SELL 주문의 kis_order_no.
    ledger_parity:
        intraday_reconcile 기준 broker 잔고 vs 로컬 원장 정합 여부.
        True = 정합(c 충족), False = 불일치.
    stuck_submitted_count:
        resolve_stuck_orders 통과 후 'submitted' 상태 잔존 주문 수.
        0이면 (d)stuck 0건 충족.
    tr_id_field_compatible:
        _inquire_daily_ccld 호출 시 BrokerFillInquiryNotImplemented가
        raise되지 않고 응답이 왔으면 True (e 충족).
        False = TR_ID/필드 미검증 또는 seam 가드 발동.
    realized_pnl_delta:
        round-trip 후 realized_pnl_cum 증분(원화).
        None이면 측정 불가. 부호는 PASS/FAIL 판정에 무관(실행 정합만 검증).
    """

    buy_fill: dict[str, Any] | None
    buy_order_no: str
    sell_fill: dict[str, Any] | None
    sell_order_no: str
    ledger_parity: bool
    stuck_submitted_count: int
    tr_id_field_compatible: bool
    realized_pnl_delta: int | None = None


@dataclasses.dataclass(frozen=True)
class SmokeItemResult:
    """개별 증거 항목(a~e)의 판정 결과."""

    satisfied: bool
    reason: str


@dataclasses.dataclass(frozen=True)
class SmokeVerdict:
    """전체 스모크 판정 결과(REQ-049-M2-2).

    Attributes
    ----------
    passed:
        True = 전체 PASS(5항목 전부 충족). False = FAIL(1개 이상 미충족).
    items:
        항목별 판정. 키: 'a'(BUY확정) / 'b'(SELL확정) / 'c'(원장정합) /
        'd'(stuck 0건) / 'e'(TR_ID·필드 호환).
    reasons:
        FAIL 사유 목록(PASS이면 빈 리스트).
    timestamp:
        판정 시각(ISO-8601 UTC).
    realized_pnl_delta:
        round-trip 실현손익 증분(정보성, 판정에 무관).
    """

    passed: bool
    items: dict[str, SmokeItemResult]
    reasons: list[str]
    timestamp: str
    realized_pnl_delta: int | None = None


def _check_buy_fill(
    evidence: SmokeEvidence,
) -> SmokeItemResult:
    """(a) BUY 확정 체결 — ODNO 매칭 + CCLD_QTY > 0 + CCLD_AVG_UNPR > 0."""
    rec = evidence.buy_fill
    if rec is None:
        return SmokeItemResult(
            satisfied=False,
            reason="BUY fill 미확인: inquire-daily-ccld가 BUY 체결 레코드를 반환하지 않음",
        )
    odno = str(rec.get("ODNO", "") or "").strip()
    if odno != str(evidence.buy_order_no).strip():
        return SmokeItemResult(
            satisfied=False,
            reason=(
                f"BUY fill ODNO 불일치: expected={evidence.buy_order_no!r} "
                f"got={odno!r}"
            ),
        )
    ccld_qty = int(rec.get("CCLD_QTY", 0) or 0)
    if ccld_qty <= 0:
        return SmokeItemResult(
            satisfied=False,
            reason=f"BUY fill CCLD_QTY <= 0 (체결수량 없음): {ccld_qty}",
        )
    ccld_avg = int(float(rec.get("CCLD_AVG_UNPR", 0) or 0))
    if ccld_avg <= 0:
        return SmokeItemResult(
            satisfied=False,
            reason=f"BUY fill CCLD_AVG_UNPR <= 0 (체결평균가 없음): {ccld_avg}",
        )
    return SmokeItemResult(
        satisfied=True,
        reason=f"BUY 확정: ODNO={odno} qty={ccld_qty} avg_price={ccld_avg}",
    )


def _check_sell_fill(
    evidence: SmokeEvidence,
) -> SmokeItemResult:
    """(b) SELL 확정 체결 — ODNO 매칭 + CCLD_QTY > 0 + CCLD_AVG_UNPR > 0."""
    rec = evidence.sell_fill
    if rec is None:
        return SmokeItemResult(
            satisfied=False,
            reason="SELL fill 미확인: inquire-daily-ccld가 SELL 체결 레코드를 반환하지 않음",
        )
    odno = str(rec.get("ODNO", "") or "").strip()
    if odno != str(evidence.sell_order_no).strip():
        return SmokeItemResult(
            satisfied=False,
            reason=(
                f"SELL fill ODNO 불일치: expected={evidence.sell_order_no!r} "
                f"got={odno!r}"
            ),
        )
    ccld_qty = int(rec.get("CCLD_QTY", 0) or 0)
    if ccld_qty <= 0:
        return SmokeItemResult(
            satisfied=False,
            reason=f"SELL fill CCLD_QTY <= 0 (체결수량 없음): {ccld_qty}",
        )
    ccld_avg = int(float(rec.get("CCLD_AVG_UNPR", 0) or 0))
    if ccld_avg <= 0:
        return SmokeItemResult(
            satisfied=False,
            reason=f"SELL fill CCLD_AVG_UNPR <= 0 (체결평균가 없음): {ccld_avg}",
        )
    return SmokeItemResult(
        satisfied=True,
        reason=f"SELL 확정: ODNO={odno} qty={ccld_qty} avg_price={ccld_avg}",
    )


def _check_ledger_parity(evidence: SmokeEvidence) -> SmokeItemResult:
    """(c) 원장 정합 — intraday_reconcile 기준 broker 잔고 vs 로컬 positions 정합."""
    if not evidence.ledger_parity:
        return SmokeItemResult(
            satisfied=False,
            reason=(
                "원장 불일치: broker 진실원(intraday_reconcile) 잔고와 "
                "로컬 positions가 정합하지 않거나 realized_pnl_cum 미반영"
            ),
        )
    return SmokeItemResult(satisfied=True, reason="원장 정합: broker 잔고 = 로컬 원장")


def _check_stuck_submitted(evidence: SmokeEvidence) -> SmokeItemResult:
    """(d) stuck 'submitted' 0건 — resolve_stuck_orders 통과 후 잔존 없음."""
    count = evidence.stuck_submitted_count
    if count > 0:
        return SmokeItemResult(
            satisfied=False,
            reason=(
                f"stuck 'submitted' {count}건 잔존: "
                "resolve_stuck_orders 통과 후에도 영구 정체 주문이 남음"
            ),
        )
    return SmokeItemResult(
        satisfied=True, reason="stuck 'submitted' 0건: 정체 주문 없음"
    )


def _check_tr_id_field_compat(evidence: SmokeEvidence) -> SmokeItemResult:
    """(e) live TR_ID/필드 실검증 — BrokerFillInquiryNotImplemented 미발동."""
    if not evidence.tr_id_field_compatible:
        return SmokeItemResult(
            satisfied=False,
            reason=(
                "live TR_ID/필드 미호환: BrokerFillInquiryNotImplemented 발동 또는 "
                "output 필드명(_parse) 불일치 — [확인 필요-1/2] 미해소"
            ),
        )
    return SmokeItemResult(
        satisfied=True,
        reason=(
            "live TR_ID/필드 호환 확인: "
            "TTTC8001R/CTSC9115R + ODNO/CCLD_QTY/CCLD_AVG_UNPR 실응답 수신"
        ),
    )


# @MX:ANCHOR: [AUTO] 스모크 증거 판정 단일 진입점 (REQ-049-M2-2).
# @MX:REASON: fan_in >= 2 (CLI 러너 + 테스트 직접 호출). I/O·전역 상태 없는
#   순수 함수 — 동일 입력은 항상 동일 판정을 산출(결정론적, REQ-049-M2-2).
#   live POST/inquiry를 mock해도 이 함수의 판정 로직은 그대로 검증됨(REQ-049-NFR-2).
def evaluate_smoke_evidence(
    evidence: SmokeEvidence,
    *,
    timestamp: str | None = None,
) -> SmokeVerdict:
    """5항목 증거를 PASS/FAIL로 판정하는 순수 함수(REQ-049-M2-2).

    I/O·전역 상태·시각·DB 접근 없음. timestamp를 주입하지 않으면 UTC now를 사용.
    동일 입력 → 항상 동일 판정(결정론적). 1항목이라도 미충족이면 FAIL(REQ-049-M2-3).

    Parameters
    ----------
    evidence:
        수집된 5항목 증거 데이터.
    timestamp:
        판정 시각(ISO-8601 UTC). None이면 현재 UTC 시각을 사용.

    Returns
    -------
    SmokeVerdict:
        PASS/FAIL + 항목별 결과 + 사유 목록 + 타임스탬프.
    """
    ts = timestamp or datetime.now(UTC).isoformat()

    item_a = _check_buy_fill(evidence)
    item_b = _check_sell_fill(evidence)
    item_c = _check_ledger_parity(evidence)
    item_d = _check_stuck_submitted(evidence)
    item_e = _check_tr_id_field_compat(evidence)

    items: dict[str, SmokeItemResult] = {
        "a": item_a,
        "b": item_b,
        "c": item_c,
        "d": item_d,
        "e": item_e,
    }

    reasons = [
        result.reason
        for result in items.values()
        if not result.satisfied
    ]
    passed = len(reasons) == 0

    return SmokeVerdict(
        passed=passed,
        items=items,
        reasons=reasons,
        timestamp=ts,
        realized_pnl_delta=evidence.realized_pnl_delta,
    )


def record_smoke_verdict(
    verdict: SmokeVerdict,
    snapshot: dict[str, Any] | None = None,
) -> None:
    """판정 결과를 audit_log에 영구 기록(REQ-049-M2-4).

    FAIL은 결코 PASS로 덮어쓰지 않는다. audit_log는 이벤트 추가만 허용하며,
    has_valid_smoke_pass()는 SMOKE_GATE_PASS 이벤트만 조회하므로
    FAIL 기록이 있어도 이후 PASS를 가리지 않는다(REQ-049-M2-4 보장).

    Parameters
    ----------
    verdict:
        evaluate_smoke_evidence()가 반환한 판정 결과.
    snapshot:
        추가 증거 스냅샷(fill 레코드, 원장 요약 등). None이면 빈 dict.
    """
    event_type = _EVENT_PASS if verdict.passed else _EVENT_FAIL
    details: dict[str, Any] = {
        "passed": verdict.passed,
        "timestamp": verdict.timestamp,
        "items": {
            k: {"satisfied": v.satisfied, "reason": v.reason}
            for k, v in verdict.items.items()
        },
        "reasons": verdict.reasons,
        "realized_pnl_delta": verdict.realized_pnl_delta,
        "snapshot": snapshot or {},
    }
    audit(event_type, actor=_ACTOR, details=details)
    LOG.info(
        "SPEC-049 smoke_gate: verdict=%s timestamp=%s reasons=%s",
        "PASS" if verdict.passed else "FAIL",
        verdict.timestamp,
        verdict.reasons or "(없음)",
    )


def has_valid_smoke_pass(*, conn_factory: Any = None) -> bool:
    """SMOKE_GATE_PASS 기록이 audit_log에 존재하는지 확인(REQ-049-M2-5).

    FAIL 기록은 무시. PASS 이벤트가 1건이라도 존재하면 True.

    Parameters
    ----------
    conn_factory:
        테스트 주입용 DB 연결 팩토리. None이면 session.connection을 사용.

    Returns
    -------
    bool:
        True = 유효한 스모크 PASS 기록 존재 → 전면 승격 허용.
        False = PASS 기록 없음 → 전면 승격 차단.
    """
    _conn = conn_factory if conn_factory is not None else connection
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM audit_log "
                    "WHERE event_type = %s LIMIT 1",
                    (_EVENT_PASS,),
                )
                row = cur.fetchone()
        return row is not None
    except Exception:
        LOG.warning(
            "SPEC-049 smoke_gate: has_valid_smoke_pass DB 조회 실패 — "
            "안전을 위해 False(차단) 반환",
            exc_info=True,
        )
        return False


class SmokeGateRequired(RuntimeError):
    """live 승격 선행 검사 실패 — 스모크 PASS 기록이 없음(REQ-049-M2-5)."""


def check_smoke_gate_precondition(*, conn_factory: Any = None) -> None:
    """live 전면 승격 선행 검사(REQ-049-M2-5).

    유효한 스모크 PASS 기록이 존재하지 않으면 SmokeGateRequired를 raise하여
    live 전면 승격을 차단한다. PASS 기록이 있으면 정상 반환(승격 허용).

    기존 _check_live_gate(REQ-MODE-02-6)의 의미는 변경하지 않는다.
    본 함수는 그 상위에 두는 선행 검사로만 동작한다(EXCLUSION #5).

    Parameters
    ----------
    conn_factory:
        테스트 주입용 DB 연결 팩토리(None이면 session.connection 사용).

    Raises
    ------
    SmokeGateRequired:
        스모크 PASS 기록이 없을 때. 운영자에게 사유를 명시.
    """
    if not has_valid_smoke_pass(conn_factory=conn_factory):
        raise SmokeGateRequired(
            "live 전면 승격 차단: 스모크 PASS 기록 없음.\n"
            "trading smoke-gate --max-qty 1 을 실행하고 PASS를 받은 뒤 승격하십시오.\n"
            "(FAIL 기록은 PASS로 해석되지 않습니다 — REQ-049-M2-4/M2-5)"
        )
