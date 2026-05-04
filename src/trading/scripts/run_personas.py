"""Manual persona invocation (M4 verification)."""

from __future__ import annotations

import argparse
import sys

from trading.personas import orchestrator


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="manual persona cycle invocation (M4)")
    p.add_argument("--cycle", choices=["pre_market", "intraday", "weekly_macro"],
                   default="pre_market")
    args = p.parse_args(argv)

    if args.cycle == "weekly_macro":
        run_id = orchestrator.run_weekly_macro()
        print(f"== weekly_macro complete · persona_run_id={run_id} ==")
        return 0

    if args.cycle == "pre_market":
        result = orchestrator.run_pre_market_cycle()
    else:
        result = orchestrator.run_intraday_cycle()

    print(f"== {result.cycle_kind} cycle complete ==")
    print(f"  macro_run_id   : {result.macro_run_id}")
    print(f"  micro_run_id   : {result.micro_run_id}")
    print(f"  decision_run_id: {result.decision_run_id}")
    print(f"  decisions      : {result.decisions}")
    print(f"  risk_runs      : {result.risk_run_ids}")
    print(f"  executed       : {result.executed_orders}")
    print(f"  rejected       : {result.rejected}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
