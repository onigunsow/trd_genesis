"""T-002/T-003 GREEN — 5차원 백테스트 채점기 순수 함수.

SPEC-TRADING-048 REQ-048-M2-1/2/3/4/5.
AC: AC-M2-1(REJECT firewall), AC-M2-1b(PASS 정상), AC-M2-3, AC-M2-4, AC-M2-5, AC-M2-6.

# @MX:NOTE: [AUTO] 시장 중립 순수 함수 — backtest.engine import 없음.
# 입력(trade_stats, portfolio_metrics, is_oos)은 모두 호출자가 주입한다.
# @MX:SPEC: SPEC-TRADING-048 REQ-048-M2-1
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from trading.edge.trade_stats import TradeStats

# ---------------------------------------------------------------------------
# 판정 상수
# ---------------------------------------------------------------------------

VERDICT_PASS = "PASS"
VERDICT_REVISE = "REVISE"
VERDICT_REJECT = "REJECT"

_PASS_MIN = 70.0
_REVISE_MIN = 50.0

# ---------------------------------------------------------------------------
# 결과 타입 — 기존 Scorecard 와 이름 충돌 회피: BacktestScoreCard
# ---------------------------------------------------------------------------


@dataclass
class BacktestScoreCard:
    """5차원 채점 결과.

    Attributes:
        score:            합계 점수 (0~100).
        verdict:          PASS / REVISE / REJECT.
        dimension_scores: {'expectancy': float, 'profit_factor': float,
                           'sample_size': float, 'mdd_risk': float, 'robustness': float}.
        warnings:         사전 체크리스트 / 인플레 함정 경고 목록.
    """

    score: float
    verdict: str
    dimension_scores: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 차원별 채점 순수 함수 (각 0~20점)
# ---------------------------------------------------------------------------


def score_expectancy(expectancy: float, *, exp_full: float = 10_000.0) -> float:
    """expectancy 차원 배점 (0~20).

    expectancy <= 0 → 0점.
    expectancy >= exp_full → 20점 (상한 클램프).
    그 사이 → 선형 비례: min(20, 20 * expectancy / exp_full).
    """
    if exp_full <= 0:
        return 0.0
    if expectancy <= 0:
        return 0.0
    return min(20.0, 20.0 * expectancy / exp_full)


def score_profit_factor(pf: float) -> float:
    """profit_factor 차원 배점 (0~20).

    PF < 1.0 → 0점.
    1.0 <= PF < 1.5 → 선형: 40*(PF-1.0).
    PF >= 1.5 → 20점.
    """
    if pf < 1.0:
        return 0.0
    if pf >= 1.5:
        return 20.0
    return 40.0 * (pf - 1.0)


def score_sample_size(n: int) -> float:
    """표본수 차원 배점 (0~20).

    n < 30 → 0점.
    30~100 → 0~15점 선형 보간.
    100~200 → 15~20점 선형 보간.
    n >= 200 → 20점.
    """
    if n < 30:
        return 0.0
    if n >= 200:
        return 20.0
    if n <= 100:
        # 30=0, 100=15 선형
        return 15.0 * (n - 30) / (100 - 30)
    # 100=15, 200=20 선형
    return 15.0 + 5.0 * (n - 100) / (100)


def score_mdd_risk(mdd: float) -> float:
    """MDD-risk 차원 배점 (0~20). mdd 는 절댓값(0~1).

    mdd >= 0.5 → 0점(파이어월).
    그 외 → 20 * (1 - |mdd| / 0.5).
    """
    abs_mdd = abs(mdd)
    if abs_mdd >= 0.5:
        return 0.0
    return 20.0 * (1.0 - abs_mdd / 0.5)


def score_robustness(
    *,
    test_years: float,
    oos_fail: bool,
    n_params: int,
    param_penalty_per: float = 3.0,
    max_params: int = 7,
) -> float:
    """robustness 차원 배점 (0~20).

    test_years < 5 → 0점.
    oos_fail → 0점.
    기본 20점에서 파라미터 초과 1개당 3점 차감(하한 0).
    """
    if test_years < 5.0:
        return 0.0
    if oos_fail:
        return 0.0
    excess = max(0, n_params - max_params)
    return max(0.0, 20.0 - excess * param_penalty_per)


# ---------------------------------------------------------------------------
# 과적합 사전 체크리스트 (경고 생성)
# ---------------------------------------------------------------------------


def _check_overfit_warnings(
    *,
    n_rule_conditions: int | None,
    max_threshold_decimals: int | None,
    annual_trades: int | None,
) -> list[str]:
    """과적합 지표 체크리스트 경고 생성."""
    warnings: list[str] = []
    if n_rule_conditions is not None and n_rule_conditions > 10:
        warnings.append(
            f"룰 조건 {n_rule_conditions}개 — 과적합 가능성 높음 (임계: 10개 초과)"
        )
    if max_threshold_decimals is not None and max_threshold_decimals >= 4:
        warnings.append(
            f"임계값 소수점 {max_threshold_decimals}자리 — 커브핏 의심"
        )
    if annual_trades is not None and annual_trades < 10:
        warnings.append(
            f"연간 거래 기회 {annual_trades}회 — 10회 미만, 통계적 무의미"
        )
    return warnings


# ---------------------------------------------------------------------------
# 인플레 함정 회피 전처리 (채점기 측 active 기간 트리밍)
# ---------------------------------------------------------------------------


def _trim_idle_prefix(
    equity_curve: Sequence[float],
    daily_returns: Sequence[float],
) -> tuple[list[float], list[float]]:
    """선행 0-weight(idle) 일자 제거 후 active 기간만 반환.

    equity_curve 에서 값이 변화하지 않는 선행 구간을 탐지하여 제거.
    두 연속 값이 동일하면 idle로 간주 (첫 번째 변동 이전).
    """
    ec = list(equity_curve)
    dr = list(daily_returns)
    if len(ec) < 2:
        return ec, dr

    # 값이 변하는 첫 인덱스 탐지
    first_active = 0
    for i in range(1, len(ec)):
        if ec[i] != ec[i - 1]:
            first_active = i
            break

    if first_active == 0:
        return ec, dr

    return ec[first_active:], dr[min(first_active, len(dr)):]


def _check_inflation_warnings(
    *,
    equity_curve: Sequence[float],
    daily_returns: Sequence[float],
    open_positions: int,
) -> tuple[list[str], list[float], list[float]]:
    """인플레 함정 회피: idle 기간 제거 + 미청산 경고."""
    warnings: list[str] = []
    trimmed_ec, trimmed_dr = _trim_idle_prefix(equity_curve, daily_returns)

    if open_positions > 0:
        warnings.append(
            f"미청산 포지션 {open_positions}건 — 승률에 미청산 포지션이 포함되어 있어 실제 승률과 다를 수 있음"
        )

    return warnings, trimmed_ec, trimmed_dr


# ---------------------------------------------------------------------------
# 메인 채점 함수
# ---------------------------------------------------------------------------


# @MX:ANCHOR: [AUTO] score_backtest — 검증 게이트의 핵심 진입점.
# @MX:REASON: SPEC-048 REQ-048-M2-1: 채점 결과가 M1 kelly 게이트·validation_gate·대시보드에 소비됨.
def score_backtest(
    trade_stats: TradeStats,
    portfolio_metrics: dict[str, Any],
    is_oos: dict[str, Any],
    *,
    scoring_params: dict[str, Any] | None = None,
    # 과적합 체크리스트 파라미터 (None이면 체크 건너뜀)
    n_rule_conditions: int | None = None,
    max_threshold_decimals: int | None = None,
    annual_trades: int | None = None,
) -> BacktestScoreCard:
    """5차원 백테스트 채점기.

    Args:
        trade_stats:       compute_trade_stats() 결과 (net-of-tax 보정 완료).
        portfolio_metrics: BacktestResult 또는 dict
                           {mdd, sharpe, cagr, equity_curve, daily_returns,
                            test_years, n_params, open_positions}.
        is_oos:            walk_forward 결과 {is_expectancy, oos_expectancy}.
        scoring_params:    배점 파라미터 재정의 dict (exp_full 등).
        n_rule_conditions: 과적합 체크리스트용 — 전략 룰 조건 수.
        max_threshold_decimals: 임계값 최대 소수점 자릿수.
        annual_trades:     연간 거래 기회 수.

    Returns:
        BacktestScoreCard.

    Notes:
        - 순수 함수: backtest.engine import / 호출 없음 (AC-M2-6).
        - 시장 중립: KRX 상수 하드코딩 없음 (AC-CORE-1).
    """
    params = scoring_params or {}
    exp_full: float = float(params.get("exp_full", 10_000.0))

    # --- 포트폴리오 지표 추출 ---
    mdd: float = float(portfolio_metrics.get("mdd", 0.0))
    test_years: float = float(portfolio_metrics.get("test_years", 0.0))
    n_params: int = int(portfolio_metrics.get("n_params", 0))
    open_positions: int = int(portfolio_metrics.get("open_positions", 0))
    equity_curve: Sequence[float] = portfolio_metrics.get("equity_curve") or []
    daily_returns: Sequence[float] = portfolio_metrics.get("daily_returns") or []

    # --- 인플레 함정 회피 전처리 ---
    inflation_warnings, _ec_active, _dr_active = _check_inflation_warnings(
        equity_curve=equity_curve,
        daily_returns=daily_returns,
        open_positions=open_positions,
    )

    # --- IS/OOS 추출 ---
    is_exp: float = float(is_oos.get("is_expectancy", 0.0))
    oos_exp: float = float(is_oos.get("oos_expectancy", 0.0))
    oos_fail = is_exp > 0 and oos_exp < is_exp * 0.5

    # --- 차원별 채점 ---
    dim_expectancy = score_expectancy(trade_stats.expectancy, exp_full=exp_full)
    dim_pf = score_profit_factor(trade_stats.profit_factor)
    dim_sample = score_sample_size(trade_stats.n)
    dim_mdd = score_mdd_risk(mdd)
    dim_robustness = score_robustness(
        test_years=test_years,
        oos_fail=oos_fail,
        n_params=n_params,
    )

    dimension_scores: dict[str, float] = {
        "expectancy": dim_expectancy,
        "profit_factor": dim_pf,
        "sample_size": dim_sample,
        "mdd_risk": dim_mdd,
        "robustness": dim_robustness,
    }

    total_score = sum(dimension_scores.values())

    # --- 경고 수집 ---
    warnings: list[str] = list(inflation_warnings)

    # OOS 실패 경고
    if oos_fail:
        warnings.append(
            f"OOS 성과(expectancy={oos_exp:.1f})가 IS(expectancy={is_exp:.1f})의 50% 미만 "
            f"— robustness 차원 실패 처리됨"
        )

    # 과적합 체크리스트 경고
    overfit_warnings = _check_overfit_warnings(
        n_rule_conditions=n_rule_conditions,
        max_threshold_decimals=max_threshold_decimals,
        annual_trades=annual_trades,
    )
    warnings.extend(overfit_warnings)

    # --- 판정 (컷오프) ---
    any_dim_zero = any(v == 0.0 for v in dimension_scores.values())
    positive_expectancy = trade_stats.expectancy > 0

    if total_score >= _PASS_MIN and not any_dim_zero and positive_expectancy:
        verdict = VERDICT_PASS
    elif total_score < _REVISE_MIN or any_dim_zero or not positive_expectancy:
        verdict = VERDICT_REJECT
    else:
        verdict = VERDICT_REVISE

    return BacktestScoreCard(
        score=total_score,
        verdict=verdict,
        dimension_scores=dimension_scores,
        warnings=warnings,
    )
