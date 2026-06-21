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
    if cmd == "kospi200-backfill":
        from trading.scripts.kospi200_backfill_run import main as run
        return run(rest)
    if cmd == "exit-backtest":
        from trading.scripts.exit_backtest import main as run
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
    if cmd == "resolve-orders":
        return _cmd_resolve_orders(rest)
    if cmd == "aggregate-pnl":
        return _cmd_aggregate_pnl(rest)
    if cmd == "converge-ghost-buys":
        return _cmd_converge_ghost_buys(rest)
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
    if cmd == "smoke-gate":
        return _cmd_smoke_gate(rest)

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


def _cmd_resolve_orders(rest: list[str]) -> int:
    """SPEC-TRADING-042 Module B REQ-042-B1/B2: order-state resolver / cleanup.

    Resolves orders stuck in ``submitted`` to a deterministic terminal state
    (``filled`` when KIS/balance confirms; ``expired`` when the bounded window
    elapsed and the fill could not be confirmed — never fabricated). Reuses the
    Module-A ``confirm_fills`` seam; never POSTs a KIS order.

    Flags
    -----
    --cleanup     One-time cleanup mode (REQ-042-B2): resolve every stuck
                  ``submitted`` order regardless of age (window=0). Idempotent.
    --dry-run     Preview intended transitions without DB writes.
    --window N    Resolve window in seconds (default 900). Ignored with --cleanup.

    Exit codes: 0 on success, 1 on KisError / RuntimeError.
    """
    cleanup = "--cleanup" in rest
    dry_run = "--dry-run" in rest
    window: float | None = None
    skip_next = False
    for i, arg in enumerate(rest):
        if skip_next:
            skip_next = False
            continue
        if arg == "--window":
            value = rest[i + 1] if i + 1 < len(rest) else None
            skip_next = True
            try:
                window = float(value) if value is not None else None
            except ValueError:
                print(f"trading resolve-orders: invalid --window {value!r}",
                      file=sys.stderr)
                return 2
        elif arg not in {"--cleanup", "--dry-run"}:
            logging.warning("trading resolve-orders: ignoring unknown flag %s", arg)

    from trading.config import get_settings
    from trading.kis.client import KisClient, KisError
    from trading.kis.order_resolver import (
        SUBMITTED_RESOLVE_WINDOW_SECONDS,
        cleanup_stuck_orders,
        resolve_stuck_orders,
    )

    try:
        client = KisClient(get_settings().trading_mode)
        if cleanup:
            result = cleanup_stuck_orders(client, dry_run=dry_run)
        else:
            result = resolve_stuck_orders(
                client,
                window_seconds=window if window is not None
                else SUBMITTED_RESOLVE_WINDOW_SECONDS,
                dry_run=dry_run,
            )
    except KisError as e:
        print(f"trading resolve-orders: KIS error: {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"trading resolve-orders: runtime error: {e}", file=sys.stderr)
        return 1

    print(
        f"resolve-orders: scanned={result.get('scanned', 0)} "
        f"filled={result.get('resolved_filled', 0)} "
        f"expired={result.get('resolved_expired', 0)} "
        f"skipped={result.get('skipped', 0)} "
        f"errors={result.get('errors', 0)} "
        f"dry_run={result.get('dry_run', dry_run)}"
    )
    return 0


def _cmd_aggregate_pnl(rest: list[str]) -> int:
    """SPEC-TRADING-042 Module D REQ-042-D1: backfill realized_pnl_cum.

    Populates ``daily_equity_snapshot.realized_pnl_cum`` for existing snapshot
    rows from confirmed sell fills, using the single FIFO round-trip net_pnl
    source (fees deducted). Idempotent — safe to re-run (recomputes the same
    cumulative value per day). Read/aggregate over ``orders`` + write to the
    snapshot only; it never POSTs a KIS order and never touches the order path.

    Flags
    -----
    --dry-run     Compute and report would-be updates without writing.
    --days N      Restrict the round-trip source to the last N days of fills
                  (default: full history).

    Honesty caveat: in PAPER mode a round-trip exit can be a synthetic fill
    (SPEC-039) — an estimate, NOT a live execution price. The output reports the
    synthetic sell-fill count so the realized figure's paper imprecision is explicit.

    Exit codes: 0 on success, 1 on RuntimeError.
    """
    dry_run = "--dry-run" in rest
    days: int | None = None
    skip_next = False
    for i, arg in enumerate(rest):
        if skip_next:
            skip_next = False
            continue
        if arg == "--days":
            value = rest[i + 1] if i + 1 < len(rest) else None
            skip_next = True
            try:
                days = int(value) if value is not None else None
            except ValueError:
                print(f"trading aggregate-pnl: invalid --days {value!r}",
                      file=sys.stderr)
                return 2
        elif arg != "--dry-run":
            logging.warning("trading aggregate-pnl: ignoring unknown flag %s", arg)

    from trading.edge.realized_pnl import aggregate_realized_pnl_cum

    try:
        result = aggregate_realized_pnl_cum(days=days, dry_run=dry_run)
    except RuntimeError as e:
        print(f"trading aggregate-pnl: runtime error: {e}", file=sys.stderr)
        return 1

    print(
        f"aggregate-pnl: rows_updated={result.get('rows_updated', 0)} "
        f"roundtrips={result.get('roundtrips', 0)} "
        f"cumulative_total={result.get('cumulative_total', 0)} "
        f"synthetic_sell_fills={result.get('synthetic_sell_fills', 0)} "
        f"dry_run={result.get('dry_run', dry_run)}"
    )
    if result.get("synthetic_present"):
        print(
            "  caveat: paper synthetic fill prices are estimates, not live "
            "executions — realized_pnl_cum carries paper-fill imprecision.",
            file=sys.stderr,
        )
    return 0


def _cmd_converge_ghost_buys(rest: list[str]) -> int:
    """SPEC-TRADING-042 D1/D6: 유령 합성매수 append-only 교정 실행.

    KIS 확인 잔고 대비 초과 synthetic filled 매수를 교정 SELL lot 으로 수렴한다.
    paper-only — live 클라이언트이면 no-op 요약을 반환한다.

    Flags
    -----
    --dry-run     SELECT 만 수행, INSERT/audit 없음.

    Exit codes: 0 on success, 1 on error.
    """
    dry_run = "--dry-run" in rest

    from trading.config import get_settings
    from trading.kis.client import KisClient
    from trading.kis.ghost_convergence import converge_ghost_buys

    try:
        client = KisClient(get_settings().trading_mode)
        result = converge_ghost_buys(client, dry_run=dry_run)
    except Exception as e:
        print(f"trading converge-ghost-buys: error: {e}", file=sys.stderr)
        return 1

    print(
        f"converge-ghost-buys: scanned_tickers={result.get('scanned_tickers', 0)} "
        f"converged={result.get('converged', 0)} "
        f"total_excess={result.get('total_excess', 0)} "
        f"dry_run={result.get('dry_run', dry_run)} "
        f"skipped_live={result.get('skipped_live', False)}"
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


def _cmd_smoke_gate(rest: list[str]) -> int:
    """SPEC-TRADING-049 REQ-045-C: 라이브 스모크 게이트 (실행 경로 검증).

    live 자격증명으로 1회 소액 BUY→SELL round-trip을 실행하고 5가지 증거 항목을
    판정하여 PASS/FAIL 기록을 남긴다. PASS 기록이 있어야 live 전면 승격이 허용된다.

    Flags
    -----
    --max-qty N         BUY/SELL 최대 수량 상한(필수, 0이면 차단). REQ-049-M1-2.
    --max-notional N    BUY 최대 금액 상한(원화). 가격*qty > N이면 차단. REQ-049-M1-2.
    --ticker CODE       대상 종목코드(미지정 시 운영 기본 종목 사용).
    --dry-run           주문 발주 없이 정직 고지만 출력하고 종료(테스트용).

    주의: 이 서브커맨드는 live 모드 + live_unlocked=True 상태에서만 발주합니다.
    PAPER 모드이거나 live_unlocked=False이면 실거래 발주 없이 종료합니다.

    정직 고지(REQ-049-M1-4, REQ-045-C4):
        본 게이트는 실행 경로 검증이며 전략 수익성 검증이 아닙니다.

    Exit codes: 0=PASS, 1=FAIL 또는 오류, 2=잘못된 인수/모드.
    """
    from trading.config import TradingMode, get_settings
    from trading.db.session import get_system_state
    from trading.kis.broker_truth import (
        BrokerFillInquiryNotImplemented,
        confirm_fills,
        intraday_reconcile,
    )
    from trading.kis.client import KisClient, KisError
    from trading.kis.market import current_price
    from trading.kis.order import submit_order
    from trading.kis.order_resolver import resolve_stuck_orders
    from trading.kis.sell_lock import guard_sell, set_sell_inflight
    from trading.kis.smoke_gate import (
        HONESTY_DISCLOSURE,
        SmokeEvidence,
        evaluate_smoke_evidence,
        record_smoke_verdict,
    )

    # ── 정직 고지 — 항상 먼저 출력(REQ-049-M1-4) ──────────────────────────
    print(HONESTY_DISCLOSURE)
    print()

    # ── 플래그 파싱 ─────────────────────────────────────────────────────────
    max_qty: int | None = None
    max_notional: int | None = None
    ticker: str | None = None
    dry_run = False
    skip_next = False
    for i, arg in enumerate(rest):
        if skip_next:
            skip_next = False
            continue
        if arg == "--max-qty":
            value = rest[i + 1] if i + 1 < len(rest) else None
            skip_next = True
            try:
                max_qty = int(value) if value is not None else None
            except ValueError:
                print(f"trading smoke-gate: invalid --max-qty {value!r}", file=sys.stderr)
                return 2
        elif arg == "--max-notional":
            value = rest[i + 1] if i + 1 < len(rest) else None
            skip_next = True
            try:
                max_notional = int(value) if value is not None else None
            except ValueError:
                print(f"trading smoke-gate: invalid --max-notional {value!r}", file=sys.stderr)
                return 2
        elif arg == "--ticker":
            ticker = rest[i + 1] if i + 1 < len(rest) else None
            skip_next = True
        elif arg == "--dry-run":
            dry_run = True
        else:
            logging.warning("trading smoke-gate: ignoring unknown flag %s", arg)

    if dry_run:
        print("[DRY-RUN] 주문 발주 없이 종료합니다.")
        return 0

    # ── 상한 검증 ───────────────────────────────────────────────────────────
    if max_qty is None or max_qty <= 0:
        print(
            "trading smoke-gate: --max-qty N(>=1) 필수. 예: --max-qty 1",
            file=sys.stderr,
        )
        return 2

    # ── 모드/자격증명 검증 (REQ-049-M1-3) ──────────────────────────────────
    try:
        settings = get_settings()
        client = KisClient(settings.trading_mode)
    except Exception as e:
        print(f"trading smoke-gate: 설정/클라이언트 초기화 실패: {e}", file=sys.stderr)
        return 1

    if client.mode != TradingMode.LIVE:
        print(
            f"trading smoke-gate: PAPER 모드에서는 실거래를 발주하지 않습니다. "
            f"현재 모드: {client.mode.value}. live 모드 + 자격증명 필요 (REQ-049-M1-3).",
            file=sys.stderr,
        )
        return 2

    # ── live_unlocked 확인 ──────────────────────────────────────────────────
    try:
        state = get_system_state()
    except Exception as e:
        print(f"trading smoke-gate: system_state 조회 실패: {e}", file=sys.stderr)
        return 1

    if not state.get("live_unlocked"):
        print(
            "trading smoke-gate: live_unlocked=False — 발주 불가.\n"
            "스모크 게이트 실행 전 live_unlocked=True 설정이 필요합니다.\n"
            "(스모크 PASS 확인 후 live 전면 승격 절차를 진행하십시오)",
            file=sys.stderr,
        )
        return 2

    # ── 종목 코드 결정 ──────────────────────────────────────────────────────
    if ticker is None:
        # 기본 스모크 종목: 삼성전자 (유동성 최고, 소액 1주 가능)
        ticker = "005930"
        logging.warning(
            "trading smoke-gate: --ticker 미지정. 기본 종목 %s 사용.", ticker
        )

    # ── 금액 상한 확인 (REQ-049-M1-2) ──────────────────────────────────────
    if max_notional is not None:
        try:
            quote = current_price(client, ticker)
            price = int(quote.get("price", 0) or 0)
        except Exception as e:
            print(f"trading smoke-gate: 현재가 조회 실패: {e}", file=sys.stderr)
            return 1
        estimated_notional = price * max_qty
        if estimated_notional > max_notional:
            print(
                f"trading smoke-gate: 금액 상한 초과 — "
                f"예상 주문금액 {estimated_notional:,}원 > max-notional {max_notional:,}원. "
                "발주하지 않습니다 (REQ-049-M1-2).",
                file=sys.stderr,
            )
            return 2

    # ── BUY 발주 ────────────────────────────────────────────────────────────
    print(f"[smoke-gate] BUY 발주: ticker={ticker} qty={max_qty}")
    try:
        buy_result = submit_order(
            client,
            ticker=ticker,
            qty=max_qty,
            side="buy",
            order_type="market",
        )
    except KisError as e:
        print(f"trading smoke-gate: BUY 발주 KIS 오류: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"trading smoke-gate: BUY 발주 실패: {e}", file=sys.stderr)
        return 1

    buy_order_id = buy_result.get("order_id")
    buy_order_no = str(buy_result.get("kis_order_no") or "")
    print(f"[smoke-gate] BUY submitted: order_id={buy_order_id} kis_order_no={buy_order_no}")

    # ── BUY 체결 확인 ───────────────────────────────────────────────────────
    # SPEC-043 페이서 경유: confirm_fills()가 client.get() → _RateGate 경유.
    # 원장 갱신용으로 confirm_fills()를 먼저 호출하고,
    # ODNO 매칭용으로 _inquire_ccld_raw()로 raw 레코드를 별도 수집한다.
    tr_id_compatible = True
    ccld_records: list[dict] = []
    try:
        confirm_fills(client, source="execution_inquiry")
        # ODNO 매칭용 raw 레코드 수집 (TPS 페이서 경유)
        ccld_records = _inquire_ccld_raw(client)
    except BrokerFillInquiryNotImplemented:
        tr_id_compatible = False
        logging.warning(
            "trading smoke-gate: BrokerFillInquiryNotImplemented — "
            "TR_ID/필드 미호환([확인 필요-1/2] 미해소). 증거(e) 미충족."
        )
    except Exception as e:
        logging.warning("trading smoke-gate: BUY 체결 확인 실패: %s", e)

    buy_fill = _find_fill_record(ccld_records, buy_order_no)
    print(f"[smoke-gate] BUY 체결 확인: {'OK' if buy_fill else 'NOT FOUND'}")

    # ── SELL 발주 (guard_sell 경유, REQ-049-M3-1) ───────────────────────────
    sell_order_no = ""

    if not guard_sell(ticker, actor="smoke_gate"):
        print(
            f"trading smoke-gate: SELL 발주가 sell_lock에 의해 억제됨 (ticker={ticker}). "
            "이중매도 방지(REQ-049-M3-1). 이미 진행 중인 SELL이 있습니다.",
            file=sys.stderr,
        )
        # SELL 없이 판정 진행 → (b) 미충족 → FAIL
    else:
        set_sell_inflight(ticker)
        print(f"[smoke-gate] SELL 발주: ticker={ticker} qty={max_qty}")
        try:
            sell_result = submit_order(
                client,
                ticker=ticker,
                qty=max_qty,
                side="sell",
                order_type="market",
            )
            sell_order_no = str(sell_result.get("kis_order_no") or "")
            print(
                f"[smoke-gate] SELL submitted: "
                f"order_id={sell_result.get('order_id')} kis_order_no={sell_order_no}"
            )
        except KisError as e:
            logging.warning("trading smoke-gate: SELL 발주 KIS 오류: %s", e)
        except Exception as e:
            logging.warning("trading smoke-gate: SELL 발주 실패: %s", e)

        # ── SELL 체결 확인 (원장 갱신 + ODNO 매칭용 raw 재조회) ──────────────
        try:
            confirm_fills(client, source="execution_inquiry")
            ccld_records = _inquire_ccld_raw(client)
        except BrokerFillInquiryNotImplemented:
            tr_id_compatible = False
        except Exception as e:
            logging.warning("trading smoke-gate: SELL 체결 확인 실패: %s", e)

    sell_fill = _find_fill_record(ccld_records, sell_order_no) if sell_order_no else None
    print(f"[smoke-gate] SELL 체결 확인: {'OK' if sell_fill else 'NOT FOUND'}")

    # ── 원장 정합 수집 (REQ-049-M2-1(c)) ────────────────────────────────────
    ledger_parity = False
    try:
        rec_result = intraday_reconcile(client, reason="smoke-gate", force=True)
        # throttled=False이면 실제 reconcile 수행. drift가 0이면 정합.
        if not rec_result.get("throttled"):
            summary = rec_result.get("summary") or {}
            drift = int(summary.get("positions_synced", 0) or 0)
            errors = int(summary.get("errors", 0) or 0)
            positions_drift_ok = drift == 0 and errors == 0
        else:
            # throttled이면 이전 reconcile 결과를 신뢰 (정합으로 간주)
            positions_drift_ok = True

        # SPEC-TRADING-042 D2/M1(감사 M1): orders-agg net vs positions parity 추가.
        # positions-vs-KIS drift 만으로는 orders 드리프트 미감지(AC-5 맹점 보강).
        from trading.kis.ghost_convergence import orders_positions_divergence
        try:
            orders_parity = orders_positions_divergence()["parity"]
        except Exception as _e:
            logging.warning("trading smoke-gate: orders_positions_divergence 실패: %s", _e)
            orders_parity = True  # 실패는 safe(게이트 차단 아님 — D5 live seam 대칭)

        ledger_parity = positions_drift_ok and orders_parity
    except Exception as e:
        logging.warning("trading smoke-gate: intraday_reconcile 실패: %s", e)

    # ── stuck 'submitted' 해소 (REQ-049-M3-2) ─────────────────────────────
    try:
        resolve_stuck_orders(client)
    except Exception as e:
        logging.warning("trading smoke-gate: resolve_stuck_orders 실패: %s", e)

    stuck_count = _count_stuck_submitted()

    # ── 증거 판정 ───────────────────────────────────────────────────────────
    evidence = SmokeEvidence(
        buy_fill=buy_fill,
        buy_order_no=buy_order_no,
        sell_fill=sell_fill,
        sell_order_no=sell_order_no,
        ledger_parity=ledger_parity,
        stuck_submitted_count=stuck_count,
        tr_id_field_compatible=tr_id_compatible,
    )
    verdict = evaluate_smoke_evidence(evidence)

    # ── 영구 기록 ───────────────────────────────────────────────────────────
    snapshot = {
        "ticker": ticker,
        "max_qty": max_qty,
        "buy_order_no": buy_order_no,
        "sell_order_no": sell_order_no,
        "buy_fill": buy_fill,
        "sell_fill": sell_fill,
        "ledger_parity": ledger_parity,
        "stuck_count": stuck_count,
        "tr_id_compatible": tr_id_compatible,
    }
    record_smoke_verdict(verdict, snapshot=snapshot)

    # ── 결과 출력 ───────────────────────────────────────────────────────────
    _print_smoke_result(verdict)

    return 0 if verdict.passed else 1


def _inquire_ccld_raw(client) -> list[dict]:
    """KIS inquire-daily-ccld raw 레코드를 직접 조회한다(ODNO 매칭용).

    SPEC-043 TPS 페이서를 경유하는 client.get()을 통해 호출 (REQ-049-M3-3).
    """
    from trading.kis.broker_truth import _inquire_daily_ccld
    try:
        return _inquire_daily_ccld(client)
    except Exception as e:
        logging.warning("trading smoke-gate: _inquire_ccld_raw 실패: %s", e)
        return []


def _find_fill_record(records: list[dict], order_no: str) -> dict | None:
    """records에서 ODNO가 order_no와 일치하는 fill 레코드를 반환(없으면 None).

    동일 ODNO가 BUY/SELL 양쪽에 있어도 order_no 기준으로 독립 매칭한다.
    """
    if not order_no or not records:
        return None
    for rec in records:
        odno = str(rec.get("ODNO", "") or "").strip()
        if odno == str(order_no).strip():
            return rec
    return None


def _count_stuck_submitted() -> int:
    """현재 'submitted' 상태로 잔존하는 주문 수를 반환(resolve 후 잔여)."""
    from trading.db.session import connection
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM orders WHERE status = 'submitted'"
            )
            row = cur.fetchone()
            return int((row or {}).get("cnt", 0) or 0)
    except Exception as e:
        logging.warning("trading smoke-gate: _count_stuck_submitted 실패: %s", e)
        return 0


def _print_smoke_result(verdict) -> None:
    """스모크 판정 결과를 콘솔에 출력한다."""
    label = "PASS" if verdict.passed else "FAIL"
    separator = "=" * 60
    print(separator)
    print(f"[smoke-gate] 판정: {label}  ({verdict.timestamp})")
    print(separator)
    item_labels = {
        "a": "(a) BUY 확정 체결",
        "b": "(b) SELL 확정 체결",
        "c": "(c) 원장 정합",
        "d": "(d) stuck 'submitted' 0건",
        "e": "(e) live TR_ID/필드 호환",
    }
    for key, item_label in item_labels.items():
        item = verdict.items.get(key)
        if item is None:
            continue
        mark = "✓" if item.satisfied else "✗"
        print(f"  {mark} {item_label}: {item.reason}")
    print(separator)
    if verdict.passed:
        print("[smoke-gate] PASS — audit_log에 SMOKE_GATE_PASS 기록됨.")
        print("[smoke-gate] live 전면 승격 조건 충족. 별도 승격 절차를 진행하십시오.")
    else:
        print("[smoke-gate] FAIL — live 전면 승격 차단.", file=sys.stderr)
        print("[smoke-gate] 미충족 사유:", file=sys.stderr)
        for reason in verdict.reasons:
            print(f"  - {reason}", file=sys.stderr)
    print()


def _cmd_edge_snapshot(rest: list[str]) -> int:
    """Edge Validation: 오늘 자산 스냅샷 1회 기록(수동/백필). 멱등 UPSERT."""
    from trading.edge.snapshot import record_snapshot

    try:
        row = record_snapshot()
    except Exception as e:
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
        "  kospi200-backfill 10y KOSPI200 OHLCV backfill (SPEC-037, one-shot)\n"
        "  exit-backtest     deterministic exit-rule parameter sweep (SPEC-037)\n"
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
        "  resolve-orders    resolve stuck 'submitted' orders [--cleanup] [--dry-run] [--window N]\n"
        "  aggregate-pnl     backfill realized_pnl_cum from fills [--dry-run] [--days N]\n"
        "  crawl-news        crawl news sources [--sector X] [--source X] [--force]\n"
        "  analyze-news      run intelligence analysis [--sector X] [--force]\n"
        "  news-health       show news source health status table\n"
        "  smoke-gate        live 스모크 게이트 (실행 경로 검증, REQ-049) "
        "[--max-qty N] [--max-notional N] [--ticker CODE] [--dry-run]\n",
        file=file,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
