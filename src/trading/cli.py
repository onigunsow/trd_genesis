"""trading CLI — single entry point for all subcommands.

Usage inside container:
    docker compose exec app trading <subcommand> [args]

Subcommands (current):
    healthcheck        — env / KIS / Telegram / DB
    migrate            — apply pending DB migrations
    check-kis          — verify KIS connectivity (token, balance, quote)
    paper-buy          — paper 1-share buy verification (M2)

Subcommands (future):
    fetch-data         — pykrx/yfinance/FRED/ECOS/DART (M3)
    backtest           — run benchmark strategy (M3)
    run-personas       — manual persona invocation (M4)
    run-strategy       — run strategy (M4)
    daily-report       — force daily report generation (M5)
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args:
        return _print_help()
    cmd, rest = args[0], args[1:]

    if cmd in ("-h", "--help", "help"):
        return _print_help()
    if cmd == "healthcheck":
        from trading.healthcheck import main as run
        return run(rest)
    if cmd == "migrate":
        from trading.db.migrate import run
        run()
        return 0
    if cmd == "check-kis":
        from trading.scripts.check_kis import main as run
        return run(rest)
    if cmd == "paper-buy":
        from trading.scripts.paper_buy_one import main as run
        return run(rest)
    if cmd == "fetch-data":
        from trading.scripts.fetch_data import main as run
        return run(rest)
    if cmd == "backtest":
        from trading.scripts.backtest_run import main as run
        return run(rest)
    if cmd == "run-personas":
        from trading.scripts.run_personas import main as run
        return run(rest)
    if cmd == "build-context":
        # build-context macro|micro|news-macro|news-micro|all
        if not rest:
            print("usage: trading build-context [macro|micro|news-micro|news-macro|all]")
            return 2
        target = rest[0]
        if target == "macro" or target == "all":
            from trading.contexts.build_macro_context import main as run
            run()
        if target == "micro" or target == "all":
            from trading.contexts.build_micro_context import main as run
            run()
        if target == "news-micro" or target == "all":
            from trading.contexts.build_micro_news import main as run
            run()
        if target == "news-macro" or target == "all":
            from trading.contexts.build_macro_news import main as run
            run()
        return 0
    if cmd == "analyze-news":
        return _cmd_analyze_news(rest)
    if cmd == "crawl-news":
        return _cmd_crawl_news(rest)
    if cmd == "news-health":
        return _cmd_news_health(rest)
    if cmd == "calendar":
        from datetime import date, timedelta
        from trading.scheduler.calendar import is_trading_day, reason_if_closed
        target = date.fromisoformat(rest[0]) if rest else date.today()
        for i in range(14):
            d = target + timedelta(days=i)
            mark = "✓ 영업일" if is_trading_day(d) else f"× 휴장 ({reason_if_closed(d)})"
            print(f"  {d} ({['월','화','수','목','금','토','일'][d.weekday()]})  {mark}")
        return 0
    if cmd == "halt":
        from trading.risk import circuit_breaker
        circuit_breaker.trip("manual cli /halt", details={"actor": "cli"})
        print("halt_state=true")
        return 0
    if cmd == "resume":
        from trading.risk import circuit_breaker
        circuit_breaker.reset(actor="cli")
        print("halt_state=false")
        return 0
    if cmd == "status":
        from trading.db.session import get_system_state
        state = get_system_state()
        print(state)
        return 0
    if cmd == "bot":
        from trading.bot.telegram_bot import run
        run()
        return 0
    if cmd == "scheduler":
        from trading.scheduler.runner import main as run
        run()
        return 0
    if cmd == "daily-report":
        from trading.reports.daily_report import generate_and_send
        text = generate_and_send()
        print(text)
        return 0

    print(f"unknown subcommand: {cmd}", file=sys.stderr)
    _print_help(file=sys.stderr)
    return 2


def _cmd_analyze_news(rest: list[str]) -> int:
    """SPEC-014: Run news intelligence analysis pipeline."""
    from trading.news.intelligence.scheduler import cli_analyze_news

    force = "--force" in rest
    sector = None
    for i, arg in enumerate(rest):
        if arg == "--sector" and i + 1 < len(rest):
            sector = rest[i + 1]

    return cli_analyze_news(force=force, sector=sector)


def _cmd_crawl_news(rest: list[str]) -> int:
    """SPEC-013: Run news crawl cycle."""
    from trading.news.crawler import crawl_all, crawl_sector, crawl_source

    force = "--force" in rest
    sector = None
    source = None

    for i, arg in enumerate(rest):
        if arg == "--sector" and i + 1 < len(rest):
            sector = rest[i + 1]
        elif arg == "--source" and i + 1 < len(rest):
            source = rest[i + 1]

    if source:
        result = crawl_source(source, force=force)
    elif sector:
        result = crawl_sector(sector, force=force)
    else:
        result = crawl_all(force=force)

    print(f"Crawl complete: {result}")
    return 0


def _cmd_news_health(rest: list[str]) -> int:
    """SPEC-013: Display news source health status."""
    from trading.news.health import get_all_health_status

    statuses = get_all_health_status()
    if not statuses:
        print("No health data yet. Run 'trading crawl-news' first.")
        return 0

    # Format as table
    print(f"{'Source':<30} {'Sector':<20} {'Status':<10} {'Rate':<8} {'Fails':<6} {'Last OK':<20} {'Last Fail':<20}")
    print("-" * 120)
    for s in statuses:
        status = "ACTIVE" if s["enabled"] else "DISABLED"
        last_ok = s["last_success"].strftime("%Y-%m-%d %H:%M") if s["last_success"] else "—"
        last_fail = s["last_failure"].strftime("%Y-%m-%d %H:%M") if s["last_failure"] else "—"
        print(
            f"{s['source_name']:<30} "
            f"{'—':<20} "
            f"{status:<10} "
            f"{s['success_rate_pct']:>5.1f}% "
            f"{s['consecutive_failures']:<6} "
            f"{last_ok:<20} "
            f"{last_fail:<20}"
        )
    return 0


def _print_help(file=sys.stdout) -> int:
    print(
        "trading <subcommand> [args]\n"
        "\n"
        "subcommands:\n"
        "  healthcheck       env / KIS / Telegram / DB\n"
        "  migrate           apply pending DB migrations\n"
        "  check-kis         verify KIS connectivity\n"
        "  paper-buy         paper 1-share buy verification (M2)\n"
        "  fetch-data        cache OHLCV / macro / disclosures (M3)\n"
        "  backtest          run rule-based benchmark backtest (M3)\n"
        "  run-personas      invoke a persona cycle (M4)\n"
        "  halt              set halt_state=true (M5)\n"
        "  resume            set halt_state=false (M5)\n"
        "  status            print system_state singleton (M5)\n"
        "  bot               run Telegram command listener (M5)\n"
        "  scheduler         start APScheduler cron loop (M5)\n"
        "  daily-report      generate and send today's report (M5)\n"
        "  crawl-news        crawl news sources [--sector X] [--source X] [--force]\n"
        "  analyze-news      run intelligence analysis [--sector X] [--force]\n"
        "  news-health       show news source health status table\n",
        file=file,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
