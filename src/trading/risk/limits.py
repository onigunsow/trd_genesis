"""Pre-order risk-limit checks (REQ-RISK-05-1, REQ-RISK-05-2).

All five hard limits enforced before any order submission. Any breach
rejects the order and triggers `record_breach()`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from trading.config import (
    RISK_DAILY_MAX_LOSS,
    RISK_DAILY_ORDER_COUNT_MAX,
    RISK_PER_TICKER_MAX_POSITION,
    RISK_SINGLE_ORDER_MAX,
    RISK_TOTAL_INVESTED_MAX,
    estimate_fee,
)
from trading.db.session import audit, connection

LOG = logging.getLogger(__name__)

LimitName = Literal[
    "daily_loss",
    "per_ticker",
    "total_invested",
    "single_order",
    "daily_count",
]


@dataclass
class LimitCheck:
    passed: bool
    breaches: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def daily_order_count_today() -> int:
    """Number of orders submitted today (mode-agnostic)."""
    sql = """
        SELECT COUNT(*) AS n FROM orders
         WHERE ts::date = CURRENT_DATE
           AND status IN ('submitted','filled','partial')
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
        return int(row["n"] or 0)


def daily_pnl_pct(initial_capital: int) -> float:
    """Approximate daily PnL as a fraction of initial_capital.

    For paper M2/M3, KIS fill price is not always populated; we use a coarse
    estimate from filled orders. Production would inspect KIS balance delta.
    """
    sql = """
        SELECT COALESCE(SUM(
            CASE
              WHEN side='buy'  AND status IN ('filled','partial')
                THEN -COALESCE(fill_price, 0) * COALESCE(fill_qty, qty)
              WHEN side='sell' AND status IN ('filled','partial')
                THEN  COALESCE(fill_price, 0) * COALESCE(fill_qty, qty)
              ELSE 0
            END
        ), 0) AS pnl
        FROM orders
        WHERE ts::date = CURRENT_DATE
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
        pnl = float(row["pnl"] or 0)
    if initial_capital <= 0:
        return 0.0
    return pnl / initial_capital


def check_pre_order(
    *,
    side: Literal["buy", "sell"],
    ticker: str,
    qty: int,
    ref_price: int,
    total_assets: int,
    holdings: list[dict],
    mode: str = "paper",
    market: str = "KOSPI",
) -> LimitCheck:
    """Run all five hard-limit checks (수수료 포함 차감). Returns LimitCheck."""
    chk = LimitCheck(passed=True)
    if total_assets <= 0:
        chk.passed = False
        chk.breaches.append("total_assets is zero or negative")
        return chk

    notional = qty * max(ref_price, 0)
    fee = estimate_fee(mode=mode, side=side, market=market, notional=notional)
    # 매수 시 실제 차감되는 매수가능금액 = notional + 수수료
    cash_impact = notional + fee if side == "buy" else 0  # 매도는 cash 증가

    # 1. single order — 수수료 포함 한도
    if cash_impact > total_assets * RISK_SINGLE_ORDER_MAX:
        chk.breaches.append(
            f"single_order: 주문금액(수수료 포함) {cash_impact:,} > 한도 "
            f"{int(total_assets * RISK_SINGLE_ORDER_MAX):,}"
        )

    # 2. daily order count
    cnt = daily_order_count_today()
    if cnt + 1 > RISK_DAILY_ORDER_COUNT_MAX:
        chk.breaches.append(f"daily_count: 오늘 주문 {cnt} → 한도 {RISK_DAILY_ORDER_COUNT_MAX}")

    # 3. daily loss (only blocks NEW orders, not the current loss-recovery sell)
    pnl_pct = daily_pnl_pct(total_assets)
    if pnl_pct <= RISK_DAILY_MAX_LOSS:
        chk.breaches.append(f"daily_loss: 오늘 손익 {pnl_pct * 100:.2f}% ≤ 한도 {RISK_DAILY_MAX_LOSS * 100:.2f}%")

    # 4. per-ticker max position (수수료 포함 차감 후 비중)
    if side == "buy":
        existing = next((h for h in holdings if h["ticker"] == ticker), None)
        existing_value = (existing["eval_amount"] if existing else 0)
        projected_value = existing_value + notional + fee
        if projected_value > total_assets * RISK_PER_TICKER_MAX_POSITION:
            chk.breaches.append(
                f"per_ticker: {ticker} 예상 보유(수수료 포함) {projected_value:,} > 한도 "
                f"{int(total_assets * RISK_PER_TICKER_MAX_POSITION):,}"
            )

    # 5. total invested
    if side == "buy":
        invested = sum(h.get("eval_amount", 0) for h in holdings)
        projected_invested = invested + notional + fee
        if projected_invested > total_assets * RISK_TOTAL_INVESTED_MAX:
            chk.breaches.append(
                f"total_invested: 투자 후 비중 "
                f"{projected_invested / total_assets * 100:.1f}% > 한도 "
                f"{RISK_TOTAL_INVESTED_MAX * 100:.1f}%"
            )

    if fee > 0:
        chk.warnings.append(f"예상 수수료 {fee:,}원 (mode={mode}, market={market})")

    chk.passed = not chk.breaches
    return chk


def record_breach(check: LimitCheck, context: dict) -> None:
    """Audit a limit breach. Caller still raises/returns to the caller."""
    audit("LIMIT_BREACH", actor="risk", details={
        "breaches": check.breaches,
        "context": context,
    })
