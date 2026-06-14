"""Phase 1/2 — 라운드트립 집계: 실현 순손익·승률·손익비·기대값·수수료드래그·자산곡선.

지표 공식은 ``backtest/engine.py`` 의 인라인 계산을 **복제**한다(engine.run() 은 전체 백테스트
루프라 재사용 불가; 상수만 import). 슬리피지/거래세 보정 수치를 항상 병기해 "페이퍼 체결가 ≠
실거래 체결가" 를 정직하게 드러낸다.

Phase 1 은 실현분만(balance=None). Phase 2 에서 ``balance`` 를 주면 미실현 평가손익을 병기한다.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Sequence

from trading.backtest.engine import (
    DEFAULT_SLIPPAGE,
    DEFAULT_TAX_RATE,
    TRADING_DAYS_PER_YEAR,
)
from trading.config import LIVE_ROUND_TRIP_COST_KOSPI
from trading.edge.roundtrips import RoundTrip, RoundTripResult, UnmatchedSell

# Phase 3: 캘린더 시간가중 지표를 신뢰할 최소 일별 스냅샷 행 수.
MIN_SNAPSHOT_ROWS = 20


@dataclass
class Analytics:
    # 표본
    n_closed: int = 0
    n_wins: int = 0
    n_losses: int = 0
    n_unmatched_sells: int = 0

    # 손익 (실현, 수수료 차감 후)
    total_net_pnl: float = 0.0
    total_gross_pnl: float = 0.0
    total_fees: float = 0.0          # 수수료 드래그
    gross_profit: float = 0.0        # 이익 거래 net 합
    gross_loss: float = 0.0          # 손실 거래 net 합의 절댓값

    # 비율 지표
    win_rate: float = 0.0            # 0~1
    profit_factor: float = 0.0       # gross_profit / gross_loss (손실 0 → inf)
    avg_win: float = 0.0
    avg_loss: float = 0.0            # 음수
    expectancy: float = 0.0          # 거래당 평균 순손익
    avg_return_pct: float = 0.0
    trade_return_sharpe: float = 0.0  # 거래당 수익률 mean/std (연율화 아님)

    # 보유기간
    avg_holding_days: float = 0.0
    median_holding_days: float = 0.0
    max_holding_days: int = 0

    # 실현 자산곡선 (이벤트시간 = 청산일 누적 순손익)
    equity_curve: list[tuple[str, float]] = field(default_factory=list)  # (exit_date, cum_net_pnl)
    realized_mdd_krw: float = 0.0    # 누적 실현손익 곡선의 최대 낙폭(절대 KRW, 음수)

    # 슬리피지/거래세 보정 (실거래 비관 추정)
    total_net_pnl_adj: float = 0.0
    expectancy_adj: float = 0.0
    profit_factor_adj: float = 0.0
    slippage_drag: float = 0.0       # 보정으로 깎인 총액 (양수)

    # SPEC-TRADING-044 M3: 비용보정 지표 (REQ-044-C2)
    sortino: float = 0.0             # Sortino 비율 (MAR=0, 거래당 수익률 기준)
    cost_adjusted_win_rate: float = 0.0  # round-trip 비용 초과 수익인 거래 비율

    # 미실현 (Phase 2; balance 주어질 때만)
    has_unrealized: bool = False
    unrealized_pnl: float = 0.0
    total_pnl_incl_unrealized: float = 0.0


def _slippage_penalty(rt: RoundTrip) -> float:
    """실거래였다면 추가로 들었을 비용(비관 추정): 양변 슬리피지 + 매도측 거래세."""
    entry_notional = rt.entry_price * rt.qty
    exit_notional = rt.exit_price * rt.qty
    return (
        entry_notional * DEFAULT_SLIPPAGE
        + exit_notional * DEFAULT_SLIPPAGE
        + exit_notional * DEFAULT_TAX_RATE
    )


def compute(
    roundtrips: Sequence[RoundTrip],
    *,
    unmatched_sells: Sequence[UnmatchedSell] | None = None,
    balance: dict[str, Any] | None = None,
) -> Analytics:
    """라운드트립 시퀀스 → 집계.

    ``balance`` 가 주어지면(Phase 2) ``pnl_total`` 을 미실현 평가손익으로 병기한다.
    """
    a = Analytics()
    a.n_unmatched_sells = len(unmatched_sells or [])

    if balance is not None:
        a.has_unrealized = True
        a.unrealized_pnl = float(balance.get("pnl_total", 0) or 0)

    if not roundtrips:
        a.total_pnl_incl_unrealized = a.unrealized_pnl
        return a

    # 청산일 순 정렬(자산곡선/MDD용).
    ordered = sorted(roundtrips, key=lambda r: (r.exit_date, r.entry_date))

    nets = [r.net_pnl for r in roundtrips]
    rets = [r.return_pct for r in roundtrips]
    wins = [r for r in roundtrips if r.is_win]
    losses = [r for r in roundtrips if not r.is_win]

    a.n_closed = len(roundtrips)
    a.n_wins = len(wins)
    a.n_losses = len(losses)
    a.total_net_pnl = sum(nets)
    a.total_gross_pnl = sum(r.gross_pnl for r in roundtrips)
    a.total_fees = sum(r.fees for r in roundtrips)
    a.gross_profit = sum(r.net_pnl for r in wins)
    a.gross_loss = abs(sum(r.net_pnl for r in losses))

    a.win_rate = a.n_wins / a.n_closed
    a.profit_factor = (
        (a.gross_profit / a.gross_loss) if a.gross_loss > 0
        else (math.inf if a.gross_profit > 0 else 0.0)
    )
    a.avg_win = statistics.mean([r.net_pnl for r in wins]) if wins else 0.0
    a.avg_loss = statistics.mean([r.net_pnl for r in losses]) if losses else 0.0
    a.expectancy = a.total_net_pnl / a.n_closed
    a.avg_return_pct = statistics.mean(rets)
    if len(rets) >= 2:
        std = statistics.pstdev(rets)
        a.trade_return_sharpe = (statistics.mean(rets) / std) if std else 0.0

    holds = [r.holding_days for r in roundtrips]
    a.avg_holding_days = statistics.mean(holds)
    a.median_holding_days = statistics.median(holds)
    a.max_holding_days = max(holds)

    # 실현 자산곡선 + 절대 MDD (engine.py running_max/drawdown 공식 복제).
    cum = 0.0
    running_max = 0.0
    mdd = 0.0
    for r in ordered:
        cum += r.net_pnl
        a.equity_curve.append((r.exit_date.isoformat(), cum))
        running_max = max(running_max, cum)
        mdd = min(mdd, cum - running_max)
    a.realized_mdd_krw = mdd

    # 슬리피지/거래세 보정.
    penalties = [_slippage_penalty(r) for r in roundtrips]
    a.slippage_drag = sum(penalties)
    nets_adj = [n - p for n, p in zip(nets, penalties)]
    a.total_net_pnl_adj = sum(nets_adj)
    a.expectancy_adj = a.total_net_pnl_adj / a.n_closed
    gross_profit_adj = sum(n for n in nets_adj if n > 0)
    gross_loss_adj = abs(sum(n for n in nets_adj if n <= 0))
    a.profit_factor_adj = (
        (gross_profit_adj / gross_loss_adj) if gross_loss_adj > 0
        else (math.inf if gross_profit_adj > 0 else 0.0)
    )

    # SPEC-TRADING-044 M3: Sortino (MAR=0, 거래당 수익률 기준) (REQ-044-C2)
    # Sortino = mean(return_pct) / downside_dev(return_pct), MAR=0
    # downside_dev: 손실 거래 수익률의 모표준편차 (단, 손실 1건이면 절댓값 대체)
    # 손실 0 건 → downside dev = 0 → Sortino = +inf
    downsides = [r for r in rets if r < 0]
    if not rets:
        a.sortino = 0.0
    elif not downsides:
        a.sortino = math.inf
    else:
        if len(downsides) == 1:
            # pstdev of a single value is 0; use abs as the deviation proxy
            dd = abs(downsides[0])
        else:
            dd = statistics.pstdev(downsides)
        mean_ret = statistics.mean(rets)
        a.sortino = (mean_ret / dd) if dd else 0.0

    # SPEC-TRADING-044 M3: cost-adjusted win rate (REQ-044-C2)
    # round-trip 비용(단일소스: LIVE_ROUND_TRIP_COST_KOSPI)을 초과한 수익 거래 비율
    cost_wins = sum(
        1 for r in roundtrips
        if r.net_pnl > LIVE_ROUND_TRIP_COST_KOSPI * r.cost_basis
    )
    a.cost_adjusted_win_rate = cost_wins / a.n_closed

    a.total_pnl_incl_unrealized = a.total_net_pnl + a.unrealized_pnl
    return a


def from_result(
    result: RoundTripResult, *, balance: dict[str, Any] | None = None
) -> Analytics:
    """RoundTripResult 편의 래퍼."""
    return compute(
        result.roundtrips, unmatched_sells=result.unmatched_sells, balance=balance
    )


# ---------------------------------------------------------------------------
# Phase 3 — 캘린더 시간가중 지표 (daily_equity_snapshot)
# ---------------------------------------------------------------------------


@dataclass
class TimeWeighted:
    available: bool = False
    n_days: int = 0
    start_value: float = 0.0
    end_value: float = 0.0
    total_return_pct: float = 0.0
    cagr: float = 0.0
    mdd: float = 0.0           # 음수 비율 (낙폭)
    sharpe: float = 0.0


def time_weighted_metrics(
    snapshots: Sequence[tuple[date, float]],
    *,
    min_rows: int = MIN_SNAPSHOT_ROWS,
) -> TimeWeighted:
    """일별 (date, total_assets) 시계열 → 캘린더 기준 CAGR/MDD/Sharpe.

    engine.run() 의 인라인 공식을 복제(running_max 낙폭, mean/std*sqrt(252)).
    행이 ``min_rows`` 미만이면 available=False(이벤트시간 지표로 폴백하도록).
    """
    tw = TimeWeighted()
    rows = sorted(snapshots, key=lambda t: t[0])
    tw.n_days = len(rows)
    if len(rows) < min_rows:
        return tw

    values = [float(v) for _, v in rows]
    if values[0] <= 0:
        return tw

    tw.available = True
    tw.start_value = values[0]
    tw.end_value = values[-1]
    tw.total_return_pct = (values[-1] / values[0] - 1.0) * 100.0

    # CAGR (캘린더 연수).
    span_days = (rows[-1][0] - rows[0][0]).days
    years = span_days / 365.25 if span_days > 0 else 0.0
    tw.cagr = ((values[-1] / values[0]) ** (1 / years) - 1.0) if years > 0 else 0.0

    # MDD (running_max 기반 — engine.py 공식 복제).
    running_max = values[0]
    mdd = 0.0
    for v in values:
        running_max = max(running_max, v)
        if running_max > 0:
            mdd = min(mdd, (v - running_max) / running_max)
    tw.mdd = mdd

    # Sharpe (일별 수익률, 연율화, rf=0).
    rets = [(values[i] / values[i - 1] - 1.0) for i in range(1, len(values)) if values[i - 1]]
    if len(rets) >= 2:
        std = statistics.pstdev(rets)
        tw.sharpe = (statistics.mean(rets) / std * math.sqrt(TRADING_DAYS_PER_YEAR)) if std else 0.0
    return tw
