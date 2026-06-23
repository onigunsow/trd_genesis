"""SPEC-TRADING-058 M1/M2 — 저변동성 팩터 + 포트폴리오 구성 + scorecard 어댑터 단위 테스트.

REQ-058-M1-1 : 저변동성 팩터 순수 함수 — 낮은 변동성 = 낮은 랭킹
REQ-058-M1-2 : 결정성 — 동일 입력 → 동일 랭킹
REQ-058-M1-3 : point-in-time — as_of_date 이후 데이터 누출 없음
REQ-058-M1-4 : 이력 부족 종목 명시적 제외 (impute 금지)
REQ-058-M2-1 : 1/N 등가중
REQ-058-M2-2 : 회전율 측정
REQ-058-M2-4a: time-weighted → scorecard 어댑터 (expectancy_adj/profit_factor_adj/n_closed)
REQ-058-M2-5 : 생존편향 fail-CLOSED (achievable=False/absent → bound-only)

설계 원칙:
- 모든 테스트는 픽스처 주입으로 실행 — 네트워크/pykrx/DB 불필요.
- pykrx는 import 금지 (KRX 로그인 사이드이펙트 차단).
- 픽스처 가격 시계열은 결정적 합성 데이터 (np.random seed 고정).
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

# ──────────────────────────────────────────────────────────────────────────────
# 픽스처 헬퍼
# ──────────────────────────────────────────────────────────────────────────────

_TRADING_START = date(2020, 1, 2)  # 픽스처 기준 시작일


def _make_prices(
    tickers: list[str],
    n_days: int,
    *,
    seed: int = 42,
    vols: dict[str, float] | None = None,
    start: date | None = None,
) -> pd.DataFrame:
    """결정적 합성 주가 시계열 생성.

    vols: {ticker: 일간 변동성 (sigma)}. 미지정 시 균등 랜덤.
    낮은 변동성 종목이 낮은 랭킹값을 받는지 검증하는 데 사용한다.
    """
    rng = np.random.default_rng(seed)
    base = start or _TRADING_START
    dates = [base + timedelta(days=i) for i in range(n_days)]
    data: dict[str, list[float]] = {}
    for ticker in tickers:
        vol = (vols or {}).get(ticker, 0.02)
        returns = rng.normal(0.0, vol, size=n_days)
        # 초기 가격 10,000원, 누적 수익률로 가격 생성
        prices = 10_000.0 * np.cumprod(1 + returns)
        data[ticker] = prices.tolist()
    return pd.DataFrame(data, index=dates)


# ──────────────────────────────────────────────────────────────────────────────
# TC-M1-1 : 낮은 변동성 → 낮은 랭킹 (선택 우선순위 높음)
# ──────────────────────────────────────────────────────────────────────────────

class TestLowVolSignalRanking:
    """REQ-058-M1-1: 변동성이 낮은 종목이 낮은 랭킹값(1위)을 받아야 한다."""

    def test_low_vol_ticker_gets_rank_one(self) -> None:
        """변동성이 명확히 다른 3종목에서 가장 낮은 변동성이 rank=1."""
        from trading.backtest.factor_lowvol import compute_low_vol_signal

        # 종목별 일간 변동성: A=저변동, B=중변동, C=고변동
        prices = _make_prices(
            ["A", "B", "C"],
            n_days=150,
            seed=42,
            vols={"A": 0.005, "B": 0.015, "C": 0.035},
        )
        as_of = prices.index[-1]  # 마지막 날짜를 기준으로

        result = compute_low_vol_signal(prices, as_of, lookback=120)

        # 변동성 낮은 순: A < B < C → 랭킹 A=1, B=2, C=3
        assert result.rankings["A"] < result.rankings["B"]
        assert result.rankings["B"] < result.rankings["C"]

    def test_excluded_tickers_not_in_rankings(self) -> None:
        """REQ-058-M1-4: 이력 부족 종목은 rankings에 포함되지 않는다."""
        from trading.backtest.factor_lowvol import compute_low_vol_signal

        prices = _make_prices(["A", "B"], n_days=150, seed=10)
        # as_of를 충분히 이른 날짜로 설정해 B가 lookback=120 미만이 되도록
        # 전체 150일에서 as_of를 100일 시점으로 → A는 100일 이력, lookback=120 미달
        as_of = prices.index[99]

        result = compute_low_vol_signal(prices, as_of, lookback=120)

        # 100일 이력 < lookback 120 → 두 종목 모두 제외되어야 함
        assert len(result.rankings) == 0
        assert set(result.excluded_tickers) == {"A", "B"}

    def test_sufficient_vs_insufficient_history(self) -> None:
        """이력 충분 종목은 랭킹에 포함, 부족 종목은 excluded_tickers에."""
        from trading.backtest.factor_lowvol import compute_low_vol_signal

        # A: 200일 이력, B: 50일 이력 (NaN으로 시작)
        n_days = 200
        base = _TRADING_START
        dates = [base + timedelta(days=i) for i in range(n_days)]

        rng = np.random.default_rng(99)
        prices_a = 10_000.0 * np.cumprod(1 + rng.normal(0, 0.01, n_days))
        prices_b = [np.nan] * 150 + (10_000.0 * np.cumprod(1 + rng.normal(0, 0.02, 50))).tolist()

        df = pd.DataFrame({"A": prices_a, "B": prices_b}, index=dates)
        as_of = df.index[-1]

        result = compute_low_vol_signal(df, as_of, lookback=120)

        # A: 200일 이력 충분 → rankings에 포함
        # B: NaN이 150일 → 실질 이력 50일 < 120 → excluded
        assert "A" in result.rankings
        assert "B" not in result.rankings
        assert "B" in result.excluded_tickers


# ──────────────────────────────────────────────────────────────────────────────
# TC-M1-2 : point-in-time 불변식 — as_of_date 이후 데이터 누출 없음
# ──────────────────────────────────────────────────────────────────────────────

class TestPointInTime:
    """REQ-058-M1-3: as_of_date 이후 데이터를 미래 누출 없이 차단해야 한다."""

    def test_future_bars_do_not_affect_ranking(self) -> None:
        """as_of_date 직전 vs 전체 데이터로 랭킹 계산 시 결과가 동일해야 한다.

        as_of_date 이후 데이터(고변동 이벤트)가 랭킹에 영향을 미치면
        point-in-time 불변식 위반이다.
        """
        from trading.backtest.factor_lowvol import compute_low_vol_signal

        # 기준 시계열: 200일, 조용한 변동성
        prices_base = _make_prices(
            ["A", "B"],
            n_days=200,
            seed=7,
            vols={"A": 0.008, "B": 0.020},
        )
        as_of = prices_base.index[139]  # 140일 시점을 기준으로

        # 기준 시계열로 랭킹 계산
        result_base = compute_low_vol_signal(prices_base, as_of, lookback=120)

        # as_of 이후에 고변동 이벤트 추가 (B의 변동성을 폭발적으로 증가)
        prices_with_future = prices_base.copy()
        future_dates = prices_base.index[140:]
        rng = np.random.default_rng(999)
        # B에 as_of 이후 극단적 변동 추가 (누출되면 랭킹이 뒤집힘)
        future_b = prices_with_future.loc[future_dates, "B"].values
        shock = rng.normal(0, 0.3, len(future_dates))
        prices_with_future.loc[future_dates, "B"] = future_b * np.cumprod(1 + shock)

        result_with_future = compute_low_vol_signal(prices_with_future, as_of, lookback=120)

        # as_of 기준 랭킹은 동일해야 함 (미래 데이터가 영향을 주면 안 됨)
        assert result_base.rankings.to_dict() == result_with_future.rankings.to_dict()


# ──────────────────────────────────────────────────────────────────────────────
# TC-M1-4 : 결정성
# ──────────────────────────────────────────────────────────────────────────────

class TestDeterminism:
    """REQ-058-M1-2: 동일 입력 → 동일 랭킹 (두 번 호출해도 동일)."""

    def test_same_input_same_output(self) -> None:
        from trading.backtest.factor_lowvol import compute_low_vol_signal

        prices = _make_prices(["X", "Y", "Z"], n_days=180, seed=55)
        as_of = prices.index[149]

        r1 = compute_low_vol_signal(prices, as_of, lookback=120)
        r2 = compute_low_vol_signal(prices, as_of, lookback=120)

        pd.testing.assert_series_equal(r1.rankings, r2.rankings)
        assert r1.excluded_tickers == r2.excluded_tickers


# ──────────────────────────────────────────────────────────────────────────────
# TC-M2-1 : 1/N 등가중 검증
# ──────────────────────────────────────────────────────────────────────────────

class TestEqualWeight:
    """REQ-058-M2-1: 선택된 종목의 비중이 1/N 등가중이어야 한다."""

    def test_equal_weight_per_selected_ticker(self) -> None:
        """선택된 종목(최저변동성 분위) 각각의 비중 = 1 / 선택된 종목 수."""
        from trading.backtest.lowvol_portfolio import build_monthly_weights

        # 5종목 픽스처, 다양한 변동성
        prices = _make_prices(
            ["A", "B", "C", "D", "E"],
            n_days=300,
            seed=100,
            vols={"A": 0.005, "B": 0.010, "C": 0.015, "D": 0.025, "E": 0.040},
        )
        # 리밸런스 날짜: prices 중 150일 이후의 첫 날짜
        rebalance_date = prices.index[149]

        weights = build_monthly_weights(
            universe_tickers=["A", "B", "C", "D", "E"],
            prices_df=prices,
            rebalance_dates=[rebalance_date],
            quantile=0.4,   # 상위 40% (=2종목) 선택
            lookback=120,
        )

        # 해당 리밸런스 날짜의 비중 행 추출
        row = weights.loc[rebalance_date]
        nonzero = row[row > 0]

        # 비중 합 = 1.0
        assert abs(nonzero.sum() - 1.0) < 1e-9, f"비중 합={nonzero.sum()}"
        # 각 종목 비중이 동일 (1/N)
        n = len(nonzero)
        for ticker, w in nonzero.items():
            assert abs(w - 1.0 / n) < 1e-9, f"종목 {ticker} 비중={w}, 기대={1/n}"


# ──────────────────────────────────────────────────────────────────────────────
# TC-M2-2 : 월간 리밸런스 비중 변화 검증
# ──────────────────────────────────────────────────────────────────────────────

class TestMonthlyRebalance:
    """REQ-058-M2-1: 월간 리밸런스마다 비중이 갱신된다."""

    def test_weights_change_across_rebalances(self) -> None:
        """두 개의 리밸런스 날짜에서 비중이 독립적으로 계산된다."""
        from trading.backtest.lowvol_portfolio import build_monthly_weights

        prices = _make_prices(
            ["A", "B", "C"],
            n_days=400,
            seed=77,
            vols={"A": 0.008, "B": 0.015, "C": 0.030},
        )
        rb1 = prices.index[149]
        rb2 = prices.index[299]  # 약 150일 후 두 번째 리밸런스

        weights = build_monthly_weights(
            universe_tickers=["A", "B", "C"],
            prices_df=prices,
            rebalance_dates=[rb1, rb2],
            quantile=0.33,
            lookback=120,
        )

        # 두 리밸런스 날짜 모두 weights 행이 존재해야 함
        assert rb1 in weights.index, "첫 번째 리밸런스 날짜가 weights에 없음"
        assert rb2 in weights.index, "두 번째 리밸런스 날짜가 weights에 없음"

        # 각 리밸런스 시점의 비중 합 = 1.0
        for rb in [rb1, rb2]:
            row = weights.loc[rb]
            total = row.sum()
            assert abs(total - 1.0) < 1e-9, f"리밸런스 {rb} 비중 합={total}"


# ──────────────────────────────────────────────────────────────────────────────
# TC-M2-3 : 회전율 측정
# ──────────────────────────────────────────────────────────────────────────────

class TestTurnover:
    """REQ-058-M2-2: 월간 회전율이 측정·보고되어야 한다."""

    def test_zero_turnover_when_same_weights(self) -> None:
        """동일한 비중이 유지되면 회전율 = 0."""
        from trading.backtest.lowvol_portfolio import measure_turnover

        # 두 리밸런스 날짜에 동일 비중
        dates = [date(2020, 1, 31), date(2020, 2, 28)]
        weights = pd.DataFrame(
            {"A": [0.5, 0.5], "B": [0.5, 0.5]},
            index=dates,
        )

        turnover = measure_turnover(weights)
        # 첫 번째 리밸런스는 이전 비중이 없어 NaN 또는 1.0
        # 두 번째 리밸런스: 비중 변화 없음 → 회전율 0
        assert turnover.iloc[-1] == pytest.approx(0.0, abs=1e-9)

    def test_full_turnover_when_portfolio_flips(self) -> None:
        """포트폴리오가 완전히 교체되면 회전율 = 1.0 (100%)."""
        from trading.backtest.lowvol_portfolio import measure_turnover

        dates = [date(2020, 1, 31), date(2020, 2, 28)]
        weights = pd.DataFrame(
            {"A": [1.0, 0.0], "B": [0.0, 1.0]},
            index=dates,
        )

        turnover = measure_turnover(weights)
        # A에서 B로 완전 교체 → 매수 1.0 + 매도 1.0 = 2.0 / 2 = 1.0
        # 또는 단순 합산: |Δw_A| + |Δw_B| = 1 + 1 = 2, /2 = 1.0
        assert turnover.iloc[-1] == pytest.approx(1.0, abs=1e-6)

    def test_turnover_below_50_pct_for_low_vol_portfolio(self) -> None:
        """저변동성 포트폴리오의 회전율이 50% 미만인지 확인 (설계 특성 검증)."""
        from trading.backtest.lowvol_portfolio import build_monthly_weights, measure_turnover

        prices = _make_prices(
            ["A", "B", "C", "D", "E"],
            n_days=500,
            seed=123,
            vols={"A": 0.005, "B": 0.007, "C": 0.020, "D": 0.030, "E": 0.040},
        )
        # 4개 리밸런스 (약 100일 간격)
        rb_dates = [prices.index[i] for i in [149, 249, 349, 449]]

        weights = build_monthly_weights(
            universe_tickers=list(prices.columns),
            prices_df=prices,
            rebalance_dates=rb_dates,
            quantile=0.4,
            lookback=120,
        )
        turnover = measure_turnover(weights)
        # 첫 번째 제외하고 나머지 회전율이 0.5 미만이어야 함 (REQ-058-M2-2)
        subsequent = turnover.dropna().iloc[1:]
        if len(subsequent) > 0:
            assert (subsequent < 0.5).all(), f"회전율 초과: {subsequent.to_dict()}"


# ──────────────────────────────────────────────────────────────────────────────
# TC-M2-4/5 : 생존편향 게이트 fail-CLOSED
# ──────────────────────────────────────────────────────────────────────────────

class TestSurvivorshipGate:
    """REQ-058-M2-5: achievable=False 또는 absent → bound-only, signed alpha 금지."""

    def _make_mock_backtest_result(self) -> object:
        """최소한의 BacktestResult 모사 객체."""
        from trading.backtest.engine import BacktestResult

        dates = pd.date_range("2020-01-02", periods=100, freq="B")
        equity = pd.Series(
            10_000_000.0 * np.cumprod(1 + np.random.default_rng(1).normal(0.001, 0.01, 100)),
            index=dates,
        )
        daily_rets = equity.pct_change().fillna(0.0)
        return BacktestResult(
            cagr=0.12,
            mdd=-0.08,
            sharpe=1.2,
            trades=50,
            final_equity=float(equity.iloc[-1]),
            equity_curve=equity,
            daily_returns=daily_rets,
        )

    def test_achievable_false_returns_bound_only(self) -> None:
        """achievable=False → survivorship_biased=True, signed alpha 금지."""
        from trading.backtest.lowvol_portfolio import check_survivorship_gate

        result = check_survivorship_gate(achievable=False)

        assert result.survivorship_biased is True
        assert "bound" in result.label.lower()

    def test_achievable_none_returns_bound_only(self) -> None:
        """achievable=None (absent) → survivorship_biased=True."""
        from trading.backtest.lowvol_portfolio import check_survivorship_gate

        result = check_survivorship_gate(achievable=None)

        assert result.survivorship_biased is True
        assert "bound" in result.label.lower()

    def test_achievable_true_allows_signed_alpha(self) -> None:
        """achievable=True → survivorship_biased=False (signed alpha 허용)."""
        from trading.backtest.lowvol_portfolio import check_survivorship_gate

        result = check_survivorship_gate(achievable=True)

        assert result.survivorship_biased is False


# ──────────────────────────────────────────────────────────────────────────────
# TC-M2-6/7 : scorecard 어댑터 — GO / NO-GO 경로
# ──────────────────────────────────────────────────────────────────────────────

class TestScorecardAdapter:
    """REQ-058-M2-4a: BacktestResult → Analytics/Benchmark 변환 후 scorecard.decide 호출."""

    def _make_kospi_returns(self, strategy_equity: pd.Series, *, alpha_pct: float) -> pd.Series:
        """전략 대비 alpha_pct만큼 낮은 KOSPI 시계열 반환."""
        strategy_total = (strategy_equity.iloc[-1] / strategy_equity.iloc[0]) - 1.0
        kospi_total = strategy_total - (alpha_pct / 100.0)
        n = len(strategy_equity)
        dates = strategy_equity.index
        kospi_daily = (1 + kospi_total) ** (1 / n) - 1
        kospi_equity = 10_000_000.0 * pd.Series(
            [(1 + kospi_daily) ** i for i in range(1, n + 1)],
            index=dates,
        )
        return kospi_equity.pct_change().fillna(kospi_daily)

    def test_adapter_go_path(self) -> None:
        """우호적 BacktestResult + 양의 알파 → scorecard.decide가 GO 또는 WEAK-GO 반환.

        n_rebalances >= 30 이고 expectancy_adj > 0, profit_factor_adj > 1.0, alpha_pct > 0
        이면 GO여야 한다.
        """
        from trading.backtest.engine import BacktestResult
        from trading.backtest.lowvol_portfolio import adapt_to_scorecard
        from trading.edge.scorecard import VERDICT_GO, VERDICT_WEAK_GO, decide

        # 안정적 상승 시계열 (양의 수익)
        n_days = 252 * 3  # 3년치
        rng = np.random.default_rng(42)
        daily_rets_arr = rng.normal(0.0008, 0.008, n_days)  # 연 20% 수준
        dates = pd.bdate_range("2020-01-02", periods=n_days)
        equity = pd.Series(
            10_000_000.0 * np.cumprod(1 + daily_rets_arr),
            index=dates,
        )
        daily_rets = pd.Series(daily_rets_arr, index=dates)

        br = BacktestResult(
            cagr=float((equity.iloc[-1] / equity.iloc[0]) ** (1 / 3) - 1),
            mdd=-0.05,
            sharpe=1.8,
            trades=100,
            final_equity=float(equity.iloc[-1]),
            equity_curve=equity,
            daily_returns=daily_rets,
        )

        # KOSPI는 전략보다 5%p 낮게 설정 (양의 알파)
        kospi_rets = self._make_kospi_returns(equity, alpha_pct=5.0)

        analytics, benchmark = adapt_to_scorecard(br, kospi_rets, n_rebalances=35)

        # 어댑터 필드 검증
        assert analytics.n_closed == 35
        assert analytics.expectancy_adj > 0, f"expectancy_adj={analytics.expectancy_adj}"
        assert analytics.profit_factor_adj > 1.0, f"profit_factor_adj={analytics.profit_factor_adj}"
        assert benchmark.available is True
        assert benchmark.alpha_pct > 0, f"alpha_pct={benchmark.alpha_pct}"

        # scorecard 판정 검증
        card = decide(analytics, benchmark)
        assert card.verdict in (VERDICT_GO, VERDICT_WEAK_GO), f"판정={card.verdict}"

    def test_adapter_nogo_path(self) -> None:
        """불리한 BacktestResult (음의 수익) → scorecard.decide가 NO-GO 반환."""
        from trading.backtest.engine import BacktestResult
        from trading.backtest.lowvol_portfolio import adapt_to_scorecard
        from trading.edge.scorecard import VERDICT_NO_GO, decide

        # 하락 시계열 (음의 수익)
        n_days = 252 * 2
        rng = np.random.default_rng(99)
        daily_rets_arr = rng.normal(-0.001, 0.015, n_days)  # 음의 드리프트
        dates = pd.bdate_range("2020-01-02", periods=n_days)
        equity = pd.Series(
            10_000_000.0 * np.cumprod(1 + daily_rets_arr),
            index=dates,
        )
        daily_rets = pd.Series(daily_rets_arr, index=dates)

        br = BacktestResult(
            cagr=float((equity.iloc[-1] / equity.iloc[0]) ** (1 / 2) - 1),
            mdd=-0.25,
            sharpe=-0.8,
            trades=80,
            final_equity=float(equity.iloc[-1]),
            equity_curve=equity,
            daily_returns=daily_rets,
        )

        # KOSPI는 전략보다 5%p 높게 (음의 알파)
        kospi_rets = self._make_kospi_returns(equity, alpha_pct=-5.0)

        analytics, benchmark = adapt_to_scorecard(br, kospi_rets, n_rebalances=30)

        # 어댑터 필드 검증: 음의 expectancy
        assert analytics.expectancy_adj < 0 or analytics.profit_factor_adj < 1.0

        # scorecard 판정 검증
        card = decide(analytics, benchmark)
        assert card.verdict == VERDICT_NO_GO, f"판정={card.verdict}, 이유={card.reasons}"

    def test_adapter_n_closed_equals_rebalance_count(self) -> None:
        """REQ-058-M3-5: n_closed는 리밸런스 주기 수여야 함 (round-trip 수 아님)."""
        from trading.backtest.engine import BacktestResult
        from trading.backtest.lowvol_portfolio import adapt_to_scorecard

        n_days = 252
        rng = np.random.default_rng(1)
        daily_rets_arr = rng.normal(0.0005, 0.01, n_days)
        dates = pd.bdate_range("2020-01-02", periods=n_days)
        equity = pd.Series(10_000_000.0 * np.cumprod(1 + daily_rets_arr), index=dates)
        daily_rets = pd.Series(daily_rets_arr, index=dates)

        br = BacktestResult(
            cagr=0.1, mdd=-0.05, sharpe=1.0, trades=1000,  # trades=1000이어도
            final_equity=float(equity.iloc[-1]),
            equity_curve=equity, daily_returns=daily_rets,
        )

        analytics, _ = adapt_to_scorecard(br, None, n_rebalances=12)

        # trades=1000이어도 n_closed는 n_rebalances=12
        assert analytics.n_closed == 12

    def test_adapter_benchmark_unavailable_when_no_kospi(self) -> None:
        """KOSPI 데이터 없음(None) → benchmark.available=False."""
        from trading.backtest.engine import BacktestResult
        from trading.backtest.lowvol_portfolio import adapt_to_scorecard

        n_days = 100
        dates = pd.bdate_range("2020-01-02", periods=n_days)
        equity = pd.Series(10_000_000.0 * np.ones(n_days), index=dates)
        daily_rets = pd.Series(np.zeros(n_days), index=dates)

        br = BacktestResult(
            cagr=0.0, mdd=0.0, sharpe=0.0, trades=0,
            final_equity=10_000_000.0, equity_curve=equity, daily_returns=daily_rets,
        )

        _, benchmark = adapt_to_scorecard(br, None, n_rebalances=5)

        assert benchmark.available is False

    def test_adapter_alpha_is_time_weighted(self) -> None:
        """REQ-058-M2-4/C-7: alpha_pct는 time-weighted (equity curve 기반)이어야 한다.

        money-weighted benchmark.py:120-131 경로를 쓰지 않음을 검증:
        어댑터가 반환하는 alpha_pct = 전략CAGR% - KOSPI_CAGR% (time-weighted).
        """
        from trading.backtest.engine import BacktestResult
        from trading.backtest.lowvol_portfolio import adapt_to_scorecard

        n_days = 252
        rng = np.random.default_rng(5)
        strat_rets_arr = rng.normal(0.0008, 0.01, n_days)
        dates = pd.bdate_range("2020-01-02", periods=n_days)
        equity = pd.Series(10_000_000.0 * np.cumprod(1 + strat_rets_arr), index=dates)
        strat_rets = pd.Series(strat_rets_arr, index=dates)

        # KOSPI를 전략보다 2%p 낮은 CAGR로 설정
        strat_total = (equity.iloc[-1] / equity.iloc[0]) - 1.0
        kospi_total = strat_total - 0.02
        kospi_rets_arr = np.full(n_days, (1 + kospi_total) ** (1 / n_days) - 1)
        kospi_rets = pd.Series(kospi_rets_arr, index=dates)

        br = BacktestResult(
            cagr=float((equity.iloc[-1] / equity.iloc[0]) - 1),
            mdd=-0.05, sharpe=1.0, trades=50,
            final_equity=float(equity.iloc[-1]),
            equity_curve=equity, daily_returns=strat_rets,
        )

        _, benchmark = adapt_to_scorecard(br, kospi_rets, n_rebalances=12)

        assert benchmark.available is True
        # alpha_pct ≈ 2.0 (time-weighted 차이)
        assert benchmark.alpha_pct == pytest.approx(2.0, abs=0.5), (
            f"alpha_pct={benchmark.alpha_pct}: time-weighted 알파 검증 실패"
        )
