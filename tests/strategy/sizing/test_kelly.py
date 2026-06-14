"""T-005 RED→GREEN — 시장 중립 Kelly/heat 코어 순수 함수.

SPEC-TRADING-048 REQ-048-M1-1/2/4/6/7, REQ-048-CORE-1/2.
AC: AC-M1-2(cap), AC-M1-4(heat), AC-M1-6(호가/최소/반올림), AC-CORE-1/2.
"""

from __future__ import annotations

import math

import pytest


class TestKellyFraction:
    """kelly_fraction(win_rate, payoff_ratio) -> float."""

    def test_positive_kelly(self) -> None:
        """W=0.6, R=2.0 → 0.6 - 0.4/2.0 = 0.4."""
        from trading.strategy.sizing.kelly import kelly_fraction
        assert abs(kelly_fraction(0.6, 2.0) - 0.4) < 1e-9

    def test_zero_win_rate_returns_negative(self) -> None:
        """W=0 → REQ-048-M1-2: kelly<=0 반환, 예외 없음."""
        from trading.strategy.sizing.kelly import kelly_fraction
        assert kelly_fraction(0.0, 2.0) <= 0

    def test_zero_payoff_ratio_returns_negative(self) -> None:
        """R=0 → kelly<=0, division-by-zero 없음."""
        from trading.strategy.sizing.kelly import kelly_fraction
        assert kelly_fraction(0.5, 0.0) <= 0

    def test_negative_payoff_ratio_returns_negative(self) -> None:
        """R<0 → kelly<=0."""
        from trading.strategy.sizing.kelly import kelly_fraction
        assert kelly_fraction(0.5, -1.0) <= 0

    def test_break_even_kelly(self) -> None:
        """W=0.5, R=1.0 → Kelly=0."""
        from trading.strategy.sizing.kelly import kelly_fraction
        result = kelly_fraction(0.5, 1.0)
        assert abs(result) < 1e-9

    def test_very_high_win_rate(self) -> None:
        """W=0.9, R=3 → 0.9 - 0.1/3 ≈ 0.8667."""
        from trading.strategy.sizing.kelly import kelly_fraction
        expected = 0.9 - 0.1 / 3.0
        assert abs(kelly_fraction(0.9, 3.0) - expected) < 1e-9


class TestHalfKellyCap:
    """half_kelly_cap → 정수 수량."""

    def test_basic_cap(self) -> None:
        """equity=1_000_000, price=10_000, kelly=0.4 → half=0.2 → 20주."""
        from trading.strategy.sizing.kelly import half_kelly_cap
        # 1_000_000 * 0.5 * 0.4 / 10_000 = 20
        qty = half_kelly_cap(0.4, 1_000_000, 10_000, lot_size=1)
        assert qty == 20

    def test_negative_kelly_returns_zero(self) -> None:
        """kelly<=0 → 0주 (REQ-048-M1-2)."""
        from trading.strategy.sizing.kelly import half_kelly_cap
        assert half_kelly_cap(-0.1, 1_000_000, 10_000) == 0
        assert half_kelly_cap(0.0, 1_000_000, 10_000) == 0

    def test_ac_m1_6_lot_size_floor(self) -> None:
        """AC-M1-6: 43.7주 → 주입된 lot_size=1 기준 floor=43주."""
        from trading.strategy.sizing.kelly import half_kelly_cap
        # equity=1_000_000 * 0.5 * kelly / price = 43.7 → kelly 역산
        # kelly = 43.7 * 2 * price / equity = 43.7*2*10_000/1_000_000 = 0.874
        kelly = 43.7 * 2 * 10_000 / 1_000_000
        qty = half_kelly_cap(kelly, 1_000_000, 10_000, lot_size=1)
        assert qty == 43

    def test_lot_size_min_order(self) -> None:
        """계산값이 lot_size 미만이면 0 반환."""
        from trading.strategy.sizing.kelly import half_kelly_cap
        # equity=100, price=10_000, kelly=0.01 → raw=0.05 < 1 → 0
        qty = half_kelly_cap(0.01, 100, 10_000, lot_size=1)
        assert qty == 0

    def test_custom_round_fn(self) -> None:
        """주입된 round_fn 적용 — ceil 사용."""
        from trading.strategy.sizing.kelly import half_kelly_cap
        # raw = 1_000_000 * 0.5 * 0.02 / 10_000 = 1.0
        qty_ceil = half_kelly_cap(0.02, 1_000_000, 10_000, lot_size=1, round_fn=math.ceil)
        qty_floor = half_kelly_cap(0.02, 1_000_000, 10_000, lot_size=1, round_fn=math.floor)
        # 정확히 1.0이라 둘 다 1이지만 타입·호출은 검증
        assert qty_ceil >= qty_floor


class TestPortfolioHeat:
    """portfolio_heat(open_positions, equity) -> float."""

    def test_empty_positions_zero(self) -> None:
        from trading.strategy.sizing.kelly import portfolio_heat
        assert portfolio_heat([], 1_000_000) == 0.0

    def test_single_position_with_stop(self) -> None:
        """위험금액 = (entry-stop)*qty / equity."""
        from trading.strategy.sizing.kelly import portfolio_heat
        pos = {"entry_price": 10_000, "qty": 10, "stop_price": 9_000}
        heat = portfolio_heat([pos], 1_000_000)
        # (10_000-9_000)*10 / 1_000_000 = 10_000/1_000_000 = 0.01
        assert abs(heat - 0.01) < 1e-9

    def test_single_position_no_stop_fallback(self) -> None:
        """손절가 없으면 명목가치 fallback: entry*qty/equity."""
        from trading.strategy.sizing.kelly import portfolio_heat
        pos = {"entry_price": 10_000, "qty": 10, "stop_price": None}
        heat = portfolio_heat([pos], 1_000_000)
        # 10_000*10/1_000_000 = 0.1
        assert abs(heat - 0.1) < 1e-9

    def test_zero_stop_uses_fallback(self) -> None:
        """stop_price=0 → 명목가치 fallback."""
        from trading.strategy.sizing.kelly import portfolio_heat
        pos = {"entry_price": 5_000, "qty": 20, "stop_price": 0}
        heat = portfolio_heat([pos], 1_000_000)
        # 5_000*20/1_000_000 = 0.1
        assert abs(heat - 0.1) < 1e-9

    def test_multiple_positions(self) -> None:
        from trading.strategy.sizing.kelly import portfolio_heat
        pos1 = {"entry_price": 10_000, "qty": 10, "stop_price": 9_500}
        pos2 = {"entry_price": 20_000, "qty": 5, "stop_price": 19_000}
        heat = portfolio_heat([pos1, pos2], 1_000_000)
        # (500*10 + 1_000*5) / 1_000_000 = (5_000+5_000)/1_000_000 = 0.01
        assert abs(heat - 0.01) < 1e-9

    def test_zero_equity_returns_zero(self) -> None:
        from trading.strategy.sizing.kelly import portfolio_heat
        assert portfolio_heat([{"entry_price": 10_000, "qty": 1}], 0.0) == 0.0


class TestReduceQtyForHeat:
    """AC-M1-4: heat 상한 초과 시 축소, 최소주문으로도 초과면 0."""

    def test_no_reduction_needed(self) -> None:
        """현재 heat + 신규 진입이 상한 이내 → 원래 수량 반환."""
        from trading.strategy.sizing.kelly import reduce_qty_for_heat
        # current_heat=0.01, cap=0.08, entry=10_000, stop=9_000, equity=1_000_000
        # unit_risk=1_000, max_qty=(0.07*1_000_000/1_000)=70, proposed=50
        qty = reduce_qty_for_heat(50, 10_000, 9_000, 0.01, 1_000_000,
                                  heat_cap=0.08, lot_size=1)
        assert qty == 50

    def test_reduction_to_fit_cap(self) -> None:
        """신규 진입이 cap 초과 → 축소."""
        from trading.strategy.sizing.kelly import reduce_qty_for_heat
        # current_heat=0.07, cap=0.08 → available=0.01
        # unit_risk=1_000 (entry=10_000, stop=9_000)
        # max_qty=0.01*1_000_000/1_000=10
        qty = reduce_qty_for_heat(50, 10_000, 9_000, 0.07, 1_000_000,
                                  heat_cap=0.08, lot_size=1)
        assert qty == 10

    def test_minimum_lot_size_exceeded_returns_zero(self) -> None:
        """lot_size로도 heat 초과 → 0 반환 (AC-M1-4 분기 b)."""
        from trading.strategy.sizing.kelly import reduce_qty_for_heat
        # current_heat=0.08, cap=0.08 → available=0 → 0
        qty = reduce_qty_for_heat(10, 10_000, 9_000, 0.08, 1_000_000,
                                  heat_cap=0.08, lot_size=1)
        assert qty == 0

    def test_no_stop_uses_notional_fallback(self) -> None:
        """손절가 없으면 명목가치 기준 축소."""
        from trading.strategy.sizing.kelly import reduce_qty_for_heat
        # current_heat=0.07, cap=0.08 → available=0.01
        # unit_risk=10_000 (fallback) → max_qty=0.01*1_000_000/10_000=1
        qty = reduce_qty_for_heat(50, 10_000, None, 0.07, 1_000_000,
                                  heat_cap=0.08, lot_size=1)
        assert qty == 1


class TestKellyMarketNeutral:
    """AC-CORE-1: kelly.py 본문에 KRX 상수 하드코딩 없음."""

    def test_no_hardcoded_krx_constants(self) -> None:
        import ast
        import inspect
        from trading.strategy.sizing import kelly as kelly_mod

        src = inspect.getsource(kelly_mod)
        tree = ast.parse(src)
        krx_constants = {0.0018, 0.18, 0.0015, 0.15, 0.00215}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                assert node.value not in krx_constants, (
                    f"KRX 하드코딩 상수 발견: {node.value}"
                )

    def test_korean_and_us_params_both_work(self) -> None:
        """동일 함수에 한국/미국 파라미터 세트로 모두 호출 가능."""
        from trading.strategy.sizing.kelly import kelly_fraction, half_kelly_cap

        # 한국 파라미터
        k_kr = kelly_fraction(0.6, 2.0)
        qty_kr = half_kelly_cap(k_kr, 5_000_000, 50_000, lot_size=1)

        # 미국 파라미터 (센트 단위: equity=USD, price=USD cents)
        k_us = kelly_fraction(0.55, 1.8)
        qty_us = half_kelly_cap(k_us, 10_000, 15_000, lot_size=1)

        # 두 호출 모두 예외 없이 완료
        assert qty_kr >= 0
        assert qty_us >= 0
