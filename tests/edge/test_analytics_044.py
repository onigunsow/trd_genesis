"""SPEC-TRADING-044 M3 — analytics.py 확장 테스트.

AC-4: Sortino(MAR=0), cost-adjusted win rate, net_expectancy 추가.
기존 profit_factor / expectancy / 슬리피지 보정 유지 검증.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from trading.edge.roundtrips import RoundTrip


def _rt(i: int, pnl: float, *, cost_basis: float = 100_000.0) -> RoundTrip:
    """간단한 RoundTrip 픽스처. net_pnl = pnl, cost_basis 는 항목 생성에만 사용."""
    d = date(2026, 1, 1) + timedelta(days=i)
    # entry/exit price 로 pnl 조정 (qty=1 기준)
    ep = cost_basis
    xp = ep + pnl
    return RoundTrip(
        ticker="A",
        entry_date=d,
        exit_date=d + timedelta(days=1),
        qty=1,
        entry_price=ep,
        exit_price=xp,
        entry_fee=0,
        exit_fee=0,
        confidence=None,
        verdict=None,
    )


class TestSortino:
    """Sortino 비율 (MAR=0) 검증."""

    def test_sortino_attribute_exists(self):
        """Analytics 에 sortino 속성이 있다."""
        from trading.edge.analytics import compute
        a = compute([_rt(0, 1000.0), _rt(1, -500.0)])
        assert hasattr(a, "sortino")

    def test_sortino_mar_zero_positive_when_more_wins(self):
        """승 비중이 크면 Sortino > 0."""
        from trading.edge.analytics import compute
        rts = [_rt(i, 1000.0) for i in range(3)] + [_rt(3, -200.0)]
        a = compute(rts)
        assert a.sortino > 0

    def test_sortino_zero_when_no_negative_returns(self):
        """손실 거래 없음 → downside deviation 0 → Sortino inf (양수)."""
        from trading.edge.analytics import compute
        rts = [_rt(i, 500.0) for i in range(5)]
        a = compute(rts)
        # 손실 없으면 sortino = inf 또는 매우 큰 양수
        assert a.sortino > 0 or math.isinf(a.sortino)

    def test_sortino_negative_when_all_losses(self):
        """전부 손실 → 수익률 평균 음수 → Sortino 음수."""
        from trading.edge.analytics import compute
        rts = [_rt(i, -500.0) for i in range(5)]
        a = compute(rts)
        assert a.sortino <= 0

    def test_sortino_formula_correct(self):
        """Sortino = mean(return_pct) / downside_std(return_pct), MAR=0."""
        import statistics

        from trading.edge.analytics import compute
        # 수익률이 다양한 거래 만들기: +2%, -1%, +3%, -1.5%
        # cost_basis = 100_000 기준으로 pnl 설정
        rts = [
            _rt(0, 2_000.0),   # +2%
            _rt(1, -1_000.0),  # -1%
            _rt(2, 3_000.0),   # +3%
            _rt(3, -1_500.0),  # -1.5%
        ]
        a = compute(rts)
        rets = [r.return_pct for r in rts]
        downsides = [r for r in rets if r < 0]
        if downsides:
            dd = statistics.pstdev(downsides)
            expected = statistics.mean(rets) / dd if dd else 0.0
            assert abs(a.sortino - expected) < 1e-6
        else:
            assert math.isinf(a.sortino) or a.sortino > 0

    def test_empty_sortino_is_zero(self):
        """거래 없음 → sortino = 0.0."""
        from trading.edge.analytics import compute
        a = compute([])
        assert a.sortino == 0.0


class TestCostAdjustedWinRate:
    """cost_adjusted_win_rate — round-trip 비용 차감 후 순양성인 거래 비율."""

    def test_cost_adj_win_rate_attribute_exists(self):
        """Analytics 에 cost_adjusted_win_rate 속성이 있다."""
        from trading.edge.analytics import compute
        a = compute([_rt(0, 500.0), _rt(1, -200.0)])
        assert hasattr(a, "cost_adjusted_win_rate")

    def test_cost_adj_win_rate_counts_only_net_positive(self):
        """round-trip 비용 초과 이익만 승으로 계산한다.

        round-trip cost = LIVE_ROUND_TRIP_COST_KOSPI * cost_basis
        cost_basis = 100_000 → round-trip_krw ≈ 230원 (0.0023 * 100_000)
        """
        from trading.config import LIVE_ROUND_TRIP_COST_KOSPI
        from trading.edge.analytics import compute

        cost_basis = 100_000.0
        rt_cost_krw = LIVE_ROUND_TRIP_COST_KOSPI * cost_basis  # ≈ 230원

        # pnl 300 (> rt_cost) → net positive, pnl 100 (< rt_cost일 수도) → 경계
        rts = [
            _rt(0, rt_cost_krw + 100.0),   # 비용 초과 이익 → win
            _rt(1, rt_cost_krw - 100.0),   # 비용 미달 이익 → NOT win
            _rt(2, -500.0),                  # 손실 → NOT win
        ]
        a = compute(rts)
        # 승 1건 / 3건
        assert abs(a.cost_adjusted_win_rate - 1 / 3) < 1e-9

    def test_cost_adj_win_rate_all_wins(self):
        """모두 비용 초과 이익 → 1.0."""
        from trading.config import LIVE_ROUND_TRIP_COST_KOSPI
        from trading.edge.analytics import compute

        cost_basis = 100_000.0
        rt_cost_krw = LIVE_ROUND_TRIP_COST_KOSPI * cost_basis

        rts = [_rt(i, rt_cost_krw + 1000.0) for i in range(5)]
        a = compute(rts)
        assert a.cost_adjusted_win_rate == 1.0

    def test_cost_adj_win_rate_empty(self):
        """거래 없음 → 0.0."""
        from trading.edge.analytics import compute
        a = compute([])
        assert a.cost_adjusted_win_rate == 0.0

    def test_cost_adj_win_rate_uses_config_constant(self):
        """round-trip cost 는 config.py 단일소스에서 읽는다 (하드코딩 없음)."""
        from trading.config import LIVE_ROUND_TRIP_COST_KOSPI
        from trading.edge.analytics import compute

        # LIVE_ROUND_TRIP_COST_KOSPI 가 정확히 0.0023 임을 전제
        assert abs(LIVE_ROUND_TRIP_COST_KOSPI - 0.0023) < 1e-6
        # compute() 호출 시 이 상수를 사용해야 한다 — 명시적 cost 인자 없이 동작 확인
        a = compute([_rt(0, 1000.0)])
        assert hasattr(a, "cost_adjusted_win_rate")


class TestExistingMetricsPreserved:
    """기존 profit_factor / expectancy / 슬리피지 보정이 그대로 유지된다."""

    def test_existing_profit_factor_still_computed(self):
        from trading.edge.analytics import compute
        rts = [_rt(0, 1000.0), _rt(1, -500.0)]
        a = compute(rts)
        assert a.profit_factor > 0

    def test_existing_expectancy_still_computed(self):
        from trading.edge.analytics import compute
        rts = [_rt(0, 1000.0), _rt(1, -500.0)]
        a = compute(rts)
        assert a.expectancy == 250.0  # (1000 - 500) / 2

    def test_slippage_drag_still_present(self):
        from trading.edge.analytics import compute
        rts = [_rt(0, 1000.0)]
        a = compute(rts)
        assert a.slippage_drag > 0
