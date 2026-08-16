"""2026-08-16 고가주 1주 예외.

1주 가격이 단일 주문/종목당 한도를 넘어도 상한 이내면 정확히 1주 허용.
"""

from __future__ import annotations

from unittest.mock import patch

from trading.risk import limits


def _check(**kw):
    base = dict(
        ticker="000660", side="buy", qty=1, ref_price=1_593_000,
        total_assets=10_000_000, holdings=[], mode="paper",
    )
    base.update(kw)
    with (
        patch.object(limits, "daily_order_count_today", return_value=0),
        patch.object(limits, "daily_pnl_pct", return_value=0.0),
        patch.object(limits, "days_since_loss_exit", return_value=None),
        patch.object(limits, "estimate_fee", return_value=0),
    ):
        return limits.check_pre_order(**base)


def _kinds(chk):
    return {b.split(":")[0] for b in chk.breaches}


def test_one_share_above_order_cap_passes():
    chk = _check()  # 15.9% > 단일 10%·종목당 15%, but ≤ 20%
    assert not ({"single_order", "per_ticker"} & _kinds(chk)), chk.breaches


def test_two_shares_still_blocked():
    assert {"single_order", "per_ticker"} <= _kinds(_check(qty=2))


def test_one_share_over_ceiling_blocked():
    with patch.object(limits, "RISK_ONE_SHARE_MAX_PCT", 0.10):
        assert "single_order" in _kinds(_check())


def test_exception_only_for_new_position():
    held = [{"ticker": "000660", "eval_amount": 1_593_000}]
    assert "per_ticker" in _kinds(_check(holdings=held))


def test_disabled_when_zero():
    with patch.object(limits, "RISK_ONE_SHARE_MAX_PCT", 0.0):
        assert "single_order" in _kinds(_check())
