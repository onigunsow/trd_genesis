"""SPEC-TRADING-024 Stage 1 — watcher package.

Adaptive market-event watchers that fire `run_intraday_cycle` between the
hard-coded 5 intraday cron times. The package is **additive only**: existing
cron baseline (07:30 pre_market + 09:30/11:00/13:30/14:30 intraday) is
preserved (Q-8 user decision). Stage 2 (REQ-024-5~9) is out of scope.

@MX:SPEC: SPEC-TRADING-024
"""
