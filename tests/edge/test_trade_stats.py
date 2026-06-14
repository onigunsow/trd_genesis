"""T-001 RED phase — 거래단위 통계 순수 함수 + net-of-tax 보정.

SPEC-TRADING-048 REQ-048-M2-1(net), REQ-048-CORE-1/2.
AC: AC-M2-1(net 입력), AC-CORE-2.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# T-001: TradeStats + compute_trade_stats 단위 테스트
# ---------------------------------------------------------------------------


class TestComputeTradeStats:
    """compute_trade_stats(roundtrips, *, sell_tax_rate=0.0) -> TradeStats."""

    def _rt(
        self,
        net_pnl: float,
        exit_price: float = 10_000.0,
        qty: int = 1,
    ) -> dict:
        """미니멀 라운드트립 딕셔너리 (net_pnl 주입 테스트용)."""
        return {
            "net_pnl": net_pnl,
            "exit_price": exit_price,
            "qty": qty,
        }

    def test_empty_returns_zero_stats(self) -> None:
        """표본 0건 → 0점, division-by-zero 없음 (Edge case)."""
        from trading.edge.trade_stats import TradeStats, compute_trade_stats

        stats = compute_trade_stats([], sell_tax_rate=0.0)
        assert isinstance(stats, TradeStats)
        assert stats.n == 0
        assert stats.win_rate == 0.0
        assert stats.avg_win == 0.0
        assert stats.avg_loss == 0.0
        assert stats.profit_factor == 0.0
        assert stats.expectancy == 0.0

    def test_all_wins_no_losses(self) -> None:
        """전부 이익 → profit_factor = 0 방지(손실 0이면 inf or 정의값 반환 확인)."""
        from trading.edge.trade_stats import compute_trade_stats

        rts = [self._rt(100.0), self._rt(200.0)]
        stats = compute_trade_stats(rts, sell_tax_rate=0.0)
        assert stats.n == 2
        assert stats.win_rate == 1.0
        assert stats.avg_win == 150.0
        assert stats.avg_loss == 0.0
        assert stats.expectancy > 0

    def test_all_losses(self) -> None:
        """전부 손실 → win_rate 0, profit_factor 0."""
        from trading.edge.trade_stats import compute_trade_stats

        rts = [self._rt(-100.0), self._rt(-50.0)]
        stats = compute_trade_stats(rts, sell_tax_rate=0.0)
        assert stats.n == 2
        assert stats.win_rate == 0.0
        assert stats.profit_factor == 0.0
        assert stats.expectancy < 0

    def test_mixed_basic(self) -> None:
        """이익 2건, 손실 1건 혼합."""
        from trading.edge.trade_stats import compute_trade_stats

        rts = [self._rt(100.0), self._rt(100.0), self._rt(-50.0)]
        stats = compute_trade_stats(rts, sell_tax_rate=0.0)
        assert stats.n == 3
        assert abs(stats.win_rate - 2 / 3) < 1e-9
        assert stats.avg_win == 100.0
        assert stats.avg_loss == 50.0
        # profit_factor = total_win / total_loss = 200 / 50 = 4.0
        assert abs(stats.profit_factor - 4.0) < 1e-9
        # expectancy = win_rate * avg_win - (1-win_rate) * avg_loss
        expected_exp = (2 / 3) * 100.0 - (1 / 3) * 50.0
        assert abs(stats.expectancy - expected_exp) < 1e-6

    def test_sell_tax_rate_reduces_net(self) -> None:
        """sell_tax_rate=0.0018 → 청산 대금의 0.18%를 net에서 추가 차감.

        net_pnl=100, exit_price=10_000, qty=1
        추가 세금 = 10_000 * 1 * 0.0018 = 18.0
        net_after_tax = 100 - 18 = 82.0
        """
        from trading.edge.trade_stats import compute_trade_stats

        rt = self._rt(net_pnl=100.0, exit_price=10_000.0, qty=1)
        stats_notax = compute_trade_stats([rt], sell_tax_rate=0.0)
        stats_tax = compute_trade_stats([rt], sell_tax_rate=0.0018)

        # 세금 차감 후 expectancy 가 작아야 함
        assert stats_tax.expectancy < stats_notax.expectancy
        # 세금 차감 후 net = 100 - 10_000*1*0.0018 = 100 - 18 = 82
        assert abs(stats_tax.expectancy - 82.0) < 1e-6

    def test_sell_tax_makes_winning_trade_losing(self) -> None:
        """세금 차감 후 이익→손실로 전환되는 경우 win 분류에서 제외 확인."""
        from trading.edge.trade_stats import compute_trade_stats

        # net_pnl=5 (수수료 이미 차감), exit_price=10_000, qty=1 → tax=18 → net=-13
        rt = self._rt(net_pnl=5.0, exit_price=10_000.0, qty=1)
        stats = compute_trade_stats([rt], sell_tax_rate=0.0018)
        assert stats.win_rate == 0.0
        assert stats.expectancy < 0

    def test_zero_sell_tax_rate_preserves_net_pnl(self) -> None:
        """sell_tax_rate=0.0 → net_pnl 그대로 사용."""
        from trading.edge.trade_stats import compute_trade_stats

        rt = self._rt(net_pnl=200.0, exit_price=50_000.0, qty=2)
        stats = compute_trade_stats([rt], sell_tax_rate=0.0)
        assert abs(stats.expectancy - 200.0) < 1e-6

    def test_large_sample_win_rate(self) -> None:
        """100건 라운드트립 win_rate 정확성."""
        from trading.edge.trade_stats import compute_trade_stats

        rts = [self._rt(10.0)] * 60 + [self._rt(-5.0)] * 40
        stats = compute_trade_stats(rts, sell_tax_rate=0.0)
        assert stats.n == 100
        assert abs(stats.win_rate - 0.6) < 1e-9
        assert abs(stats.avg_win - 10.0) < 1e-9
        assert abs(stats.avg_loss - 5.0) < 1e-9

    def test_no_hardcoded_krx_constants(self) -> None:
        """코어 함수가 KRX 상수를 하드코딩하지 않음 — 시장 중립 (AC-CORE-1 선행 체크)."""
        import ast
        import inspect
        from trading.edge import trade_stats

        src = inspect.getsource(trade_stats)
        # 거래세율 0.0018, 0.0015, 0.0002 등 한국 특유 숫자가 모듈 본문에 없어야 함
        # (주입 파라미터로만 받아야 함)
        tree = ast.parse(src)
        # 모든 숫자 리터럴 확인 — 0.0018, 0.18 등 KRX 거래세
        krx_constants = {0.0018, 0.18, 0.15, 0.0015, 0.00215}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                assert node.value not in krx_constants, (
                    f"KRX 하드코딩 상수 발견: {node.value} — 주입 파라미터로 변경 필요"
                )
