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
    RISK_SELL_BUDGET_RESERVE,
    RISK_SINGLE_ORDER_MAX,
    RISK_TOTAL_INVESTED_MAX,
    estimate_fee,
)
from trading.db.session import audit, connection

LOG = logging.getLogger(__name__)

# SPEC-TRADING-040 M3 (REQ-040-3a): the per-day sell-budget reserve. Buys are
# capped at RISK_DAILY_ORDER_COUNT_MAX - SELL_BUDGET_RESERVE; the reserved slots
# are sell-only. Sells are excluded from the count entirely (REQ-040-3b).
SELL_BUDGET_RESERVE = RISK_SELL_BUDGET_RESERVE

LimitName = Literal[
    "daily_loss",
    "per_ticker",
    "total_invested",
    "single_order",
    "daily_count",
]

# SPEC-TRADING-062 (REQ-062-A3): breach 접두 토큰(':' 앞) 중 계좌 전체 위험을 뜻하는
# 집합. 이 집합에 속한 토큰이 하나라도 있으면 회로차단(전체 halt)을 트립한다. 나머지
# breach(avg_down/repeat_buy/per_ticker/total_invested 등)는 해당 주문만 거부하는
# per-signal 자문 차단이며 계좌 전체를 멈출 이유가 없다. 시장 종속 값이 아니므로
# US 시장을 포함한 다른 시장에도 그대로 재사용 가능하다(하드코딩 금지 원칙).
ACCOUNT_HALT_BREACH_TOKENS = frozenset({"daily_loss"})


def requires_circuit_halt(breaches: list[str]) -> bool:
    """breach 접두 토큰 기준으로 회로차단(전체 halt)이 필요한지 판별하는 순수 함수.

    각 breach 문자열은 ``"<token>: <message>"`` 형태이며, 첫 ':' 앞부분을 토큰으로
    본다. 토큰 중 하나라도 ``ACCOUNT_HALT_BREACH_TOKENS``에 속하면 True.
    """
    for breach in breaches:
        token = breach.split(":", 1)[0].strip()
        if token in ACCOUNT_HALT_BREACH_TOKENS:
            return True
    return False


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


# @MX:ANCHOR: daily_pnl_pct is the daily-loss circuit-breaker input.
# fan_in: check_pre_order (the pre-order hard-limit gate) and the briefing
# prompts both consume it.
# @MX:REASON: SPEC-039 — this MUST be *realized* P&L (completed round-trips that
# exit today), NOT net trade cash flow. The old cash-flow formula counted buy
# cash-outflow as loss, so a net-buy day reported a phantom loss (2026-06-01:
# -3.34% false daily_loss halt while the day's realized P&L was +24,283). A buy
# still held contributes nothing — only completed round-trips do. Mode-agnostic:
# correct for both paper and live.
def daily_pnl_pct(initial_capital: int) -> float:
    """Today's *realized* P&L as a fraction of initial_capital.

    Realized P&L is the sum of net P&L over FIFO-matched round-trips that exit
    today (reuses ``edge.roundtrips`` — the same pure cost-matching used by the
    edge scorecard). Open buy lots (still held) contribute zero, so a net-buy
    day is never mistaken for a loss.
    """
    if initial_capital <= 0:
        return 0.0
    # Imported lazily to avoid a circular import (edge.roundtrips -> db.session).
    from trading.edge import roundtrips

    today = date.today()
    result = roundtrips.build_roundtrips(roundtrips.load_fill_rows())
    realized = sum(
        rt.net_pnl for rt in result.roundtrips if rt.exit_date == today
    )
    return realized / initial_capital


def buy_count_today(ticker: str) -> int:
    """Number of BUY orders for `ticker` today (mode-agnostic).

    SPEC-TRADING-040 M4 (REQ-040-4a): backs the 단기과열 1-buy-per-day cap. Counts
    submitted/filled/partial BUY orders so a same-day repeat is detectable before
    it is submitted. Reuses the ``orders`` table (no new column — SPEC Q-6).
    """
    sql = """
        SELECT COUNT(*) AS n FROM orders
         WHERE ts::date = CURRENT_DATE
           AND ticker = %s
           AND side = 'buy'
           AND status IN ('submitted','filled','partial')
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (ticker,))
        row = cur.fetchone()
        return int(row["n"] or 0)


# SPEC-TRADING-040 M4: at most one BUY per KST day for a 단기과열 ticker.
_OVERHEAT_MAX_BUYS_PER_DAY = 1


# @MX:ANCHOR: SPEC-TRADING-040 — the pre-order hard-limit gate. fan_in: every
# orchestrator buy/sell path (pre_market / intraday / event) calls it before
# submitting. The SPEC-040 additions (sell-budget separation, 단기과열 repeat-buy
# block) are ADDITIVE: a SELL is never newly blocked, and a BUY only gains the
# preventive sell-budget reserve + overheat repeat/avg-down guards. The live
# count semantics (``daily_order_count_today``) are unchanged (REQ-040-3c).
# @MX:REASON: money/risk gate on the live order path — a regression here lets a
# bad order through or starves a risk-reducing exit.
# @MX:SPEC: SPEC-TRADING-040
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
    overheated: bool = False,
    held_pnl_pct: float | None = None,
) -> LimitCheck:
    """Run all hard-limit checks (수수료 포함 차감). Returns LimitCheck.

    SPEC-TRADING-040 additions (additive, buy-affecting only):
    - ``overheated``: the ticker is 단기과열(stat_cls=55). When True a BUY is
      capped at one per KST day (REQ-040-4a) and refused while the position is at
      an unrealised loss (no averaging down — REQ-040-4b).
    - ``held_pnl_pct``: the current holding's P&L% (for the avg-down guard).
    - daily_count is now SIDE-AWARE (REQ-040-3): buys are capped at
      ``RISK_DAILY_ORDER_COUNT_MAX - SELL_BUDGET_RESERVE`` so K slots survive for
      risk-reducing exits; a SELL is never blocked by the count.
    """
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

    # 2. daily order count — SPEC-040 M3: side-aware sell-budget separation.
    # A SELL is risk-reducing and NEVER blocked by (or counted against) the
    # daily order count (REQ-040-3b). A BUY is capped at MAX - K so K slots are
    # always reserved for pending exits (REQ-040-3a). The underlying count query
    # (``daily_order_count_today``) is unchanged — live semantics preserved.
    if side == "buy":
        cnt = daily_order_count_today()
        buy_limit = RISK_DAILY_ORDER_COUNT_MAX - SELL_BUDGET_RESERVE
        if cnt + 1 > buy_limit:
            chk.breaches.append(
                f"daily_count: 오늘 주문 {cnt} → 매수 한도 {buy_limit} "
                f"(전체 {RISK_DAILY_ORDER_COUNT_MAX}, 매도예산 {SELL_BUDGET_RESERVE} 확보)"
            )

    # 2b. SPEC-040 M4: 단기과열 repeat-buy block + no averaging down on a loss.
    # Buy-only — a SELL is never subject to these gates (exits always allowed).
    if side == "buy" and overheated:
        if buy_count_today(ticker) >= _OVERHEAT_MAX_BUYS_PER_DAY:
            chk.breaches.append(
                f"repeat_buy: {ticker} 단기과열 당일 매수 {_OVERHEAT_MAX_BUYS_PER_DAY}회 초과 차단"
            )
        if held_pnl_pct is not None and held_pnl_pct < 0:
            chk.breaches.append(
                f"avg_down: {ticker} 단기과열·손실({held_pnl_pct:+.2f}%) 물타기 매수 거부"
            )

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
