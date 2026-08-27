"""매수 수량 삭감 (2026-08-27).

decision.jinja 는 "단일 주문 한도를 초과하면 분할한다" 고 약속하는데 코드는
통째로 거부만 해왔다 — 삭감 경로가 매도(clamp_sell_to_confirmed)에만 있었다.

실측: 035420 을 5/5~5/8 사흘간 15번 제안했으나 한 번도 주문이 되지 못했다.
거부 사유 중 하나가 "단일 주문 한도 100만원" 이라고 스스로 쓰면서 474만원
(21주 x 226,000원, 한도의 4.7배)을 산정한 내부 모순이었다. confidence 는
0.72 로 전체 데이터셋 최고치였고, 20거래일 뒤 +28.6퍼센트였다.

가장 위험한 회귀는 "사면 안 되는 것을 조금 사는" 것이다. 크기 외 위반이
하나라도 섞이면 삭감하지 않는다.
"""

from __future__ import annotations

from unittest.mock import patch

from trading.risk import limits


def _check(**kw):
    base = dict(
        side="buy", ticker="035420", qty=21, ref_price=226_000,
        total_assets=10_000_000, holdings=[], mode="paper", confidence=0.72,
    )
    base.update(kw)
    with (
        patch.object(limits, "daily_pnl_pct", return_value=0.0),
        patch.object(limits, "daily_order_count_today", return_value=0),
        patch.object(limits, "buy_count_today", return_value=0),
        patch.object(limits, "days_since_loss_exit", return_value=None),
    ):
        return limits.check_pre_order(**base)


class TestClampComputesLargestPassingQty:
    def test_naver_case_yields_four_shares(self):
        """21주 474만원 -> 한도 100만원 안의 4주(904,000원)."""
        chk = _check()
        assert chk.passed is False
        assert chk.size_only_breach is True
        assert chk.allowed_qty == 4

    def test_clamped_qty_actually_passes_on_recheck(self):
        """계산만 하고 끝내면 안 된다 — 그 수량으로 다시 검사해 통과해야 한다."""
        chk = _check()
        again = _check(qty=chk.allowed_qty)
        assert again.passed is True, again.breaches

    def test_passing_order_reports_its_own_qty(self):
        chk = _check(qty=4)
        assert chk.passed is True
        assert chk.allowed_qty == 4

    def test_existing_position_shrinks_the_budget(self):
        """이미 들고 있으면 종목당 한도가 예산을 좁힌다."""
        held = [{"ticker": "035420", "qty": 5, "eval_amount": 1_130_000,
                 "avg_cost": 226_000, "pnl_pct": 0.0}]
        chk = _check(holdings=held)
        assert chk.allowed_qty is not None
        assert chk.allowed_qty < 4


class TestNonSizeBreachesAreNeverClamped:
    """수량을 줄여도 해소되지 않는 위반은 삭감 대상이 아니다."""

    def test_confidence_floor_blocks_clamping(self):
        chk = _check(confidence=0.45)
        assert chk.size_only_breach is False
        assert chk.allowed_qty == 0

    def test_repeat_buy_blocks_clamping(self):
        with patch.object(limits, "buy_count_today", return_value=1):
            with (
                patch.object(limits, "daily_pnl_pct", return_value=0.0),
                patch.object(limits, "daily_order_count_today", return_value=0),
                patch.object(limits, "days_since_loss_exit", return_value=None),
            ):
                chk = limits.check_pre_order(
                    side="buy", ticker="035420", qty=21, ref_price=226_000,
                    total_assets=10_000_000, holdings=[], mode="paper",
                    overheated=True, confidence=0.72,
                )
        assert any(b.startswith("repeat_buy") for b in chk.breaches)
        assert chk.size_only_breach is False
        assert chk.allowed_qty == 0

    def test_daily_loss_blocks_clamping(self):
        with (
            patch.object(limits, "daily_pnl_pct", return_value=-0.05),
            patch.object(limits, "daily_order_count_today", return_value=0),
            patch.object(limits, "buy_count_today", return_value=0),
            patch.object(limits, "days_since_loss_exit", return_value=None),
        ):
            chk = limits.check_pre_order(
                side="buy", ticker="035420", qty=21, ref_price=226_000,
                total_assets=10_000_000, holdings=[], mode="paper", confidence=0.72,
            )
        assert chk.size_only_breach is False
        assert chk.allowed_qty == 0


class TestSellIsUnaffected:
    def test_sell_gets_no_allowed_qty(self):
        """매도는 clamp_sell_to_confirmed 가 담당한다 — 여기서 건드리지 않는다."""
        held = [{"ticker": "035420", "qty": 5, "eval_amount": 1_130_000,
                 "avg_cost": 226_000, "pnl_pct": -3.0}]
        chk = _check(side="sell", qty=5, holdings=held)
        assert chk.allowed_qty is None


class TestNoAffordableQty:
    def test_budget_below_one_share_yields_zero(self):
        """1주도 못 사면 0 — 삭감으로 억지 진입하지 않는다."""
        chk = _check(total_assets=1_000_000, qty=21)
        assert chk.allowed_qty == 0
