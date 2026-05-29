"""SPEC-TRADING-037 REQ-037-2 — exit-rule parameter sweep CLI.

Loads cached KOSPI200 OHLCV (run ``trading kospi200-backfill`` first), derives a
per-symbol ATR%, runs the deterministic exit-rule parameter sweep, prints a
ranked robustness report, and persists the result to ``benchmark_runs``
(strategy label ``exit_sweep`` — no new migration needed, SPEC Q-5).

================================ SCOPE LIMIT ================================
The sweep validates ONLY deterministic exit rules. The mechanical "buy every
Nth day" entry model is a look-ahead-free control variable, NOT the LLM entry
edge — its output is "a robust exit-parameter set", not "evidence the strategy
makes money" (SPEC C-1). Entry-edge profitability is confirmed only by forward
paper trading (``edge-report``).
============================================================================

Usage:
    trading exit-backtest [--source pykrx] [--start 2015-01-01] [--end ...]
                          [--every-n 5] [--symbols 005930,000660]
                          [--stop-atr 1.0,1.5,2.0] [--floor -5,-7,-10]
                          [--take-atr 1.5,2.0,3.0] [--no-persist]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta

from trading.backtest.exit_sweep import recommend, run_sweep
from trading.data.cache import cached_ohlcv

LOG = logging.getLogger(__name__)

DEFAULT_STOP_ATR = [1.0, 1.5, 2.0]
DEFAULT_FLOOR = [-5.0, -7.0, -10.0]
DEFAULT_TAKE_ATR = [1.5, 2.0, 3.0]


def _parse_floats(raw: str) -> list[float]:
    return [float(x.strip()) for x in raw.split(",") if x.strip()]


def _atr_pct_from_bars(bars: list[dict]) -> float | None:
    """Approximate ATR% over the loaded history (mean true-range / mean close).

    A history-wide ATR% is appropriate here because the sweep tests how exit
    rules behave across the whole period, not as of a single trading day.
    """
    if len(bars) < 2:
        return None
    trs: list[float] = []
    for i in range(1, len(bars)):
        high = float(bars[i]["high"])
        low = float(bars[i]["low"])
        prev_close = float(bars[i - 1]["close"])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    if not trs:
        return None
    mean_tr = sum(trs) / len(trs)
    mean_close = sum(float(b["close"]) for b in bars) / len(bars)
    if mean_close <= 0:
        return None
    return (mean_tr / mean_close) * 100.0


def _load_universe_prices(
    source: str, symbols: list[str], start: date, end: date,
) -> tuple[dict[str, list[dict]], dict[str, float]]:
    """Load cached OHLCV + per-symbol ATR%. Symbols with no data are skipped."""
    price_data: dict[str, list[dict]] = {}
    atr_by_symbol: dict[str, float] = {}
    for sym in symbols:
        bars = cached_ohlcv(source, sym, start, end)
        if not bars:
            LOG.warning("no cached data for %s — run kospi200-backfill first", sym)
            continue
        atr_pct = _atr_pct_from_bars(bars)
        if atr_pct is None:
            continue
        price_data[sym] = bars
        atr_by_symbol[sym] = atr_pct
    return price_data, atr_by_symbol


def _resolve_symbols(arg_symbols: str | None) -> list[str]:
    if arg_symbols:
        return [s.strip() for s in arg_symbols.split(",") if s.strip()]
    # Default: the full backfilled KOSPI200 universe + index.
    from trading.data.kospi200_backfill import kospi200_universe

    return kospi200_universe()


def _persist(recommendation, universe: str, start: date, end: date) -> int:
    """Persist the recommended set + full sweep into benchmark_runs (Q-5)."""
    from trading.db.session import connection

    rec = recommendation
    summary = {
        "scope_limit": (
            "deterministic exit rules only; LLM entry edge NOT validated "
            "(look-ahead) — confirm via forward paper edge-report"
        ),
        "recommended": {
            "stop_atr_mult": rec.params.stop_atr_mult,
            "stop_floor_pct": rec.params.stop_floor_pct,
            "take_atr_mult": rec.params.take_atr_mult,
        },
        "rationale": rec.rationale,
        "all_results": [
            {
                "stop_atr_mult": m.params.stop_atr_mult,
                "stop_floor_pct": m.params.stop_floor_pct,
                "take_atr_mult": m.params.take_atr_mult,
                "win_rate": m.win_rate,
                "expectancy": m.expectancy,
                "avg_return_pct": m.avg_return_pct,
                "mdd": m.mdd,
                "avg_hold_days": m.avg_hold_days,
                "trades": m.trades,
            }
            for m in rec.ranked
        ],
    }
    sql = """
        INSERT INTO benchmark_runs
            (strategy, universe, start_date, end_date, params,
             cagr, mdd, sharpe, trades, final_equity, summary)
        VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s::jsonb)
        RETURNING id
    """
    params_json = json.dumps({
        "stop_atr_mult": rec.params.stop_atr_mult,
        "stop_floor_pct": rec.params.stop_floor_pct,
        "take_atr_mult": rec.params.take_atr_mult,
        "win_rate": rec.metrics.win_rate,
        "expectancy": rec.metrics.expectancy,
    })
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (
            "exit_sweep", universe, start, end, params_json,
            None, float(rec.metrics.mdd), None,
            int(rec.metrics.trades), None,
            json.dumps(summary),
        ))
        row = cur.fetchone()
        return row["id"] if isinstance(row, dict) else row[0]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="SPEC-037 deterministic exit-rule parameter sweep "
                    "(exit rules ONLY — entry edge not validated)",
    )
    p.add_argument("--source", default="pykrx")
    p.add_argument("--symbols", help="comma-separated tickers (default: KOSPI200 universe)")
    p.add_argument("--start", default=(date.today() - timedelta(days=365 * 10)).isoformat())
    p.add_argument("--end", default=date.today().isoformat())
    p.add_argument("--every-n", type=int, default=5, help="mechanical entry cadence")
    p.add_argument("--stop-atr", default=None, help="grid e.g. 1.0,1.5,2.0")
    p.add_argument("--floor", default=None, help="grid e.g. -5,-7,-10")
    p.add_argument("--take-atr", default=None, help="grid e.g. 1.5,2.0,3.0")
    p.add_argument("--no-persist", action="store_true")
    args = p.parse_args(argv)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    stop_atr = _parse_floats(args.stop_atr) if args.stop_atr else DEFAULT_STOP_ATR
    floor = _parse_floats(args.floor) if args.floor else DEFAULT_FLOOR
    take_atr = _parse_floats(args.take_atr) if args.take_atr else DEFAULT_TAKE_ATR

    symbols = _resolve_symbols(args.symbols)
    price_data, atr_by_symbol = _load_universe_prices(args.source, symbols, start, end)
    if not price_data:
        print("no price data loaded — run `trading kospi200-backfill` first",
              file=sys.stderr)
        return 1

    print(f"loaded {len(price_data)} symbols ({start} ~ {end}), "
          f"grid {len(stop_atr)}x{len(floor)}x{len(take_atr)} = "
          f"{len(stop_atr) * len(floor) * len(take_atr)} combos")
    print("SCOPE: deterministic EXIT rules only — entry edge NOT validated "
          "(look-ahead); confirm via forward paper edge-report")

    results = run_sweep(
        price_data, atr_by_symbol,
        stop_atr_mults=stop_atr, stop_floor_pcts=floor, take_atr_mults=take_atr,
        every_n=args.every_n,
    )
    rec = recommend(results)

    print("\n== exit-rule sweep (ranked by robustness) ==")
    print(f"{'stopX':>6} {'floor%':>7} {'takeX':>6} "
          f"{'win%':>6} {'exp%':>7} {'mdd%':>7} {'hold':>5} {'n':>5}")
    for m in rec.ranked:
        print(f"{m.params.stop_atr_mult:>6.2f} {m.params.stop_floor_pct:>7.1f} "
              f"{m.params.take_atr_mult:>6.2f} {m.win_rate * 100:>6.1f} "
              f"{m.expectancy:>7.2f} {m.mdd * 100:>7.1f} "
              f"{m.avg_hold_days:>5.1f} {m.trades:>5}")

    print(f"\n== RECOMMENDED ==\n{rec.rationale}")

    if not args.no_persist:
        universe = args.symbols or "KOSPI200"
        run_id = _persist(rec, universe, start, end)
        print(f"\npersisted: benchmark_runs id={run_id} (strategy=exit_sweep)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
