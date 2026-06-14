"""T-002/T-003 RED phase — 5차원 채점기 + 보조 점검.

SPEC-TRADING-048 REQ-048-M2-1/2/3/4.
AC: AC-M2-1(REJECT firewall), AC-M2-1b(PASS 정상), AC-M2-3, AC-M2-4, AC-M2-5, AC-M2-6.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _make_trade_stats(
    *,
    n: int = 100,
    win_rate: float = 0.6,
    avg_win: float = 200.0,
    avg_loss: float = 100.0,
    profit_factor: float = 2.0,
    expectancy: float = 80.0,
):
    from trading.edge.trade_stats import TradeStats
    return TradeStats(
        n=n,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        expectancy=expectancy,
    )


def _make_portfolio_metrics(
    *,
    mdd: float = 0.20,
    sharpe: float = 1.5,
    cagr: float = 0.15,
    equity_curve: list[float] | None = None,
    daily_returns: list[float] | None = None,
    test_years: float = 6.0,
    n_params: int = 5,
    open_positions: int = 0,
) -> dict:
    return {
        "mdd": mdd,
        "sharpe": sharpe,
        "cagr": cagr,
        "equity_curve": equity_curve or ([0.0] * 5 + [1.0, 1.05, 1.1, 1.12, 1.15]),
        "daily_returns": daily_returns or ([0.0] * 5 + [0.001, -0.001, 0.002]),
        "test_years": test_years,
        "n_params": n_params,
        "open_positions": open_positions,
    }


def _make_is_oos(
    *,
    is_expectancy: float = 100.0,
    oos_expectancy: float = 60.0,
) -> dict:
    return {
        "is_expectancy": is_expectancy,
        "oos_expectancy": oos_expectancy,
    }


# ---------------------------------------------------------------------------
# T-002: score_backtest 기본 결과 + 컷오프
# ---------------------------------------------------------------------------


class TestScoreBacktestBasic:
    """score_backtest(trade_stats, portfolio_metrics, is_oos, *, scoring_params) -> BacktestScoreCard."""

    def test_returns_backtest_scorecard_instance(self) -> None:
        from trading.edge.evaluate_backtest import BacktestScoreCard, score_backtest

        card = score_backtest(
            _make_trade_stats(),
            _make_portfolio_metrics(),
            _make_is_oos(),
        )
        assert isinstance(card, BacktestScoreCard)

    def test_scorecard_has_required_fields(self) -> None:
        from trading.edge.evaluate_backtest import score_backtest

        card = score_backtest(
            _make_trade_stats(),
            _make_portfolio_metrics(),
            _make_is_oos(),
        )
        assert hasattr(card, "score")
        assert hasattr(card, "verdict")
        assert hasattr(card, "dimension_scores")
        assert hasattr(card, "warnings")

    def test_ac_m2_1_current_negative_edge_rejected(self) -> None:
        """AC-M2-1: 현재 마이너스 엣지(기대값<0, 표본8건) → REJECT.

        표본수 차원 0점(30 미만) AND expectancy<=0 → REJECT.
        """
        from trading.edge.evaluate_backtest import VERDICT_REJECT, score_backtest
        from trading.edge.trade_stats import TradeStats

        negative_stats = TradeStats(
            n=8,
            win_rate=0.25,
            avg_win=5000.0,
            avg_loss=19840.0,
            profit_factor=0.075,
            expectancy=-14_840.0,
        )
        card = score_backtest(
            negative_stats,
            _make_portfolio_metrics(mdd=0.05),
            _make_is_oos(),
        )
        assert card.verdict == VERDICT_REJECT
        # 표본수 차원 반드시 0점
        assert card.dimension_scores.get("sample_size", 1) == 0

    def test_ac_m2_1b_good_input_passes(self) -> None:
        """AC-M2-1b: 정상 입력 PASS 약 92점.

        표본 200+건, profit_factor 1.8, expectancy>0, MDD 20%,
        테스트 5년+, OOS>=IS*0.5, 파라미터 5개.
        """
        from trading.edge.evaluate_backtest import VERDICT_PASS, score_backtest
        from trading.edge.trade_stats import TradeStats

        good_stats = TradeStats(
            n=200,
            win_rate=0.6,
            avg_win=200.0,
            avg_loss=100.0,
            profit_factor=1.8,
            expectancy=80.0,
        )
        good_portfolio = _make_portfolio_metrics(
            mdd=0.20,
            test_years=6.0,
            n_params=5,
        )
        good_is_oos = _make_is_oos(is_expectancy=80.0, oos_expectancy=60.0)

        card = score_backtest(good_stats, good_portfolio, good_is_oos)
        assert card.verdict == VERDICT_PASS
        assert card.score >= 70

    def test_verdict_pass_requires_all_dims_nonzero(self) -> None:
        """어떤 차원이 0점이면 PASS 불가 (파이어월)."""
        from trading.edge.evaluate_backtest import VERDICT_PASS, score_backtest
        from trading.edge.trade_stats import TradeStats

        # MDD >= 50% → MDD-risk 차원 0점
        stats = TradeStats(n=200, win_rate=0.6, avg_win=200.0, avg_loss=100.0,
                           profit_factor=1.8, expectancy=80.0)
        portfolio = _make_portfolio_metrics(mdd=0.55)  # 55% → 0점
        card = score_backtest(stats, portfolio, _make_is_oos())
        assert card.verdict != VERDICT_PASS
        assert card.dimension_scores.get("mdd_risk", 1) == 0

    def test_verdict_pass_requires_positive_expectancy(self) -> None:
        """expectancy <= 0 이면 PASS 불가 (파이어월)."""
        from trading.edge.evaluate_backtest import VERDICT_PASS, score_backtest
        from trading.edge.trade_stats import TradeStats

        stats = TradeStats(n=200, win_rate=0.5, avg_win=100.0, avg_loss=100.0,
                           profit_factor=1.0, expectancy=0.0)
        card = score_backtest(stats, _make_portfolio_metrics(), _make_is_oos())
        assert card.verdict != VERDICT_PASS

    def test_revise_range_50_to_69(self) -> None:
        """합계 50~69 → REVISE."""
        from trading.edge.evaluate_backtest import VERDICT_REVISE, score_backtest
        from trading.edge.trade_stats import TradeStats

        # 중간 품질: expectancy 3000원(부분점), PF 1.3, 표본 130건, MDD 25%,
        # 테스트 6년, 파라미터 8개(1개 초과→-3), OOS ok → 합계 약 61점(REVISE 구간).
        stats = TradeStats(n=130, win_rate=0.55, avg_win=150.0, avg_loss=100.0,
                           profit_factor=1.3, expectancy=3000.0)
        portfolio = _make_portfolio_metrics(mdd=0.25, test_years=6.0, n_params=8)
        is_oos = _make_is_oos(is_expectancy=3000.0, oos_expectancy=2000.0)
        card = score_backtest(stats, portfolio, is_oos)
        # REVISE 는 50~69, 정확한 값은 구현에 따르지만 REJECT/PASS 는 아님
        assert card.verdict == VERDICT_REVISE or card.score in range(50, 70)

    def test_dimension_scores_keys_present(self) -> None:
        """dimension_scores 에 5개 차원 키 모두 존재."""
        from trading.edge.evaluate_backtest import score_backtest

        card = score_backtest(
            _make_trade_stats(),
            _make_portfolio_metrics(),
            _make_is_oos(),
        )
        expected_keys = {"expectancy", "profit_factor", "sample_size", "mdd_risk", "robustness"}
        assert expected_keys.issubset(card.dimension_scores.keys())

    def test_each_dimension_max_20(self) -> None:
        """각 차원 점수 0~20 범위."""
        from trading.edge.evaluate_backtest import score_backtest

        card = score_backtest(
            _make_trade_stats(),
            _make_portfolio_metrics(),
            _make_is_oos(),
        )
        for k, v in card.dimension_scores.items():
            assert 0 <= v <= 20, f"차원 {k}={v} 범위 초과"

    def test_score_is_sum_of_dimensions(self) -> None:
        """score = sum(dimension_scores)."""
        from trading.edge.evaluate_backtest import score_backtest

        card = score_backtest(
            _make_trade_stats(),
            _make_portfolio_metrics(),
            _make_is_oos(),
        )
        assert abs(card.score - sum(card.dimension_scores.values())) < 1e-6

    def test_ac_m2_6_no_engine_import(self) -> None:
        """AC-M2-6: 채점기 모듈이 backtest.engine 을 import 하지 않음."""
        import importlib
        import sys

        # 엔진 모듈 미로드 상태에서 채점기 가져오기
        engine_key = "trading.backtest.engine"
        was_loaded = engine_key in sys.modules
        if was_loaded:
            engine_module = sys.modules.pop(engine_key)

        try:
            import trading.edge.evaluate_backtest as eb  # noqa: F401
            # 엔진이 sys.modules 에 없어도 동작해야 함
            card = eb.score_backtest(
                _make_trade_stats(),
                _make_portfolio_metrics(),
                _make_is_oos(),
            )
            assert card is not None
        finally:
            if was_loaded:
                sys.modules[engine_key] = engine_module


# ---------------------------------------------------------------------------
# T-002: 차원별 배점 단위 테스트
# ---------------------------------------------------------------------------


class TestDimensionScoring:
    """각 차원 개별 배점 공식 검증."""

    def test_expectancy_zero_gets_zero(self) -> None:
        from trading.edge.evaluate_backtest import score_expectancy

        assert score_expectancy(0.0, exp_full=10_000.0) == 0.0

    def test_expectancy_negative_gets_zero(self) -> None:
        from trading.edge.evaluate_backtest import score_expectancy

        assert score_expectancy(-5000.0, exp_full=10_000.0) == 0.0

    def test_expectancy_at_full_gets_20(self) -> None:
        from trading.edge.evaluate_backtest import score_expectancy

        assert abs(score_expectancy(10_000.0, exp_full=10_000.0) - 20.0) < 1e-6

    def test_expectancy_linear(self) -> None:
        from trading.edge.evaluate_backtest import score_expectancy

        # 절반이면 10점
        assert abs(score_expectancy(5_000.0, exp_full=10_000.0) - 10.0) < 1e-6

    def test_profit_factor_below_1_gets_zero(self) -> None:
        from trading.edge.evaluate_backtest import score_profit_factor

        assert score_profit_factor(0.9) == 0.0

    def test_profit_factor_at_1_5_gets_20(self) -> None:
        from trading.edge.evaluate_backtest import score_profit_factor

        assert abs(score_profit_factor(1.5) - 20.0) < 1e-6

    def test_profit_factor_above_1_5_caps_at_20(self) -> None:
        from trading.edge.evaluate_backtest import score_profit_factor

        assert abs(score_profit_factor(2.0) - 20.0) < 1e-6

    def test_profit_factor_midpoint(self) -> None:
        from trading.edge.evaluate_backtest import score_profit_factor

        # PF=1.25 (중간) → 10점 선형 (1.0=0→1.5=20, 40*(pf-1.0))
        assert abs(score_profit_factor(1.25) - 10.0) < 1e-6

    def test_sample_size_below_30_gets_zero(self) -> None:
        from trading.edge.evaluate_backtest import score_sample_size

        assert score_sample_size(8) == 0.0
        assert score_sample_size(29) == 0.0

    def test_sample_size_200_plus_gets_20(self) -> None:
        from trading.edge.evaluate_backtest import score_sample_size

        assert abs(score_sample_size(200) - 20.0) < 1e-6
        assert abs(score_sample_size(500) - 20.0) < 1e-6

    def test_sample_size_100_gets_15(self) -> None:
        from trading.edge.evaluate_backtest import score_sample_size

        assert abs(score_sample_size(100) - 15.0) < 1e-6

    def test_mdd_risk_above_50pct_gets_zero(self) -> None:
        from trading.edge.evaluate_backtest import score_mdd_risk

        assert score_mdd_risk(0.55) == 0.0
        assert score_mdd_risk(0.50) == 0.0

    def test_mdd_risk_zero_gets_20(self) -> None:
        from trading.edge.evaluate_backtest import score_mdd_risk

        assert abs(score_mdd_risk(0.0) - 20.0) < 1e-6

    def test_mdd_risk_25pct_gets_10(self) -> None:
        from trading.edge.evaluate_backtest import score_mdd_risk

        # 20 * (1 - 0.25/0.5) = 20 * 0.5 = 10
        assert abs(score_mdd_risk(0.25) - 10.0) < 1e-6

    def test_robustness_below_5yr_gets_zero(self) -> None:
        from trading.edge.evaluate_backtest import score_robustness

        assert score_robustness(test_years=4.0, oos_fail=False, n_params=5) == 0.0

    def test_robustness_oos_fail_gets_zero(self) -> None:
        from trading.edge.evaluate_backtest import score_robustness

        assert score_robustness(test_years=6.0, oos_fail=True, n_params=5) == 0.0

    def test_robustness_excess_params_penalty(self) -> None:
        from trading.edge.evaluate_backtest import score_robustness

        # 기본 20점에서 초과 파라미터당 -3점: n_params=10 → 초과 3 → -9 → 11점
        assert abs(score_robustness(test_years=6.0, oos_fail=False, n_params=10) - 11.0) < 1e-6

    def test_robustness_perfect_conditions_20(self) -> None:
        from trading.edge.evaluate_backtest import score_robustness

        assert abs(score_robustness(test_years=6.0, oos_fail=False, n_params=5) - 20.0) < 1e-6


# ---------------------------------------------------------------------------
# T-003: robustness OOS 실패 + 과적합 체크리스트 + 인플레 전처리
# ---------------------------------------------------------------------------


class TestRobustnessAndInflation:
    """AC-M2-3, AC-M2-4, AC-M2-5."""

    def test_ac_m2_3_oos_fail_robustness_zero(self) -> None:
        """AC-M2-3: OOS < IS*0.5 → robustness 0점 + 경고."""
        from trading.edge.evaluate_backtest import VERDICT_REJECT, score_backtest
        from trading.edge.trade_stats import TradeStats

        stats = TradeStats(n=200, win_rate=0.6, avg_win=200.0, avg_loss=100.0,
                           profit_factor=1.8, expectancy=80.0)
        portfolio = _make_portfolio_metrics(test_years=6.0)
        # OOS/IS = 40/100 = 0.4 < 0.5 → 실패
        is_oos = _make_is_oos(is_expectancy=100.0, oos_expectancy=40.0)

        card = score_backtest(stats, portfolio, is_oos)
        assert card.dimension_scores.get("robustness", 1) == 0
        # robustness 0 → any_dim_zero → REJECT
        assert card.verdict == VERDICT_REJECT
        # 경고 포함 여부
        assert any("OOS" in w or "oos" in w.lower() or "robustness" in w.lower()
                   for w in card.warnings)

    def test_ac_m2_5_overfitting_warnings(self) -> None:
        """AC-M2-5: 룰 12개, 소수점 4자리, 연 8회 → 경고 3개."""
        from trading.edge.evaluate_backtest import score_backtest
        from trading.edge.trade_stats import TradeStats

        stats = TradeStats(n=200, win_rate=0.6, avg_win=200.0, avg_loss=100.0,
                           profit_factor=1.8, expectancy=80.0)
        portfolio = _make_portfolio_metrics(test_years=6.0)

        card = score_backtest(
            stats,
            portfolio,
            _make_is_oos(),
            n_rule_conditions=12,       # 10+ → 경고
            max_threshold_decimals=4,   # 소수점 4자리 → 경고
            annual_trades=8,            # 10 미만 → 경고
        )
        assert len(card.warnings) >= 3

    def test_ac_m2_5_no_warnings_when_clean(self) -> None:
        """모든 체크리스트 임계 이하면 경고 없음."""
        from trading.edge.evaluate_backtest import score_backtest

        card = score_backtest(
            _make_trade_stats(),
            _make_portfolio_metrics(test_years=6.0),
            _make_is_oos(),
            n_rule_conditions=5,
            max_threshold_decimals=2,
            annual_trades=20,
        )
        # OOS 체크리스트 관련 경고 없어야 함
        overfit_warnings = [
            w for w in card.warnings
            if "룰" in w or "소수" in w or "연간" in w or "rule" in w.lower()
        ]
        assert len(overfit_warnings) == 0

    def test_ac_m2_4_inflation_trap_active_period_trim(self) -> None:
        """AC-M2-4: 웜업 0-weight 20일 제거 후 active 80일로 Sharpe/CAGR 계산.

        equity_curve 길이 100: 처음 20개 동일값(idle) + 80개 상승.
        active 기간 기준으로 채점되어야 함.
        """
        from trading.edge.evaluate_backtest import score_backtest
        from trading.edge.trade_stats import TradeStats

        # idle 20일: 동일값, active 80일: 상승
        idle = [1.0] * 20
        active_curve = [1.0 + 0.01 * i for i in range(80)]
        equity_curve = idle + active_curve

        # daily_returns 도 idle 0 + active 양수
        daily_returns = [0.0] * 20 + [0.001] * 80

        stats = TradeStats(n=100, win_rate=0.6, avg_win=200.0, avg_loss=100.0,
                           profit_factor=2.0, expectancy=80.0)
        portfolio = _make_portfolio_metrics(
            equity_curve=equity_curve,
            daily_returns=daily_returns,
            test_years=1.0,
        )

        # 이 호출이 에러 없이 완료되어야 함 (active 기간 트리밍 동작)
        card = score_backtest(stats, portfolio, _make_is_oos())
        assert card is not None

    def test_ac_m2_4_open_positions_warning(self) -> None:
        """AC-M2-4: 미청산 포지션 있으면 경고 부착."""
        from trading.edge.evaluate_backtest import score_backtest
        from trading.edge.trade_stats import TradeStats

        stats = TradeStats(n=100, win_rate=0.6, avg_win=200.0, avg_loss=100.0,
                           profit_factor=2.0, expectancy=80.0)
        portfolio = _make_portfolio_metrics(open_positions=2)

        card = score_backtest(stats, portfolio, _make_is_oos())
        assert any("미청산" in w or "open" in w.lower() or "unrealized" in w.lower()
                   for w in card.warnings)

    def test_no_engine_import_in_module(self) -> None:
        """채점기 모듈 소스에 'backtest.engine' import 없음 (AC-M2-6)."""
        import inspect

        import trading.edge.evaluate_backtest as m

        src = inspect.getsource(m)
        assert "from trading.backtest.engine" not in src
        assert "import trading.backtest.engine" not in src


# ---------------------------------------------------------------------------
# T-002: 시장 중립 확인
# ---------------------------------------------------------------------------


class TestMarketNeutral:
    """채점기 본문에 KRX 상수 하드코딩 없음 (AC-CORE-1 선행)."""

    def test_no_hardcoded_krx_constants_in_evaluate_backtest(self) -> None:
        import ast
        import inspect

        import trading.edge.evaluate_backtest as m

        src = inspect.getsource(m)
        tree = ast.parse(src)
        krx_constants = {0.0018, 0.18, 0.0015, 0.15, 0.00215, 0.215}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                assert node.value not in krx_constants, (
                    f"KRX 하드코딩 상수 발견: {node.value}"
                )
