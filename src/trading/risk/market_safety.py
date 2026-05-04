"""Pre-order market safety checks (REQ-KIS-02-12).

Independent of code limits (limits.py). Checks ticker-level state via KIS quote:
- 거래정지·관리종목·투자위험·투자경고·단기과열 → 매매 차단
- 상하한가 도달/근접 (±1%) → 매매 차단
- 매수가능금액 부족 (nrcvb_buy_amt 차감 후) → 매매 차단

Returns SafetyResult; orchestrator uses it to gate orders.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from trading.kis.account import balance
from trading.kis.client import KisClient
from trading.kis.market import current_price, stat_cls_label

LOG = logging.getLogger(__name__)


@dataclass
class SafetyResult:
    passed: bool
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    quote: dict | None = None
    buyable_effective: int | None = None


def check_pre_order_safety(
    client: KisClient,
    *,
    ticker: str,
    side: str,                # 'buy' | 'sell'
    qty: int,
    notional: int,
) -> SafetyResult:
    """Pre-order live safety check.

    Returns SafetyResult.passed=False if any blocker found.
    Cheap KIS calls only (current_price + balance — already cached at orchestrator level
    in production; here we re-fetch to ensure freshness for safety-critical decision).
    """
    res = SafetyResult(passed=True)

    # 1. Quote — stat_cls + upper/lower limits
    try:
        q = current_price(client, ticker)
    except Exception as e:  # noqa: BLE001
        res.passed = False
        res.blockers.append(f"quote_fetch_failed: {e}")
        return res
    res.quote = q

    if not q["is_normal"]:
        label = stat_cls_label(q["stat_cls"])
        res.blockers.append(f"stat_cls={q['stat_cls']} ({label}) — 매매 차단")

    if side == "buy" and q["near_upper_limit"]:
        res.blockers.append(
            f"near_upper_limit: 현재가 {q['price']:,} vs 상한가 {q['upper_limit']:,} (1% 이내)"
        )
    if side == "sell" and q["near_lower_limit"]:
        res.blockers.append(
            f"near_lower_limit: 현재가 {q['price']:,} vs 하한가 {q['lower_limit']:,} (1% 이내)"
        )

    # 2. 매수가능금액 (buy 시만)
    if side == "buy":
        try:
            bal = balance(client)
        except Exception as e:  # noqa: BLE001
            res.warnings.append(f"balance_fetch_failed: {e}")
            return res

        buyable_eff = bal.get("buyable_effective", bal.get("buyable", 0))
        res.buyable_effective = buyable_eff

        if notional > buyable_eff:
            res.blockers.append(
                f"buyable_short: 주문 {notional:,} > 실효 매수가능 {buyable_eff:,} "
                f"(미체결 매수금 {bal.get('nrcvb_buy_amt', 0):,} 차감 후)"
            )
        elif notional > buyable_eff * 0.95:
            res.warnings.append(
                f"buyable_tight: 주문 {notional:,} ≈ 실효 매수가능 {buyable_eff:,}의 "
                f"{notional / buyable_eff * 100:.1f}%"
            )

    res.passed = not res.blockers
    return res
