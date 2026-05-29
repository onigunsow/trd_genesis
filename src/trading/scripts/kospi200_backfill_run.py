"""SPEC-TRADING-037 REQ-037-1 — 10-year KOSPI200 OHLCV backfill CLI.

Populates the existing ``ohlcv`` cache with ~10 years of daily bars for the full
KOSPI200 constituent universe plus the KOSPI index (1001), reusing
``data.pykrx_adapter.fetch_incremental`` (idempotent, resumes from last cached
date). Long-running and one-shot — run operationally, not on a cycle.

Usage:
    trading kospi200-backfill [--years 10] [--start 2015-01-01]
                              [--max-retries 4] [--base-delay 2.0]
                              [--symbol-delay 0.3] [--symbols 005930,000660]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from trading.data.kospi200_backfill import (
    DEFAULT_BACKFILL_YEARS,
    backfill_all,
    kospi200_universe,
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="SPEC-037 10-year KOSPI200 OHLCV backfill (one-shot)",
    )
    p.add_argument("--years", type=int, default=DEFAULT_BACKFILL_YEARS)
    p.add_argument("--start", default=None, help="override start date (YYYY-MM-DD)")
    p.add_argument("--max-retries", type=int, default=4)
    p.add_argument("--base-delay", type=float, default=2.0)
    p.add_argument("--symbol-delay", type=float, default=0.3)
    p.add_argument("--symbols", help="comma-separated override (default: full KOSPI200)")
    args = p.parse_args(argv)

    if args.start:
        default_start = date.fromisoformat(args.start)
    else:
        default_start = date.today() - timedelta(days=365 * args.years)

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = kospi200_universe()

    print(f"backfilling {len(symbols)} symbols from {default_start} "
          f"(KOSPI index + full KOSPI200 constituents)")

    report = backfill_all(
        symbols, default_start,
        max_retries=args.max_retries,
        base_delay=args.base_delay,
        symbol_delay=args.symbol_delay,
    )

    print("\n== backfill complete ==")
    print(f"  loaded : {len(report.loaded)} symbols")
    print(f"  skipped: {len(report.skipped)} symbols")
    print(f"  rows   : {report.total_rows}")
    if report.skipped:
        print(f"  skipped list: {', '.join(report.skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
