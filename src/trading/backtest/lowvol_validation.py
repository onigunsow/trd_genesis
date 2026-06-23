"""SPEC-TRADING-058 M3 — Walk-forward OOS 검증 + Bonferroni + 50% 할인 + 단일 AND 판정.

REQ-058-M3-1 : walk-forward = 리밸런스별 반복 point-in-time engine.run (단일 full-sample 금지)
REQ-058-M3-2 : Bonferroni-adjusted 유의성 (N=1 -> alpha/1, 제네릭 메커니즘)
REQ-058-M3-3 : 50% 백테스트 할인 (McLean-Pontiff haircut) 후 GO 판정
REQ-058-M3-4 : 기존 scorecard.decide 재사용 (임계 약화 금지)
REQ-058-M3-4a: 단일 AND 판정 함수 (Bonferroni AND haircut-양수 AND scorecard-GO)
REQ-058-M3-5 : n = 리밸런스 주기 수 (trades/round-trip 수 아님)
REQ-058-M3-6 : GO → 페이퍼 전용 승급 (라이브 경로 미접촉)
REQ-058-M3-7 : "알파 없음" = 유효한 성공 결과
REQ-058-M3-8 : 비용/생존편향 정직성 플래그

설계 원칙:
- engine.run, scorecard.decide, adapt_to_scorecard, check_survivorship_gate 재사용.
- benchmark.py money-weighted 알파 사용 금지 (C-7, EX-11).
- 순수 함수 / 주입 가능 — 단위 테스트 픽스처 주입 지원.
- [HARD] 라이브 경로(order.py / smoke_gate.py / live_unlocked) 미접촉.

# @MX:ANCHOR: [AUTO] 단일 AND 판정 함수 — M3 OOS 최종 판정 진입점
# @MX:REASON: REQ-058-M3-4a; Bonferroni·haircut·scorecard-GO 세 조건을 AND로 합성.
#             이 함수를 우회하면 부호만 양수인 알파가 PASS로 오인될 수 있다.
# @MX:SPEC: SPEC-TRADING-058
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

LOG = logging.getLogger(__name__)

# Bonferroni 기본 유의 수준
_DEFAULT_ALPHA = 0.05
# 리밸런스 주기 최소 표본 (REQ-058-M3-5, scorecard._MIN_SAMPLE=30 상속)
_MIN_REBALANCES = 30


# ──────────────────────────────────────────────────────────────────────────────
# 데이터 클래스
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class WalkForwardWindow:
    """단일 리밸런스 window의 OOS 결과.

    start_date: 포트폴리오 구성 기준일 (= 리밸런스 날짜 T).
    end_date:   OOS 측정 종료일 (= 다음 리밸런스 날짜 T+1 - 1일, 또는 데이터 끝).
    backtest_result: 해당 window에서 engine.run이 반환한 BacktestResult.
    """

    start_date: date
    end_date: date
    backtest_result: Any  # BacktestResult (순환 import 회피를 위해 Any)


@dataclass
class WalkForwardResult:
    """전체 walk-forward OOS 결과.

    windows: 리밸런스별 WalkForwardWindow 목록.
    n_rebalances: 유효 window 수 (= len(windows)).
    all_oos_returns: 모든 window의 일간 OOS 수익률 이어붙임.
    raw_alpha_pct: 전략 총수익률 - KOSPI 총수익률 (time-weighted, %).
    """

    windows: list[WalkForwardWindow] = field(default_factory=list)
    n_rebalances: int = 0
    all_oos_returns: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    raw_alpha_pct: float = 0.0


@dataclass
class BonferroniResult:
    """Bonferroni-adjusted 유의성 판정 결과 (REQ-058-M3-2)."""

    n_factors: int
    alpha_level: float
    adjusted_alpha: float
    p_value: float
    t_stat: float
    significant: bool


@dataclass
class VerdictResult:
    """단일 AND 판정 함수 결과 (REQ-058-M3-4a)."""

    verdict: str          # "PASS" / "NON-PASS" / "INCONCLUSIVE"
    reason: str           # 판정 이유 (한국어)
    is_paper_only: bool   # GO → 페이퍼 전용 (REQ-058-M3-6)


@dataclass
class WalkForwardValidationReport:
    """render_verdict_report가 생성하는 구조화 리포트 객체.

    텍스트 출력과 별도로 프로그래밍 방식으로 접근 가능하도록 제공.
    """

    raw_alpha_pct: float
    discounted_alpha_pct: float
    bonferroni_passed: bool
    n_rebalances: int
    verdict: str
    survivorship_label: str
    cost_honesty_flag: str
    is_valid_no_alpha_result: bool


# ──────────────────────────────────────────────────────────────────────────────
# REQ-058-M3-3: 50% 백테스트 할인 (McLean-Pontiff haircut)
# ──────────────────────────────────────────────────────────────────────────────

def apply_alpha_haircut(raw_alpha_pct: float, haircut: float = 0.5) -> float:
    """raw 알파에 McLean-Pontiff 50% 할인을 적용한다.

    [HARD] GO 판정은 반드시 이 함수 결과(할인된 알파)로 내려야 한다 (REQ-058-M3-3).

    Args:
        raw_alpha_pct: 측정된 백테스트 알파 (%).
        haircut: 할인 비율 (기본 0.5 = 50%).

    Returns:
        할인된 알파 (%).
    """
    return raw_alpha_pct * (1.0 - haircut)


# ──────────────────────────────────────────────────────────────────────────────
# REQ-058-M3-2: Bonferroni-adjusted 유의성
# ──────────────────────────────────────────────────────────────────────────────

def apply_bonferroni(
    oos_daily_returns: pd.Series,
    *,
    n_factors: int = 1,
    alpha_level: float = _DEFAULT_ALPHA,
) -> BonferroniResult:
    """OOS 일간 수익률에 Bonferroni-adjusted t-test를 적용한다.

    # @MX:NOTE: [AUTO] N=1이면 조정 유의 수준 = alpha_level (Bonferroni alpha/1 = alpha).
    #           SPEC-059가 팩터를 추가하면 n_factors=2+ 전달 시 자동으로 강화된다.

    Args:
        oos_daily_returns: OOS 전체 기간의 일간 수익률 시계열.
        n_factors: 검정된 팩터 수 (Bonferroni 분모). 기본 N=1.
        alpha_level: 기본 유의 수준 (기본 0.05).

    Returns:
        BonferroniResult (significant: bool 포함).
    """
    adjusted_alpha = alpha_level / max(1, n_factors)

    if len(oos_daily_returns) < 2:
        return BonferroniResult(
            n_factors=n_factors,
            alpha_level=alpha_level,
            adjusted_alpha=adjusted_alpha,
            p_value=1.0,
            t_stat=0.0,
            significant=False,
        )

    # 단측 t-test: 수익률 평균 > 0 검정 (양의 알파 방향, numpy만으로 구현)
    arr = oos_daily_returns.dropna().values.astype(float)
    n = len(arr)
    mean = float(arr.mean())
    std = float(arr.std(ddof=1))

    if std == 0.0 or n < 2:
        t_stat_val = 0.0
        p_one_sided = 0.5
    else:
        t_stat_val = mean / (std / math.sqrt(n))
        # 자유도 = n - 1; Student-t CDF를 정규분포 근사 (n이 클 때 충분)
        # 단측 p-value (오른쪽 꼬리 = mean > 0)
        # 정규분포 근사: Φ(-|t|) for two-sided / 2
        # erf를 사용한 정규분포 CDF 근사
        abs_t = abs(t_stat_val)
        # 표준정규 CDF P(Z > abs_t) = (1 - erf(abs_t/sqrt(2))) / 2
        p_right_tail = (1.0 - math.erf(abs_t / math.sqrt(2.0))) / 2.0
        if t_stat_val > 0:
            p_one_sided = p_right_tail
        else:
            p_one_sided = 1.0 - p_right_tail

    t_stat = t_stat_val
    significant = float(p_one_sided) < adjusted_alpha

    LOG.debug(
        "bonferroni: t=%.3f p_one=%.4f adj_alpha=%.4f significant=%s",
        t_stat, p_one_sided, adjusted_alpha, significant,
    )

    return BonferroniResult(
        n_factors=n_factors,
        alpha_level=alpha_level,
        adjusted_alpha=adjusted_alpha,
        p_value=float(p_one_sided),
        t_stat=float(t_stat),
        significant=significant,
    )


# ──────────────────────────────────────────────────────────────────────────────
# REQ-058-M3-1: Walk-forward OOS (반복 point-in-time engine.run)
# ──────────────────────────────────────────────────────────────────────────────

def run_walk_forward_oos(
    prices_df: pd.DataFrame,
    rebalance_dates: list[date],
    universe_tickers: list[str],
    kospi_returns: pd.Series | None = None,
    *,
    quantile: float = 0.25,
    lookback: int = 120,
) -> WalkForwardResult:
    """리밸런스별 반복 point-in-time engine.run으로 walk-forward OOS를 실행한다.

    [HARD] 단일 full-sample engine.run을 OOS로 보고하지 않는다 (REQ-058-M3-1).
    각 리밸런스 T에서:
      1. T까지의 데이터로 저변동성 포트폴리오 비중 계산 (point-in-time).
      2. [T, T+1) 기간(미래 unseen window)에서 engine.run.
      3. 결과를 windows에 추가.

    # @MX:WARN: [AUTO] 루프 내 engine.run 반복 호출 — 리밸런스 수가 많으면 느릴 수 있음
    # @MX:REASON: REQ-058-M3-1 위반 방지가 성능보다 중요; single full-sample 금지가 핵심.

    Args:
        prices_df: 전체 가격 DataFrame (index=date, columns=ticker).
        rebalance_dates: 월간 리밸런스 기준일 목록 (오름차순 정렬 권장).
        universe_tickers: 유니버스 종목 코드.
        kospi_returns: KOSPI 일간 수익률 (OOS 알파 계산용). None이면 알파=0.
        quantile: 저변동성 분위 (기본 0.25).
        lookback: 변동성 lookback 거래일 (기본 120).

    Returns:
        WalkForwardResult.
    """
    import trading.backtest.engine as engine_mod
    from trading.backtest.factor_lowvol import compute_low_vol_signal

    sorted_dates = sorted(rebalance_dates)
    windows: list[WalkForwardWindow] = []
    all_oos_returns_list: list[pd.Series] = []

    # 전체 가격 날짜 (pd.Timestamp → date 변환)
    all_price_dates = [
        d.date() if hasattr(d, "date") else d for d in prices_df.index
    ]
    valid_tickers = sorted(set(universe_tickers) & set(prices_df.columns))

    for i, rb_date in enumerate(sorted_dates):
        # 다음 리밸런스 날짜 (OOS window 종료)
        if i + 1 < len(sorted_dates):
            next_rb = sorted_dates[i + 1]
        else:
            # 마지막 리밸런스: 데이터 끝까지
            next_rb = None

        # OOS window: rb_date 이후 ~ next_rb 이전 (미래 unseen)
        if next_rb is not None:
            oos_mask = [
                rb_date < d < next_rb for d in all_price_dates
            ]
        else:
            oos_mask = [d > rb_date for d in all_price_dates]

        oos_dates = [d for d, m in zip(all_price_dates, oos_mask, strict=False) if m]
        if not oos_dates:
            LOG.debug("walk_forward: %s OOS window 빈 기간 — skip", rb_date)
            continue

        # OOS window 날짜를 pd.Timestamp로 변환 (prices_df 인덱스 타입 맞춤)
        oos_dates_ts = pd.to_datetime(oos_dates)

        # point-in-time 팩터 계산 (T까지 데이터만 사용)
        result_factor = compute_low_vol_signal(
            prices_df[valid_tickers],
            rb_date,
            lookback=lookback,
        )

        if result_factor.rankings.empty:
            LOG.warning("walk_forward: %s — 팩터 랭킹 없음, skip", rb_date)
            continue

        # 저변동성 분위 선택 → 1/N 등가중
        n_total = len(result_factor.rankings)
        n_select = max(1, round(n_total * quantile))
        selected = result_factor.rankings.nsmallest(n_select).index.tolist()
        weight = 1.0 / len(selected)

        # OOS 기간 가격 슬라이스
        if len(oos_dates_ts) > 0:
            oos_prices = prices_df.loc[oos_dates_ts, valid_tickers]
        else:
            oos_prices = pd.DataFrame()
        if oos_prices.empty or len(oos_prices) < 2:
            LOG.debug("walk_forward: %s OOS 가격 데이터 부족 — skip", rb_date)
            continue

        # 비중 DataFrame: OOS window 전체에 동일 비중 적용 (월간 고정)
        oos_weights = pd.DataFrame(0.0, index=oos_prices.index, columns=valid_tickers)
        for ticker in selected:
            if ticker in oos_weights.columns:
                oos_weights[ticker] = weight

        # engine.run (point-in-time, OOS window만)
        br = engine_mod.run(oos_prices, oos_weights)

        window = WalkForwardWindow(
            start_date=rb_date,
            end_date=oos_dates[-1],
            backtest_result=br,
        )
        windows.append(window)
        all_oos_returns_list.append(br.daily_returns)

    # 모든 OOS 수익률 이어붙임
    if all_oos_returns_list:
        all_oos_returns = pd.concat(all_oos_returns_list)
    else:
        all_oos_returns = pd.Series(dtype=float)

    # time-weighted 알파 계산 (전략 총수익 vs KOSPI 총수익)
    raw_alpha_pct = _compute_raw_alpha(all_oos_returns, kospi_returns, prices_df)

    return WalkForwardResult(
        windows=windows,
        n_rebalances=len(windows),
        all_oos_returns=all_oos_returns,
        raw_alpha_pct=raw_alpha_pct,
    )


def _compute_raw_alpha(
    oos_daily_returns: pd.Series,
    kospi_returns: pd.Series | None,
    prices_df: pd.DataFrame,
) -> float:
    """OOS 전략 수익률 - KOSPI 수익률 (time-weighted, %).

    [HARD] benchmark.py:120-131의 money-weighted 알파 사용 금지 (C-7, EX-11).
    """
    if len(oos_daily_returns) < 2:
        return 0.0

    # 전략 총수익률 (time-weighted equity-curve 기반)
    strat_equity = (1 + oos_daily_returns).cumprod()
    strat_total = float(strat_equity.iloc[-1]) - 1.0

    if kospi_returns is None or len(kospi_returns) == 0:
        return strat_total * 100.0

    # OOS 기간과 겹치는 KOSPI 수익률
    common_idx = oos_daily_returns.index.intersection(kospi_returns.index)
    if len(common_idx) < 2:
        return strat_total * 100.0

    kospi_oos = kospi_returns.loc[common_idx]
    kospi_equity = (1 + kospi_oos).cumprod()
    kospi_total = float(kospi_equity.iloc[-1]) - 1.0

    return (strat_total - kospi_total) * 100.0


# ──────────────────────────────────────────────────────────────────────────────
# REQ-058-M3-4a: 단일 AND 판정 함수
# ──────────────────────────────────────────────────────────────────────────────

def compose_verdict(
    bonferroni_passed: bool,
    discounted_alpha_pct: float,
    scorecard: Any,  # trading.edge.scorecard.Scorecard
    survivorship_gate: Any,  # trading.backtest.lowvol_portfolio.SurvivorshipGateResult
    n_rebalances: int,
) -> VerdictResult:
    """세 조건 AND 합성으로 최종 OOS 판정을 내린다.

    [HARD] 세 조건 모두 충족해야 PASS (REQ-058-M3-4a):
      1. 생존편향 downgrade 없음 (REQ-058-M2-5 단락 조건)
      2. n_rebalances >= 30 (REQ-058-M3-5)
      3. Bonferroni-adjusted 유의성 통과 (REQ-058-M3-2)
      4. 50% 할인 후 알파 양수 (REQ-058-M3-3)
      5. scorecard.decide == GO (REQ-058-M3-4)

    단락 순서:
      생존편향 → INCONCLUSIVE(n<30) → Bonferroni → 할인 알파 → scorecard

    # @MX:ANCHOR: [AUTO] 최종 판정 합성 진입점 — 단락 조건 순서 불변
    # @MX:REASON: REQ-058-M3-4a; 조건 순서 변경 시 부호만 양수인 알파가 PASS로 누출 가능.
    """
    from trading.edge.scorecard import VERDICT_GO

    # ── 단락 1: 생존편향 downgrade (REQ-058-M2-5 상속)
    if survivorship_gate.survivorship_biased:
        return VerdictResult(
            verdict="NON-PASS",
            reason=f"생존편향 단락: {survivorship_gate.label}",
            is_paper_only=False,
        )

    # ── 단락 2: 리밸런스 주기 부족 (REQ-058-M3-5)
    if n_rebalances < _MIN_REBALANCES:
        return VerdictResult(
            verdict="INCONCLUSIVE",
            reason=(
                f"리밸런스 주기 {n_rebalances}개 < {_MIN_REBALANCES}개 — "
                "통계적 유의성 없음, INCONCLUSIVE (trades 수와 무관)"
            ),
            is_paper_only=False,
        )

    # ── 단락 3: Bonferroni 유의성 미통과 (REQ-058-M3-2)
    if not bonferroni_passed:
        return VerdictResult(
            verdict="NON-PASS",
            reason=(
                "Bonferroni 유의성 미통과 — 양성 알파 부호만으로는 PASS 불가 (REQ-058-M3-2)"
            ),
            is_paper_only=False,
        )

    # ── 단락 4: 50% 할인 후 알파 비양수 (REQ-058-M3-3)
    if discounted_alpha_pct <= 0:
        return VerdictResult(
            verdict="NON-PASS",
            reason=(
                f"50% 할인 후 알파 {discounted_alpha_pct:+.2f}%p ≤ 0 — "
                "비용·McLean-Pontiff 보정 후 양의 OOS 알파 없음"
            ),
            is_paper_only=False,
        )

    # ── 단락 5: scorecard NO-GO (REQ-058-M3-4)
    if scorecard.verdict != VERDICT_GO:
        return VerdictResult(
            verdict="NON-PASS",
            reason=(
                f"scorecard 판정 {scorecard.verdict} — GO 아님 "
                f"({'; '.join(scorecard.reasons[:2])})"
            ),
            is_paper_only=False,
        )

    # ── 모두 통과 → PASS (페이퍼 전용, REQ-058-M3-6)
    return VerdictResult(
        verdict="PASS",
        reason=(
            f"Bonferroni 유의성 통과 AND 50% 할인 후 알파 +{discounted_alpha_pct:.2f}%p "
            f"AND scorecard GO — 페이퍼 OOS 수집으로 승급 (라이브 아님)"
        ),
        is_paper_only=True,
    )


# ──────────────────────────────────────────────────────────────────────────────
# REQ-058-M3-7/8: 정직한 판정 리포트
# ──────────────────────────────────────────────────────────────────────────────

def render_verdict_report(
    raw_alpha_pct: float,
    discounted_alpha_pct: float,
    bonferroni_passed: bool,
    n_rebalances: int,
    verdict: str,
    survivorship_label: str,
    cost_honesty_flag: str,
    is_valid_no_alpha_result: bool,
) -> str:
    """OOS 판정 결과를 정직하게 서술하는 리포트 텍스트를 생성한다.

    [HARD] "알파 없음" = 유효한 성공 결과로 표현 (REQ-058-M3-7).
    [HARD] 생존편향 bound-only 시 첫 번째 주의사항으로 등장 (REQ-058-M3-8).
    [HARD] 비용 모델 정직성 플래그 포함 (REQ-058-M3-8).
    """
    lines: list[str] = []
    lines.append("━" * 56)
    lines.append("SPEC-TRADING-058 M3 — Walk-forward OOS 판정 리포트")
    lines.append("━" * 56)

    # ── 생존편향 경고 (dominant caveat, REQ-058-M3-8)
    if "bound only" in survivorship_label or "상한" in survivorship_label:
        lines.append("")
        lines.append("⚠️ [생존편향 경고 — 최우선 주의사항]")
        lines.append(f"  {survivorship_label}")
        lines.append("  생존편향이 해소되기 전까지 이하 수치는 상한값으로만 해석할 것.")
    else:
        lines.append("")
        lines.append(f"✓ 생존편향 게이트: {survivorship_label}")

    # ── 알파 수치 (raw + 할인, REQ-058-M3-3)
    lines.append("")
    lines.append("【 알파 (time-weighted, REQ-058-M2-4a 어댑터 경유) 】")
    lines.append(f"  raw 알파:           {raw_alpha_pct:+.2f}%p")
    lines.append(f"  50% 할인 후 알파:   {discounted_alpha_pct:+.2f}%p  (McLean-Pontiff 2016)")
    lines.append(f"  Bonferroni 유의성:  {'통과' if bonferroni_passed else '미통과'}")
    lines.append(f"  리밸런스 주기 수:   {n_rebalances}개")

    # ── 판정 (REQ-058-M3-7: "알파 없음" = 유효한 결과)
    lines.append("")
    lines.append("【 최종 판정 】")
    verdict_emoji = {
        "PASS": "🟢 PASS (페이퍼 OOS 수집 승급)",
        "NON-PASS": "🔴 NON-PASS",
        "INCONCLUSIVE": "⚪ INCONCLUSIVE (표본 부족)",
    }.get(verdict, f"⚪ {verdict}")
    lines.append(f"  {verdict_emoji}")

    if is_valid_no_alpha_result:
        lines.append("")
        lines.append("  ※ 이 결과는 유효한 결과입니다.")
        lines.append(
            "  저변동성 팩터가 비용·생존편향·50% 할인 보정 후 양의 OOS 알파를 보이지 않는 것은"
        )
        lines.append("  결함이 아닌 신뢰할 수 있는 측정의 성공적 결과입니다 (SPEC-058 §6).")

    if verdict == "PASS":
        lines.append("")
        lines.append("  ⚠️ GO 판정이더라도 페이퍼 OOS 수집 전용 (라이브 아님, REQ-058-M3-6).")

    # ── 비용 정직성 플래그 (REQ-058-M3-8)
    lines.append("")
    lines.append("【 비용 모델 정직성 (REQ-058-M3-8) 】")
    lines.append(f"  {cost_honesty_flag}")
    lines.append("  • 거래세 0.18% = 실제 0.18-0.23% 범위의 하단(floor) -> 알파 상향 편향.")
    lines.append("  • 슬리피지 0.05% = 대형주 기준 낙관적 추정.")
    lines.append("  • 실제 소형/저유동성 종목 비용은 이를 초과해 알파를 감소시킬 수 있음.")

    lines.append("")
    lines.append("━" * 56)
    return "\n".join(lines)
