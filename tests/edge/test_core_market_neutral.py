"""T-013 — 회귀 스위트 + 시장 중립 dual-param 증명.

SPEC-TRADING-048 REQ-048-NFR-1/2, REQ-048-CORE-1.
AC: AC-CORE-1(한국/미국 dual-param), AC-NFR-1(0회귀).
"""

from __future__ import annotations

import ast
import inspect

import pytest


# ---------------------------------------------------------------------------
# AC-CORE-1: 코어 모듈 KRX 상수 하드코딩 0건 + 한국/미국 dual-param 증명
# ---------------------------------------------------------------------------

_KRX_CONSTANTS = {0.0018, 0.18, 0.0015, 0.15, 0.00215}


def _check_no_krx_constants(module) -> None:
    """모듈 소스에 KRX 상수가 없음을 검증."""
    src = inspect.getsource(module)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            assert node.value not in _KRX_CONSTANTS, (
                f"{module.__name__}: KRX 하드코딩 상수 발견 {node.value}"
            )


class TestCoreMarketNeutralGrep:
    """코어 모듈 본문 KRX 상수 grep 0건."""

    def test_trade_stats_no_krx(self) -> None:
        from trading.edge import trade_stats
        _check_no_krx_constants(trade_stats)

    def test_evaluate_backtest_no_krx(self) -> None:
        from trading.edge import evaluate_backtest
        _check_no_krx_constants(evaluate_backtest)

    def test_kelly_no_krx(self) -> None:
        from trading.strategy.sizing import kelly
        _check_no_krx_constants(kelly)

    def test_postmortem_no_krx(self) -> None:
        from trading.edge import postmortem
        _check_no_krx_constants(postmortem)


class TestCoreDualParam:
    """동일 코어를 한국/미국 파라미터로 각각 호출해 출력이 파라미터에만 의존함을 증명."""

    def test_kelly_fraction_dual_param(self) -> None:
        from trading.strategy.sizing.kelly import kelly_fraction

        # 한국 파라미터 (KRX 승률/손익비)
        k_kr = kelly_fraction(0.60, 2.0)
        # 미국 파라미터 (다른 승률/손익비)
        k_us = kelly_fraction(0.55, 1.8)

        assert k_kr != k_us  # 입력이 다르면 출력이 다름
        assert isinstance(k_kr, float)
        assert isinstance(k_us, float)

    def test_half_kelly_cap_dual_param(self) -> None:
        from trading.strategy.sizing.kelly import kelly_fraction, half_kelly_cap

        k_kr = kelly_fraction(0.60, 2.0)
        k_us = kelly_fraction(0.55, 1.8)

        # 한국: 원화 자기자본, KRX 주가
        qty_kr = half_kelly_cap(k_kr, 5_000_000, 50_000, lot_size=1)
        # 미국: USD 자기자본, 미국 주가
        qty_us = half_kelly_cap(k_us, 10_000, 150, lot_size=1)

        assert qty_kr >= 0
        assert qty_us >= 0

    def test_compute_trade_stats_dual_param(self) -> None:
        from trading.edge.trade_stats import compute_trade_stats

        rts = [
            {"net_pnl": 1000.0, "exit_price": 50_000.0, "qty": 1},
            {"net_pnl": -500.0, "exit_price": 50_000.0, "qty": 1},
        ]

        # 한국 거래세율 주입
        stats_kr = compute_trade_stats(rts, sell_tax_rate=0.0018)
        # 미국 가상 세율(SEC fee 등) 주입
        stats_us = compute_trade_stats(rts, sell_tax_rate=0.0001)

        # 세율이 다르면 expectancy 가 다름
        assert stats_kr.expectancy != stats_us.expectancy

    def test_score_backtest_dual_param(self) -> None:
        from trading.edge.evaluate_backtest import score_backtest
        from trading.edge.trade_stats import TradeStats

        stats = TradeStats(n=200, win_rate=0.6, avg_win=200.0, avg_loss=100.0,
                           profit_factor=1.8, expectancy=80.0)
        portfolio = {
            "mdd": 0.20, "sharpe": 1.5, "cagr": 0.15,
            "equity_curve": [1.0] * 5 + [1.0 + 0.01 * i for i in range(80)],
            "daily_returns": [0.0] * 5 + [0.001] * 80,
            "test_years": 6.0, "n_params": 5, "open_positions": 0,
        }
        is_oos = {"is_expectancy": 80.0, "oos_expectancy": 60.0}

        # 한국 exp_full 파라미터 주입
        card_kr = score_backtest(stats, portfolio, is_oos,
                                 scoring_params={"exp_full": 10_000.0})
        # 미국 exp_full (USD 기준 다른 기준)
        card_us = score_backtest(stats, portfolio, is_oos,
                                 scoring_params={"exp_full": 500.0})

        # 코드 수정 없이 두 파라미터 세트 모두 실행 완료
        assert card_kr is not None
        assert card_us is not None

    def test_postmortem_dual_param(self) -> None:
        from trading.edge.postmortem import classify_decision_outcome

        decision = {"side": "buy", "confidence": 0.7, "persona": "decision"}
        rt = {"net_pnl": 1000.0}

        # 한국 파라미터 (KOSPI 상대수익 임계)
        out_kr = classify_decision_outcome(
            decision, rt, 0.02, 0.03, "neutral",
            thresholds={"confidence_threshold": 0.6, "relative_threshold": 0.0},
        )
        # 미국 파라미터 (SPY 상대수익 임계)
        out_us = classify_decision_outcome(
            decision, rt, 0.01, 0.02, "neutral",
            thresholds={"confidence_threshold": 0.65, "relative_threshold": 0.0},
        )

        assert out_kr.label is not None
        assert out_us.label is not None


class TestSizingModeInvariant:
    """SIZING_MODE 기본값 OFF(llm_direct) 불변 + confidence 비증폭 불변."""

    def test_sizing_mode_default_llm_direct(self) -> None:
        """SIZING_MODE 기본값이 llm_direct (T-006 불변 회귀)."""
        import os
        # env var 미설정 시 기본값
        saved = os.environ.pop("SIZING_MODE", None)
        try:
            import importlib
            import trading.config as cfg
            importlib.reload(cfg)
            assert cfg.SIZING_MODE == "llm_direct"
        finally:
            if saved is not None:
                os.environ["SIZING_MODE"] = saved
            import trading.config as cfg2
            importlib.reload(cfg2)

    def test_heat_cap_default_0_08(self) -> None:
        """heat_cap 기본값 0.08 (T-006)."""
        import os
        saved = os.environ.pop("SIZING_HEAT_CAP", None)
        try:
            from trading.config import SizingParams
            p = SizingParams()
            assert abs(p.heat_cap - 0.08) < 1e-9
        finally:
            if saved is not None:
                os.environ["SIZING_HEAT_CAP"] = saved
