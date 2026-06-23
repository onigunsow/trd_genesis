"""SPEC-TRADING-058 M3 — Walk-forward OOS 검증 + Bonferroni + 50% 할인 + 단일 AND 판정 단위 테스트.

REQ-058-M3-1 : walk-forward = 리밸런스별 반복 point-in-time engine.run (단일 full-sample 금지)
REQ-058-M3-2 : Bonferroni-adjusted 유의성 (N=1 -> alpha/1)
REQ-058-M3-3 : 50% 백테스트 할인 (haircut) 후 GO 판정
REQ-058-M3-4a: 단일 AND 판정 함수 (Bonferroni AND haircut-양수 AND scorecard-GO)
REQ-058-M3-5 : n = 리밸런스 주기 수 (trades 수 아님)
REQ-058-M3-7 : "알파 없음" = 유효한 성공 결과 (에러 아님)
REQ-058-M3-8 : 비용/생존편향 정직성 플래그

설계 원칙:
- 모든 테스트는 픽스처 주입으로 실행 — 네트워크/pykrx/DB 불필요.
- pykrx는 import 금지 (KRX 로그인 사이드이펙트 차단).
- 결정적 합성 가격 데이터 (np.random seed 고정).
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

# ──────────────────────────────────────────────────────────────────────────────
# 픽스처 헬퍼
# ──────────────────────────────────────────────────────────────────────────────

_N_TICKERS = 6
_TICKER_NAMES = [f"T{i:04d}" for i in range(_N_TICKERS)]


def _make_price_df(
    n_days: int = 500,
    start: date = date(2019, 1, 2),
    seed: int = 42,
) -> pd.DataFrame:
    """결정적 합성 가격 DataFrame 생성.

    각 종목의 변동성이 다르게 설정되어 저변동성 팩터가 의미 있게 작동한다.
    """
    rng = np.random.RandomState(seed)
    vols = [0.01, 0.015, 0.02, 0.025, 0.03, 0.04]  # 종목별 일간 변동성
    dates = pd.date_range(start=start, periods=n_days, freq="B")
    data: dict[str, np.ndarray] = {}
    for _i, (ticker, vol) in enumerate(zip(_TICKER_NAMES, vols, strict=False)):
        returns = rng.normal(0.0002, vol, n_days)
        prices = 10000.0 * np.exp(np.cumsum(returns))
        data[ticker] = prices
    return pd.DataFrame(data, index=dates)


def _make_rebalance_dates(
    price_df: pd.DataFrame,
    start_offset: int = 130,
    n_rebalances: int = 5,
) -> list[date]:
    """price_df에서 n_rebalances개의 월간 리밸런스 날짜를 생성."""
    all_dates = [d.date() for d in price_df.index]
    step = 21  # 월간 = 약 21 거래일
    result = []
    idx = start_offset
    while len(result) < n_rebalances and idx < len(all_dates):
        result.append(all_dates[idx])
        idx += step
    return result


def _make_kospi_returns(price_df: pd.DataFrame, seed: int = 99) -> pd.Series:
    """결정적 합성 KOSPI 일간 수익률."""
    rng = np.random.RandomState(seed)
    idx = price_df.index
    returns = rng.normal(0.0003, 0.012, len(idx))
    return pd.Series(returns, index=idx)


# ──────────────────────────────────────────────────────────────────────────────
# REQ-058-M3-1: walk-forward = 리밸런스별 반복 engine.run (단일 full-sample 금지)
# ──────────────────────────────────────────────────────────────────────────────

class TestWalkForwardMultipleEngineRuns:
    """REQ-058-M3-1: walk-forward는 각 리밸런스별로 별도 engine.run을 호출해야 한다."""

    def test_walk_forward_calls_engine_run_per_rebalance(self):
        """walk-forward는 리밸런스 N개에 대해 engine.run을 N번 (또는 N-1번) 호출한다.

        단일 full-sample engine.run 1회 호출을 OOS로 보고하면 REQ-058-M3-1 위반.
        """
        from trading.backtest.lowvol_validation import run_walk_forward_oos

        price_df = _make_price_df(n_days=500)
        rebalance_dates = _make_rebalance_dates(price_df, n_rebalances=4)
        universe_tickers = _TICKER_NAMES[:]
        kospi_returns = _make_kospi_returns(price_df)

        # engine.run 호출 횟수 추적
        import trading.backtest.engine as engine_mod
        call_count = [0]
        original_run = engine_mod.run

        def counting_run(*args, **kwargs):
            call_count[0] += 1
            return original_run(*args, **kwargs)

        with patch.object(engine_mod, "run", side_effect=counting_run):
            run_walk_forward_oos(
                prices_df=price_df,
                rebalance_dates=rebalance_dates,
                universe_tickers=universe_tickers,
                kospi_returns=kospi_returns,
            )

        # 리밸런스 N-1개 이상의 engine.run 호출이 있어야 한다
        # (마지막 리밸런스는 다음 날짜가 없어 제외될 수 있음)
        assert call_count[0] >= len(rebalance_dates) - 1, (
            f"engine.run이 {call_count[0]}번 호출됨 — "
            f"리밸런스 {len(rebalance_dates)}개에 대해 최소 {len(rebalance_dates) - 1}번 필요. "
            "단일 full-sample engine.run 1회는 REQ-058-M3-1 위반."
        )

    def test_walk_forward_per_window_result_structure(self):
        """각 리밸런스 window는 독립적인 WalkForwardWindow 결과를 가진다."""
        from trading.backtest.lowvol_validation import (
            WalkForwardResult,
            WalkForwardWindow,
            run_walk_forward_oos,
        )

        price_df = _make_price_df(n_days=500)
        rebalance_dates = _make_rebalance_dates(price_df, n_rebalances=4)
        universe_tickers = _TICKER_NAMES[:]
        kospi_returns = _make_kospi_returns(price_df)

        result = run_walk_forward_oos(
            prices_df=price_df,
            rebalance_dates=rebalance_dates,
            universe_tickers=universe_tickers,
            kospi_returns=kospi_returns,
        )

        assert isinstance(result, WalkForwardResult)
        # 각 window는 리밸런스 시작일/종료일을 가짐
        assert len(result.windows) > 0
        for window in result.windows:
            assert isinstance(window, WalkForwardWindow)
            assert window.start_date <= window.end_date
            assert window.backtest_result is not None

    def test_walk_forward_uses_only_data_up_to_rebalance_date(self):
        """각 window에서 팩터 계산은 리밸런스 날짜 T까지의 데이터만 사용한다 (point-in-time).

        미래 데이터가 사용되면 REQ-058-M1-3 + REQ-058-M3-1 위반.
        """
        from trading.backtest.lowvol_validation import run_walk_forward_oos

        price_df = _make_price_df(n_days=500)
        rebalance_dates = _make_rebalance_dates(price_df, n_rebalances=3)
        universe_tickers = _TICKER_NAMES[:]
        kospi_returns = _make_kospi_returns(price_df)

        # compute_low_vol_signal 호출 시 as_of_date 기록
        recorded_as_of_dates: list[date] = []
        from trading.backtest import factor_lowvol

        original_fn = factor_lowvol.compute_low_vol_signal

        def recording_fn(prices_df, as_of_date, **kwargs):
            recorded_as_of_dates.append(as_of_date)
            return original_fn(prices_df, as_of_date, **kwargs)

        with patch.object(factor_lowvol, "compute_low_vol_signal", side_effect=recording_fn):
            run_walk_forward_oos(
                prices_df=price_df,
                rebalance_dates=rebalance_dates,
                universe_tickers=universe_tickers,
                kospi_returns=kospi_returns,
            )

        # 기록된 as_of_date가 리밸런스 날짜와 일치해야 함
        assert len(recorded_as_of_dates) > 0
        for as_of in recorded_as_of_dates:
            assert any(as_of <= rb for rb in rebalance_dates), (
                f"as_of_date={as_of}가 어떤 리밸런스 날짜보다도 크다 — look-ahead 누출 의심"
            )


# ──────────────────────────────────────────────────────────────────────────────
# REQ-058-M3-3: 50% haircut
# ──────────────────────────────────────────────────────────────────────────────

class TestAlphaHaircut:
    """REQ-058-M3-3: 50% 백테스트 할인이 GO 판정 전에 적용되어야 한다."""

    def test_haircut_halves_the_alpha(self):
        """apply_alpha_haircut(10.0) = 5.0 (정확히 절반)."""
        from trading.backtest.lowvol_validation import apply_alpha_haircut

        raw_alpha = 10.0
        discounted = apply_alpha_haircut(raw_alpha)
        assert discounted == pytest.approx(5.0), (
            f"50% 할인 후 알파는 5.0이어야 함, 실제={discounted}"
        )

    def test_haircut_on_negative_alpha(self):
        """음수 알파도 절반으로 줄어든다 (절댓값 기준)."""
        from trading.backtest.lowvol_validation import apply_alpha_haircut

        raw_alpha = -8.0
        discounted = apply_alpha_haircut(raw_alpha)
        assert discounted == pytest.approx(-4.0)

    def test_haircut_on_zero_alpha(self):
        """알파 0은 할인 후에도 0."""
        from trading.backtest.lowvol_validation import apply_alpha_haircut

        assert apply_alpha_haircut(0.0) == pytest.approx(0.0)

    def test_haircut_does_not_modify_raw_alpha_in_report(self):
        """리포트는 raw 알파와 할인 알파를 모두 표시해야 한다 (REQ-058-M3-3)."""
        from trading.backtest.lowvol_validation import WalkForwardValidationReport

        # report 객체가 raw_alpha_pct와 discounted_alpha_pct 모두 갖는지 확인
        report = WalkForwardValidationReport(
            raw_alpha_pct=12.0,
            discounted_alpha_pct=6.0,
            bonferroni_passed=True,
            n_rebalances=35,
            verdict="PASS",
            survivorship_label="생존편향 게이트 통과",
            cost_honesty_flag="비용 모델 보수적이지 않음",
            is_valid_no_alpha_result=False,
        )
        assert report.raw_alpha_pct == 12.0
        assert report.discounted_alpha_pct == 6.0


# ──────────────────────────────────────────────────────────────────────────────
# REQ-058-M3-2: Bonferroni 보정
# ──────────────────────────────────────────────────────────────────────────────

class TestBonferroniCorrection:
    """REQ-058-M3-2: Bonferroni-adjusted 유의성 판정."""

    def test_bonferroni_n1_uses_full_alpha(self):
        """N=1 팩터일 때 Bonferroni 보정 후 유의 수준은 그대로 alpha."""
        from trading.backtest.lowvol_validation import apply_bonferroni

        # 명확히 유의한 t-통계량 (수동 p-value 계산)
        # 일간 수익률이 양수로 치우친 시계열 → p < 0.05
        rng = np.random.RandomState(0)
        # 평균 0.003 (높은 양수 수익)로 설정해 확실히 유의하도록
        daily_returns = pd.Series(rng.normal(0.003, 0.01, 252))

        result = apply_bonferroni(daily_returns, n_factors=1)
        assert result.n_factors == 1
        assert result.alpha_level == pytest.approx(0.05)
        assert result.adjusted_alpha == pytest.approx(0.05)  # N=1이면 동일
        assert isinstance(result.significant, bool)

    def test_bonferroni_n2_halves_alpha(self):
        """N=2 팩터일 때 조정 유의 수준 = 0.05 / 2 = 0.025."""
        from trading.backtest.lowvol_validation import apply_bonferroni

        rng = np.random.RandomState(0)
        daily_returns = pd.Series(rng.normal(0.001, 0.01, 252))

        result = apply_bonferroni(daily_returns, n_factors=2)
        assert result.adjusted_alpha == pytest.approx(0.025)

    def test_bonferroni_insignificant_negative_returns(self):
        """음수 평균 수익률 → Bonferroni 유의성 미통과."""
        from trading.backtest.lowvol_validation import apply_bonferroni

        # 음수 평균 (확실히 미유의 또는 반대 방향)
        rng = np.random.RandomState(7)
        daily_returns = pd.Series(rng.normal(-0.003, 0.02, 100))

        result = apply_bonferroni(daily_returns, n_factors=1)
        assert result.significant is False

    def test_bonferroni_significant_strong_positive_returns(self):
        """강한 양수 수익률 → 유의성 통과."""
        from trading.backtest.lowvol_validation import apply_bonferroni

        # 매우 높은 일간 평균으로 확실히 유의
        rng = np.random.RandomState(42)
        daily_returns = pd.Series(rng.normal(0.005, 0.005, 252))

        result = apply_bonferroni(daily_returns, n_factors=1)
        assert result.significant is True


# ──────────────────────────────────────────────────────────────────────────────
# REQ-058-M3-4a: 단일 AND 판정 함수
# ──────────────────────────────────────────────────────────────────────────────

class TestComposeVerdict:
    """REQ-058-M3-4a: 세 조건 AND 합성 판정 함수."""

    def _make_go_scorecard(self):
        """GO 스코어카드 픽스처."""
        from trading.edge.scorecard import GRADE_MODERATE, VERDICT_GO, Scorecard
        return Scorecard(grade=GRADE_MODERATE, verdict=VERDICT_GO, reasons=["테스트 GO"])

    def _make_no_go_scorecard(self):
        """NO-GO 스코어카드 픽스처."""
        from trading.edge.scorecard import GRADE_MODERATE, VERDICT_NO_GO, Scorecard
        return Scorecard(grade=GRADE_MODERATE, verdict=VERDICT_NO_GO, reasons=["테스트 NO-GO"])

    def _make_survivorship_passed(self):
        """생존편향 게이트 통과 픽스처."""
        from trading.backtest.lowvol_portfolio import SurvivorshipGateResult
        return SurvivorshipGateResult(
            survivorship_biased=False,
            label="생존편향 게이트 통과",
            achievable=True,
        )

    def _make_survivorship_bound_only(self):
        """생존편향 bound-only 픽스처."""
        from trading.backtest.lowvol_portfolio import SurvivorshipGateResult
        return SurvivorshipGateResult(
            survivorship_biased=True,
            label="생존편향 상한 — bound only",
            achievable=None,
        )

    def test_all_three_conditions_pass_gives_pass(self):
        """Bonferroni AND 할인후-양수 AND scorecard-GO → PASS.

        REQ-058-M3-4a: 세 조건 모두 충족 시에만 PASS.
        """
        from trading.backtest.lowvol_validation import compose_verdict

        result = compose_verdict(
            bonferroni_passed=True,
            discounted_alpha_pct=5.0,  # 양수
            scorecard=self._make_go_scorecard(),
            survivorship_gate=self._make_survivorship_passed(),
            n_rebalances=35,
        )
        assert result.verdict == "PASS", (
            f"세 조건 모두 충족 시 PASS여야 함, 실제={result.verdict}"
        )
        assert result.is_paper_only is True  # 페이퍼 전용 (REQ-058-M3-6)

    def test_scorecard_go_but_bonferroni_fail_gives_non_pass(self):
        """scorecard-GO이지만 Bonferroni 미통과 → NOT PASS.

        REQ-058-M3-4a: 긍정 alpha 부호만으로는 PASS 불가.
        """
        from trading.backtest.lowvol_validation import compose_verdict

        result = compose_verdict(
            bonferroni_passed=False,  # ← 미통과
            discounted_alpha_pct=5.0,
            scorecard=self._make_go_scorecard(),
            survivorship_gate=self._make_survivorship_passed(),
            n_rebalances=35,
        )
        assert result.verdict != "PASS", (
            f"Bonferroni 미통과 시 PASS 불가, 실제={result.verdict}"
        )

    def test_bonferroni_pass_but_scorecard_no_go_gives_non_pass(self):
        """Bonferroni 통과이지만 scorecard NO-GO → NOT PASS."""
        from trading.backtest.lowvol_validation import compose_verdict

        result = compose_verdict(
            bonferroni_passed=True,
            discounted_alpha_pct=5.0,
            scorecard=self._make_no_go_scorecard(),  # ← NO-GO
            survivorship_gate=self._make_survivorship_passed(),
            n_rebalances=35,
        )
        assert result.verdict != "PASS"

    def test_positive_alpha_sign_only_not_pass(self):
        """할인 후 알파가 양수이더라도 Bonferroni 미통과 → NOT PASS.

        REQ-058-M3-4a: "양성 부호만으로 PASS 불가" 명시.
        """
        from trading.backtest.lowvol_validation import compose_verdict

        result = compose_verdict(
            bonferroni_passed=False,  # Bonferroni 미통과
            discounted_alpha_pct=8.0,  # 양수
            scorecard=self._make_go_scorecard(),
            survivorship_gate=self._make_survivorship_passed(),
            n_rebalances=35,
        )
        assert result.verdict != "PASS"

    def test_survivorship_bound_only_short_circuits_to_non_pass(self):
        """생존편향 bound-only이면 나머지 조건 무관하게 즉시 NON-PASS.

        REQ-058-M3-4a: survivorship downgrade → 단락.
        """
        from trading.backtest.lowvol_validation import compose_verdict

        result = compose_verdict(
            bonferroni_passed=True,
            discounted_alpha_pct=10.0,  # 높은 양수
            scorecard=self._make_go_scorecard(),
            survivorship_gate=self._make_survivorship_bound_only(),  # ← bound-only
            n_rebalances=35,
        )
        assert result.verdict != "PASS", (
            "생존편향 bound-only 시 PASS 불가 (단락 조건)"
        )
        # 단락 이유가 survivorship임을 명시
        assert "생존편향" in result.reason or "survivorship" in result.reason.lower()

    def test_inconclusive_when_n_below_30(self):
        """n < 30 리밸런스 주기 → INCONCLUSIVE.

        REQ-058-M3-5: n = 리밸런스 주기 수, n < 30 → INCONCLUSIVE never PASS.
        """
        from trading.backtest.lowvol_validation import compose_verdict

        result = compose_verdict(
            bonferroni_passed=True,
            discounted_alpha_pct=5.0,
            scorecard=self._make_go_scorecard(),
            survivorship_gate=self._make_survivorship_passed(),
            n_rebalances=15,  # ← 30 미만
        )
        assert result.verdict == "INCONCLUSIVE", (
            f"n=15 < 30 리밸런스 주기이면 INCONCLUSIVE여야 함, 실제={result.verdict}"
        )

    def test_negative_discounted_alpha_gives_non_pass(self):
        """할인 후 알파가 음수 → NOT PASS."""
        from trading.backtest.lowvol_validation import compose_verdict

        result = compose_verdict(
            bonferroni_passed=True,
            discounted_alpha_pct=-3.0,  # 음수
            scorecard=self._make_go_scorecard(),
            survivorship_gate=self._make_survivorship_passed(),
            n_rebalances=35,
        )
        assert result.verdict != "PASS"


# ──────────────────────────────────────────────────────────────────────────────
# REQ-058-M3-7/8: 정직한 판정 + 비용/생존편향 플래그
# ──────────────────────────────────────────────────────────────────────────────

class TestHonestVerdictFraming:
    """REQ-058-M3-7/8: "알파 없음"이 유효한 성공 결과임을 텍스트로 표현."""

    def test_no_alpha_result_is_valid_success_in_report(self):
        """alpha 없음 결과의 리포트 텍스트에 "실패" / "오류" 대신 "유효한 결과" 표현.

        REQ-058-M3-7: "no positive net OOS alpha" = valid successful outcome.
        """
        from trading.backtest.lowvol_validation import render_verdict_report

        report_text = render_verdict_report(
            raw_alpha_pct=-2.0,
            discounted_alpha_pct=-1.0,
            bonferroni_passed=False,
            n_rebalances=35,
            verdict="NON-PASS",
            survivorship_label="생존편향 게이트 통과",
            cost_honesty_flag="비용 모델 보수적이지 않음",
            is_valid_no_alpha_result=True,
        )

        # "알파 없음" = 유효한 결과임을 명시
        assert "유효" in report_text, (
            "알파 없음 결과는 '유효한 결과'임을 리포트에 명시해야 함 (REQ-058-M3-7)"
        )
        # "실패" / "오류" 표현 없어야 함
        assert "오류" not in report_text
        assert "실패" not in report_text

    def test_cost_honesty_flag_in_report(self):
        """리포트에 비용 모델이 보수적이지 않다는 경고가 포함되어야 한다 (REQ-058-M3-8)."""
        from trading.backtest.lowvol_validation import render_verdict_report

        report_text = render_verdict_report(
            raw_alpha_pct=5.0,
            discounted_alpha_pct=2.5,
            bonferroni_passed=True,
            n_rebalances=35,
            verdict="PASS",
            survivorship_label="생존편향 게이트 통과",
            cost_honesty_flag="비용 모델 보수적이지 않음",
            is_valid_no_alpha_result=False,
        )
        # 비용 정직성 플래그 포함
        assert "비용" in report_text or "cost" in report_text.lower()

    def test_survivorship_bound_only_is_dominant_caveat(self):
        """생존편향 bound-only 시 리포트의 첫 주의 사항이 생존편향이어야 한다 (REQ-058-M3-8)."""
        from trading.backtest.lowvol_validation import render_verdict_report

        report_text = render_verdict_report(
            raw_alpha_pct=8.0,
            discounted_alpha_pct=4.0,
            bonferroni_passed=True,
            n_rebalances=35,
            verdict="NON-PASS",
            survivorship_label="생존편향 상한 — bound only",
            cost_honesty_flag="비용 모델 보수적이지 않음",
            is_valid_no_alpha_result=False,
        )
        # 생존편향이 리포트 초반에 등장해야 함
        lowered = report_text.lower()
        survivorship_pos = lowered.find("생존편향")
        cost_pos = lowered.find("비용")
        assert survivorship_pos != -1, "생존편향 언급 없음"
        assert survivorship_pos < cost_pos or cost_pos == -1, (
            "생존편향이 비용보다 먼저 언급되어야 함 (dominant caveat)"
        )

    def test_both_raw_and_discounted_alpha_shown(self):
        """리포트에 raw 알파와 할인 알파가 모두 표시되어야 한다 (REQ-058-M3-3)."""
        from trading.backtest.lowvol_validation import render_verdict_report

        report_text = render_verdict_report(
            raw_alpha_pct=10.0,
            discounted_alpha_pct=5.0,
            bonferroni_passed=True,
            n_rebalances=35,
            verdict="PASS",
            survivorship_label="생존편향 게이트 통과",
            cost_honesty_flag="비용 모델 보수적이지 않음",
            is_valid_no_alpha_result=False,
        )
        # 두 값이 모두 표시 (소수점 없이도 인식)
        assert "10" in report_text or "10.0" in report_text
        assert "5" in report_text or "5.0" in report_text


# ──────────────────────────────────────────────────────────────────────────────
# REQ-058-M3-5: n = 리밸런스 주기 수 (trades 수 아님)
# ──────────────────────────────────────────────────────────────────────────────

class TestRebalancePeriodCount:
    """REQ-058-M3-5: n_closed는 리밸런스 주기 수로 설정되어야 한다."""

    def test_n_rebalances_fed_to_analytics(self):
        """adapt_to_scorecard에 전달된 n_rebalances가 analytics.n_closed로 반영된다."""
        from trading.backtest.engine import BacktestResult
        from trading.backtest.lowvol_portfolio import adapt_to_scorecard

        # 가짜 BacktestResult 생성
        n_days = 100
        eq = pd.Series(
            np.linspace(10_000_000, 11_000_000, n_days),
            index=pd.date_range("2020-01-01", periods=n_days, freq="B"),
        )
        dr = eq.pct_change().fillna(0.0)
        br = BacktestResult(
            cagr=0.1, mdd=-0.05, sharpe=1.0, trades=5,
            final_equity=float(eq.iloc[-1]),
            equity_curve=eq,
            daily_returns=dr,
        )

        n_rb = 7  # 리밸런스 주기 수
        analytics, _ = adapt_to_scorecard(br, None, n_rebalances=n_rb)

        assert analytics.n_closed == n_rb, (
            f"n_closed={analytics.n_closed}이 아닌 리밸런스 수={n_rb}이어야 함"
        )

    def test_high_trade_count_does_not_leak_pass(self):
        """trades 수가 높아도 n_rebalances < 30이면 INCONCLUSIVE.

        REQ-058-M3-5: trades 수가 _MIN_SAMPLE=30을 넘어도 리밸런스 수 < 30이면 PASS 불가.
        """
        from trading.backtest.lowvol_portfolio import SurvivorshipGateResult
        from trading.backtest.lowvol_validation import compose_verdict
        from trading.edge.scorecard import GRADE_MODERATE, VERDICT_GO, Scorecard

        scorecard = Scorecard(grade=GRADE_MODERATE, verdict=VERDICT_GO, reasons=[])
        surv = SurvivorshipGateResult(survivorship_biased=False, label="OK", achievable=True)

        result = compose_verdict(
            bonferroni_passed=True,
            discounted_alpha_pct=5.0,
            scorecard=scorecard,
            survivorship_gate=surv,
            n_rebalances=10,  # ← 리밸런스 수 < 30 (trades와 무관)
        )
        # 리밸런스 수가 30 미만이면 PASS가 아니어야 함
        assert result.verdict != "PASS", (
            f"리밸런스 수=10 < 30, trades 수가 많아도 PASS 불가. 실제={result.verdict}"
        )
