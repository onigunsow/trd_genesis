"""APScheduler runner — runs persona cycles + daily report on KRX trading days.

Invoked as: trading scheduler  (long-running process inside container).
"""

from __future__ import annotations

import logging
import signal

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from trading.contexts import (
    build_macro_context,
    build_macro_news,
    build_micro_context,
    build_micro_news,
)
from trading.monitoring import data_freshness
from trading.personas import orchestrator, retrospective
from trading.reports import daily_report
from trading.risk.auto_resume import run_premarket_auto_resume
from trading.risk.blocked_cache import refresh_blocked_tickers
from trading.scheduler.calendar import is_trading_day, reason_if_closed
from trading.screener import daily_screen
from trading.scripts import refresh_market_data
from trading.watchers import blocked_release as _watcher_blocked_release
from trading.watchers import price_threshold as _watcher_price_threshold
from trading.watchers import volume_anomaly as _watcher_volume_anomaly

LOG = logging.getLogger(__name__)
KST = pytz.timezone("Asia/Seoul")


def _run_news_crawl() -> None:
    """SPEC-013: Run news crawl cycle (feature flag checked internally)."""
    from trading.news.crawler import crawl_all

    crawl_all()


def _run_news_intelligence() -> None:
    """SPEC-014: Run news intelligence analysis pipeline (legacy single-step).

    Kept for backward compatibility. Prefer split export/import jobs.
    """
    from trading.news.intelligence.scheduler import scheduled_run

    scheduled_run()


def _run_news_export() -> None:
    """SPEC-014: Export unanalyzed articles for host Claude CLI processing."""
    from trading.news.intelligence.scheduler import scheduled_export

    scheduled_export()


def _run_news_import() -> None:
    """SPEC-014: Import host CLI results and run post-analysis pipeline."""
    from trading.news.intelligence.scheduler import scheduled_import

    scheduled_import()


def _run_fill_sync() -> None:
    """SPEC-TRADING-029 REQ-029-4: run one fill_sync cycle from the scheduler.

    Imports are deferred so the heavy ``trading.kis`` stack only loads when
    the cron actually fires (matches the pattern used by ``_run_news_crawl``
    et al. in this module). The orchestrator returns a summary dict with
    ``queried`` / ``transitioned`` / ``errors`` / ``dry_run`` keys — they are
    logged at INFO so operators can grep the container logs for cycle health.
    """
    from trading.config import get_settings
    from trading.kis.client import KisClient
    from trading.kis.fills import fill_sync as _fill_sync

    client = KisClient(get_settings().trading_mode)
    result = _fill_sync(client, dry_run=False)
    LOG.info(
        "fill_sync queried=%d transitioned=%d errors=%d",
        int(result.get("queried", 0)),
        int(result.get("transitioned", 0)),
        int(result.get("errors", 0)),
    )


def _wrap(name: str, fn, *args, **kwargs):
    """Run `fn` only if today is a KRX trading day (Mon-Fri ∩ no public holidays)."""
    if not is_trading_day():
        why = reason_if_closed()
        LOG.info("%s skipped (non-trading day: %s) — Anthropic 토큰 절약", name, why)
        return
    try:
        LOG.info("%s start", name)
        fn(*args, **kwargs)
        LOG.info("%s ok", name)
    except Exception:
        LOG.exception("%s failed", name)


def _safe_call(name: str, fn, *args, **kwargs):
    """Run `fn` regardless of trading day (used for static context builders).

    Builders themselves are guarded (see contexts/utils.guarded_build) so failure
    keeps the previous .md and emits system_error.
    """
    try:
        LOG.info("%s start", name)
        fn(*args, **kwargs)
        LOG.info("%s ok", name)
    except Exception:
        LOG.exception("%s failed", name)


def main() -> None:
    sched = BlockingScheduler(timezone=KST)

    # SPEC-013 — News crawl 6x/day (always runs; .md is "current snapshot")
    _NEWS_CRAWL_TIMES = [
        (8, 0),  # pre-market (1h before KRX open)
        (11, 0),  # intraday
        (14, 30),  # intraday (merged 14:00 + 15:00)
        (22, 0),  # evening
        (1, 0),  # overnight
        (4, 0),  # overnight
    ]
    for h, m in _NEWS_CRAWL_TIMES:
        sched.add_job(
            lambda: _safe_call("news_crawl_v2", _run_news_crawl),
            CronTrigger(hour=h, minute=m, timezone=KST),
            id=f"news_crawl_{h:02d}{m:02d}",
            name=f"news_crawl_v2 {h:02d}:{m:02d}",
        )

    # SPEC-014 — News intelligence: 2-step host CLI pipeline
    # Step 1: Export articles for host Claude CLI (5 min after each crawl)
    _NEWS_EXPORT_TIMES = [
        (8, 5),  # post crawl 08:00
        (11, 5),  # post crawl 11:00
        (14, 35),  # post crawl 14:30
        (22, 5),  # post crawl 22:00
        (1, 5),  # post crawl 01:00
        (4, 5),  # post crawl 04:00
    ]
    for h, m in _NEWS_EXPORT_TIMES:
        sched.add_job(
            lambda: _safe_call("news_export", _run_news_export),
            CronTrigger(hour=h, minute=m, timezone=KST),
            id=f"news_export_{h:02d}{m:02d}",
            name=f"news_export {h:02d}:{m:02d}",
        )

    # Step 2: Import host results + run pipeline (15 min after each crawl)
    # Allows 10 min for host cron to run claude CLI at :10/:40
    _NEWS_IMPORT_TIMES = [
        (8, 15),  # host analyzes at 08:10
        (11, 15),  # host analyzes at 11:10
        (14, 45),  # host analyzes at 14:40
        (22, 15),  # host analyzes at 22:10
        (1, 15),  # host analyzes at 01:10
        (4, 15),  # host analyzes at 04:10
    ]
    for h, m in _NEWS_IMPORT_TIMES:
        sched.add_job(
            lambda: _safe_call("news_import", _run_news_import),
            CronTrigger(hour=h, minute=m, timezone=KST),
            id=f"news_import_{h:02d}{m:02d}",
            name=f"news_import {h:02d}:{m:02d}",
        )

    # SPEC-007 — Static context builders (run regardless of trading day; cheap)
    # macro_context 06:00 — every day (uses cached data)
    sched.add_job(
        lambda: _safe_call("build_macro_context", build_macro_context.main),
        CronTrigger(hour=6, minute=0, timezone=KST),
        id="ctx_macro",
        name="build_macro_context 06:00",
    )
    # micro_context 06:30 — every day
    sched.add_job(
        lambda: _safe_call("build_micro_context", build_micro_context.main),
        CronTrigger(hour=6, minute=30, timezone=KST),
        id="ctx_micro",
        name="build_micro_context 06:30",
    )
    # micro_news 06:45 — trading days only
    sched.add_job(
        lambda: _wrap("build_micro_news", build_micro_news.main),
        CronTrigger(day_of_week="mon-fri", hour=6, minute=45, timezone=KST),
        id="ctx_micro_news",
        name="build_micro_news 06:45",
    )
    # macro_news Friday 16:30 — single LLM call
    sched.add_job(
        lambda: _safe_call("build_macro_news", build_macro_news.main),
        CronTrigger(day_of_week="fri", hour=16, minute=30, timezone=KST),
        id="ctx_macro_news",
        name="build_macro_news Fri 16:30",
    )

    # Phase 1: Mechanical filter + export pending_screen.json (06:30)
    # Phase 2: Host cron runs Claude CLI at 06:35 (scripts/daily_screen.sh)
    # Orchestrator reads screened_tickers.json at 07:30 (pre_market cycle)
    sched.add_job(
        lambda: _safe_call("daily_screen", daily_screen.run),
        CronTrigger(day_of_week="mon-fri", hour=6, minute=30, timezone=KST),
        id="daily_screen",
        name="daily_screen 06:30",
    )

    # SPEC-TRADING-029 v0.2.0 REQ-029-4/6: KIS order lifecycle sync.
    # Reconciles local orders/positions against KIS inquire-balance holdings
    # every 60s during the trading session (FIFO attribution of held qty to open
    # BUY orders + positions mirror). Wrapped in _wrap() so the KRX trading-day
    # guard suppresses execution on weekends and holidays.
    # @MX:NOTE: hour="9-15" includes the full 15:** window — ~30 extra calls
    # after the 15:30 close per session is harmless because balance reconcile is
    # idempotent (newly_filled = max(0, held_qty - already_accounted) is 0 once
    # everything is accounted; see AC-029-11). Trade-off is favored over a more
    # complex cron expression. @MX:SPEC: SPEC-TRADING-029
    sched.add_job(
        lambda: _wrap("fill_sync", _run_fill_sync),
        CronTrigger(
            day_of_week="mon-fri",
            hour="9-15",
            minute="*",
            second="0",
            timezone=KST,
        ),
        id="fill_sync",
        name="fill_sync 09:00-15:30 every 60s",
    )

    # SPEC-TRADING-026 (c-cron): refresh the blocked cache at 06:20 — BEFORE the
    # 06:30 screener — so the screener sees the same-day 단기과열 set. Previously
    # 07:25 (after the screener), which left the SPEC-025/026 blocked filter
    # reading a stale file and silently degrading to an empty set. The screener's
    # today-OR-yesterday tolerance (SPEC-026 _load_blocked_map) remains the
    # safety net if this refresh is late or fails. Still well before the 07:30
    # pre_market cycle.
    sched.add_job(
        lambda: _wrap("blocked_tickers_cache", refresh_blocked_tickers),
        CronTrigger(day_of_week="mon-fri", hour=6, minute=20, timezone=KST),
        id="blocked_cache",
        name="blocked_tickers 06:20",
    )

    # SPEC-TRADING-032 REQ-032-1: pre-market auto-resume 07:25 — runs BEFORE the
    # 07:30 pre_market cycle so a benign automatic limit halt (daily_count etc.,
    # but NOT daily_loss/manual) is cleared in time for the day's first trade.
    # _wrap gives the KRX trading-day guard (Q-3: holidays auto-skip).
    # @MX:SPEC: SPEC-TRADING-032
    sched.add_job(
        lambda: _wrap("premarket_auto_resume", run_premarket_auto_resume),
        CronTrigger(day_of_week="mon-fri", hour=7, minute=25, timezone=KST),
        id="premarket_auto_resume",
        name="premarket_auto_resume 07:25",
    )

    # Pre-market 07:30
    sched.add_job(
        lambda: _wrap("pre_market", orchestrator.run_pre_market_cycle),
        CronTrigger(day_of_week="mon-fri", hour=7, minute=30, timezone=KST),
        id="pre_market",
        name="pre_market 07:30",
    )

    # @MX:NOTE: SPEC-TRADING-024 REQ-024-1 v0.3.0 — adaptive intraday cron.
    # This adaptive */15 cron is the SOLE intraday cycle driver — every 15 min
    # between 09:00 and 15:30 KST (mon-fri). The legacy hard-coded slots at
    # 09:30/11:00/13:30/14:30 were REMOVED in v0.3.0: every one of those minutes
    # lands on a */15 boundary (:00/:15/:30/:45), so run_intraday_cycle
    # double-fired in the same minute (observed 2026-05-18 09:30 KST), causing
    # duplicate LLM dispatch. Neither the orchestrator cached-micro reuse nor the
    # watcher in-flight lock prevented the double LLM call, so the slots had to
    # go. Cadence default configurable via
    # .moai/config/sections/scheduler.yaml (intraday_interval_minutes).
    # @MX:SPEC: SPEC-TRADING-024
    sched.add_job(
        lambda: _wrap("intraday_adaptive", orchestrator.run_intraday_cycle),
        CronTrigger(
            day_of_week="mon-fri",
            hour="9-15",
            minute="*/15",
            timezone=KST,
        ),
        id="intraday_adaptive",
        name="intraday_adaptive */15 (09-15 KST)",
    )

    # SPEC-TRADING-024 REQ-024-2/3/4 — 5-min watcher pollers (mon-fri 09-15 KST).
    sched.add_job(
        lambda: _wrap(
            "watcher_price_threshold",
            _watcher_price_threshold.poll_price_threshold,
        ),
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5", timezone=KST),
        id="watcher_price_threshold",
        name="watcher_price_threshold */5 (09-15 KST)",
    )
    sched.add_job(
        lambda: _wrap(
            "watcher_volume_anomaly",
            _watcher_volume_anomaly.poll_volume_anomaly,
        ),
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5", timezone=KST),
        id="watcher_volume_anomaly",
        name="watcher_volume_anomaly */5 (09-15 KST)",
    )
    sched.add_job(
        lambda: _wrap(
            "watcher_blocked_release",
            _watcher_blocked_release.poll_blocked_release,
        ),
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5", timezone=KST),
        id="watcher_blocked_release",
        name="watcher_blocked_release */5 (09-15 KST)",
    )

    # Daily report 16:00
    sched.add_job(
        lambda: _wrap("daily_report", daily_report.generate_and_send),
        CronTrigger(day_of_week="mon-fri", hour=16, minute=0, timezone=KST),
        id="daily_report",
        name="daily_report 16:00",
    )

    # Weekly macro: Friday 17:00
    sched.add_job(
        lambda: _wrap("weekly_macro", orchestrator.run_weekly_macro),
        CronTrigger(day_of_week="fri", hour=17, minute=0, timezone=KST),
        id="weekly_macro",
        name="weekly_macro 17:00",
    )

    # Retrospective: Sunday 18:00
    sched.add_job(
        lambda: _wrap("retrospective", retrospective.run),
        CronTrigger(day_of_week="sun", hour=18, minute=0, timezone=KST),
        id="retrospective",
        name="retrospective 18:00",
    )

    # SPEC-TRADING-019 — Market data automated refresh layer
    # REQ-019-1: Daily OHLCV refresh — 16:00 KST mon-fri (KRX EOD)
    sched.add_job(
        lambda: _wrap("data_refresh_ohlcv", refresh_market_data.refresh_ohlcv),
        CronTrigger(day_of_week="mon-fri", hour=16, minute=0, timezone=KST),
        id="data_refresh_ohlcv",
        name="data_refresh_ohlcv 16:00",
    )
    # REQ-019-2: Daily flows refresh — 16:05 KST mon-fri (5 min after OHLCV)
    sched.add_job(
        lambda: _wrap("data_refresh_flows", refresh_market_data.refresh_flows),
        CronTrigger(day_of_week="mon-fri", hour=16, minute=5, timezone=KST),
        id="data_refresh_flows",
        name="data_refresh_flows 16:05",
    )
    # REQ-019-3: Weekly fundamentals refresh — Sunday 18:00 KST (no trading-day guard)
    sched.add_job(
        lambda: _safe_call("data_refresh_fundamentals", refresh_market_data.refresh_fundamentals),
        CronTrigger(day_of_week="sun", hour=18, minute=0, timezone=KST),
        id="data_refresh_fundamentals",
        name="data_refresh_fundamentals Sun 18:00",
    )
    # REQ-019-4: Daily DART disclosure refresh — 18:00 KST every day (DART 365/yr)
    sched.add_job(
        lambda: _safe_call("data_refresh_disclosures", refresh_market_data.refresh_disclosures),
        CronTrigger(hour=18, minute=0, timezone=KST),
        id="data_refresh_disclosures",
        name="data_refresh_disclosures 18:00",
    )
    # REQ-019-5: Stale-data monitor — 09:00 KST mon-fri
    sched.add_job(
        lambda: _wrap("data_freshness_check", data_freshness.check_and_alert),
        CronTrigger(day_of_week="mon-fri", hour=9, minute=0, timezone=KST),
        id="data_freshness_check",
        name="data_freshness_check 09:00",
    )

    LOG.info("trading scheduler starting (KST cron)")
    signal.signal(signal.SIGTERM, lambda *_: sched.shutdown(wait=False))

    # REQ-019-7 (P0 escalated): bootstrap backfill before serving cron jobs.
    # Non-fatal — if bootstrap fails, scheduler still starts and the 09:00
    # stale-monitor will catch the gap on its next run.
    try:
        refresh_market_data.bootstrap_backfill_if_empty()
    except Exception:
        LOG.exception("bootstrap_backfill_if_empty failed; continuing with scheduler start")

    sched.start()


# @MX:NOTE: SPEC-TRADING-017 -- fallback basicConfig for direct module
# execution (`python -m trading.scheduler.runner`). When the container
# entrypoint `trading scheduler` routes through cli.main(), the CLI-level
# bootstrap has already configured the root logger and this call is a
# stdlib no-op. Retained intentionally; safe to remove only if direct
# module execution is dropped from the operator workflow.
# @MX:SPEC: SPEC-TRADING-017
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # SPEC-TRADING-026 (security): mute httpx/httpcore so the Telegram bot token
    # in request URLs is never logged (mirrors trading.cli._bootstrap_logging).
    for _noisy in ("httpx", "httpcore"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)
    main()
