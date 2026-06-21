"""SPEC-TRADING-057 M2 — 진입 피처 OOS 알파 측정기.

REQ-057-M2-1  : 닫힌 측정 목록 (RSI/PER/foreign — score 피처만 랭킹)
REQ-057-M2-2  : point-in-time 기준 랭킹 (미래 데이터 사용 금지)
REQ-057-M2-3  : time-weighted equity-curve 알파 (engine.run 경유)
REQ-057-M2-3a : Bonferroni 다중검정 보정 (양의 부호 ≠ PASS)
REQ-057-M2-3b : 표본 floor 미달 → INCONCLUSIVE
REQ-057-M2-4  : LLM 레이어 백테스트 금지
M1-6b         : achievable=False → "생존편향 상한" 강제 레이블

설계 원칙:
- 모든 provider는 의존성 주입 인자 — 단위 테스트는 픽스처를 주입한다.
- pykrx / DB 는 lazy import (기본 provider 내부에서만) — 테스트 컬렉션 시 import 금지.
- engine.run 만이 알파 계산의 단일 경로 (benchmark.py money-weighted 사용 금지).
- 랭킹 포트폴리오는 score 피처(A 클래스)에만 형성; 하드 게이트(B 클래스)는 대상 아님.
- ADR-057-4: 재사용 가능 표면 — SPEC-058 팩터 백테스트도 동일 인터페이스 사용 가능.

# @MX:ANCHOR: [AUTO] 진입 피처 OOS 알파 측정 진입점
# @MX:REASON: SPEC-057 M2 핵심 함수; fan_in >= 2 예상 (M2 harness + SPEC-058).
#             Bonferroni / 표본floor / achievable 플래그 처리가 모두 여기서 수행된다.
# @MX:SPEC: SPEC-TRADING-057
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd

LOG = logging.getLogger(__name__)

# ── 상수 ─────────────────────────────────────────────────────────────────

# REQ-057-M2-3a: 기본 유의수준 (Bonferroni 분모는 호출자가 전달)
_DEFAULT_ALPHA_LEVEL: float = 0.05

# REQ-057-M2-3b: 기본 표본 floor
_DEFAULT_SAMPLE_FLOOR: int = 30

# REQ-057-M2-1: 닫힌 score 피처 목록 (class A)
SCORE_FEATURES: tuple[str, ...] = ("rsi", "per", "foreign_5d")

# REQ-057-M2-1: 하드 게이트 목록 (class B — 랭킹 포트폴리오 형성 불가)
HARD_GATE_FEATURES: tuple[str, ...] = ("market_cap", "turnover")

# 기본 보유 기간 (리밸런싱 간격, 영업일 기준 근사)
_DEFAULT_HOLDING_DAYS: int = 20

# 기본 상위 quantile
_DEFAULT_TOP_QUANTILE: float = 0.25


# ── 결과 타입 ──────────────────────────────────────────────────────────────

# @MX:NOTE: [AUTO] label 값 집합:
#   PASS              — Bonferroni 유의 + 양의 net_alpha
#   NOT_PASS          — 유의하지 않거나 net_alpha <= 0
#   INCONCLUSIVE      — 표본 floor 미달
#   SURVIVORSHIP_BOUND — achievable=False (부호 보고 금지)
_VALID_LABELS = frozenset(["PASS", "NOT_PASS", "INCONCLUSIVE", "SURVIVORSHIP_BOUND"])


@dataclass
class FeatureAlphaResult:
    """단일 피처의 OOS 알파 측정 결과.

    Attributes:
        feature_name: 측정한 피처 이름 (예: "rsi", "per", "foreign_5d").
        label: PASS / NOT_PASS / INCONCLUSIVE / SURVIVORSHIP_BOUND.
        net_alpha: time-weighted OOS 알파 vs KOSPI.
            SURVIVORSHIP_BOUND이면 None (부호 보고 금지).
        p_value: 기간별 초과수익의 t-검정 p-value (표본 부족 시 None).
        bonferroni_threshold: Bonferroni 보정 유의수준 (alpha / N).
        rebalance_count: 실제 사용된 리밸런싱 기간 수.
        sample_floor: 적용된 표본 floor.
        survivorship_biased: achievable=False 유니버스가 하나라도 있으면 True.
        bound_only: SURVIVORSHIP_BOUND 레이블 시 True (net_alpha 사용 금지 신호).
        detail: 보조 설명 문자열.
        equity_curve: 피처 포트폴리오 equity curve (None 가능).
        kospi_equity_curve: KOSPI equity curve (None 가능).
    """

    feature_name: str
    label: str
    net_alpha: float | None
    p_value: float | None
    bonferroni_threshold: float
    rebalance_count: int
    sample_floor: int
    survivorship_biased: bool
    bound_only: bool
    detail: str
    equity_curve: pd.Series | None = field(default=None, repr=False)
    kospi_equity_curve: pd.Series | None = field(default=None, repr=False)


# ── 공개 헬퍼 함수 ─────────────────────────────────────────────────────────

def select_top_quantile(
    features: dict[str, float | None],
    top_quantile: float = _DEFAULT_TOP_QUANTILE,
) -> list[str]:
    """피처 값 기준 상위 quantile 종목을 반환한다.

    Args:
        features: {ticker: feature_value} — None은 제외.
        top_quantile: 상위 비율 (예: 0.25 = 상위 25%).

    Returns:
        상위 quantile 종목 코드 목록 (피처 내림차순 정렬).
    """
    # None 제거
    valid = [(t, v) for t, v in features.items() if v is not None]
    if not valid:
        return []

    # 피처 내림차순 정렬 (높을수록 좋음)
    valid.sort(key=lambda x: x[1], reverse=True)

    n_select = max(1, round(len(valid) * top_quantile))
    return [t for t, _ in valid[:n_select]]


def equal_weights(tickers: list[str]) -> dict[str, float]:
    """종목 목록에 균등 가중치를 부여한다.

    Args:
        tickers: 가중치를 부여할 종목 목록.

    Returns:
        {ticker: 1/N} — 합계 1.0.
    """
    if not tickers:
        return {}
    w = 1.0 / len(tickers)
    return {t: w for t in tickers}


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

def _compute_period_return(daily_returns: pd.Series) -> float:
    """일별 수익률 시리즈 → 기간 수익률 (time-weighted, 복리)."""
    if len(daily_returns) == 0:
        return 0.0
    return float((1 + daily_returns).prod() - 1)


def _ttest_1samp_p(values: list[float]) -> float:
    """단일 표본 t-검정 p-value (양측, scipy 없이 numpy로 구현).

    H0: 모집단 평균 = 0 (초과수익이 0인지 검정).

    Args:
        values: 검정할 수치 목록 (len >= 2).

    Returns:
        p-value (양측), [0, 1] 범위.
    """
    import math
    import numpy as np

    arr = np.array(values, dtype=float)
    n = len(arr)
    if n < 2:
        return 1.0

    mean = float(arr.mean())
    std = float(arr.std(ddof=1))
    if std == 0.0:
        # 분산 0 → 모든 값이 동일: mean != 0이면 p=0, mean=0이면 p=1
        return 0.0 if abs(mean) > 1e-12 else 1.0

    t_stat = mean / (std / math.sqrt(n))
    df = n - 1

    # t-분포 CDF 근사: regularized incomplete beta function via numpy
    # P(|T| > |t_stat|) = 2 * P(T > |t_stat|) = 2 * (1 - CDF(|t_stat|, df))
    # I(x; a, b) 를 사용: x = df / (df + t^2), a = df/2, b = 0.5
    x = df / (df + t_stat ** 2)
    p_value = float(_betainc(df / 2, 0.5, x))
    # 양측: p = betainc(...) 이 이미 양측 확률을 줌 (Abramowitz & Stegun)
    return max(0.0, min(1.0, p_value))


def _betainc(a: float, b: float, x: float) -> float:
    """정규화된 불완전 베타 함수 I(x; a, b) 근사 (Lentz 연분수, 수치 안정)."""
    import math

    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0

    # 대칭성 활용: x > (a+1)/(a+b+2) 이면 I(x;a,b) = 1 - I(1-x; b, a)
    if x > (a + 1.0) / (a + b + 2.0):
        return 1.0 - _betainc(b, a, 1.0 - x)

    # 로그 베타 상수
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(a * math.log(x) + b * math.log(1.0 - x) - lbeta) / a

    # Lentz 연분수 알고리즘 (Numerical Recipes 6.4)
    TINY = 1.0e-30
    MAX_ITER = 200
    EPS = 3.0e-7

    # Lentz 연분수 — even/odd 계수를 교번하여 적용
    f = TINY
    C = f
    D = 0.0
    delta = 1.0  # 수렴 판정 변수 초기화
    for m in range(MAX_ITER):
        # even step (m=0일 때는 d=1)
        d_even = 1.0 if m == 0 else (m * (b - m) * x) / ((a + 2 * m - 1) * (a + 2 * m))
        for d in (d_even, -((a + m) * (a + b + m) * x) / ((a + 2 * m) * (a + 2 * m + 1))):
            D = 1.0 + d * D
            if abs(D) < TINY:
                D = TINY
            D = 1.0 / D
            C = 1.0 + d / C
            if abs(C) < TINY:
                C = TINY
            delta = C * D
            f *= delta

        if abs(delta - 1.0) < EPS:
            break

    return front * (f - TINY)


def _build_weights_dataframe(
    dates: pd.Index,
    tickers: list[str],
    selected: list[str],
) -> pd.DataFrame:
    """특정 기간 동안 selected 종목에 균등 가중치를 부여한 weights DataFrame을 만든다.

    Args:
        dates: 가중치를 적용할 날짜 인덱스.
        tickers: 전체 종목 목록 (columns).
        selected: 선택된 종목 목록 (균등 가중).

    Returns:
        DataFrame(index=dates, columns=tickers, values=weight or 0).
    """
    weights_row = equal_weights(selected)
    data = {t: weights_row.get(t, 0.0) for t in tickers}
    return pd.DataFrame(
        {t: [data[t]] * len(dates) for t in tickers},
        index=dates,
    )


# @MX:ANCHOR: [AUTO] OOS 알파 측정 핵심 함수 — 단일 피처 기준
# @MX:REASON: REQ-057-M2-3/M2-3a/M2-3b; Bonferroni + floor + achievable 처리.
#             engine.run만이 알파 계산 경로 (benchmark.py 사용 금지).
# @MX:SPEC: SPEC-TRADING-057
def measure_feature_alpha(
    feature_name: str,
    rebalance_dates: list[date],
    universe_provider: Callable[[date], Any],   # -> UniverseResult
    feature_extractor: Callable[[date, list[str]], dict[str, float | None]],
    prices_provider: Callable[[list[str], date, date], pd.DataFrame],
    kospi_returns_provider: Callable[[date, date], pd.Series],
    *,
    top_quantile: float = _DEFAULT_TOP_QUANTILE,
    holding_days: int = _DEFAULT_HOLDING_DAYS,
    sample_floor: int = _DEFAULT_SAMPLE_FLOOR,
    bonferroni_n: int = 3,
    alpha_level: float = _DEFAULT_ALPHA_LEVEL,
) -> FeatureAlphaResult:
    """단일 score 피처의 OOS 알파를 측정한다.

    Args:
        feature_name: 측정할 피처 이름 (REQ-057-M2-1 class A 목록 권장).
        rebalance_dates: 리밸런싱 날짜 목록 (오름차순).
        universe_provider: (date) -> UniverseResult — as-of-date 유니버스 공급.
        feature_extractor: (as_of_date, tickers) -> {ticker: value | None}.
            as_of_date는 반드시 rebalance_date 이하여야 함 (REQ-057-M2-2).
        prices_provider: (tickers, start, end) -> DataFrame(index=date, cols=ticker).
        kospi_returns_provider: (start, end) -> Series(index=date, name=daily_return).
        top_quantile: 상위 quantile 비율 (기본 0.25 = 상위 25%).
        holding_days: 리밸런싱 간격(일) — prices_provider 호출 범위.
        sample_floor: 최소 리밸런싱 횟수 (미달 시 INCONCLUSIVE, REQ-057-M2-3b).
        bonferroni_n: 동시 검정 피처 수 (Bonferroni 분모, REQ-057-M2-3a).
        alpha_level: 전체 유의수준 (기본 0.05).

    Returns:
        FeatureAlphaResult — label/net_alpha/p_value 등 포함.

    Notes:
        - REQ-057-M2-4: LLM 재량 레이어는 측정하지 않는다.
        - REQ-057-M1-6b: achievable=False이면 net_alpha=None, label=SURVIVORSHIP_BOUND.
        - benchmark.py money-weighted 알파(`:120-131`)와 혼용하지 않는다
          (ADR-057-5: 이 함수는 time-weighted equity-curve 전용).
    """
    # numpy는 pyproject.toml 의존성에 포함됨 (scipy 불필요)

    bonferroni_threshold = alpha_level / bonferroni_n

    # ── 생존편향 게이트 확인 ──────────────────────────────────────────────
    # 유니버스 재구성 시도 전 achievable 확인 — 최소 1개라도 False이면 바운드
    # (실제로는 리밸런싱 루프에서 확인하지만, 빈 rebalance_dates 케이스 처리)
    if not rebalance_dates:
        return FeatureAlphaResult(
            feature_name=feature_name,
            label="INCONCLUSIVE",
            net_alpha=None,
            p_value=None,
            bonferroni_threshold=bonferroni_threshold,
            rebalance_count=0,
            sample_floor=sample_floor,
            survivorship_biased=False,
            bound_only=False,
            detail="리밸런싱 날짜가 비어 있음",
        )

    # ── 리밸런싱 루프 — point-in-time 기준 피처 추출 및 수익률 수집 ─────
    # @MX:NOTE: [AUTO] 루프 내부에서 feature_extractor는 반드시 as_of=rebalance_date로만 호출
    #           (REQ-057-M2-2 look-ahead 금지)
    survivorship_biased = False
    period_feature_returns: list[float] = []
    period_kospi_returns: list[float] = []
    all_feature_daily: list[pd.Series] = []
    all_kospi_daily: list[pd.Series] = []

    sorted_dates = sorted(rebalance_dates)

    for i, rebal_date in enumerate(sorted_dates):
        # as-of-date 유니버스 재구성 (M1-6 생존편향 게이트)
        universe = universe_provider(rebal_date)

        # achievable=False → 생존편향 플래그 설정, 데이터 수집 중단
        if not universe.achievable:
            survivorship_biased = True
            LOG.warning(
                "feature_alpha_measurer: %s date=%s achievable=False → "
                "생존편향 상한, 알파 보고 금지",
                feature_name, rebal_date.isoformat(),
            )
            break

        tickers = universe.tickers
        if not tickers:
            LOG.info(
                "feature_alpha_measurer: %s date=%s 유니버스 비어 있음 → 기간 스킵",
                feature_name, rebal_date.isoformat(),
            )
            continue

        # REQ-057-M2-2: feature_extractor는 as_of=rebalance_date로만 호출
        features = feature_extractor(rebal_date, tickers)

        # 상위 quantile 선택
        selected = select_top_quantile(features, top_quantile=top_quantile)
        if not selected:
            LOG.info(
                "feature_alpha_measurer: %s date=%s 선택 종목 없음 → 기간 스킵",
                feature_name, rebal_date.isoformat(),
            )
            continue

        # 보유 기간 결정: 다음 리밸런싱일 또는 holding_days 후
        if i + 1 < len(sorted_dates):
            hold_end = sorted_dates[i + 1] - timedelta(days=1)
        else:
            hold_end = rebal_date + timedelta(days=holding_days)

        # prices 로드
        prices_df = prices_provider(tickers, rebal_date, hold_end)
        if prices_df.empty:
            LOG.info(
                "feature_alpha_measurer: %s date=%s prices 비어 있음 → 기간 스킵",
                feature_name, rebal_date.isoformat(),
            )
            continue

        # engine.run 호환 weights DataFrame 구성
        price_dates = prices_df.index
        price_tickers = list(prices_df.columns)
        selected_in_prices = [t for t in selected if t in price_tickers]
        if not selected_in_prices:
            continue

        weights_df = _build_weights_dataframe(price_dates, price_tickers, selected_in_prices)

        # engine.run → time-weighted 수익률 (REQ-057-M2-3)
        from trading.backtest.engine import run as engine_run

        bt_result = engine_run(prices_df, weights_df)
        feature_period_ret = _compute_period_return(bt_result.daily_returns)

        # KOSPI 수익률 (동일 기간, time-weighted)
        kospi_daily = kospi_returns_provider(rebal_date, hold_end)
        # 날짜 인덱스 정렬하여 겹치는 기간만 사용
        common_idx = bt_result.daily_returns.index.intersection(kospi_daily.index)
        if len(common_idx) == 0:
            kospi_period_ret = _compute_period_return(kospi_daily)
        else:
            kospi_period_ret = _compute_period_return(kospi_daily.loc[common_idx])

        period_feature_returns.append(feature_period_ret)
        period_kospi_returns.append(kospi_period_ret)
        all_feature_daily.append(bt_result.daily_returns)
        all_kospi_daily.append(kospi_daily)

    # ── 생존편향 상한 처리 (REQ-057-M1-6b) ──────────────────────────────
    if survivorship_biased:
        return FeatureAlphaResult(
            feature_name=feature_name,
            label="SURVIVORSHIP_BOUND",
            net_alpha=None,   # 부호 보고 금지
            p_value=None,
            bonferroni_threshold=bonferroni_threshold,
            rebalance_count=len(period_feature_returns),
            sample_floor=sample_floor,
            survivorship_biased=True,
            bound_only=True,
            detail=(
                "생존편향 상한 — 부호 보고 금지 (REQ-057-M1-6b). "
                "as-of-date 유니버스 재구성 불가."
            ),
        )

    # ── 표본 floor 검사 (REQ-057-M2-3b) ─────────────────────────────────
    rebalance_count = len(period_feature_returns)
    if rebalance_count < sample_floor:
        return FeatureAlphaResult(
            feature_name=feature_name,
            label="INCONCLUSIVE",
            net_alpha=(
                float(sum(period_feature_returns) - sum(period_kospi_returns))
                if period_feature_returns
                else None
            ),
            p_value=None,
            bonferroni_threshold=bonferroni_threshold,
            rebalance_count=rebalance_count,
            sample_floor=sample_floor,
            survivorship_biased=False,
            bound_only=False,
            detail=(
                f"표본 부족: 리밸런싱 {rebalance_count}회 < floor {sample_floor}회 "
                f"(REQ-057-M2-3b). 결론 보류."
            ),
        )

    # ── Bonferroni 검정 (REQ-057-M2-3a) ─────────────────────────────────
    import numpy as np

    excess_returns = [f - k for f, k in zip(period_feature_returns, period_kospi_returns)]
    net_alpha_total = float(sum(excess_returns))

    # 기간별 초과수익 t-검정 (H0: mean excess return = 0)
    # numpy 순수 구현 — scipy 불필요, 양측 t-분포 p-value 계산
    if len(excess_returns) >= 2:
        p_value = _ttest_1samp_p(excess_returns)
    else:
        p_value = 1.0   # 단일 관측으로는 검정 불가

    # equity curve 합산 (참고용)
    combined_feature_daily = (
        pd.concat(all_feature_daily).sort_index() if all_feature_daily else pd.Series(dtype=float)
    )
    combined_kospi_daily = (
        pd.concat(all_kospi_daily).sort_index() if all_kospi_daily else pd.Series(dtype=float)
    )

    # PASS 판정: Bonferroni 유의 + 양의 알파
    if net_alpha_total > 0 and p_value <= bonferroni_threshold:
        label = "PASS"
        detail = (
            f"Bonferroni 유의 (p={p_value:.4f} <= threshold={bonferroni_threshold:.4f}), "
            f"net_alpha={net_alpha_total:+.4f}"
        )
    else:
        label = "NOT_PASS"
        if net_alpha_total <= 0:
            detail = f"알파 음수 또는 0 (net_alpha={net_alpha_total:+.4f})"
        else:
            detail = (
                f"알파 양수이나 Bonferroni 유의 미달 "
                f"(p={p_value:.4f} > threshold={bonferroni_threshold:.4f}). "
                f"양의 부호 ≠ PASS (REQ-057-M2-3a)."
            )

    return FeatureAlphaResult(
        feature_name=feature_name,
        label=label,
        net_alpha=net_alpha_total,
        p_value=p_value,
        bonferroni_threshold=bonferroni_threshold,
        rebalance_count=rebalance_count,
        sample_floor=sample_floor,
        survivorship_biased=False,
        bound_only=False,
        detail=detail,
        equity_curve=combined_feature_daily,
        kospi_equity_curve=combined_kospi_daily,
    )
