"""Run a benchmark backtest and persist result to benchmark_runs.

Usage:
    trading backtest --strategy sma_cross --symbol 005930 --start 2019-01-01
    trading backtest --strategy dual_momentum --symbols 005930,069500 --start 2019-01-01
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

import pandas as pd

from trading.config import BACKFILL_START_DATE
from trading.data.cache import cached_ohlcv
from trading.db.session import connection
from trading.strategies.dual_momentum import DualMomentum
from trading.strategies.sma_cross import SmaCross
from trading.backtest.engine import run as bt_run


def load_prices(source: str, symbols: list[str], start: date, end: date) -> pd.DataFrame:
    """Build a (date x asset) close price DataFrame from cached OHLCV."""
    frames = {}
    for sym in symbols:
        rows = cached_ohlcv(source, sym, start, end)
        if not rows:
            print(f"  WARN no cached data for {sym} — run fetch-data first", file=sys.stderr)
            continue
        df = pd.DataFrame(rows)
        df["ts"] = pd.to_datetime(df["ts"])
        df = df.set_index("ts").sort_index()
        frames[sym] = df["close"].astype(float)
    if not frames:
        raise RuntimeError("no price data loaded")
    return pd.DataFrame(frames).sort_index()


def persist(strategy: str, universe: str, start: date, end: date,
            params: dict, result, prices: pd.DataFrame) -> int:
    summary = {
        "first_close": float(prices.iloc[0].mean()),
        "last_close": float(prices.iloc[-1].mean()),
        "trading_days": int(len(prices)),
        "assets": list(prices.columns),
    }
    sql = """
        INSERT INTO benchmark_runs
            (strategy, universe, start_date, end_date, params,
             cagr, mdd, sharpe, trades, final_equity, summary)
        VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s::jsonb)
        RETURNING id
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (
            strategy, universe, start, end,
            json.dumps(params),
            float(result.cagr), float(result.mdd), float(result.sharpe),
            int(result.trades), float(result.final_equity),
            json.dumps(summary),
        ))
        row = cur.fetchone()
        return row["id"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="run a rule-based benchmark backtest (M3)")
    p.add_argument("--strategy", choices=["sma_cross", "dual_momentum"], required=True)
    p.add_argument("--source", default="pykrx")
    p.add_argument("--symbol", help="single ticker for sma_cross")
    p.add_argument("--symbols", help="comma-separated tickers for dual_momentum")
    p.add_argument("--start", default=BACKFILL_START_DATE)
    p.add_argument("--end", default=date.today().isoformat())
    p.add_argument("--initial-capital", type=float, default=10_000_000)
    p.add_argument("--fast", type=int, default=20, help="sma_cross fast window")
    p.add_argument("--slow", type=int, default=60, help="sma_cross slow window")
    p.add_argument("--lookback", type=int, default=12, help="dual_momentum lookback months")
    args = p.parse_args(argv)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    if args.strategy == "sma_cross":
        if not args.symbol:
            print("--symbol required for sma_cross", file=sys.stderr)
            return 2
        symbols = [args.symbol]
        strat = SmaCross(fast=args.fast, slow=args.slow)
        universe = args.symbol
    else:
        if not args.symbols:
            print("--symbols required for dual_momentum", file=sys.stderr)
            return 2
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
        strat = DualMomentum(lookback_months=args.lookback)
        universe = ",".join(symbols)

    prices = load_prices(args.source, symbols, start, end)
    print(f"loaded {prices.shape[0]} days x {prices.shape[1]} assets "
          f"({prices.index.min().date()} ~ {prices.index.max().date()})")

    sr = strat.compute(prices)
    result = bt_run(prices, sr.weights, initial_capital=args.initial_capital)

    print("== backtest result ==")
    print(f"  strategy   : {sr.name} {sr.params}")
    print(f"  CAGR       : {result.cagr * 100:.2f}%")
    print(f"  MDD        : {result.mdd * 100:.2f}%")
    print(f"  Sharpe     : {result.sharpe:.2f}")
    print(f"  trades     : {result.trades}")
    print(f"  final eq.  : {result.final_equity:,.0f}원 (start {args.initial_capital:,.0f})")

    run_id = persist(sr.name, universe, start, end, sr.params, result, prices)
    print(f"  persisted  : benchmark_runs id={run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
