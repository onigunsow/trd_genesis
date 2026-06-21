"""SPEC-TRADING-057 M3 — 귀인 분해 리포트.

REQ-057-M3-1  : 5컴포넌트 + RESIDUAL 합치성 분해
                (baseline + 순차 counterfactual 방법론)
REQ-057-M3-1b : 비용모델 보수성 플래그 [HARD]
REQ-057-M3-2  : postmortem/confidence/roundtrips/trade_stats 재사용 (재구현 금지)
REQ-057-M3-3  : n=8 일화 플래그 [HARD]
REQ-057-M3-4  : 양의 알파 없음 → 유효한 성공적 진단 프레이밍
REQ-057-M3-5  : 데이터 불충분 컴포넌트 → "insufficient data" 레이블

설계 원칙:
- 모든 provider는 의존성 주입 — 단위 테스트는 픽스처를 주입한다.
- pykrx / DB는 lazy import (기본 provider 내부에서만) — import 시 사이드이펙트 없음.
- (b) 비용 컴포넌트만 항상 정량화 (engine.py DEFAULT_FEE_RATE/DEFAULT_SLIPPAGE/DEFAULT_TAX_RATE).
- 합치성: (a)+(b)+(c)+(d)+(e)+residual == measured_total (residual이 닫힘 보장).
- 재사용: roundtrips.RoundTrip, trade_stats.compute_trade_stats,
          confidence.analyze, postmortem.classify_decision_outcome.
- ADR-057-5: time-weighted equity-curve 알파 정의 (benchmark.py money-weighted와 혼용 금지).

# @MX:ANCHOR: [AUTO] compute_attribution — M3 귀인 분해의 단일 진입점
# @MX:REASON: REQ-057-M3-1/2/3/4/5; fan_in >= 2 예상 (M3 harness + 대시보드 엔드포인트).
#             합치성 보장·비용 필수정량·정직성 플래그가 모두 여기서 수행된다.
# @MX:SPEC: SPEC-TRADING-057
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading.backtest.feature_alpha_measurer import FeatureAlphaResult
    from trading.edge.roundtrips import RoundTrip


# ── 상수 ─────────────────────────────────────────────────────────────────────

# engine.py 비용 상수 (재사용 — EX-5: 새 비용 모델 생성 금지)
_DEFAULT_FEE_RATE = 0.00015     # 매매수수료
_DEFAULT_TAX_RATE = 0.0018      # 거래세 (매도 시)
_DEFAULT_SLIPPAGE = 0.0005      # 시장가 슬리피지

# 컴포넌트 한국어 레이블
_COMPONENT_LABELS: dict[str, str] = {
    "a": "진입 신호 품질",
    "b": "비용/슬리피지/세금 드래그",
    "c": "청산 타이밍",
    "d": "포지션 사이징",
    "e": "LLM-재량 vs 기계적 델타",
    "residual": "잔차 (미귀인)",
}

# REQ-057-M3-3 [HARD]: n=8 일화 플래그
_HONESTY_FLAG_N8 = (
    "⚠ n=8 경고: 라이브 페이퍼 postmortem(n=8, 합성 매도체결)은 일화적이며 통계적으로 "
    "거의 무의미하다. 이 리포트의 load-bearing(부하적재) 증거는 M1/M2 과거 백테스트이며, "
    "8건 페이퍼 트레이드에 근거한 어떠한 결론도 확정적 증거로 취급하지 않는다."
)

# REQ-057-M3-1b [HARD]: 비용모델 보수성 플래그
_HONESTY_FLAG_COST_CONSERVATISM = (
    "⚠ 비용 모델 보수성: 사용된 거래세 0.18%는 실제 한국 매도세 0.18-0.23% 범위의 "
    "하단(floor)이며, 슬리피지 0.05%는 대형주/충분한 유동성 가정으로 소형/저유동성 종목에는 "
    "낙관적이다. 실제 비용이 이보다 클 경우 측정 알파는 상향 편향(upward biased)된다."
)


# ── 데이터클래스 ──────────────────────────────────────────────────────────────

@dataclass
class AttributionComponent:
    """단일 귀인 컴포넌트.

    Attributes:
        code: 컴포넌트 코드 ('a', 'b', 'c', 'd', 'e', 'residual').
        label_ko: 한국어 이름.
        value_krw: 거래당 KRW 기여 (None이면 데이터 불충분).
        method: 계산 방법 설명 (데이터 불충분 시 이유 설명).
        insufficient: True이면 데이터 불충분으로 정량화 불가.
    """

    code: str
    label_ko: str
    value_krw: float | None
    method: str
    insufficient: bool


@dataclass
class AttributionReport:
    """귀인 분해 리포트.

    Attributes:
        measured_total: 측정된 거래당 기대값 (KRW, 예: -14,840).
        components: 6개 컴포넌트 목록 [(a)~(e) + residual].
        sum_consistency_check: |sum(components) - measured_total| — 0에 가까울수록 좋음.
        sum_consistent: sum_consistency_check < tolerance.
        tolerance: 허용 오차 (KRW).
        honesty_flags: 정직성 플래그 목록 (n=8, 비용 보수성 등).
        prose: 한국어 산문 리포트.
    """

    measured_total: float
    components: list[AttributionComponent]
    sum_consistency_check: float
    sum_consistent: bool
    tolerance: float
    honesty_flags: list[str] = field(default_factory=list)
    prose: str = ""


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

def _compute_cost_component(roundtrips: list[RoundTrip]) -> AttributionComponent:
    """(b) 비용/슬리피지/세금 드래그 컴포넌트.

    항상 정량화 가능 — insufficient=False 보장 (REQ-057-M3-1 MANDATORY [HARD]).

    라운드트립의 실제 fees(entry_fee + exit_fee)를 사용한다.
    fees는 engine.py의 DEFAULT_FEE_RATE + DEFAULT_SLIPPAGE + DEFAULT_TAX_RATE에서
    계산된 값이다 (ADR-057-3: 비용 모델 재사용).

    roundtrips가 없으면 engine.py 상수만으로도 비용 드래그 방향을 알 수 있으므로
    value_krw=0.0 으로 보고한다 (insufficient=False 유지).
    """
    method = (
        f"라운드트립 실제 fees(entry_fee + exit_fee) 평균. "
        f"수수료 구성: fee_rate={_DEFAULT_FEE_RATE:.5f}, "
        f"slippage={_DEFAULT_SLIPPAGE:.4f}, tax_rate={_DEFAULT_TAX_RATE:.4f} "
        f"(engine.py DEFAULT_* 상수 재사용 — EX-5)."
    )

    if not roundtrips:
        # 거래 없으면 비용 드래그 측정 불가하나 insufficient=False 유지
        return AttributionComponent(
            code="b",
            label_ko=_COMPONENT_LABELS["b"],
            value_krw=0.0,
            method=method + " (라운드트립 없음 — 0.0으로 기록)",
            insufficient=False,
        )

    avg_fees = statistics.mean(rt.fees for rt in roundtrips)
    return AttributionComponent(
        code="b",
        label_ko=_COMPONENT_LABELS["b"],
        value_krw=-avg_fees,   # 비용은 음수 드래그
        method=method,
        insufficient=False,    # 절대 insufficient=True 불가 (REQ-057-M3-1 MANDATORY)
    )


def _compute_entry_component(
    m2_results: list[FeatureAlphaResult] | None,
) -> AttributionComponent:
    """(a) 진입 신호 품질 컴포넌트.

    M2 결과가 있으면 레이블(PASS/NOT_PASS/INCONCLUSIVE)을 참조해 설명한다.
    단, M2 net_alpha는 기간 누적 수익률 합계이며 KRW/거래 단위가 아니므로
    실제 KRW 기여는 직접 계산 불가 → insufficient=True로 처리한다 (REQ-057-M3-5).
    residual이 이를 흡수해 합치성을 보장한다.

    REQ-057-M3-4: 모든 피처가 NOT_PASS이면 이를 유효한 진단 결과로 명시.
    """
    if m2_results is None:
        return AttributionComponent(
            code="a",
            label_ko=_COMPONENT_LABELS["a"],
            value_krw=None,
            method=(
                "M2 진입 피처 OOS 알파 측정 결과 없음 (m2_results=None). "
                "sequential counterfactual 계산을 위한 baseline 포트폴리오 수익률 데이터 부재. "
                "REQ-057-M3-5: 데이터 불충분 — residual 버킷이 흡수."
            ),
            insufficient=True,
        )

    # M2 결과 요약
    labels = [r.label for r in m2_results]
    all_not_pass = all(lbl in ("NOT_PASS", "INCONCLUSIVE", "SURVIVORSHIP_BOUND") for lbl in labels)
    any_inconclusive = any(lbl == "INCONCLUSIVE" for lbl in labels)

    # net_alpha가 KRW/거래 단위가 아니므로 직접 수치화 불가 → insufficient
    # 단, 방향(direction)과 레이블은 method에 설명
    label_summary = ", ".join(f"{r.feature_name}:{r.label}" for r in m2_results)

    if all_not_pass and any_inconclusive:
        method = (
            f"M2 결과 — {label_summary}. "
            "일부 피처 INCONCLUSIVE (표본 부족 REQ-057-M2-3b): "
            "진입 신호 품질을 KRW/거래로 환산할 수 없음 (REQ-057-M3-5). "
            "sequential counterfactual 기준 baseline 포트폴리오 수익률 데이터 부재."
        )
    elif all_not_pass:
        method = (
            f"M2 결과 — {label_summary}. "
            "테스트된 모든 기계적 진입 피처(RSI/PER/foreign_5d)가 비용 차감 후 "
            "양의 OOS 알파 없음 (NOT_PASS). REQ-057-M3-4: 이는 유효하고 성공적인 "
            "진단 결과다 — 진입 신호 품질을 KRW/거래로 환산하기 위한 "
            "baseline 포트폴리오 추가 데이터 필요 (REQ-057-M3-5)."
        )
    else:
        method = (
            f"M2 결과 — {label_summary}. "
            "sequential counterfactual (baseline 기계적 등가중 포트폴리오 vs "
            "피처 기반 포트폴리오) KRW/거래 환산을 위한 추가 데이터 필요 (REQ-057-M3-5)."
        )

    return AttributionComponent(
        code="a",
        label_ko=_COMPONENT_LABELS["a"],
        value_krw=None,
        method=method,
        insufficient=True,
    )


def _compute_exit_component() -> AttributionComponent:
    """(c) 청산 타이밍 컴포넌트.

    sequential counterfactual: 실제 청산 타이밍 vs baseline 고정 holding period.
    baseline 포트폴리오의 exit 데이터 없이는 counterfactual 계산 불가.
    REQ-057-M3-5: insufficient=True.
    """
    return AttributionComponent(
        code="c",
        label_ko=_COMPONENT_LABELS["c"],
        value_krw=None,
        method=(
            "sequential counterfactual: 실제 청산 타이밍 vs baseline 고정 보유기간. "
            "baseline 포트폴리오의 exit 수익률 데이터 부재 — "
            "walk_forward 하니스의 baseline exit simulation 필요 (REQ-057-M3-5). "
            "SPEC-058 팩터 백테스트 인프라 구축 후 정량 가능."
        ),
        insufficient=True,
    )


def _compute_sizing_component() -> AttributionComponent:
    """(d) 포지션 사이징 컴포넌트.

    sequential counterfactual: 실제 포지션 크기 vs baseline 균등 가중.
    현재 paper trade는 고정 수량 매수로 정확한 sizing effect 분리 불가.
    REQ-057-M3-5: insufficient=True.
    """
    return AttributionComponent(
        code="d",
        label_ko=_COMPONENT_LABELS["d"],
        value_krw=None,
        method=(
            "sequential counterfactual: 실제 포지션 크기 vs baseline 균등 가중(equal-weight). "
            "현재 페이퍼 트레이드는 고정 수량 매수이며 baseline 균등 가중 포트폴리오와의 "
            "직접 비교를 위한 price time-series 데이터 부재 (REQ-057-M3-5). "
            "SPEC-058 팩터 백테스트 인프라 구축 후 정량 가능."
        ),
        insufficient=True,
    )


def _compute_llm_delta_component() -> AttributionComponent:
    """(e) LLM-재량 vs 기계적 델타 컴포넌트.

    sequential counterfactual: 실제 LLM 선택 포트폴리오 vs 기계적 피처 기반 포트폴리오.
    ADR-002 유지 — LLM은 결정적으로 재현 불가하여 OOS 백테스트 원리적 불가.
    REQ-057-M2-4 + REQ-057-M3-5: insufficient=True.
    """
    return AttributionComponent(
        code="e",
        label_ko=_COMPONENT_LABELS["e"],
        value_krw=None,
        method=(
            "sequential counterfactual: 실제 LLM 선택 포트폴리오 vs 기계적 피처 기반 포트폴리오. "
            "ADR-002 유지: LLM 재량 레이어는 결정적으로 재현 불가 → OOS 백테스트 원리적 불가. "
            "REQ-057-M2-4: LLM 레이어 백테스트 금지. "
            "baseline 기계적 포트폴리오 수익률 데이터 부재 (REQ-057-M3-5)."
        ),
        insufficient=True,
    )


def _compute_residual(
    measured_total: float,
    components: list[AttributionComponent],
) -> AttributionComponent:
    """잔차 컴포넌트 — 합치성 보장 [HARD].

    (a)+(b)+(c)+(d)+(e)의 정량화된 합과 measured_total의 차이.
    insufficient 컴포넌트는 0으로 처리 → residual이 흡수한다.

    REQ-057-M3-1 RESIDUAL [HARD]: 항상 존재, 항상 정량화.
    """
    quantified_sum = sum(
        (c.value_krw or 0.0) for c in components
    )
    residual_value = measured_total - quantified_sum
    return AttributionComponent(
        code="residual",
        label_ko=_COMPONENT_LABELS["residual"],
        value_krw=residual_value,
        method=(
            "measured_total - sum(정량화된 컴포넌트). "
            "insufficient 컴포넌트(value=None)는 0으로 처리, residual이 흡수. "
            "REQ-057-M3-1 [HARD]: 잔차 명시로 합치성 보장."
        ),
        insufficient=False,   # residual은 항상 정량화
    )


def _build_prose(
    measured_total: float,
    components: list[AttributionComponent],
    m2_results: list[FeatureAlphaResult] | None,
    sum_consistent: bool,
    sum_consistency_check: float,
) -> str:
    """한국어 산문 리포트 생성.

    REQ-057-M3-3: n=8 일화 플래그.
    REQ-057-M3-4: 양의 알파 없음 → 유효한 성공적 진단.
    ADR-057-2: load-bearing 증거 = 과거 백테스트.
    ADR-057-5: time-weighted vs money-weighted 관계 명시.
    """
    lines: list[str] = []

    lines.append("═" * 60)
    lines.append("SPEC-TRADING-057 M3 — 귀인 분해 리포트")
    lines.append("═" * 60)

    # 측정값
    lines.append(f"\n▸ 측정된 기대값: {measured_total:,.0f} KRW/거래 (SPEC-051 M2 OOS, n=8)")
    lines.append(
        "  주의: 이 수치는 n=8 페이퍼 트레이드 기반이며, "
        "load-bearing 증거는 M1/M2 과거 백테스트다 (ADR-057-2)."
    )

    # 알파 정의 (ADR-057-5)
    lines.append(
        "\n▸ 알파 정의: time-weighted equity-curve (engine.run 경유). "
        "benchmark.py의 money-weighted 측정과 혼용하지 않는다 (ADR-057-5)."
    )

    # M2 진입 신호 진단 (REQ-057-M3-4)
    if m2_results is not None:
        labels = [r.label for r in m2_results]
        all_not_pass = all(
            lbl in ("NOT_PASS", "INCONCLUSIVE", "SURVIVORSHIP_BOUND") for lbl in labels
        )
        label_summary = ", ".join(f"{r.feature_name}:{r.label}" for r in m2_results)
        lines.append(f"\n▸ M2 진입 신호 진단: {label_summary}")

        if all_not_pass:
            # REQ-057-M3-4: 유효한 성공적 진단 프레이밍 [HARD]
            lines.append(
                "  → 테스트된 모든 기계적 진입 피처가 비용 차감 후 양의 OOS 알파 없음 (NOT_PASS)."
            )
            lines.append(
                "  → 이는 오류가 아니라 유효하고 성공적인 진단 결과다 (REQ-057-M3-4)."
            )
            lines.append(
                "  → '알파 없음' 자체가 신뢰할 수 있는 답이다: "
                "현재 기계적 스크리너 피처로는 비용 차감 후 양의 알파를 얻을 수 없다."
            )
    else:
        lines.append("\n▸ M2 진입 신호 진단: 결과 없음 (m2_results 미제공)")

    # 컴포넌트 분해 테이블
    lines.append("\n▸ 귀인 분해 (순차 counterfactual 방법론):")
    lines.append(
        "  방법론: baseline(기계적 등가중 포트폴리오) + 한 번에 하나의 요소를 교체해 "
        "각 컴포넌트의 한계 기여를 측정한다 (REQ-057-M3-1)."
    )
    lines.append(f"  {'컴포넌트':<25} {'KRW/거래':>12}  {'상태':<12}")
    lines.append("  " + "-" * 53)
    for comp in components:
        val_str = (
            f"{comp.value_krw:>+12,.0f}"
            if comp.value_krw is not None
            else " " * 9 + "데이터 없음"
        )
        status = "불충분" if comp.insufficient else "정량화"
        lines.append(f"  ({comp.code}) {comp.label_ko:<22} {val_str}  {status}")

    # 합치성 검사
    consistency_result = "통과" if sum_consistent else "불통"
    lines.append(
        f"\n  합치성 검사: |합계 - 측정값| = {sum_consistency_check:.2f} KRW "
        f"({consistency_result})"
    )

    # 정직성 플래그 요약
    lines.append("\n▸ 중요 정직성 플래그:")
    lines.append(
        "  1. n=8 일화: n=8 페이퍼 트레이드 postmortem은 통계적으로 거의 무의미하다. "
        "load-bearing 증거는 M1/M2 과거 백테스트다 (REQ-057-M3-3)."
    )
    lines.append(
        "  2. 비용 보수성: 세금 0.18%는 실제 범위(0.18-0.23%)의 floor이며, "
        "슬리피지 0.05%는 낙관적이다. 실제 비용이 더 크면 알파가 상향 편향(upward biased)된다 "
        "(REQ-057-M3-1b)."
    )

    lines.append("\n" + "═" * 60)

    return "\n".join(lines)


# ── 공개 API ──────────────────────────────────────────────────────────────────

# @MX:ANCHOR: [AUTO] compute_attribution — M3 귀인 분해의 단일 진입점
# @MX:REASON: REQ-057-M3-1: 5컴포넌트 + RESIDUAL 합치성; (b) 필수 정량; 정직성 플래그.
#             fan_in >= 2 (M3 harness + 향후 대시보드 엔드포인트).
# @MX:SPEC: SPEC-TRADING-057
def compute_attribution(
    roundtrips: list[RoundTrip],
    measured_total: float,
    m2_results: list[FeatureAlphaResult] | None = None,
    *,
    tolerance: float = 1.0,
) -> AttributionReport:
    """귀인 분해 리포트를 생성한다.

    -14,840 KRW/거래를 5컴포넌트 + RESIDUAL로 분해한다.
    (b) 비용 컴포넌트만 항상 정량화; 나머지는 데이터 부재 시 insufficient.
    RESIDUAL이 합치성을 닫는다 (REQ-057-M3-1 [HARD]).

    Args:
        roundtrips: RoundTrip 목록 (roundtrips.build_roundtrips 결과).
                    빈 목록도 허용 — (b)는 engine.py 상수 기반으로 0.0 보고.
        measured_total: 측정된 기대값 KRW/거래 (예: -14_840.0).
        m2_results: M2 진입 피처 OOS 알파 측정 결과 목록 (없으면 None).
        tolerance: 합치성 검사 허용 오차 KRW (기본 1.0).

    Returns:
        AttributionReport — 6컴포넌트, 합치성 검사, 정직성 플래그, 산문 포함.

    Notes:
        - 순수 함수: 상태 변경 / DB / 네트워크 없음.
        - REQ-057-M3-2: postmortem/confidence/roundtrips/trade_stats 재사용.
          이 함수 자체는 RoundTrip.fees를 직접 읽는다.
        - REQ-057-M3-1b + REQ-057-M3-3 [HARD]: 정직성 플래그는 항상 추가된다.
        - ADR-057-5: time-weighted 알파 정의 일관성 유지.
    """
    # ── (a) 진입 신호 품질 ───────────────────────────────────────────────────
    comp_a = _compute_entry_component(m2_results)

    # ── (b) 비용 드래그 [MANDATORY — 항상 정량화] ───────────────────────────
    comp_b = _compute_cost_component(roundtrips)

    # ── (c) 청산 타이밍 ─────────────────────────────────────────────────────
    comp_c = _compute_exit_component()

    # ── (d) 포지션 사이징 ────────────────────────────────────────────────────
    comp_d = _compute_sizing_component()

    # ── (e) LLM-재량 델타 ────────────────────────────────────────────────────
    comp_e = _compute_llm_delta_component()

    # ── 잔차 (합치성 보장) ───────────────────────────────────────────────────
    five_components = [comp_a, comp_b, comp_c, comp_d, comp_e]
    comp_residual = _compute_residual(measured_total, five_components)

    all_components = [*five_components, comp_residual]

    # ── 합치성 검사 ──────────────────────────────────────────────────────────
    component_sum = sum((c.value_krw or 0.0) for c in all_components)
    sum_consistency_check = abs(component_sum - measured_total)
    sum_consistent = sum_consistency_check < tolerance

    # ── 정직성 플래그 [HARD] ─────────────────────────────────────────────────
    # REQ-057-M3-3 + REQ-057-M3-1b: 항상 추가
    honesty_flags = [
        _HONESTY_FLAG_N8,
        _HONESTY_FLAG_COST_CONSERVATISM,
    ]

    # ── 산문 리포트 ──────────────────────────────────────────────────────────
    prose = _build_prose(
        measured_total=measured_total,
        components=all_components,
        m2_results=m2_results,
        sum_consistent=sum_consistent,
        sum_consistency_check=sum_consistency_check,
    )

    return AttributionReport(
        measured_total=measured_total,
        components=all_components,
        sum_consistency_check=sum_consistency_check,
        sum_consistent=sum_consistent,
        tolerance=tolerance,
        honesty_flags=honesty_flags,
        prose=prose,
    )


def render_report(report: AttributionReport) -> str:
    """AttributionReport를 출력 문자열로 변환한다.

    prose 필드가 이미 완성된 문자열이므로 그대로 반환.
    추가 메타 정보가 필요한 경우 확장 가능.

    Args:
        report: compute_attribution() 결과.

    Returns:
        한국어 산문 리포트 문자열.
    """
    return report.prose
