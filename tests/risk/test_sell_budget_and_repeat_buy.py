"""SPEC-TRADING-040 — daily_count sell-budget separation (M3) + 단기과열 repeat-buy
block (M4). Reproduction-first money/risk tests.

All DB reads (``daily_order_count_today`` / ``daily_pnl_pct`` / the per-ticker
same-day buy counter) are patched so the tests stay offline.

M3 (REQ-040-3): buys count toward ``RISK_DAILY_ORDER_COUNT_MAX``; sells are NEVER
blocked by the daily-order count and never increment it. A sell-budget K is
reserved so buys cannot starve risk-reducing exits before a halt trips.

M4 (REQ-040-4): a 단기과열 (overheated) ticker is allowed at most one BUY per KST
trading day, and any BUY while the position is at an unrealised LOSS (averaging
down) is refused. Sells are unaffected.

@MX:SPEC: SPEC-TRADING-040
"""

from __future__ import annotations

from unittest.mock import patch

from trading.risk import limits


def _check(
    *,
    side: str,
    overheated: bool = False,
    held_pnl_pct: float | None = None,
    same_day_buys: int = 0,
    order_count: int = 0,
    ticker: str = "086790",
    qty: int = 1,
    ref_price: int = 1,
    holdings: list[dict] | None = None,
) -> limits.LimitCheck:
    """Run check_pre_order with only the M3/M4 gates live.

    daily_pnl_pct is forced to 0 (no daily_loss), and notional is kept tiny so
    single/per-ticker/total never breach. The order-count and same-day-buy
    counters are injected.
    """
    with (
        patch.object(limits, "daily_pnl_pct", return_value=0.0),
        patch.object(limits, "daily_order_count_today", return_value=order_count),
        patch.object(limits, "buy_count_today", return_value=same_day_buys),
    ):
        return limits.check_pre_order(
            side=side,  # type: ignore[arg-type]
            ticker=ticker,
            qty=qty,
            ref_price=ref_price,
            total_assets=10_000_000,
            holdings=holdings if holdings is not None else [],
            mode="paper",
            market="KOSPI",
            overheated=overheated,
            held_pnl_pct=held_pnl_pct,
        )


# --------------------------------------------------------------------------- #
# M3 — daily_count sell-budget separation
# --------------------------------------------------------------------------- #
class TestSellBudgetSeparation:
    def test_buy_blocked_when_budget_reserve_reached(self):
        """REQ-040-3a: buys are capped at MAX - K so K sell slots survive.

        With MAX=10 and K=2, the 9th buy (count already 8 → would be 9) must
        breach daily_count even though the raw MAX is not yet hit.
        """
        from trading import config

        # count at MAX - K means the next buy crosses into the reserved budget.
        at_reserve = config.RISK_DAILY_ORDER_COUNT_MAX - limits.SELL_BUDGET_RESERVE
        chk = _check(side="buy", order_count=at_reserve)
        assert any(b.startswith("daily_count") for b in chk.breaches)

    def test_buy_allowed_below_reserve(self):
        """A buy comfortably below the reserve threshold passes the count gate."""
        chk = _check(side="buy", order_count=0)
        assert not any(b.startswith("daily_count") for b in chk.breaches)

    def test_sell_never_blocked_by_daily_count_at_max(self):
        """REQ-040-3b (reproduction): a risk-reducing sell is NOT blocked even
        when the daily order count is already at the hard MAX.

        Reproduces the 5/26 & 5/28 pattern where buys consumed the counter and
        the pending sell was starved. Before the fix the sell tripped daily_count.
        """
        from trading import config

        chk = _check(side="sell", order_count=config.RISK_DAILY_ORDER_COUNT_MAX)
        assert not any(b.startswith("daily_count") for b in chk.breaches)

    def test_sell_passes_when_count_over_max(self):
        """Even a count above MAX must not block a sell (exits always allowed)."""
        from trading import config

        chk = _check(side="sell", order_count=config.RISK_DAILY_ORDER_COUNT_MAX + 5)
        assert chk.passed


# --------------------------------------------------------------------------- #
# M4 — 단기과열 repeat-buy block + no averaging-down on a loss
# --------------------------------------------------------------------------- #
class TestRepeatBuyBlock:
    def test_overheated_second_same_day_buy_blocked(self):
        """REQ-040-4a (reproduction): a 2nd same-day BUY of a 단기과열 ticker is
        blocked. Reproduces the 6/2 086790 x7 averaging-down pattern.
        """
        chk = _check(side="buy", overheated=True, same_day_buys=1)
        assert any(b.startswith("repeat_buy") for b in chk.breaches)
        assert not chk.passed

    def test_overheated_first_same_day_buy_allowed(self):
        """The first BUY of the day is allowed (1/day, not 0/day)."""
        chk = _check(side="buy", overheated=True, same_day_buys=0)
        assert not any(b.startswith("repeat_buy") for b in chk.breaches)

    def test_non_overheated_repeat_buy_not_blocked_by_overheat_rule(self):
        """A normal (non-overheated) ticker is not subject to the 1/day cap."""
        chk = _check(side="buy", overheated=False, same_day_buys=3)
        assert not any(b.startswith("repeat_buy") for b in chk.breaches)

    def test_overheated_buy_on_losing_position_blocked(self):
        """REQ-040-4b: averaging down a losing 단기과열 holding is refused even on
        the first same-day buy (value-trap avoidance)."""
        chk = _check(
            side="buy", overheated=True, same_day_buys=0, held_pnl_pct=-3.0
        )
        assert any(b.startswith("avg_down") for b in chk.breaches)
        assert not chk.passed

    def test_buy_on_winning_overheated_position_first_buy_allowed(self):
        """A first same-day buy of a winning overheated position is allowed."""
        chk = _check(
            side="buy", overheated=True, same_day_buys=0, held_pnl_pct=2.0
        )
        assert not any(b.startswith("avg_down") for b in chk.breaches)

    def test_sell_of_overheated_loser_not_blocked(self):
        """A SELL is never subject to the repeat-buy / avg-down gates."""
        chk = _check(
            side="sell", overheated=True, same_day_buys=5, held_pnl_pct=-9.0,
        )
        assert chk.passed
