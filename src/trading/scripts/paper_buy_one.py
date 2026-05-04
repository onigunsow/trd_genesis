"""Paper-mode 1-share buy verification (M2 acceptance).

Verifies REQ-KIS-02-1, REQ-KIS-02-3, REQ-KIS-02-4, REQ-MODE-02-5, REQ-BRIEF-04-8 (early).

Usage:
    trading paper-buy --ticker 005930 --qty 1
"""

from __future__ import annotations

import argparse
import sys

from trading.config import TradingMode, get_settings
from trading.kis.account import balance
from trading.kis.client import KisClient
from trading.kis.market import current_price
from trading.kis.order import buy as kis_buy
from trading.alerts.telegram import trade_briefing


def run(ticker: str, qty: int, order_type: str = "market") -> int:
    settings = get_settings()
    if settings.trading_mode != TradingMode.PAPER:
        print(f"FAIL: paper-buy requires TRADING_MODE=paper (current={settings.trading_mode.value})")
        return 1

    client = KisClient(TradingMode.PAPER)
    print(f"== paper-buy {ticker} x {qty} ({order_type}) ==")

    # 1. Quote (best-effort, for context)
    try:
        q = current_price(client, ticker)
        print(f"  current price: {q['price']:,}원 ({q['change_pct']:+.2f}%)")
        ref_price = q["price"]
    except Exception as e:  # noqa: BLE001
        print(f"  WARN quote failed: {e}")
        ref_price = 0

    # 2. Pre-balance
    bal_before = balance(client)
    print(f"  pre-balance  : 총자산 {bal_before['total_assets']:,}원, "
          f"매수가능 {bal_before['buyable']:,}원")

    # 3. Submit (briefing on both success and rejection — REQ-BRIEF-04-8 spirit)
    from trading.alerts.telegram import system_briefing
    try:
        result = kis_buy(client, ticker=ticker, qty=qty, order_type=order_type)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        print(f"  ORDER FAILED : {msg}")
        # Briefing the rejection so the time-series channel still has a record.
        try:
            system_briefing(
                "주문 거부",
                f"{ticker} {qty}주 매수 시도 실패\n사유: {msg}",
            )
            print("  telegram     : rejection briefing sent")
        except Exception as te:  # noqa: BLE001
            print(f"  telegram     : WARN brief failed: {te}")
        return 1
    print(f"  order submit : DB id={result['order_id']} kis={result['kis_order_no']!r} "
          f"status={result['status']} msg={result['msg']}")

    # 4. Post-balance (KIS may not reflect immediately for market orders)
    bal_after = balance(client)
    total = bal_after["total_assets"]
    cash = bal_after["cash_d2"]
    equity = bal_after["stock_eval"]
    cash_pct = (cash / total * 100) if total else 0.0
    equity_pct = (equity / total * 100) if total else 0.0
    print(f"  post-balance : 총자산 {total:,}원 (현금 {cash_pct:.1f}% / 주식 {equity_pct:.1f}%)")

    # 5. Telegram briefing
    name = None
    for h in bal_after["holdings"]:
        if h["ticker"] == ticker:
            name = h["name"]
            break
    try:
        trade_briefing(
            side="buy",
            ticker=ticker,
            name=name,
            qty=qty,
            fill_price=ref_price or None,
            fee=0,                          # KIS paper fee will appear on fill
            mode=client.mode.value,
            total_assets=total,
            cash_pct=cash_pct,
            equity_pct=equity_pct,
            note=f"M2 verification — DB order_id={result['order_id']}",
        )
        print("  telegram     : sent")
    except Exception as e:  # noqa: BLE001
        print(f"  telegram     : WARN send failed: {e}")

    print("OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="paper-mode 1-share buy verification (M2)")
    p.add_argument("--ticker", default="005930", help="6-digit ticker (default 005930 삼성전자)")
    p.add_argument("--qty", type=int, default=1)
    p.add_argument("--type", dest="order_type", choices=["market", "limit"], default="market")
    args = p.parse_args(argv)
    return run(args.ticker, args.qty, args.order_type)


if __name__ == "__main__":
    sys.exit(main())
