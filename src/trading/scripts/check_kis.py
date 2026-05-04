"""KIS connectivity end-to-end check.

Steps:
1. Acquire token (or use cached)
2. Query balance
3. Query a quote (Samsung 005930)
4. Print summary
"""

from __future__ import annotations

import argparse
import sys

from trading.config import TradingMode, get_settings
from trading.kis.account import balance
from trading.kis.client import KisClient
from trading.kis.market import current_price


def run(mode: TradingMode | None = None) -> int:
    settings = get_settings()
    use_mode = mode if mode is not None else settings.trading_mode
    print(f"== check-kis: mode={use_mode.value} ==")

    client = KisClient(use_mode)
    print(f"  base url   : {client.base}")
    print(f"  account    : {client.account_prefix}-{client.account_suffix}")

    try:
        bal = balance(client)
        print(f"  balance    : 총자산 {bal['total_assets']:,}원, 매수가능 {bal['buyable']:,}원, "
              f"보유 {len(bal['holdings'])}종목")
    except Exception as e:  # noqa: BLE001
        print(f"  balance    : ERROR {e}")
        return 1

    try:
        q = current_price(client, "005930")
        print(f"  005930     : {q['price']:,}원 ({q['change_pct']:+.2f}%)  거래량 {q['volume']:,}")
    except Exception as e:  # noqa: BLE001
        print(f"  005930     : ERROR {e}")
        return 1

    print("OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="check KIS connectivity (M2)")
    p.add_argument("--mode", choices=["paper", "live"], default=None,
                   help="override TRADING_MODE; default = .env TRADING_MODE")
    args = p.parse_args(argv)
    mode = TradingMode(args.mode) if args.mode else None
    return run(mode)


if __name__ == "__main__":
    sys.exit(main())
