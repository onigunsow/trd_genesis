"""Phase 1/3 — 표본 등급 + go/no-go 판정 + 한국어 렌더 + 한계 푸터 + 실거래 준비 게이트.

정직성 원칙: 한계 푸터는 **항상** 출력된다. 표본이 부족하면 어떤 좋은 수치가 나와도 GO 가
될 수 없다. GO 는 슬리피지 보정 후에도 기대값>0 · 손익비>1.0 · KOSPI 알파>0 · 표본 충분(≥30)
을 모두 만족할 때만.
"""

from __future__ import annotations

import inspect
import math
import stat
from dataclasses import dataclass, field
from typing import Any

from trading.edge.analytics import Analytics
from trading.edge.benchmark import Benchmark

# 표본 등급
GRADE_INSUFFICIENT = "INSUFFICIENT"   # < 10
GRADE_WEAK = "WEAK"                    # 10–29
GRADE_MODERATE = "MODERATE"           # 30–99
GRADE_OK = "OK"                       # ≥ 100

# 판정
VERDICT_NO_GO = "NO-GO"
VERDICT_INCONCLUSIVE = "INCONCLUSIVE"
VERDICT_WEAK_GO = "WEAK-GO"
VERDICT_GO = "GO"

_MIN_SAMPLE = 30


def grade_sample(n: int) -> str:
    if n < 10:
        return GRADE_INSUFFICIENT
    if n < 30:
        return GRADE_WEAK
    if n < 100:
        return GRADE_MODERATE
    return GRADE_OK


@dataclass
class Scorecard:
    grade: str
    verdict: str
    reasons: list[str] = field(default_factory=list)


def decide(analytics: Analytics, benchmark: Benchmark) -> Scorecard:
    """슬리피지 보정 후 지표 + 표본 등급으로 go/no-go 판정."""
    n = analytics.n_closed
    grade = grade_sample(n)
    sufficient = n >= _MIN_SAMPLE
    positive_edge = analytics.expectancy_adj > 0 and analytics.profit_factor_adj > 1.0
    alpha_ok = benchmark.available and benchmark.alpha_pct > 0

    reasons: list[str] = []

    if n < 10:
        reasons.append(f"표본 부족(n={n}<10) — 통계적 의미 없음")
        return Scorecard(grade=grade, verdict=VERDICT_NO_GO, reasons=reasons)

    if not positive_edge:
        if analytics.expectancy_adj <= 0:
            reasons.append(
                f"슬리피지 보정 후 거래당 기대값 음성({analytics.expectancy_adj:,.0f}원)"
            )
        if analytics.profit_factor_adj <= 1.0:
            reasons.append(
                f"슬리피지 보정 후 손익비 ≤1.0({_pf(analytics.profit_factor_adj)})"
            )
        return Scorecard(grade=grade, verdict=VERDICT_NO_GO, reasons=reasons)

    # 여기부터 보정 후에도 양성 엣지.
    reasons.append(
        f"보정 후 기대값 +{analytics.expectancy_adj:,.0f}원/거래, "
        f"손익비 {_pf(analytics.profit_factor_adj)}"
    )

    if not sufficient:
        reasons.append(f"단, 표본 부족(n={n}<{_MIN_SAMPLE}) — 잠정적")
        return Scorecard(grade=grade, verdict=VERDICT_WEAK_GO, reasons=reasons)

    if not benchmark.available:
        reasons.append("KOSPI 비교 데이터 없음 — 알파 미확인")
        return Scorecard(grade=grade, verdict=VERDICT_INCONCLUSIVE, reasons=reasons)

    if not alpha_ok:
        reasons.append(
            f"그러나 KOSPI 알파 음성({benchmark.alpha_pct:+.1f}%p) — 지수보다 못함"
        )
        return Scorecard(grade=grade, verdict=VERDICT_INCONCLUSIVE, reasons=reasons)

    reasons.append(f"KOSPI 대비 알파 +{benchmark.alpha_pct:.1f}%p, 표본 충분(n={n})")
    return Scorecard(grade=grade, verdict=VERDICT_GO, reasons=reasons)


# ---------------------------------------------------------------------------
# 실거래 준비 게이트 (엣지 판정과 무관 — 실거래 전 반드시 해소. 보고만, 구현 아님)
# ---------------------------------------------------------------------------

# 2026-08-09: 이 목록은 원래 하드코딩된 문자열 3개였고 실제 상태를 한 번도
# 확인하지 않았다. 그 사이 세 항목이 모두 해소됐는데도 리포트는 계속 "실거래 전
# 반드시 해소" 라고 보고했다(손실 한도는 이미 -2.5%, 익절 기록은 SPEC-038 이
# DB 로 옮김, .env 는 이미 0600). 해소된 항목을 계속 경고하면 운영자는 게이트를
# 무시하게 되고, 그러면 진짜 게이트도 함께 무시된다. 실전 전환 판단을 떠받치는
# 신호이므로 거짓 양성은 그 자체로 결함이다 → 실측 기반으로 전환.

# 실거래 전 권장하는 일일 손실 한도 하한. 이보다 빡빡하면(예: -1.0%) 정상 ±2%
# 변동에도 halt 가 반복 트립되어 손절까지 막힌다.
_RECOMMENDED_DAILY_MAX_LOSS = -0.025


@dataclass(frozen=True)
class GoLiveGate:
    """실거래 준비 게이트 1건의 판정 결과.

    ``evidence`` 는 판정 근거가 된 실측값이다. 근거 없는 판정은 검증할 수 없고,
    검증할 수 없는 게이트는 결국 무시된다.
    """

    key: str
    message: str
    resolved: bool
    evidence: str


def _daily_max_loss() -> float:
    """실효 일일 손실 한도 (테스트 seam)."""
    from trading.config import RISK_DAILY_MAX_LOSS

    return RISK_DAILY_MAX_LOSS


def _gate_daily_loss_limit() -> GoLiveGate:
    limit = _daily_max_loss()
    resolved = limit <= _RECOMMENDED_DAILY_MAX_LOSS
    return GoLiveGate(
        key="daily_loss_limit",
        message=(
            f"일일 손실 한도 {limit * 100:.2f}%가 너무 빡빡 "
            "(config.py RISK_DAILY_MAX_LOSS). 정상 ±2% 변동에도 halt 반복 트립·"
            f"손절 차단 위험 → {_RECOMMENDED_DAILY_MAX_LOSS * 100:.1f}% 이하 권장."
        ),
        resolved=resolved,
        evidence=(
            f"RISK_DAILY_MAX_LOSS 실측 {limit * 100:.2f}% "
            f"(권장 {_RECOMMENDED_DAILY_MAX_LOSS * 100:.1f}% 이하)"
        ),
    )


def _gate_took_profit_persistence() -> GoLiveGate:
    """익절 기록이 재시작에도 살아남는지.

    SPEC-TRADING-038 이 메모리 dict 를 ``position_action_markers`` 테이블로
    옮겼다. 판정 근거는 watchdog 이 실제로 그 테이블 경로를 쓰는지 여부다.
    """
    try:
        from trading.watchers import position_watchdog

        source = inspect.getsource(position_watchdog._action_done_today)
        resolved = "position_action_markers" in source
        evidence = (
            "watchdog._action_done_today 가 position_action_markers 조회"
            if resolved
            else "watchdog._action_done_today 에 DB 조회 없음 (메모리 전용)"
        )
    except Exception as exc:  # 판정 불가 → 해소로 단정하지 않는다
        resolved = False
        evidence = f"판정 불가: {exc}"

    return GoLiveGate(
        key="took_profit_persistence",
        message=(
            "익절 기록이 메모리에만 존재. 컨테이너 재시작 시 같은 종목 절반 또 "
            "매도 위험 → DB 영속화 필요."
        ),
        resolved=resolved,
        evidence=evidence,
    )


def _gate_env_permissions() -> GoLiveGate:
    """.env 파일 권한이 소유자 전용(0600)인지."""
    try:
        from trading.config import project_root

        path = project_root() / ".env"
        mode = stat.S_IMODE(path.stat().st_mode)
        resolved = mode == 0o600
        # 판정된 경우에만 문제를 단정한다.
        message = f".env 권한이 {mode:04o} — 소유자 외에도 읽힌다 → chmod 600 .env 필요."
        evidence = f".env 권한 실측 {mode:04o}"
    except Exception as exc:
        # 컨테이너에는 .env 가 마운트되지 않는다(환경변수는 compose env_file 로
        # 주입). 파일이 안 보인다고 권한이 나쁘다고 단정하면 거짓 경보가 되고,
        # 좋다고 단정하면 거짓 안심이 된다. 확인 불가임을 그대로 말한다.
        resolved = False
        message = ".env 권한을 이 실행 환경에서 확인할 수 없다 → 호스트에서 직접 확인 필요."
        evidence = f"판정 불가: {exc}"

    return GoLiveGate(
        key="env_permissions",
        message=message,
        resolved=resolved,
        evidence=evidence,
    )


def _gate_credential_rotation() -> GoLiveGate:
    """키 회전 여부는 코드가 확인할 수 없다.

    발급 이력은 KIS·Anthropic·텔레그램 각 콘솔에만 있으므로, 여기서 해소로
    단정하면 거짓 안심을 준다. 항상 미해소로 남겨 운영자가 직접 판단하게 한다.
    """
    return GoLiveGate(
        key="credential_rotation",
        message=(
            "자격증명 평문 저장 (.env 의 KIS 실거래 키/시크릿·Anthropic·텔레그램·"
            "Postgres 비밀번호) → 실거래 전 키 재발급(rotation) 권장."
        ),
        resolved=False,
        evidence="코드로 확인 불가 — 발급 이력은 각 콘솔에만 존재(운영자 확인 필요)",
    )


def evaluate_go_live_gates() -> list[GoLiveGate]:
    """실거래 준비 게이트를 실측해 판정한다."""
    return [
        _gate_daily_loss_limit(),
        _gate_took_profit_persistence(),
        _gate_env_permissions(),
        _gate_credential_rotation(),
    ]


def render_go_live_gates(gates: list[GoLiveGate] | None = None) -> str:
    """미해소 게이트를 근거와 함께 렌더링한다.

    해소된 항목은 블로커로 나열하지 않되, 몇 건이 확인됐는지는 남긴다 —
    '점검을 안 한 것'과 '점검해서 통과한 것'은 구분돼야 한다.
    """
    gates = evaluate_go_live_gates() if gates is None else gates
    unresolved = [g for g in gates if not g.resolved]
    resolved = [g for g in gates if g.resolved]

    lines = ["【 실거래 준비 게이트 (엣지 판정과 무관 — 실거래 전 반드시 해소) 】"]
    if not unresolved:
        lines.append("  미해소 없음 — 아래 항목 전부 실측 확인됨.")
    for i, g in enumerate(unresolved, 1):
        lines.append(f"  {i}. {g.message}")
        lines.append(f"     └ 근거: {g.evidence}")
    for g in resolved:
        lines.append(f"  ✅ {g.key}: {g.evidence}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 렌더링
# ---------------------------------------------------------------------------


def _pf(pf: float) -> str:
    return "∞" if math.isinf(pf) else f"{pf:.2f}"


def _won(x: float) -> str:
    return f"{x:,.0f}원"


def limitations_footer(analytics: Analytics, *, time_weighted: bool) -> str:
    lines = [
        "─" * 48,
        "⚠️ 한계 (반드시 인지):",
        "  • 페이퍼 체결가 ≠ 실거래 체결가(슬리피지·시장충격 없음). 보정 수치는 비관 추정이며 "
        "실거래는 더 나쁠 수 있음.",
    ]
    if analytics.n_closed < _MIN_SAMPLE:
        lines.append(
            f"  • 표본 {analytics.n_closed}건 < {_MIN_SAMPLE}건 — 통계적 유의성 없음. 우연일 수 있음."
        )
    else:
        lines.append(f"  • 표본 {analytics.n_closed}건 — 과거 성적이지 미래 보장 아님.")
    if not time_weighted:
        lines.append(
            "  • 자산곡선/낙폭은 이벤트시간(청산일) 기준 — 캘린더 시간가중 아님(일별 스냅샷 누적 대기)."
        )
    if analytics.has_unrealized:
        lines.append("  • 미실현 평가손익은 청산되지 않은 추정치 — 실현 시 달라질 수 있음.")
    lines.append(
        "  • 양성 엣지가 보여도 소액·점진 실거래로 시작 권장."
    )
    return "\n".join(lines)


def render(
    analytics: Analytics,
    benchmark: Benchmark,
    card: Scorecard,
    *,
    days: int | None = None,
    confidence_text: str | None = None,
    time_weighted_text: str | None = None,
    time_weighted: bool = False,
) -> str:
    """전체 엣지 리포트 한국어 텍스트."""
    a = analytics
    period = f"최근 {days}일" if days else "전체 기간"
    out: list[str] = []
    out.append("━" * 48)
    out.append(f"📊 엣지 검증 리포트 — {period} (paper)")
    out.append("━" * 48)

    # 판정 헤드라인
    emoji = {
        VERDICT_GO: "🟢", VERDICT_WEAK_GO: "🟡",
        VERDICT_INCONCLUSIVE: "⚪", VERDICT_NO_GO: "🔴",
    }.get(card.verdict, "⚪")
    out.append(f"{emoji} 판정: {card.verdict}  (표본 등급: {card.grade})")
    for r in card.reasons:
        out.append(f"   • {r}")
    out.append("")

    if a.n_closed == 0:
        out.append("청산된 라운드트립이 없습니다. (매수 후 미청산이거나 데이터 없음)")
        if a.has_unrealized:
            out.append(f"미실현 평가손익(balance): {_won(a.unrealized_pnl)}")
        out.append("")
        out.append(limitations_footer(a, time_weighted=time_weighted))
        return "\n".join(out)

    # 수익성
    out.append("【 수익성 (실현, 수수료 차감) 】")
    out.append(f"  라운드트립: {a.n_closed}건  (승 {a.n_wins} / 패 {a.n_losses})")
    out.append(f"  승률: {a.win_rate*100:.1f}%")
    out.append(f"  총 실현 순손익: {_won(a.total_net_pnl)}")
    out.append(f"  손익비(profit factor): {_pf(a.profit_factor)}")
    out.append(f"  평균이익: {_won(a.avg_win)}  /  평균손실: {_won(a.avg_loss)}")
    out.append(f"  거래당 기대값: {_won(a.expectancy)}  (평균수익률 {a.avg_return_pct:+.2f}%)")
    out.append(f"  수수료 드래그: {_won(a.total_fees)}")
    out.append(
        f"  보유기간: 평균 {a.avg_holding_days:.1f}일 / 중앙값 {a.median_holding_days:.0f}일 "
        f"/ 최장 {a.max_holding_days}일"
    )
    out.append(f"  실현 자산곡선 최대낙폭(이벤트시간): {_won(a.realized_mdd_krw)}")
    out.append("")

    # 슬리피지 보정
    out.append("【 슬리피지·거래세 보정 (실거래 비관 추정) 】")
    out.append(f"  보정 차감액: -{_won(a.slippage_drag)}")
    out.append(f"  보정 후 총 순손익: {_won(a.total_net_pnl_adj)}")
    out.append(
        f"  보정 후 기대값(net expectancy): {_won(a.expectancy_adj)}"
        f"  /  손익비: {_pf(a.profit_factor_adj)}"  # REQ-044-C1
    )
    out.append("")

    # SPEC-TRADING-044 M3: 비용보정 엣지 지표 (REQ-044-C2)
    sortino_str = "∞" if math.isinf(a.sortino) else f"{a.sortino:.3f}"
    out.append("【 비용보정 엣지 지표 (SPEC-TRADING-044) 】")
    out.append(f"  Sortino 비율(MAR=0): {sortino_str}")
    out.append(f"  비용보정 승률: {a.cost_adjusted_win_rate*100:.1f}%"
               "  (round-trip 비용 초과 거래 비율)")
    out.append("")

    # 미실현 (Phase 2)
    if a.has_unrealized:
        out.append("【 미실현 포함 】")
        out.append(f"  미실현 평가손익(balance): {_won(a.unrealized_pnl)}")
        out.append(f"  실현+미실현 합산: {_won(a.total_pnl_incl_unrealized)}")
        out.append("")

    # 벤치마크
    out.append("【 KOSPI 매수후보유 대비 (원가기준 집계, 시간가중 아님) 】")
    if benchmark.available:
        out.append(
            f"  기간: {benchmark.start} ~ {benchmark.end}"
        )
        out.append(f"  전략 수익률(실투입 원가 대비): {benchmark.strategy_return_pct:+.2f}%")
        out.append(f"  KOSPI 수익률: {benchmark.kospi_return_pct:+.2f}%")
        out.append(f"  알파: {benchmark.alpha_pct:+.2f}%p")
    else:
        out.append("  KOSPI 데이터 없음 — 알파 미확인.")
    out.append("")

    # 미매칭 매도
    if a.n_unmatched_sells:
        out.append(
            f"⚠️ 매수 재고를 초과한 매도 {a.n_unmatched_sells}건 — FIFO 매칭 불가(데이터 정합성 확인 필요)."
        )
        out.append("")

    # Phase 2 confidence
    if confidence_text:
        out.append(confidence_text)
        out.append("")

    # Phase 3 time-weighted
    if time_weighted_text:
        out.append(time_weighted_text)
        out.append("")

    # 실거래 준비 게이트
    out.append(render_go_live_gates())
    out.append("")

    out.append(limitations_footer(a, time_weighted=time_weighted))
    return "\n".join(out)
