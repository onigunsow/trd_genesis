"""SPEC-TRADING-046 M3: orchestrator seam + feature flag 테스트.

AC-4 — sizing_mode=llm_direct (default): byte-for-byte 현 동작 보존.
AC-5 — sizing_mode=deterministic: 결정적 사이징 모듈 호출, 양쪽 qty 영속.

이 테스트는 구현보다 먼저 작성됩니다 (RED 단계 → GREEN 단계).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 헬퍼: _execute_signal 임포트
# ---------------------------------------------------------------------------

def _get_execute_signal():
    from trading.personas.orchestrator import _execute_signal
    return _execute_signal


# ---------------------------------------------------------------------------
# AC-4: sizing_mode=llm_direct 기본 경로 — byte-for-byte 보존 (REQ-046-E1/E2)
# ---------------------------------------------------------------------------

class TestLlmDirectDefaultPath:
    """sizing_mode=llm_direct 기본 경로: 사이징 모듈 미호출, LLM qty 사용."""

    def _make_fake_client(self):
        client = MagicMock()
        client.mode = MagicMock()
        client.mode.value = "paper"
        return client

    @patch("trading.personas.orchestrator.SIZING_MODE", "llm_direct")
    @patch("trading.personas.orchestrator.intraday_reconcile")
    @patch("trading.personas.orchestrator.guard_sell")
    @patch("trading.personas.orchestrator.kis_buy")
    @patch("trading.personas.orchestrator.audit")
    def test_llm_direct_uses_sig_qty_for_buy(
        self, mock_audit, mock_buy, mock_guard_sell, mock_reconcile
    ):
        """AC-4: llm_direct 모드에서 BUY는 sig['qty'] 를 그대로 사용한다."""
        _execute_signal = _get_execute_signal()
        client = self._make_fake_client()

        mock_buy.return_value = {"order_id": 42}

        sig = {
            "ticker": "005930",
            "side": "buy",
            "qty": 7,       # LLM 이 낸 qty
            "rationale": "test",
        }

        result = _execute_signal(client, sig, decision_id=1)
        assert result == 42

        # kis_buy 가 LLM qty=7 로 호출되어야 함
        called_qty = mock_buy.call_args.kwargs.get("qty") or mock_buy.call_args[1].get("qty")
        assert called_qty == 7, f"llm_direct 모드에서 LLM qty=7 이 그대로 사용되어야 한다, 실제={called_qty}"

    @patch("trading.personas.orchestrator.SIZING_MODE", "llm_direct")
    @patch("trading.personas.orchestrator.audit")
    def test_llm_direct_does_not_call_sizing_module(self, mock_audit):
        """AC-4: llm_direct 모드에서 compute_qty 가 호출되지 않아야 한다."""
        _execute_signal = _get_execute_signal()
        client = self._make_fake_client()

        with patch("trading.personas.orchestrator.compute_qty") as mock_sizing:
            sig = {"ticker": "005930", "side": "buy", "qty": 5}

            # qty=5 이므로 실제로 실행되겠지만 sizing 모듈은 호출 안 됨
            # kis_buy 실패해도 상관없음 — 단지 sizing 호출 여부만 검사
            try:
                _execute_signal(client, sig, decision_id=1)
            except Exception:
                pass

            mock_sizing.assert_not_called()

    @patch("trading.personas.orchestrator.SIZING_MODE", "llm_direct")
    @patch("trading.personas.orchestrator.audit")
    def test_llm_direct_zero_qty_returns_none(self, mock_audit):
        """AC-4: llm_direct 모드에서 qty=0 은 None 반환 (현 동작 보존)."""
        _execute_signal = _get_execute_signal()
        client = self._make_fake_client()

        sig = {"ticker": "005930", "side": "buy", "qty": 0}
        result = _execute_signal(client, sig, decision_id=1)
        assert result is None

    @patch("trading.personas.orchestrator.SIZING_MODE", "llm_direct")
    @patch("trading.personas.orchestrator.audit")
    def test_llm_direct_hold_returns_none(self, mock_audit):
        """AC-4: llm_direct 모드에서 side=hold 는 None 반환 (현 동작 보존)."""
        _execute_signal = _get_execute_signal()
        client = self._make_fake_client()

        sig = {"ticker": "005930", "side": "hold", "qty": 3}
        result = _execute_signal(client, sig, decision_id=1)
        assert result is None


# ---------------------------------------------------------------------------
# AC-5: sizing_mode=deterministic — 결정적 사이징 호출 + 양쪽 qty 기록
# ---------------------------------------------------------------------------

class TestDeterministicSizingSeam:
    """sizing_mode=deterministic: compute_qty 호출, 두 qty 감사 로그."""

    def _make_fake_client(self):
        client = MagicMock()
        client.mode = MagicMock()
        client.mode.value = "paper"
        return client

    def _make_portfolio_state(
        self,
        total_assets: int = 10_000_000,
        cash: int = 10_000_000,
        atr_pct: float | None = 2.0,
        ref_price: int = 50_000,
    ) -> dict:
        return {
            "total_assets": total_assets,
            "cash": cash,
            "atr_pct": atr_pct,
            "ref_price": ref_price,
            "holdings": [],
        }

    @patch("trading.personas.orchestrator.SIZING_MODE", "deterministic")
    @patch("trading.personas.orchestrator.intraday_reconcile")
    @patch("trading.personas.orchestrator.kis_buy")
    @patch("trading.personas.orchestrator.audit")
    def test_deterministic_buy_uses_computed_qty(
        self, mock_audit, mock_buy, mock_reconcile
    ):
        """AC-5: deterministic 모드에서 BUY 는 compute_qty 결과를 사용한다."""
        _execute_signal = _get_execute_signal()
        client = self._make_fake_client()

        mock_buy.return_value = {"order_id": 99}

        # vol_target=1%, total_assets=10M, atr_pct=2%
        # → notional = (0.01 * 10M) / 0.02 = 5M
        # → qty = floor(5M / 50000) = 100
        portfolio_state = self._make_portfolio_state(
            total_assets=10_000_000,
            cash=10_000_000,
            atr_pct=2.0,
            ref_price=50_000,
        )

        sig = {
            "ticker": "005930",
            "side": "buy",
            "qty": 3,       # LLM 이 낸 qty (어드바이저리)
        }

        result = _execute_signal(client, sig, decision_id=1, portfolio_state=portfolio_state)
        assert result == 99

        # kis_buy 가 deterministic qty (100) 로 호출되어야 함, LLM qty=3 이 아님
        called_qty = mock_buy.call_args.kwargs.get("qty") or mock_buy.call_args[1].get("qty")
        assert called_qty != 3, "deterministic 모드에서 LLM qty=3 을 그대로 사용하면 안 됨"
        assert called_qty > 3, f"deterministic qty={called_qty} 이 LLM qty=3 보다 커야 함"

    @patch("trading.personas.orchestrator.SIZING_MODE", "deterministic")
    @patch("trading.personas.orchestrator.intraday_reconcile")
    @patch("trading.personas.orchestrator.kis_buy")
    @patch("trading.personas.orchestrator.audit")
    def test_deterministic_advisory_qty_preserved_in_sig(
        self, mock_audit, mock_buy, mock_reconcile
    ):
        """AC-5: deterministic 모드에서 sig 에 advisory_qty 가 기록된다 (REQ-046-E3)."""
        _execute_signal = _get_execute_signal()
        client = self._make_fake_client()

        mock_buy.return_value = {"order_id": 99}

        llm_qty = 3
        portfolio_state = self._make_portfolio_state()

        sig = {
            "ticker": "005930",
            "side": "buy",
            "qty": llm_qty,
        }

        _execute_signal(client, sig, decision_id=1, portfolio_state=portfolio_state)

        # sig 에 advisory_qty 가 LLM 원본 qty 로 보존되어야 함
        assert "advisory_qty" in sig, "sig 에 advisory_qty 가 기록되어야 함 (REQ-046-E3)"
        assert sig["advisory_qty"] == llm_qty

    @patch("trading.personas.orchestrator.SIZING_MODE", "deterministic")
    @patch("trading.personas.orchestrator.audit")
    def test_deterministic_sell_bypasses_sizing(self, mock_audit):
        """AC-5: SELL 신호는 deterministic 모드에서도 사이징 건드리지 않는다."""
        _execute_signal = _get_execute_signal()
        client = self._make_fake_client()

        portfolio_state = self._make_portfolio_state()

        # SELL: qty=0 이면 SPEC-042 가드 이전에 None 반환 (현 동작)
        sig = {"ticker": "005930", "side": "sell", "qty": 0}
        result = _execute_signal(client, sig, decision_id=1, portfolio_state=portfolio_state)
        # qty=0 SELL 이면 None (현 동작 보존)
        assert result is None

    @patch("trading.personas.orchestrator.SIZING_MODE", "deterministic")
    @patch("trading.personas.orchestrator.audit")
    def test_deterministic_no_portfolio_state_falls_through(self, mock_audit):
        """portfolio_state 없으면 llm_direct fallback — 현 동작 보존."""
        _execute_signal = _get_execute_signal()
        client = self._make_fake_client()

        sig = {"ticker": "005930", "side": "buy", "qty": 0}
        # portfolio_state 없이 deterministic → llm qty=0 → None
        result = _execute_signal(client, sig, decision_id=1)
        assert result is None


# ---------------------------------------------------------------------------
# SPEC-042 공존: deterministic 모드에서도 SELL guard 가 동작해야 한다
# ---------------------------------------------------------------------------

class TestSpec042Coexistence:
    """SPEC-042 sell-guard 가 deterministic 모드에서도 보존된다 (REQ-046-E2)."""

    def _make_fake_client(self):
        client = MagicMock()
        client.mode = MagicMock()
        client.mode.value = "paper"
        return client

    @patch("trading.personas.orchestrator.SIZING_MODE", "deterministic")
    @patch("trading.personas.orchestrator.resolve_stuck_orders")
    @patch("trading.personas.orchestrator.guard_sell")
    @patch("trading.personas.orchestrator.audit")
    def test_deterministic_sell_still_checks_guard_sell(
        self, mock_audit, mock_guard_sell, mock_resolve
    ):
        """SELL 경로에서 guard_sell 이 호출된다 (SPEC-042 코드 경로 보존)."""
        _execute_signal = _get_execute_signal()
        client = self._make_fake_client()

        mock_guard_sell.return_value = False  # SELL 차단 시뮬레이션

        portfolio_state = {
            "total_assets": 10_000_000,
            "cash": 0,
            "atr_pct": 2.0,
            "ref_price": 50_000,
            "holdings": [],
        }

        sig = {"ticker": "005930", "side": "sell", "qty": 5}
        result = _execute_signal(client, sig, decision_id=1, portfolio_state=portfolio_state)

        # guard_sell 이 False 를 반환하면 None
        assert result is None
        mock_guard_sell.assert_called_once()
