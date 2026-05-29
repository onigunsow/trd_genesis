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

import logging
import os
import sys

# @MX:NOTE: SPEC-TRADING-017 root-logger bootstrap helper. Reads
# TRADING_LOG_LEVEL (case-insensitive); falls back to INFO with a single
# WARNING line on unrecognised values. Idempotent via stdlib
# logging.basicConfig() semantics -- do NOT add force=True (it would
# silently displace caller-supplied handlers, e.g. pytest's caplog).
# @MX:SPEC: SPEC-TRADING-017
_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}


def _bootstrap_logging() -> None:
    """Install a stdout root-logger handler if one is not already present.

    Resolves the log level from ``TRADING_LOG_LEVEL`` (case-insensitive),
    defaulting to INFO. An unrecognised value also yields INFO and emits a
    single WARNING line so the typo is visible in the logs.
    """
    raw = os.environ.get("TRADING_LOG_LEVEL", "").strip()
    invalid_value: str | None = None
    if not raw:
        level_name = "INFO"
    else:
        upper = raw.upper()
        if upper in _VALID_LOG_LEVELS:
            level_name = upper
        else:
            level_name = "INFO"
            invalid_value = raw

    level = getattr(logging, level_name)

    # basicConfig is a no-op when the root logger already has a handler,
    # which is exactly the idempotency contract we want (REQ-017-1-5).
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    # Even when basicConfig was a no-op (handlers pre-existed), honour the
    # requested level on the root logger. This is acceptable per SPEC and
    # is required so `TRADING_LOG_LEVEL` takes effect under pytest, which
    # attaches its own LogCaptureHandler before our bootstrap runs.
    logging.getLogger().setLevel(level)

    # SPEC-TRADING-026 (security): httpx logs every request URL at INFO. For the
    # Telegram bot that URL embeds the bot token (…/bot<TOKEN>/getUpdates),
    # leaking the secret into container logs. Force httpx/httpcore to WARNING
    # regardless of the root level so the token is never logged.
    for _noisy in ("httpx", "httpcore"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

    if invalid_value is not None:
        logging.warning(
            "TRADING_LOG_LEVEL=%r is not one of %s; falling back to INFO",
            invalid_value,
            sorted(_VALID_LOG_LEVELS),
        )


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    # @MX:NOTE: SPEC-TRADING-017 -- bootstrap the root logger before any
    # subcommand dispatch so all long-running services (scheduler, bot)
    # emit their LOG.* output to stdout. MUST remain the first non-trivial
    # statement of main() after argv normalisation; do not move it into
    # individual subcommand branches.
    # @MX:SPEC: SPEC-TRADING-017
    _bootstrap_logging()
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
    if cmd == "fill-sync":
        return _cmd_fill_sync(rest)
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
    if cmd == "edge-report":
        return _cmd_edge_report(rest)
    if cmd == "edge-snapshot":
        return _cmd_edge_snapshot(rest)

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


def _cmd_fill_sync(rest: list[str]) -> int:
    """SPEC-TRADING-029 REQ-029-5: manual / backfill entry point for fill_sync.

    Flags
    -----
    --dry-run         Preview intended transitions without DB writes.
    --start YYYYMMDD  Accepted but ignored. SPEC-029 v0.2.0 reconciles against
                      the current KIS inquire-balance snapshot, which has no
                      historical date dimension, so --start has no effect; it
                      warns and continues. Kept for forward CLI compatibility.

    Exit codes: 0 on success, 1 on KisError / RuntimeError. Any unknown flag
    emits a WARNING but does not fail the command (forward-compat with the
    scheduler driver, which already runs unattended).
    """
    dry_run = False
    skip_next = False
    known_flags = {"--dry-run", "--start"}
    for i, arg in enumerate(rest):
        if skip_next:
            skip_next = False
            continue
        if arg == "--dry-run":
            dry_run = True
            continue
        if arg == "--start":
            value = rest[i + 1] if i + 1 < len(rest) else "<missing>"
            logging.warning(
                "trading fill-sync: --start %s ignored — v0.2.0 reconciles the "
                "current KIS balance snapshot (no historical date dimension)",
                value,
            )
            skip_next = True
            continue
        if arg not in known_flags:
            logging.warning(
                "trading fill-sync: ignoring unknown flag %s", arg
            )

    from trading.config import get_settings
    from trading.kis.client import KisClient, KisError
    from trading.kis.fills import fill_sync as _fill_sync

    try:
        client = KisClient(get_settings().trading_mode)
        result = _fill_sync(client, dry_run=dry_run)
    except KisError as e:
        print(f"trading fill-sync: KIS error: {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"trading fill-sync: runtime error: {e}", file=sys.stderr)
        return 1

    print(
        f"fill_sync: queried={result.get('queried', 0)} "
        f"transitioned={result.get('transitioned', 0)} "
        f"errors={result.get('errors', 0)} "
        f"dry_run={result.get('dry_run', dry_run)}"
    )
    return 0


def _cmd_edge_report(rest: list[str]) -> int:
    """Edge Validation: 페이퍼 성적 → go/no-go 판정 리포트.

    Flags
    -----
    --days N              최근 N일만 (미지정 시 전체 기간).
    --telegram            텔레그램으로도 전송.
    --include-unrealized  balance() 호출해 미실현 평가손익 병기 (KIS 접속 필요).

    기본은 KIS 호출 없이 기존 DB 데이터만 사용한다.
    """
    days: int | None = None
    telegram = False
    include_unrealized = False
    skip_next = False
    for i, arg in enumerate(rest):
        if skip_next:
            skip_next = False
            continue
        if arg == "--telegram":
            telegram = True
        elif arg == "--include-unrealized":
            include_unrealized = True
        elif arg == "--days":
            value = rest[i + 1] if i + 1 < len(rest) else None
            skip_next = True
            try:
                days = int(value) if value is not None else None
            except ValueError:
                print(f"trading edge-report: invalid --days {value!r}", file=sys.stderr)
                return 2
        else:
            logging.warning("trading edge-report: ignoring unknown flag %s", arg)

    from trading.edge.report import generate_and_send

    text = generate_and_send(
        days, telegram=telegram, include_unrealized=include_unrealized
    )
    print(text)
    return 0


def _cmd_edge_snapshot(rest: list[str]) -> int:
    """Edge Validation: 오늘 자산 스냅샷 1회 기록(수동/백필). 멱등 UPSERT."""
    from trading.edge.snapshot import record_snapshot

    try:
        row = record_snapshot()
    except Exception as e:  # noqa: BLE001
        print(f"trading edge-snapshot: error: {e}", file=sys.stderr)
        return 1
    print(
        f"equity_snapshot {row['trading_day']}: "
        f"total_assets={row['total_assets']:,} stock_eval={row['stock_eval']:,} "
        f"cash={row['cash']:,} unrealized={row['unrealized_pnl']:,}"
    )
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
        "  edge-report       paper 성적 → go/no-go 판정 [--days N] [--telegram] [--include-unrealized]\n"
        "  edge-snapshot     오늘 자산 스냅샷 1회 기록 (멱등 UPSERT)\n"
        "  fill-sync         sync KIS fill confirmations [--dry-run] [--start YYYYMMDD]\n"
        "  crawl-news        crawl news sources [--sector X] [--source X] [--force]\n"
        "  analyze-news      run intelligence analysis [--sector X] [--force]\n"
        "  news-health       show news source health status table\n",
        file=file,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
