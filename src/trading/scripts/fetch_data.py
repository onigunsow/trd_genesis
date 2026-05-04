"""Fetch and cache market data (M3).

Usage:
    trading fetch-data --symbol 005930 --source pykrx
    trading fetch-data --symbol ^GSPC --source yfinance
    trading fetch-data --fred DFF
    trading fetch-data --ecos
    trading fetch-data --dart --recent 7
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from trading.config import BACKFILL_START_DATE
from trading.data import dart_adapter, ecos_adapter, fred_adapter, pykrx_adapter, yfinance_adapter

DEFAULT_START = date.fromisoformat(BACKFILL_START_DATE)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="fetch and cache market data (M3)")
    p.add_argument("--source", choices=["pykrx", "yfinance"], default=None)
    p.add_argument("--symbol", help="ticker for pykrx/yfinance")
    p.add_argument("--start", default=DEFAULT_START.isoformat())
    p.add_argument("--end", default=date.today().isoformat())
    p.add_argument("--fred", help="FRED series_id (e.g. DFF)")
    p.add_argument("--ecos", action="store_true", help="fetch default ECOS series")
    p.add_argument("--dart", action="store_true", help="fetch recent DART disclosures")
    p.add_argument("--recent", type=int, default=7, help="DART recent days window")
    p.add_argument("--fundamentals", help="ticker for pykrx fundamentals")
    p.add_argument("--flows", help="ticker for pykrx foreign/institution/individual flows")
    args = p.parse_args(argv)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    if args.source == "pykrx":
        if not args.symbol:
            print("--symbol required for pykrx", file=sys.stderr)
            return 2
        n = pykrx_adapter.fetch_ohlcv(args.symbol, start, end)
        print(f"pykrx {args.symbol}: {n} rows cached")
        return 0

    if args.source == "yfinance":
        if not args.symbol:
            print("--symbol required for yfinance", file=sys.stderr)
            return 2
        n = yfinance_adapter.fetch_ohlcv(args.symbol, start, end)
        print(f"yfinance {args.symbol}: {n} rows cached")
        return 0

    if args.fred:
        n = fred_adapter.fetch_series(args.fred, start, end)
        print(f"FRED {args.fred}: {n} rows cached")
        return 0

    if args.ecos:
        total = 0
        for stat, cycle, item, label in ecos_adapter.DEFAULT_SERIES:
            try:
                n = ecos_adapter.fetch_series(stat, cycle, item, label, start, end)
                print(f"ECOS {label}: {n} rows")
                total += n
            except Exception as e:  # noqa: BLE001
                print(f"ECOS {label}: ERROR {e}")
        print(f"ECOS total: {total}")
        return 0

    if args.fundamentals:
        n = pykrx_adapter.fetch_fundamentals(args.fundamentals, start, end)
        print(f"pykrx fundamentals {args.fundamentals}: {n} rows cached")
        return 0

    if args.flows:
        n = pykrx_adapter.fetch_flows(args.flows, start, end)
        print(f"pykrx flows {args.flows}: {n} rows cached")
        return 0

    if args.dart:
        end_d = date.today()
        start_d = end_d - timedelta(days=args.recent)
        rows = dart_adapter.list_recent(start_d, end_d)
        print(f"DART {start_d}~{end_d}: {len(rows)} disclosures cached")
        return 0

    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
