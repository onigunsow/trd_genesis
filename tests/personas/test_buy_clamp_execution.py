"""매수 삭감이 실행부까지 실제로 도달하는가 (2026-08-27).

check_pre_order 가 allowed_qty 를 계산하는 것과, 오케스트레이터가 그 값으로
수량을 줄여 다시 검사하고 주문을 내보내는 것은 별개다. 계산만 맞고 배선이
빠져 있으면 로그에는 아무것도 안 뜨는데, 그게 "발동 조건이 없었다" 인지
"배선이 없다" 인지 구분할 수 없다 — 실제 삭감은 사이징이 작아 드물게만 일어나므로
로그를 기다려서는 확인이 안 된다.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests.personas.test_intraday_cycle import _decision_result, _risk_result
from trading.risk.limits import LimitCheck


def _run(*, first_check: LimitCheck, second_check: LimitCheck | None = None):
    """첫 검사에서 삭감 가능 위반 → 삭감 후 재검사 → 주문."""
    cached_micro_row = {
        "id": 42,
        "response_json": {"candidates": {"buy": [{"ticker": "086790"}], "sell": [], "hold": []}},
    }
    signals = [{"ticker": "086790", "side": "buy", "qty": 21, "rationale": "test",
                "confidence": 0.72}]
    checks = [first_check] + ([second_check] if second_check else [])

    with (
        patch("trading.personas.orchestrator.macro_persona") as mock_macro,
        patch("trading.personas.orchestrator.micro_persona") as mock_micro,
        patch("trading.personas.orchestrator.decision_persona") as mock_decision,
        patch("trading.personas.orchestrator.risk_persona") as mock_risk,
        patch("trading.personas.orchestrator.tg"),
        patch("trading.personas.orchestrator.get_settings") as mock_settings,
        patch("trading.personas.orchestrator.get_system_state") as mock_state,
        patch("trading.personas.orchestrator._gather_assets") as mock_assets,
        patch("trading.personas.orchestrator.get_blocked_tickers") as mock_blocked,
        patch("trading.personas.orchestrator.check_pre_order_safety") as mock_safety,
        patch("trading.personas.orchestrator.check_pre_order", side_effect=checks) as chk,
        patch("trading.personas.orchestrator.record_breach") as mock_breach,
        patch("trading.personas.orchestrator.audit") as mock_audit,
        patch("trading.personas.orchestrator.circuit_breaker"),
        patch("trading.personas.orchestrator.KisClient"),
        patch("trading.personas.orchestrator._count_holds_today", return_value=0),
        patch("trading.personas.orchestrator._execute_signal", return_value=901) as ex,
    ):
        mock_macro.latest_cached.return_value = {
            "id": 10, "response": "bullish", "response_json": {"regime": "bull"}}
        mock_micro.latest_cached.return_value = cached_micro_row
        mock_decision.run.return_value = (_decision_result(signals=signals), [101])
        mock_risk.run.return_value = (_risk_result(verdict="APPROVE"), 201, "APPROVE")
        mock_settings.return_value = MagicMock(trading_mode="paper")
        mock_state.return_value = {"halt_state": False}
        mock_assets.return_value = {"total_assets": 10_000_000, "cash_d2": 9_600_000,
                                    "stock_eval": 400_000, "holdings": []}
        mock_blocked.return_value = {"blocked": {}}
        mock_safety.return_value = MagicMock(passed=True, quote={"price": 226_000})

        from trading.personas.orchestrator import run_intraday_cycle
        result = run_intraday_cycle(today="2026-08-27")

    return result, chk, ex, mock_audit, mock_breach


_OVERSIZE = ["single_order: 주문금액(수수료 포함) 4,746,000 > 한도 1,000,000"]


class TestClampReachesExecution:
    def test_oversized_buy_is_reduced_and_ordered(self):
        """거부로 끝나지 않고 줄인 수량으로 주문이 나가야 한다."""
        result, chk, ex, audit, breach = _run(
            first_check=LimitCheck(passed=False, breaches=list(_OVERSIZE), allowed_qty=4),
            second_check=LimitCheck(passed=True, allowed_qty=4),
        )
        assert ex.called, "삭감 후 주문이 실행되지 않았다"
        assert 901 in result.executed_orders
        assert not breach.called, "삭감으로 해소됐으면 breach 기록으로 끝나면 안 된다"

    def test_recheck_runs_with_the_clamped_qty(self):
        """계산만 믿으면 한도 넘긴 주문이 샌다 — 줄인 수량으로 반드시 재검사."""
        _, chk, _, _, _ = _run(
            first_check=LimitCheck(passed=False, breaches=list(_OVERSIZE), allowed_qty=4),
            second_check=LimitCheck(passed=True, allowed_qty=4),
        )
        assert chk.call_count == 2
        first_qty = chk.call_args_list[0].kwargs["qty"]
        # 사이징 단계가 이미 한 번 줄일 수 있으므로 절대값이 아니라 관계로 본다.
        assert chk.call_args_list[1].kwargs["qty"] == 4
        assert first_qty != 4, "재검사가 원래 수량 그대로 돌았다 — 삭감이 반영되지 않았다"

    def test_clamp_is_audited_with_both_quantities(self):
        """왜 적게 샀는지 사후에 재구성할 수 있어야 한다."""
        _, chk, _, audit, _ = _run(
            first_check=LimitCheck(passed=False, breaches=list(_OVERSIZE), allowed_qty=4),
            second_check=LimitCheck(passed=True, allowed_qty=4),
        )
        ev = [c for c in audit.call_args_list if c.args and c.args[0] == "ORDER_QTY_CLAMPED"]
        assert ev, "ORDER_QTY_CLAMPED 감사 기록이 없다"
        d = ev[0].kwargs["details"]
        assert d["requested_qty"] == chk.call_args_list[0].kwargs["qty"]
        assert d["clamped_qty"] == 4
        assert d["breaches"] == _OVERSIZE


class TestClampDoesNotFireWhenItMustNot:
    def test_non_size_breach_is_rejected_not_clamped(self):
        """물타기 금지 종목을 '조금만' 사는 일이 없어야 한다."""
        result, chk, ex, _, breach = _run(
            first_check=LimitCheck(
                passed=False,
                breaches=["avg_down: 086790 단기과열·손실(-1.20%) 물타기 매수 거부"],
                allowed_qty=0,
            ),
        )
        assert chk.call_count == 1, "삭감 불가 위반인데 재검사를 돌렸다"
        assert not ex.called
        assert 101 in result.rejected
        assert breach.called

    def test_recheck_failure_falls_back_to_rejection(self):
        """줄여도 통과 못 하면 종전대로 거부한다."""
        result, chk, ex, _, breach = _run(
            first_check=LimitCheck(passed=False, breaches=list(_OVERSIZE), allowed_qty=4),
            second_check=LimitCheck(passed=False, breaches=list(_OVERSIZE), allowed_qty=0),
        )
        assert chk.call_count == 2
        assert not ex.called
        assert 101 in result.rejected
        assert breach.called
