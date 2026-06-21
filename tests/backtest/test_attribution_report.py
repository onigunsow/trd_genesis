"""SPEC-TRADING-057 M3 — 귀인 분해 리포트 단위 테스트 (RED-first).

REQ-057-M3-1  : 5컴포넌트 + RESIDUAL 합치성 분해 (baseline + 순차 counterfactual)
REQ-057-M3-1b : 비용모델 보수성 플래그 (HARD)
REQ-057-M3-2  : postmortem/confidence/roundtrips/trade_stats 재사용 (재구현 금지)
REQ-057-M3-3  : n=8 일화 플래그 (HARD)
REQ-057-M3-4  : 양의 알파 없음 → 유효한 성공적 진단 프레이밍
REQ-057-M3-5  : 데이터 불충분 컴포넌트 → "insufficient data" 레이블

설계 원칙:
- 모든 테스트는 픽스처 주입으로 실행 — pykrx/DB/네트워크 불필요.
- RoundTrip 픽스처는 roundtrips.py의 데이터클래스를 직접 생성한다.
- M2 결과(FeatureAlphaResult) 픽스처는 feature_alpha_measurer.py에서 생성.
- 합치성: (a)+(b)+(c)+(d)+(e)+residual == measured_total (허용 오차 내).
- (b)는 항상 정량화 — insufficient=False 보장 (HARD).
"""
from __future__ import annotations

import re
from datetime import date

from trading.edge.roundtrips import RoundTrip

# ── 픽스처 헬퍼 ────────────────────────────────────────────────────────────────

def _make_roundtrip(
    ticker: str = "005930",
    entry_price: float = 60_000.0,
    exit_price: float = 62_000.0,
    qty: int = 10,
    entry_fee: float = 90.0,
    exit_fee: float = 120.0,
    confidence: float | None = 0.8,
    verdict: str | None = "APPROVE",
    persona: str | None = "macro",
) -> RoundTrip:
    """단순 픽스처 라운드트립 생성."""
    return RoundTrip(
        ticker=ticker,
        entry_date=date(2025, 1, 2),
        exit_date=date(2025, 1, 22),
        qty=qty,
        entry_price=entry_price,
        exit_price=exit_price,
        entry_fee=entry_fee,
        exit_fee=exit_fee,
        confidence=confidence,
        verdict=verdict,
        persona=persona,
    )


def _make_roundtrips(n: int = 8) -> list[RoundTrip]:
    """n개 픽스처 라운드트립 목록 (n=8 기본 = 실제 페이퍼 트레이드 수)."""
    return [
        _make_roundtrip(
            ticker=f"0000{i:02d}",
            entry_price=50_000.0 + i * 1_000,
            exit_price=49_000.0 + i * 1_000,   # 손실 시나리오
            qty=5,
            entry_fee=75.0,
            exit_fee=100.0,
        )
        for i in range(n)
    ]


def _make_m2_no_positive_alpha() -> list:
    """M2 결과: 모든 피처 NOT_PASS (양의 알파 없음 시나리오)."""
    from trading.backtest.feature_alpha_measurer import FeatureAlphaResult
    return [
        FeatureAlphaResult(
            feature_name=name,
            label="NOT_PASS",
            net_alpha=-0.05,
            p_value=0.45,
            bonferroni_threshold=0.0167,
            rebalance_count=40,
            sample_floor=30,
            survivorship_biased=False,
            bound_only=False,
            detail=f"{name}: 알파 음수",
        )
        for name in ("rsi", "per", "foreign_5d")
    ]


def _make_m2_inconclusive() -> list:
    """M2 결과: 모든 피처 INCONCLUSIVE (표본 부족)."""
    from trading.backtest.feature_alpha_measurer import FeatureAlphaResult
    return [
        FeatureAlphaResult(
            feature_name="rsi",
            label="INCONCLUSIVE",
            net_alpha=None,
            p_value=None,
            bonferroni_threshold=0.0167,
            rebalance_count=10,
            sample_floor=30,
            survivorship_biased=False,
            bound_only=False,
            detail="표본 부족",
        )
    ]


# ── 모듈 임포트 가능성 테스트 ───────────────────────────────────────────────────

class TestImportability:
    """pykrx / DB 없이 모듈이 임포트 가능해야 한다."""

    def test_module_importable_without_pykrx(self):
        """pykrx 없이 attribution_report 임포트 가능."""
        import sys
        # pykrx를 sys.modules에서 임시 제거해도 임포트가 성공해야 한다.
        pykrx_mod = sys.modules.pop("pykrx", None)
        try:
            # 이미 임포트된 경우를 위해 캐시도 제거
            mod_key = "trading.backtest.attribution_report"
            cached = sys.modules.pop(mod_key, None)
            from trading.backtest import attribution_report  # noqa: F401
            # 재캐시
            if cached is not None:
                sys.modules[mod_key] = cached
        finally:
            if pykrx_mod is not None:
                sys.modules["pykrx"] = pykrx_mod

    def test_module_importable_without_db(self):
        """DB 접속 없이 attribution_report 임포트 가능 (top-level DB 호출 없음)."""
        # 임포트만으로 예외가 발생하지 않아야 한다.
        from trading.backtest import attribution_report
        assert attribution_report is not None

    def test_compute_attribution_callable(self):
        """compute_attribution 함수가 존재하고 callable해야 한다."""
        from trading.backtest.attribution_report import compute_attribution
        assert callable(compute_attribution)

    def test_attribution_report_dataclass_importable(self):
        """AttributionReport 데이터클래스가 임포트 가능해야 한다."""
        from trading.backtest.attribution_report import AttributionReport
        assert AttributionReport is not None

    def test_attribution_component_dataclass_importable(self):
        """AttributionComponent 데이터클래스가 임포트 가능해야 한다."""
        from trading.backtest.attribution_report import AttributionComponent
        assert AttributionComponent is not None


# ── 리포트 구조 테스트 ─────────────────────────────────────────────────────────

class TestReportStructure:
    """AttributionReport의 기본 구조 계약."""

    def test_measured_total_stored_in_report(self):
        """measured_total이 입력값 그대로 리포트에 저장된다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0)
        assert report.measured_total == -14_840.0

    def test_report_has_six_components(self):
        """컴포넌트 목록에 (a)(b)(c)(d)(e) + residual = 6개가 있어야 한다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0)
        assert len(report.components) == 6

    def test_component_codes_include_all_five_letters(self):
        """컴포넌트 코드에 a, b, c, d, e, residual이 모두 있어야 한다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0)
        codes = {c.code for c in report.components}
        assert codes == {"a", "b", "c", "d", "e", "residual"}

    def test_report_has_prose_field(self):
        """prose 필드가 있고 비어있지 않아야 한다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0)
        assert isinstance(report.prose, str)
        assert len(report.prose) > 0

    def test_report_has_honesty_flags(self):
        """honesty_flags 목록이 있어야 한다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0)
        assert isinstance(report.honesty_flags, list)

    def test_component_label_ko_not_empty(self):
        """모든 컴포넌트의 label_ko가 비어 있지 않아야 한다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0)
        for comp in report.components:
            assert len(comp.label_ko) > 0, f"component {comp.code!r} has empty label_ko"

    def test_component_method_not_empty(self):
        """모든 컴포넌트의 method 필드가 비어 있지 않아야 한다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0)
        for comp in report.components:
            assert len(comp.method) > 0, f"component {comp.code!r} has empty method"


# ── 합치성(Sum Consistency) 테스트 ────────────────────────────────────────────

class TestSumConsistency:
    """(a)+(b)+(c)+(d)+(e)+residual == measured_total (REQ-057-M3-1 RESIDUAL)."""

    def test_sum_consistency_within_tolerance(self):
        """컴포넌트 합계 + residual == measured_total (허용 오차 내)."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        measured = -14_840.0
        report = compute_attribution(rts, measured_total=measured, tolerance=1.0)

        # 정량화된 컴포넌트(residual 포함) 전체 합산
        total = 0.0
        for comp in report.components:
            v = comp.value_krw if comp.value_krw is not None else 0.0
            total += v

        assert abs(total - measured) < 1.0, (
            f"sum={total:.2f}, measured={measured:.2f}, diff={abs(total-measured):.4f}"
        )

    def test_sum_consistent_flag_true_when_within_tolerance(self):
        """sum_consistent == True のとき |check| < tolerance."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0, tolerance=1.0)
        assert report.sum_consistent is True

    def test_sum_consistency_check_value_near_zero(self):
        """sum_consistency_check 절댓값이 허용 오차 내여야 한다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0, tolerance=1.0)
        assert report.sum_consistency_check < 1.0, (
            f"sum_consistency_check={report.sum_consistency_check:.4f} >= tolerance=1.0"
        )

    def test_residual_component_present(self):
        """residual 컴포넌트가 명시적으로 존재해야 한다 (REQ-057-M3-1 RESIDUAL HARD)."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0)
        residual_comps = [c for c in report.components if c.code == "residual"]
        assert len(residual_comps) == 1, "residual 컴포넌트가 정확히 1개 있어야 한다"

    def test_residual_has_quantified_value(self):
        """residual 컴포넌트는 insufficient=False이어야 한다 (항상 닫힘)."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0)
        residual = next(c for c in report.components if c.code == "residual")
        assert residual.insufficient is False
        assert residual.value_krw is not None

    def test_sum_consistency_with_zero_measured_total(self):
        """측정값이 0일 때도 합치성이 성립한다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(4)
        report = compute_attribution(rts, measured_total=0.0, tolerance=1.0)
        total = sum(
            (c.value_krw or 0.0) for c in report.components
        )
        assert abs(total - 0.0) < 1.0

    def test_sum_consistency_with_positive_measured_total(self):
        """측정값이 양수일 때도 합치성이 성립한다 (이익 시나리오)."""
        from trading.backtest.attribution_report import compute_attribution
        rts = [
            _make_roundtrip(
                entry_price=50_000.0,
                exit_price=55_000.0,  # 이익
                entry_fee=75.0,
                exit_fee=100.0,
            )
        ]
        report = compute_attribution(rts, measured_total=5_000.0 - 175.0, tolerance=1.0)
        total = sum((c.value_krw or 0.0) for c in report.components)
        assert abs(total - report.measured_total) < 1.0

    def test_tolerance_stored_in_report(self):
        """tolerance 값이 리포트에 저장된다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(3)
        report = compute_attribution(rts, measured_total=-1_000.0, tolerance=5.0)
        assert report.tolerance == 5.0


# ── 비용 컴포넌트 (b) 테스트 ──────────────────────────────────────────────────

class TestCostComponent:
    """(b) 비용/슬리피지/세금 드래그는 항상 정량화 (REQ-057-M3-1 MANDATORY, HARD)."""

    def _get_cost_comp(self, report) -> object:
        return next(c for c in report.components if c.code == "b")

    def test_cost_component_always_quantified_never_insufficient(self):
        """(b) 비용 컴포넌트는 절대 insufficient=True가 될 수 없다 (HARD)."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0)
        comp = self._get_cost_comp(report)
        assert comp.insufficient is False, "(b) 비용 컴포넌트는 항상 정량화되어야 한다"

    def test_cost_component_value_is_not_none(self):
        """(b) value_krw가 None이 아니어야 한다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0)
        comp = self._get_cost_comp(report)
        assert comp.value_krw is not None

    def test_cost_component_is_negative_drag(self):
        """(b) 비용은 음수 드래그 — 항상 <= 0이어야 한다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0)
        comp = self._get_cost_comp(report)
        assert comp.value_krw is not None
        assert comp.value_krw <= 0.0, f"(b) 비용은 0 이하여야 한다, got {comp.value_krw}"

    def test_cost_component_derived_from_roundtrip_fees(self):
        """(b) 값이 roundtrips의 fees(entry_fee + exit_fee)로부터 계산된다."""
        from trading.backtest.attribution_report import compute_attribution
        # 정확한 수수료 픽스처
        rts = [
            _make_roundtrip(entry_fee=100.0, exit_fee=200.0),
            _make_roundtrip(entry_fee=50.0, exit_fee=100.0),
        ]
        expected_avg_fees = (100.0 + 200.0 + 50.0 + 100.0) / 2   # = 225.0 per trade
        report = compute_attribution(rts, measured_total=-14_840.0)
        comp = self._get_cost_comp(report)
        assert comp.value_krw is not None
        # 비용은 음수이므로 절댓값 비교
        assert abs(abs(comp.value_krw) - expected_avg_fees) < 1.0, (
            f"예상 비용={expected_avg_fees:.2f}, 실제={comp.value_krw:.2f}"
        )

    def test_cost_component_quantified_even_when_all_others_insufficient(self):
        """(b)는 다른 컴포넌트가 모두 insufficient이어도 항상 정량화된다."""
        from trading.backtest.attribution_report import compute_attribution
        # m2_results=None → (a)는 insufficient
        # 다른 컴포넌트들도 insufficient 가능
        rts = _make_roundtrips(3)
        report = compute_attribution(rts, measured_total=-5_000.0, m2_results=None)
        comp = self._get_cost_comp(report)
        assert comp.insufficient is False
        assert comp.value_krw is not None

    def test_cost_component_references_engine_constants_in_method(self):
        """(b) method 필드에 engine.py 상수(fee_rate/slippage/tax) 언급이 있어야 한다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0)
        comp = self._get_cost_comp(report)
        method_lower = comp.method.lower()
        # fee_rate, slippage, tax 중 하나 이상이 언급되어야 한다
        assert any(
            kw in method_lower
            for kw in ("fee", "slippage", "tax", "수수료", "세금", "슬리피지")
        ), f"method에 비용 모델 언급 없음: {comp.method!r}"


# ── Insufficient Data 처리 테스트 ─────────────────────────────────────────────

class TestInsufficientData:
    """데이터 불충분 컴포넌트 → 'insufficient data' 레이블 (REQ-057-M3-5)."""

    def test_entry_component_insufficient_when_no_m2_results(self):
        """M2 결과 없으면 (a) 진입 신호 품질은 insufficient이어야 한다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0, m2_results=None)
        comp_a = next(c for c in report.components if c.code == "a")
        assert comp_a.insufficient is True

    def test_insufficient_component_value_is_none(self):
        """insufficient 컴포넌트의 value_krw는 None이어야 한다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0, m2_results=None)
        comp_a = next(c for c in report.components if c.code == "a")
        if comp_a.insufficient:
            assert comp_a.value_krw is None

    def test_residual_absorbs_insufficient_components(self):
        """insufficient 컴포넌트가 많을수록 residual이 더 크다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        measured = -14_840.0
        # m2_results=None → (a) insufficient → residual이 더 큰 절댓값을 가짐
        report_no_m2 = compute_attribution(rts, measured_total=measured, m2_results=None)
        residual_no_m2 = next(c for c in report_no_m2.components if c.code == "residual")

        # (b)만 정량화 + residual이 나머지를 흡수해야 합치성이 성립
        assert residual_no_m2.value_krw is not None
        # 합치성은 여전히 보장되어야 한다
        total = sum((c.value_krw or 0.0) for c in report_no_m2.components)
        assert abs(total - measured) < 1.0

    def test_exit_timing_insufficient_without_baseline_data(self):
        """(c) 청산 타이밍 컴포넌트는 baseline exit 데이터 없으면 insufficient."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0)
        comp_c = next(c for c in report.components if c.code == "c")
        # 기본 상태(baseline exit 데이터 없음)에서는 insufficient이어야 한다
        assert comp_c.insufficient is True

    def test_sizing_insufficient_without_baseline_data(self):
        """(d) 포지션 사이징 컴포넌트는 baseline 사이징 데이터 없으면 insufficient."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0)
        comp_d = next(c for c in report.components if c.code == "d")
        assert comp_d.insufficient is True

    def test_llm_delta_insufficient_without_baseline_data(self):
        """(e) LLM-재량 델타 컴포넌트는 기계적 비교 데이터 없으면 insufficient."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0)
        comp_e = next(c for c in report.components if c.code == "e")
        assert comp_e.insufficient is True

    def test_insufficient_component_has_method_explanation(self):
        """insufficient 컴포넌트도 method 필드가 있어야 한다 (왜 insufficient인지 설명)."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0, m2_results=None)
        for comp in report.components:
            if comp.insufficient:
                assert len(comp.method) > 0, (
                    f"insufficient 컴포넌트 {comp.code!r}에 method 설명 없음"
                )


# ── 정직성 플래그 테스트 ──────────────────────────────────────────────────────

class TestHonestyFlags:
    """정직성 플래그 (REQ-057-M3-1b, REQ-057-M3-3, REQ-057-M3-4)."""

    def test_n8_honesty_flag_present(self):
        """n=8 일화 플래그가 honesty_flags에 있어야 한다 (REQ-057-M3-3 HARD)."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0)
        # "n=8" 또는 "8건" 언급이 있는 플래그
        found = any("n=8" in f or "8건" in f or "8 " in f for f in report.honesty_flags)
        assert found, (
            f"n=8 일화 플래그가 없다. flags={report.honesty_flags}"
        )

    def test_n8_flag_mentions_anecdotal_or_load_bearing(self):
        """n=8 플래그가 '일화적' 또는 '부하적재'/'load-bearing'을 언급해야 한다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0)
        keywords = ("일화", "load-bearing", "부하적재", "통계적으로", "M1", "M2", "백테스트")
        found_flag = None
        for f in report.honesty_flags:
            if "n=8" in f or "8건" in f or "8 " in f:
                found_flag = f
                break
        assert found_flag is not None, "n=8 플래그 없음"
        assert any(kw in found_flag for kw in keywords), (
            f"n=8 플래그가 관련 키워드를 포함하지 않음: {found_flag!r}"
        )

    def test_cost_conservatism_flag_present(self):
        """비용 모델 보수성 플래그가 있어야 한다 (REQ-057-M3-1b HARD)."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0)
        # 세금 0.18% floor 또는 슬리피지 낙관적 언급
        keywords = ("0.18", "floor", "세금", "세율", "슬리피지", "낙관", "slippage", "tax")
        found = any(
            any(kw in f for kw in keywords)
            for f in report.honesty_flags
        )
        assert found, (
            f"비용 모델 보수성 플래그 없음. flags={report.honesty_flags}"
        )

    def test_cost_conservatism_flag_mentions_upward_bias(self):
        """비용 보수성 플래그가 알파 상향 편향(upward bias)을 언급해야 한다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0)
        keywords = ("상향", "편향", "bias", "biased", "과소", "underestimate")
        # 비용 관련 플래그 중 편향 언급 확인
        cost_flags = [
            f for f in report.honesty_flags
            if any(kw in f for kw in ("0.18", "세금", "슬리피지", "slippage", "tax", "비용"))
        ]
        assert cost_flags, "비용 관련 플래그 없음"
        has_bias = any(
            any(kw in f for kw in keywords)
            for f in cost_flags
        )
        assert has_bias, f"비용 플래그에 편향 언급 없음: {cost_flags}"

    def test_no_positive_alpha_is_valid_success_framing_in_prose(self):
        """M2 결과에 양의 알파 없음 → prose에 '유효한 진단' 또는 동의어가 있어야 한다
        (REQ-057-M3-4)."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        m2 = _make_m2_no_positive_alpha()
        report = compute_attribution(rts, measured_total=-14_840.0, m2_results=m2)
        keywords = (
            "유효한", "성공적", "진단", "valid", "successful",
            "양의 알파 없음", "NOT_PASS", "알파가 없다", "알파 없음",
        )
        found = any(kw in report.prose for kw in keywords)
        assert found, (
            f"'유효한 진단' 프레이밍 없음. prose 앞 200자: {report.prose[:200]!r}"
        )

    def test_no_positive_alpha_not_labeled_as_error(self):
        """양의 알파 없음이 오류나 실패로 단정 레이블되지 않아야 한다 (REQ-057-M3-4).

        '오류가 아니라' 같은 부정 문맥은 허용한다 — 오직 '이는 오류다/실패다' 형태의
        단정 표현만 검사한다.
        """
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        m2 = _make_m2_no_positive_alpha()
        report = compute_attribution(rts, measured_total=-14_840.0, m2_results=m2)
        # 단정적 오류/실패 레이블 패턴 (부정 문맥 제외)
        # "오류가 아니라", "오류가 아님" 등의 패턴은 허용
        prose = report.prose
        # "오류다", "오류입니다", "오류임" 등 단정 패턴만 검사
        bad_patterns = [
            r"오류다\b", r"오류입니다\b", r"오류임\b",
            r"실패다\b", r"실패입니다\b", r"실패임\b",
            r"\bis an error\b", r"\bis a failure\b",
        ]
        for pat in bad_patterns:
            m = re.search(pat, prose, re.IGNORECASE)
            assert m is None, (
                f"오류/실패 단정 패턴 발견: {pat!r} → {m.group()!r}. "
                f"prose={prose[:300]!r}"
            )

    def test_honesty_flags_at_least_two(self):
        """정직성 플래그가 최소 2개 이상 있어야 한다 (n=8 + 비용 보수성)."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0)
        assert len(report.honesty_flags) >= 2, (
            f"플래그 수 {len(report.honesty_flags)} < 2"
        )


# ── M2 결과 통합 테스트 ───────────────────────────────────────────────────────

class TestM2Integration:
    """M2 FeatureAlphaResult와의 통합 (REQ-057-M3-2)."""

    def test_m2_results_accepted_as_input(self):
        """compute_attribution이 m2_results 인자를 받는다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        m2 = _make_m2_no_positive_alpha()
        # 예외 없이 실행되어야 한다
        report = compute_attribution(rts, measured_total=-14_840.0, m2_results=m2)
        assert report is not None

    def test_m2_results_none_does_not_crash(self):
        """m2_results=None이어도 실행이 가능하다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0, m2_results=None)
        assert report is not None

    def test_all_not_pass_in_m2_triggers_valid_success_framing(self):
        """모든 M2 피처가 NOT_PASS → 유효한 성공 진단 문구가 있어야 한다 (REQ-057-M3-4)."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        m2 = _make_m2_no_positive_alpha()
        report = compute_attribution(rts, measured_total=-14_840.0, m2_results=m2)
        # 유효한 진단 문구가 있어야 한다
        success_keywords = (
            "유효한", "성공적인", "성공적", "valid", "successful",
            "알파 없음", "NOT_PASS", "진단 완료",
        )
        found = any(kw in report.prose for kw in success_keywords)
        assert found

    def test_m2_inconclusive_reflected_in_entry_component(self):
        """M2가 INCONCLUSIVE이면 (a) 진입 컴포넌트가 적절히 반영해야 한다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        m2 = _make_m2_inconclusive()
        report = compute_attribution(rts, measured_total=-14_840.0, m2_results=m2)
        # INCONCLUSIVE인 경우 (a)는 insufficient이어야 한다
        comp_a = next(c for c in report.components if c.code == "a")
        # INCONCLUSIVE → 진단 불가 → insufficient
        assert comp_a.insufficient is True

    def test_sum_consistency_holds_with_m2_results(self):
        """M2 결과가 있어도 합치성이 성립한다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        m2 = _make_m2_no_positive_alpha()
        measured = -14_840.0
        report = compute_attribution(rts, measured_total=measured, m2_results=m2)
        total = sum((c.value_krw or 0.0) for c in report.components)
        assert abs(total - measured) < 1.0

    def test_sum_consistency_holds_without_m2_results(self):
        """M2 결과 없어도 합치성이 성립한다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        measured = -14_840.0
        report = compute_attribution(rts, measured_total=measured, m2_results=None)
        total = sum((c.value_krw or 0.0) for c in report.components)
        assert abs(total - measured) < 1.0


# ── 엣지 케이스 ────────────────────────────────────────────────────────────────

class TestEdgeCases:
    """엣지 케이스 및 경계 조건."""

    def test_empty_roundtrips_does_not_crash(self):
        """빈 roundtrips 목록에서도 실행이 가능하다."""
        from trading.backtest.attribution_report import compute_attribution
        report = compute_attribution([], measured_total=-14_840.0)
        assert report is not None

    def test_empty_roundtrips_cost_component_still_not_insufficient(self):
        """빈 roundtrips에서도 (b) 비용 컴포넌트는 insufficient=False이어야 한다."""
        from trading.backtest.attribution_report import compute_attribution
        report = compute_attribution([], measured_total=-14_840.0)
        comp_b = next(c for c in report.components if c.code == "b")
        assert comp_b.insufficient is False

    def test_single_roundtrip(self):
        """roundtrip 1개에서도 합치성이 성립한다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = [_make_roundtrip(entry_fee=100.0, exit_fee=200.0)]
        measured = -500.0
        report = compute_attribution(rts, measured_total=measured, tolerance=1.0)
        total = sum((c.value_krw or 0.0) for c in report.components)
        assert abs(total - measured) < 1.0

    def test_custom_tolerance(self):
        """tolerance 파라미터가 정상 동작한다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0, tolerance=100.0)
        assert report.tolerance == 100.0

    def test_large_positive_measured_total(self):
        """큰 양의 측정값에서도 합치성이 성립한다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(3)
        measured = 100_000.0
        report = compute_attribution(rts, measured_total=measured, tolerance=1.0)
        total = sum((c.value_krw or 0.0) for c in report.components)
        assert abs(total - measured) < 1.0

    def test_prose_mentions_measured_total(self):
        """prose에 측정값 -14,840이 언급되어야 한다."""
        from trading.backtest.attribution_report import compute_attribution
        rts = _make_roundtrips(8)
        report = compute_attribution(rts, measured_total=-14_840.0)
        # 숫자 형태 언급 확인 (14840 또는 14,840)
        assert "14" in report.prose
        assert "840" in report.prose or "14,840" in report.prose
