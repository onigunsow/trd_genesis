"""SPEC-TRADING-058 M2 — 저변동성 포트폴리오 구성 + 비용 인지 백테스트 + scorecard 어댑터.

REQ-058-M2-1 : 1/N 등가중 + 월간 리밸런스
REQ-058-M2-2 : 회전율 측정 (<50%/월 설계 요구사항)
REQ-058-M2-3 : engine.run 재사용 (새 비용 모델 금지)
REQ-058-M2-4 : 알파 = time-weighted equity-curve 기반
REQ-058-M2-4a: BacktestResult -> Analytics/Benchmark 어댑터 (B3)
REQ-058-M2-5 : 생존편향 fail-CLOSED (achievable=False/absent -> bound-only)
REQ-058-M2-6 : achievable=True 시 as-of-date 유니버스 재구성

설계 원칙:
- engine.run, scorecard.decide를 재사용. 새 비용 모델 생성 금지.
- benchmark.py money-weighted 알파 사용 금지 (C-7, EX-11).
- 순수 함수 또는 주입 가능 의존성 -- 단위 테스트 픽스처 주입 지원.

# @MX:ANCHOR: [AUTO] scorecard 어댑터 -- time-weighted BacktestResult -> Analytics/Benchmark
# @MX:REASON: REQ-058-M2-4a(B3); M3 walk-forward 판정이 이 함수 경유(fan_in >= 2).
#             money-weighted 알파가 이 경로로 재유입되면 GO 게이트 오염된다.
# @MX:SPEC: SPEC-TRADING-058
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date

import pandas as pd

LOG = logging.getLogger(__name__)

# 저변동성 선택 분위 기본값 (낮은 변동성 상위 25%)
DEFAULT_QUANTILE = 0.25
# 저변동성 lookback 기본값 -- factor_lowvol.DEFAULT_LOOKBACK과 동일 소스
_DEFAULT_LOOKBACK = 120


# ──────────────────────────────────────────────────────────────────────────────
# 생존편향 게이트 (REQ-058-M2-5, fail-CLOSED)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SurvivorshipGateResult:
    """생존편향 PRECONDITION GATE 결과.

    survivorship_biased=True  -> signed alpha 보고 금지, bound-only 레이블 강제.
    survivorship_biased=False -> achievable=True 명시 확인됨, signed alpha 허용.
    """

    survivorship_biased: bool
    label: str  # 결과 레이블 (리포트에 표시)
    achievable: bool | None = None


def check_survivorship_gate(achievable: bool | None) -> SurvivorshipGateResult:
    """SPEC-057 achievable 플래그로 생존편향 게이트를 판정한다.

    # @MX:WARN: [AUTO] fail-CLOSED 게이트 -- achievable 부재/None은 bound-only로 처리
    # @MX:REASON: REQ-058-M2-5; 부재를 signed-alpha로 fail-open하면 -14,840 오류 재발.

    Args:
        achievable: SPEC-057 UniverseResult.achievable.
                    True  -> as-of-date 멤버십 확인됨 -> signed alpha 허용.
                    False -> 재구성 불가 -> bound-only.
                    None  -> 기록 부재 -> bound-only (fail-CLOSED).

    Returns:
        SurvivorshipGateResult.
    """
    if achievable is True:
        return SurvivorshipGateResult(
            survivorship_biased=False,
            label="생존편향 게이트 통과 (as-of-date 멤버십 확인됨)",
            achievable=True,
        )

    if achievable is False:
        label = (
            "생존편향 상한 -- signed alpha 보고 금지, bound only "
            "(as-of-date 멤버십 재구성 불가)"
        )
    else:  # None: 기록 부재
        label = (
            "생존편향 상한 -- signed alpha 보고 금지, bound only "
            "(achievable 기록 부재 -- fail-CLOSED 기본값)"
        )

    LOG.warning("survivorship gate: achievable=%s -> bound-only", achievable)
    return SurvivorshipGateResult(
        survivorship_biased=True,
        label=label,
        achievable=achievable,
    )


# ──────────────────────────────────────────────────────────────────────────────
# 포트폴리오 비중 구성 (REQ-058-M2-1)
# ──────────────────────────────────────────────────────────────────────────────

def build_monthly_weights(
    universe_tickers: list[str],
    prices_df: pd.DataFrame,
    rebalance_dates: list[date],
    *,
    quantile: float = DEFAULT_QUANTILE,
    lookback: int = _DEFAULT_LOOKBACK,
) -> pd.DataFrame:
    """저변동성 분위 -> 1/N 등가중 월간 리밸런스 비중 행렬을 구성한다.

    Args:
        universe_tickers: 유니버스 종목 코드 목록.
        prices_df: DataFrame(index=date, columns=ticker, values=close).
        rebalance_dates: 리밸런스 기준일 목록 (월간 cadence 권장).
        quantile: 선택할 저변동성 분위 (0.25 = 하위 25% 변동성 = 최저변동 25%).
        lookback: 변동성 계산 lookback (거래일, 기본 120).

    Returns:
        DataFrame(index=rebalance_date, columns=ticker, values=weight).
        각 행의 비중 합 = 1.0 (1/N 등가중, REQ-058-M2-1).
        선택되지 않은 종목 비중 = 0.0.
    """
    from trading.backtest.factor_lowvol import compute_low_vol_signal

    all_tickers = sorted(set(universe_tickers) & set(prices_df.columns))
    weights_rows: list[dict] = []

    for rb_date in rebalance_dates:
        # point-in-time 팩터 계산 (REQ-058-M1-3 상속)
        result = compute_low_vol_signal(
            prices_df[all_tickers],
            rb_date,
            lookback=lookback,
        )

        if result.rankings.empty:
            # 이력 충분한 종목 없음 -> 해당 리밸런스 건너뜀 (균등 0 비중)
            LOG.warning(
                "build_monthly_weights: %s 리밸런스 -- 랭킹 가능 종목 없음 (전부 0 비중)",
                rb_date,
            )
            weights_rows.append({t: 0.0 for t in all_tickers})
            continue

        # 상위 quantile 종목 선택 (가장 낮은 변동성 = rank 낮음)
        n_total = len(result.rankings)
        n_select = max(1, round(n_total * quantile))
        selected = result.rankings.nsmallest(n_select).index.tolist()

        # 1/N 등가중 (REQ-058-M2-1)
        weight = 1.0 / len(selected)
        row = {t: 0.0 for t in all_tickers}
        for ticker in selected:
            row[ticker] = weight

        weights_rows.append(row)

    if not weights_rows:
        return pd.DataFrame(columns=all_tickers)

    return pd.DataFrame(weights_rows, index=rebalance_dates, columns=all_tickers)


# ──────────────────────────────────────────────────────────────────────────────
# 회전율 측정 (REQ-058-M2-2)
# ──────────────────────────────────────────────────────────────────────────────

def measure_turnover(weights_df: pd.DataFrame) -> pd.Series:
    """리밸런스별 월간 회전율을 계산한다.

    회전율 = (|delta_w_i|의 합) / 2  (매수+매도 양방향을 한 번으로 집계).

    첫 번째 리밸런스는 이전 비중이 없어 NaN.
    50% 초과 시 REQ-058-M2-2 위반.

    Args:
        weights_df: DataFrame(index=rebalance_date, columns=ticker, values=weight).

    Returns:
        Series(index=rebalance_date, values=turnover 0~1).
    """
    if len(weights_df) < 2:
        return pd.Series([float("nan")] * len(weights_df), index=weights_df.index)

    diff = weights_df.diff().abs()
    # 첫 행은 NaN (이전 비중 없음)
    turnover = diff.sum(axis=1) / 2.0
    turnover.iloc[0] = float("nan")
    return turnover


# ──────────────────────────────────────────────────────────────────────────────
# scorecard 어댑터 내부 클래스 (REQ-058-M2-4a, B3)
# 함수보다 먼저 정의 -- forward reference 없이 반환 타입 어노테이션 가능
# ──────────────────────────────────────────────────────────────────────────────

class _AdaptedAnalytics:
    """BacktestResult -> scorecard.decide 호환 Analytics-형 객체.

    scorecard.decide가 읽는 필드:
      - n_closed       : 리밸런스 주기 수 (REQ-058-M3-5)
      - expectancy_adj : 일간 수익률 평균 * 초기자본 (시간가중, 원화 단위)
      - profit_factor_adj: 이익일 합 / 손실일 합의 절댓값

    [HARD] money-weighted 경로 없음. equity-curve / daily_returns만 사용.
    """

    def __init__(
        self,
        n_rebalances: int,
        daily_returns: pd.Series,
        equity_curve: pd.Series,
    ) -> None:
        self.n_closed: int = n_rebalances

        # 초기 자본 (equity_curve 첫 값)
        initial_capital = float(equity_curve.iloc[0]) if len(equity_curve) else 10_000_000.0

        # expectancy_adj: 일간 수익률 평균 * 초기자본 (시간가중 기대값 근사)
        # scorecard가 "원화 단위 기대값 > 0" 조건을 검사함
        if len(daily_returns) > 0:
            mean_ret = float(daily_returns.mean())
            self.expectancy_adj: float = mean_ret * initial_capital
        else:
            self.expectancy_adj = 0.0

        # profit_factor_adj: 이익일 합 / 손실일 합 절댓값 (time-weighted)
        gains = daily_returns[daily_returns > 0]
        losses = daily_returns[daily_returns < 0]
        gross_profit = float(gains.sum()) * initial_capital
        gross_loss = abs(float(losses.sum())) * initial_capital

        if gross_loss > 0:
            self.profit_factor_adj: float = gross_profit / gross_loss
        elif gross_profit > 0:
            self.profit_factor_adj = math.inf
        else:
            self.profit_factor_adj = 0.0

        # scorecard.decide가 참조하는 나머지 필드 (기본값 -- GO 판정에 영향 없음)
        self.has_unrealized: bool = False
        self.unrealized_pnl: float = 0.0


class _AdaptedBenchmark:
    """BacktestResult + KOSPI 수익률 -> scorecard.decide 호환 Benchmark-형 객체.

    [HARD] alpha_pct = 전략 총수익률% - KOSPI 총수익률% (time-weighted).
    benchmark.py:120-131의 money-weighted alpha_pct를 사용하지 않는다.
    """

    def __init__(
        self,
        equity_curve: pd.Series,
        kospi_returns: pd.Series | None,
    ) -> None:
        self.available: bool = False
        self.alpha_pct: float = 0.0

        if kospi_returns is None or len(kospi_returns) == 0:
            return

        if len(equity_curve) < 2:
            return

        # 전략 총수익률 (time-weighted equity-curve 기반)
        strat_total = (float(equity_curve.iloc[-1]) / float(equity_curve.iloc[0])) - 1.0

        # KOSPI 총수익률 (time-weighted: 일간 수익률 누적)
        kospi_equity = (1 + kospi_returns).cumprod()
        if len(kospi_equity) < 1:
            return

        kospi_total = float(kospi_equity.iloc[-1]) - 1.0

        # time-weighted alpha = 전략 총수익률 - KOSPI 총수익률 (백분율)
        self.alpha_pct = (strat_total - kospi_total) * 100.0
        self.available = True


# ──────────────────────────────────────────────────────────────────────────────
# scorecard 어댑터 함수 (공개 API)
# ──────────────────────────────────────────────────────────────────────────────

def adapt_to_scorecard(
    backtest_result: object,
    kospi_returns: pd.Series | None,
    n_rebalances: int,
) -> tuple[_AdaptedAnalytics, _AdaptedBenchmark]:
    """BacktestResult(time-weighted) -> scorecard.decide 입력으로 변환한다.

    [HARD] money-weighted 알파(benchmark.py:120-131)를 사용하지 않는다 (C-7, EX-11).
    알파는 전략 총수익률% - KOSPI 총수익률% (time-weighted equity-curve 기반).

    Args:
        backtest_result: engine.run의 BacktestResult.
        kospi_returns: KOSPI 일간 수익률 pd.Series (index=date).
                       None이면 benchmark.available=False.
        n_rebalances: 리밸런스 주기 수 -> Analytics.n_closed (REQ-058-M3-5).
                      round-trip 수가 아님.

    Returns:
        (analytics, benchmark): scorecard.decide(analytics, benchmark) 직접 사용 가능.

    # @MX:NOTE: [AUTO] n_closed = 리밸런스 수 (trades/round-trip 수 아님)
    # REQ-058-M3-5: n<30 리밸런스 주기 -> INCONCLUSIVE, trades 수로 leak 금지.
    """
    equity: pd.Series = backtest_result.equity_curve  # type: ignore[union-attr]
    daily_rets: pd.Series = backtest_result.daily_returns  # type: ignore[union-attr]

    analytics = _AdaptedAnalytics(n_rebalances, daily_rets, equity)
    benchmark = _AdaptedBenchmark(equity, kospi_returns)
    return analytics, benchmark
