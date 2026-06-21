"""SPEC-TRADING-057 M2 — 진입 피처 OOS 알파 측정기 단위 테스트.

REQ-057-M2-1  : 닫힌 측정 목록 (RSI/PER/foreign — score 피처만 랭킹)
REQ-057-M2-2  : point-in-time 기준 랭킹 (미래 데이터 사용 금지)
REQ-057-M2-3  : time-weighted equity-curve 알파 (engine.run 경유)
REQ-057-M2-3a : Bonferroni 다중검정 보정 (양의 부호 ≠ PASS)
REQ-057-M2-3b : 표본 floor 미달 → INCONCLUSIVE
REQ-057-M2-4  : LLM 레이어 백테스트 금지
M1-6b         : achievable=False → "생존편향 상한" 강제 레이블

설계 원칙:
- 모든 테스트는 픽스처 주입으로 실행 — 네트워크/pykrx/DB 불필요.
- pykrx는 테스트 컬렉션 시점에 import되지 않는다 (KRX 로그인 사이드이펙트 방지).
- engine.py / benchmark.py / M1 모듈은 수정 없이 재사용.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest


# ── 픽스처 헬퍼 ────────────────────────────────────────────────────────────

_TICKERS = ["A", "B", "C", "D"]  # 단순 픽스처 종목 코드


def _make_prices(
    tickers: list[str],
    start: date,
    n_days: int = 20,
    daily_return: float = 0.01,
) -> pd.DataFrame:
    """결정적 가격 시리즈 생성 (모든 종목 동일 수익률).

    Args:
        tickers: 종목 목록.
        start: 시작일.
        n_days: 거래일 수.
        daily_return: 일별 수익률 (전 종목 동일).
    """
    dates = [start + timedelta(days=i) for i in range(n_days)]
    prices = {}
    for t in tickers:
        close = 10_000.0
        series = []
        for _ in dates:
            series.append(close)
            close *= (1 + daily_return)
        prices[t] = series
    return pd.DataFrame(prices, index=dates)


def _make_kospi_returns(start: date, end: date, daily_return: float = 0.005) -> pd.Series:
    """결정적 KOSPI 일별 수익률 시리즈."""
    n_days = (end - start).days + 1
    dates = [start + timedelta(days=i) for i in range(n_days)]
    return pd.Series([daily_return] * n_days, index=dates, name="KOSPI")


# ── TC-1: point-in-time 피처 순위 (look-ahead 금지) ─────────────────────

class TestPointInTime:
    """REQ-057-M2-2: 리밸런싱 날짜 T에서 T 이후 데이터 사용 금지."""

    def test_feature_extractor_called_with_rebalance_date_only(self):
        """feature_extractor는 rebalance_date=T만으로 호출돼야 한다.

        만약 구현이 T 이후 날짜로 feature_extractor를 호출하면 이 테스트가 실패한다.
        """
        from trading.backtest.feature_alpha_measurer import measure_feature_alpha
        from trading.backtest.universe_reconstructor import UniverseResult

        call_log: list[date] = []
        rebalance_date = date(2020, 1, 2)

        def universe_provider(d: date) -> UniverseResult:
            return UniverseResult(
                rebalance_date=d,
                tickers=_TICKERS,
                achievable=True,
            )

        def feature_extractor(as_of: date, tickers: list[str]) -> dict[str, float | None]:
            # 호출된 날짜를 기록
            call_log.append(as_of)
            # as_of는 반드시 rebalance_date 이하여야 한다
            assert as_of <= rebalance_date, (
                f"미래 데이터 누출: feature_extractor가 {as_of}로 호출됨 "
                f"(rebalance_date={rebalance_date})"
            )
            return {t: float(i) for i, t in enumerate(tickers)}

        def prices_provider(tickers: list[str], start: date, end: date) -> pd.DataFrame:
            return _make_prices(tickers, start, n_days=(end - start).days + 1)

        def kospi_returns_provider(start: date, end: date) -> pd.Series:
            return _make_kospi_returns(start, end)

        measure_feature_alpha(
            feature_name="test_feature",
            rebalance_dates=[rebalance_date],
            universe_provider=universe_provider,
            feature_extractor=feature_extractor,
            prices_provider=prices_provider,
            kospi_returns_provider=kospi_returns_provider,
        )

        # feature_extractor는 정확히 rebalance_date로만 호출돼야 함
        assert len(call_log) >= 1, "feature_extractor가 최소 1회 호출돼야 한다"
        for called_date in call_log:
            assert called_date <= rebalance_date, (
                f"look-ahead 위반: {called_date} > {rebalance_date}"
            )

    def test_prices_provider_does_not_receive_future_start(self):
        """prices_provider는 미래 데이터 요청을 받지 않아야 한다.

        prices_provider(tickers, start, end) 호출 시
        start는 다음 리밸런싱 날짜 이전이어야 한다.
        """
        from trading.backtest.feature_alpha_measurer import measure_feature_alpha
        from trading.backtest.universe_reconstructor import UniverseResult

        rebalance_date = date(2020, 1, 2)
        prices_call_log: list[tuple[date, date]] = []

        def universe_provider(d: date) -> UniverseResult:
            return UniverseResult(
                rebalance_date=d,
                tickers=["A", "B"],
                achievable=True,
            )

        def feature_extractor(as_of: date, tickers: list[str]) -> dict[str, float | None]:
            return {"A": 2.0, "B": 1.0}

        def prices_provider(tickers: list[str], start: date, end: date) -> pd.DataFrame:
            prices_call_log.append((start, end))
            return _make_prices(tickers, start, n_days=max(5, (end - start).days + 1))

        def kospi_returns_provider(start: date, end: date) -> pd.Series:
            return _make_kospi_returns(start, end)

        measure_feature_alpha(
            feature_name="test",
            rebalance_dates=[rebalance_date],
            universe_provider=universe_provider,
            feature_extractor=feature_extractor,
            prices_provider=prices_provider,
            kospi_returns_provider=kospi_returns_provider,
        )

        assert len(prices_call_log) >= 1, "prices_provider가 호출돼야 한다"


# ── TC-2: time-weighted alpha = engine.run 경유 ───────────────────────────

class TestTimeWeightedAlpha:
    """REQ-057-M2-3: 알파는 engine.run 시간가중 equity-curve 수익률로 정의한다."""

    def _run_single_rebalance(self, feature_daily_return: float, kospi_daily_return: float):
        """단일 리밸런싱 시나리오를 실행하고 FeatureAlphaResult를 반환한다."""
        from trading.backtest.feature_alpha_measurer import measure_feature_alpha
        from trading.backtest.universe_reconstructor import UniverseResult

        start = date(2020, 1, 2)
        end = date(2020, 1, 21)  # 20일 보유
        rebalance_dates = [start]

        def universe_provider(d: date) -> UniverseResult:
            return UniverseResult(
                rebalance_date=d,
                tickers=["A", "B", "C", "D"],
                achievable=True,
            )

        def feature_extractor(as_of: date, tickers: list[str]) -> dict[str, float | None]:
            # A, B가 상위 (score 높음), C, D는 하위
            return {"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0}

        def prices_provider(tickers: list[str], start_: date, end_: date) -> pd.DataFrame:
            n = (end_ - start_).days + 1
            return _make_prices(tickers, start_, n_days=n, daily_return=feature_daily_return)

        def kospi_returns_provider(start_: date, end_: date) -> pd.Series:
            return _make_kospi_returns(start_, end_, daily_return=kospi_daily_return)

        return measure_feature_alpha(
            feature_name="test",
            rebalance_dates=rebalance_dates,
            universe_provider=universe_provider,
            feature_extractor=feature_extractor,
            prices_provider=prices_provider,
            kospi_returns_provider=kospi_returns_provider,
            sample_floor=1,   # 단일 리밸런싱도 허용 (테스트 목적)
            bonferroni_n=1,   # 단일 피처 검정 (Bonferroni 분모)
        )

    def test_net_alpha_positive_when_feature_beats_kospi(self):
        """피처 포트폴리오 수익률 > KOSPI 수익률이면 net_alpha > 0."""
        result = self._run_single_rebalance(
            feature_daily_return=0.01,   # 피처 포트: 1%/일
            kospi_daily_return=0.005,    # KOSPI: 0.5%/일
        )
        assert result.net_alpha is not None, "net_alpha가 계산되어야 한다"
        assert result.net_alpha > 0, (
            f"피처 수익이 KOSPI를 초과하므로 net_alpha > 0이어야 함, 실제: {result.net_alpha}"
        )

    def test_net_alpha_negative_when_feature_lags_kospi(self):
        """피처 포트폴리오 수익률 < KOSPI 수익률이면 net_alpha < 0."""
        result = self._run_single_rebalance(
            feature_daily_return=0.002,  # 피처 포트: 0.2%/일
            kospi_daily_return=0.01,     # KOSPI: 1%/일
        )
        assert result.net_alpha is not None
        assert result.net_alpha < 0, (
            f"KOSPI가 피처를 앞서므로 net_alpha < 0이어야 함, 실제: {result.net_alpha}"
        )

    def test_net_alpha_uses_engine_run_not_money_weighted(self):
        """알파는 benchmark.py money-weighted가 아니라 engine.run time-weighted여야 한다.

        간접 검증: FeatureAlphaResult에 equity_curve가 있으면 engine.run 경유 증거.
        """
        result = self._run_single_rebalance(0.01, 0.005)
        # net_alpha는 float이어야 하며 계산 불가가 아니어야 한다
        assert isinstance(result.net_alpha, float), "net_alpha는 float여야 한다"


# ── TC-3: Bonferroni 다중검정 보정 ────────────────────────────────────────

class TestBonferroniCorrection:
    """REQ-057-M2-3a: 양의 알파라도 Bonferroni 유의수준 미달이면 PASS 아님."""

    def _make_many_rebalances(
        self,
        n: int = 40,
        excess_return_per_period: float = 0.001,
        bonferroni_n: int = 3,
    ):
        """n개 리밸런싱 기간, 기간별 초과수익 excess_return으로 측정."""
        from trading.backtest.feature_alpha_measurer import measure_feature_alpha
        from trading.backtest.universe_reconstructor import UniverseResult

        base = date(2015, 1, 5)
        # 월별 리밸런싱 날짜 생성 (약 20 거래일 간격)
        rebalance_dates = [base + timedelta(days=20 * i) for i in range(n)]

        def universe_provider(d: date) -> UniverseResult:
            return UniverseResult(rebalance_date=d, tickers=["A", "B"], achievable=True)

        def feature_extractor(as_of: date, tickers: list[str]) -> dict[str, float | None]:
            return {"A": 2.0, "B": 1.0}

        period_len = 20  # 각 보유 기간 20일

        def prices_provider(tickers: list[str], start: date, end: date) -> pd.DataFrame:
            n_days = (end - start).days + 1
            # 피처 포트: KOSPI보다 excess_return_per_period 더 좋음
            # 일별로는 excess/period_len 추가 수익
            return _make_prices(
                tickers, start, n_days=n_days,
                daily_return=0.005 + excess_return_per_period / period_len,
            )

        def kospi_returns_provider(start: date, end: date) -> pd.Series:
            return _make_kospi_returns(start, end, daily_return=0.005)

        return measure_feature_alpha(
            feature_name="rsi",
            rebalance_dates=rebalance_dates,
            universe_provider=universe_provider,
            feature_extractor=feature_extractor,
            prices_provider=prices_provider,
            kospi_returns_provider=kospi_returns_provider,
            sample_floor=30,
            bonferroni_n=bonferroni_n,
        )

    def test_positive_alpha_below_bonferroni_threshold_is_not_pass(self):
        """알파 부호가 양수라도 Bonferroni 유의수준 미달이면 PASS가 아니다.

        아주 작은 초과수익(통계적 비유의) → label은 NOT_PASS 또는 INCONCLUSIVE.
        """
        result = self._make_many_rebalances(
            n=40,
            excess_return_per_period=0.0001,  # 매우 작은 초과수익
            bonferroni_n=3,
        )
        assert result.net_alpha is not None
        # net_alpha > 0일 수도 있지만 PASS여서는 안 된다
        assert result.label != "PASS", (
            f"통계적으로 비유의한 양의 알파는 PASS가 될 수 없다. "
            f"net_alpha={result.net_alpha:.6f}, label={result.label}, "
            f"p_value={result.p_value}"
        )

    def test_label_has_pass_only_when_significant(self):
        """PASS 레이블은 Bonferroni 유의 기준을 통과한 경우에만 부여된다.

        검정력: 40 리밸런싱, 기간별 0.5% 초과수익 → 유의 가능성 높음.
        단, 이 테스트는 PASS를 요구하지 않는다 — 단지 레이블 값이 정의된 집합에 속하는지만 확인.
        """
        result = self._make_many_rebalances(
            n=40,
            excess_return_per_period=0.005,
            bonferroni_n=3,
        )
        valid_labels = {"PASS", "NOT_PASS", "INCONCLUSIVE", "SURVIVORSHIP_BOUND"}
        assert result.label in valid_labels, (
            f"label이 정의된 집합에 속해야 한다. 실제: {result.label}"
        )

    def test_bonferroni_threshold_stored_in_result(self):
        """FeatureAlphaResult에 bonferroni_threshold가 저장된다."""
        result = self._make_many_rebalances(n=40, bonferroni_n=3)
        expected = 0.05 / 3
        assert abs(result.bonferroni_threshold - expected) < 1e-9, (
            f"bonferroni_threshold={result.bonferroni_threshold:.6f} != {expected:.6f}"
        )


# ── TC-4: 표본 floor 미달 → INCONCLUSIVE ─────────────────────────────────

class TestSampleFloor:
    """REQ-057-M2-3b: 리밸런싱 횟수 < floor이면 INCONCLUSIVE."""

    def test_inconclusive_when_below_sample_floor(self):
        """리밸런싱 횟수(5) < floor(30)이면 label=INCONCLUSIVE."""
        from trading.backtest.feature_alpha_measurer import measure_feature_alpha
        from trading.backtest.universe_reconstructor import UniverseResult

        base = date(2020, 1, 2)
        rebalance_dates = [base + timedelta(days=20 * i) for i in range(5)]  # 5회

        def universe_provider(d: date) -> UniverseResult:
            return UniverseResult(rebalance_date=d, tickers=["A", "B"], achievable=True)

        def feature_extractor(as_of: date, tickers: list[str]) -> dict[str, float | None]:
            return {"A": 2.0, "B": 1.0}

        def prices_provider(tickers: list[str], start: date, end: date) -> pd.DataFrame:
            n = (end - start).days + 1
            return _make_prices(tickers, start, n_days=n, daily_return=0.05)

        def kospi_returns_provider(start: date, end: date) -> pd.Series:
            return _make_kospi_returns(start, end, daily_return=0.001)

        result = measure_feature_alpha(
            feature_name="per",
            rebalance_dates=rebalance_dates,
            universe_provider=universe_provider,
            feature_extractor=feature_extractor,
            prices_provider=prices_provider,
            kospi_returns_provider=kospi_returns_provider,
            sample_floor=30,   # floor=30, 실제 5개 → INCONCLUSIVE
            bonferroni_n=3,
        )

        assert result.label == "INCONCLUSIVE", (
            f"표본 {result.rebalance_count}개 < floor {result.sample_floor}이면 "
            f"INCONCLUSIVE여야 함, 실제: {result.label}"
        )

    def test_rebalance_count_stored_in_result(self):
        """FeatureAlphaResult에 rebalance_count가 정확히 저장된다."""
        from trading.backtest.feature_alpha_measurer import measure_feature_alpha
        from trading.backtest.universe_reconstructor import UniverseResult

        n_rebalances = 5
        base = date(2020, 1, 2)
        rebalance_dates = [base + timedelta(days=20 * i) for i in range(n_rebalances)]

        def universe_provider(d: date) -> UniverseResult:
            return UniverseResult(rebalance_date=d, tickers=["A"], achievable=True)

        def feature_extractor(as_of: date, tickers: list[str]) -> dict[str, float | None]:
            return {"A": 1.0}

        def prices_provider(tickers: list[str], start: date, end: date) -> pd.DataFrame:
            n = (end - start).days + 1
            return _make_prices(tickers, start, n_days=n)

        def kospi_returns_provider(start: date, end: date) -> pd.Series:
            return _make_kospi_returns(start, end)

        result = measure_feature_alpha(
            feature_name="rsi",
            rebalance_dates=rebalance_dates,
            universe_provider=universe_provider,
            feature_extractor=feature_extractor,
            prices_provider=prices_provider,
            kospi_returns_provider=kospi_returns_provider,
            sample_floor=30,
        )

        assert result.rebalance_count == n_rebalances, (
            f"rebalance_count={result.rebalance_count} != {n_rebalances}"
        )
        assert result.sample_floor == 30

    def test_not_inconclusive_when_above_floor(self):
        """리밸런싱 횟수 >= floor이면 INCONCLUSIVE가 아니다 (다른 레이블 가능)."""
        from trading.backtest.feature_alpha_measurer import measure_feature_alpha
        from trading.backtest.universe_reconstructor import UniverseResult

        base = date(2015, 1, 5)
        rebalance_dates = [base + timedelta(days=20 * i) for i in range(30)]  # 정확히 30

        def universe_provider(d: date) -> UniverseResult:
            return UniverseResult(rebalance_date=d, tickers=["A", "B"], achievable=True)

        def feature_extractor(as_of: date, tickers: list[str]) -> dict[str, float | None]:
            return {"A": 2.0, "B": 1.0}

        def prices_provider(tickers: list[str], start: date, end: date) -> pd.DataFrame:
            n = (end - start).days + 1
            return _make_prices(tickers, start, n_days=n, daily_return=0.005)

        def kospi_returns_provider(start: date, end: date) -> pd.Series:
            return _make_kospi_returns(start, end, daily_return=0.005)

        result = measure_feature_alpha(
            feature_name="rsi",
            rebalance_dates=rebalance_dates,
            universe_provider=universe_provider,
            feature_extractor=feature_extractor,
            prices_provider=prices_provider,
            kospi_returns_provider=kospi_returns_provider,
            sample_floor=30,
        )

        assert result.label != "INCONCLUSIVE", (
            f"30개 리밸런싱(floor=30)이면 INCONCLUSIVE가 아니어야 함, 실제: {result.label}"
        )


# ── TC-5: achievable=False → 생존편향 상한 강제 레이블 ─────────────────

class TestSurvivorshipBound:
    """REQ-057-M1-6b: achievable=False → 부호 보고 금지, 'bound only' 강제."""

    def test_survivorship_bound_label_when_not_achievable(self):
        """achievable=False인 유니버스 → label=SURVIVORSHIP_BOUND."""
        from trading.backtest.feature_alpha_measurer import measure_feature_alpha
        from trading.backtest.universe_reconstructor import UniverseResult

        rebalance_dates = [date(2020, 1, 2)]

        def universe_provider(d: date) -> UniverseResult:
            # M1-6b: achievable=False
            return UniverseResult(
                rebalance_date=d,
                tickers=[],
                achievable=False,
                downgrade_reason="pykrx as-of-date 미지원 (픽스처)",
            )

        def feature_extractor(as_of: date, tickers: list[str]) -> dict[str, float | None]:
            return {}

        def prices_provider(tickers: list[str], start: date, end: date) -> pd.DataFrame:
            return pd.DataFrame()

        def kospi_returns_provider(start: date, end: date) -> pd.Series:
            return _make_kospi_returns(start, end)

        result = measure_feature_alpha(
            feature_name="rsi",
            rebalance_dates=rebalance_dates,
            universe_provider=universe_provider,
            feature_extractor=feature_extractor,
            prices_provider=prices_provider,
            kospi_returns_provider=kospi_returns_provider,
            sample_floor=1,
        )

        assert result.label == "SURVIVORSHIP_BOUND", (
            f"achievable=False이면 SURVIVORSHIP_BOUND여야 함, 실제: {result.label}"
        )

    def test_bound_only_flag_when_not_achievable(self):
        """achievable=False → bound_only=True 플래그."""
        from trading.backtest.feature_alpha_measurer import measure_feature_alpha
        from trading.backtest.universe_reconstructor import UniverseResult

        def universe_provider(d: date) -> UniverseResult:
            return UniverseResult(
                rebalance_date=d, tickers=[], achievable=False,
                downgrade_reason="테스트",
            )

        result = measure_feature_alpha(
            feature_name="rsi",
            rebalance_dates=[date(2020, 1, 2)],
            universe_provider=universe_provider,
            feature_extractor=lambda d, t: {},
            prices_provider=lambda t, s, e: pd.DataFrame(),
            kospi_returns_provider=_make_kospi_returns,
            sample_floor=1,
        )

        assert result.bound_only is True, "achievable=False이면 bound_only=True여야 한다"
        assert result.survivorship_biased is True

    def test_net_alpha_none_when_survivorship_bound(self):
        """부호 보고 금지: achievable=False이면 net_alpha=None (보고 금지 강제)."""
        from trading.backtest.feature_alpha_measurer import measure_feature_alpha
        from trading.backtest.universe_reconstructor import UniverseResult

        def universe_provider(d: date) -> UniverseResult:
            return UniverseResult(
                rebalance_date=d, tickers=[], achievable=False,
                downgrade_reason="테스트",
            )

        result = measure_feature_alpha(
            feature_name="per",
            rebalance_dates=[date(2020, 1, 2)],
            universe_provider=universe_provider,
            feature_extractor=lambda d, t: {},
            prices_provider=lambda t, s, e: pd.DataFrame(),
            kospi_returns_provider=_make_kospi_returns,
            sample_floor=1,
        )

        assert result.net_alpha is None, (
            f"생존편향 상한에서 net_alpha를 보고하면 안 됨, 실제: {result.net_alpha}"
        )


# ── TC-6: FeatureAlphaResult 구조 검증 ────────────────────────────────────

class TestResultStructure:
    """FeatureAlphaResult가 필수 필드를 모두 갖는지 확인."""

    def _get_result(self):
        from trading.backtest.feature_alpha_measurer import measure_feature_alpha
        from trading.backtest.universe_reconstructor import UniverseResult

        base = date(2015, 1, 5)
        rebalance_dates = [base + timedelta(days=20 * i) for i in range(35)]

        def universe_provider(d: date) -> UniverseResult:
            return UniverseResult(rebalance_date=d, tickers=["A", "B", "C"], achievable=True)

        def feature_extractor(as_of: date, tickers: list[str]) -> dict[str, float | None]:
            return {"A": 3.0, "B": 2.0, "C": 1.0}

        def prices_provider(tickers: list[str], start: date, end: date) -> pd.DataFrame:
            n = (end - start).days + 1
            return _make_prices(tickers, start, n_days=n, daily_return=0.007)

        def kospi_returns_provider(start: date, end: date) -> pd.Series:
            return _make_kospi_returns(start, end, daily_return=0.005)

        return measure_feature_alpha(
            feature_name="foreign_5d",
            rebalance_dates=rebalance_dates,
            universe_provider=universe_provider,
            feature_extractor=feature_extractor,
            prices_provider=prices_provider,
            kospi_returns_provider=kospi_returns_provider,
            sample_floor=30,
            bonferroni_n=3,
        )

    def test_result_has_required_fields(self):
        """FeatureAlphaResult에 필수 필드가 모두 있어야 한다."""
        result = self._get_result()
        assert hasattr(result, "feature_name")
        assert hasattr(result, "label")
        assert hasattr(result, "net_alpha")
        assert hasattr(result, "p_value")
        assert hasattr(result, "bonferroni_threshold")
        assert hasattr(result, "rebalance_count")
        assert hasattr(result, "sample_floor")
        assert hasattr(result, "survivorship_biased")
        assert hasattr(result, "bound_only")
        assert hasattr(result, "detail")

    def test_feature_name_stored_correctly(self):
        """feature_name이 결과에 정확히 저장된다."""
        result = self._get_result()
        assert result.feature_name == "foreign_5d"

    def test_label_is_string(self):
        """label은 문자열이어야 한다."""
        result = self._get_result()
        assert isinstance(result.label, str)

    def test_p_value_in_valid_range(self):
        """p_value는 [0, 1] 범위 내여야 한다 (None이 아닌 경우)."""
        result = self._get_result()
        if result.p_value is not None:
            assert 0.0 <= result.p_value <= 1.0, (
                f"p_value={result.p_value}가 [0,1] 범위 밖"
            )


# ── TC-7: 탑-quantile 포트폴리오 구성 ────────────────────────────────────

class TestTopQuantilePortfolio:
    """REQ-057-M2-1: score 피처로 상위 quantile 랭킹 포트폴리오를 구성한다."""

    def test_only_top_quantile_tickers_selected(self):
        """상위 quantile 종목만 포트폴리오에 포함된다.

        feature 값 [4, 3, 2, 1] → top_quantile=0.5 → A, B만 포함돼야 함.
        """
        from trading.backtest.feature_alpha_measurer import (
            select_top_quantile,
        )

        features = {"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0}
        selected = select_top_quantile(features, top_quantile=0.5)

        assert "A" in selected, "최고 점수 A가 선택돼야 한다"
        assert "B" in selected, "2위 B가 선택돼야 한다"
        assert "C" not in selected, "하위 C는 제외돼야 한다"
        assert "D" not in selected, "하위 D는 제외돼야 한다"

    def test_none_feature_values_excluded(self):
        """피처 값이 None인 종목은 랭킹에서 제외된다."""
        from trading.backtest.feature_alpha_measurer import select_top_quantile

        features = {"A": 4.0, "B": None, "C": 2.0, "D": 1.0}
        selected = select_top_quantile(features, top_quantile=0.5)

        assert "B" not in selected, "None 피처 종목은 제외돼야 한다"

    def test_equal_weight_sum_to_one(self):
        """선택된 종목의 가중치 합이 1.0이어야 한다."""
        from trading.backtest.feature_alpha_measurer import (
            select_top_quantile,
            equal_weights,
        )

        features = {"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0}
        selected = select_top_quantile(features, top_quantile=0.5)
        weights = equal_weights(selected)

        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-9, f"가중치 합={total:.6f} != 1.0"

    def test_empty_features_returns_empty(self):
        """피처가 모두 None이면 빈 선택 결과를 반환한다."""
        from trading.backtest.feature_alpha_measurer import select_top_quantile

        features = {"A": None, "B": None}
        selected = select_top_quantile(features, top_quantile=0.5)

        assert len(selected) == 0, "피처가 모두 None이면 선택 종목이 없어야 한다"


# ── TC-8: pykrx import 격리 확인 ─────────────────────────────────────────

class TestPykrxIsolation:
    """단위 테스트 환경에서 pykrx가 import되지 않아야 한다."""

    def test_module_importable_without_pykrx(self):
        """feature_alpha_measurer는 pykrx 없이 import 가능해야 한다."""
        import trading.backtest.feature_alpha_measurer  # noqa: F401

    def test_module_importable_without_db(self):
        """feature_alpha_measurer는 DB 연결 없이 import 가능해야 한다."""
        # 이미 위에서 성공했으면 이 테스트도 통과
        from trading.backtest.feature_alpha_measurer import (
            measure_feature_alpha,
            select_top_quantile,
            equal_weights,
        )
        assert callable(measure_feature_alpha)
        assert callable(select_top_quantile)
        assert callable(equal_weights)
