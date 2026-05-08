"""APScheduler runner — runs persona cycles + daily report on KRX trading days.

Invoked as: trading scheduler  (long-running process inside container).
"""

from __future__ import annotations

import logging
import signal
from datetime import date

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from trading.contexts import (
    build_macro_context,
    build_macro_news,
    build_micro_context,
    build_micro_news,
)
from trading.personas import orchestrator, retrospective
from trading.reports import daily_report
from trading.risk.blocked_cache import refresh_blocked_tickers
from trading.scheduler.calendar import is_trading_day, reason_if_closed
from trading.screener import daily_screen

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
    except Exception:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
        LOG.exception("%s failed", name)


def main() -> None:
    sched = BlockingScheduler(timezone=KST)

    # SPEC-013 — News crawl 6x/day (always runs; .md is "current snapshot")
    _NEWS_CRAWL_TIMES = [
        (8, 0),    # pre-market (1h before KRX open)
        (11, 0),   # intraday
        (14, 30),  # intraday (merged 14:00 + 15:00)
        (22, 0),   # evening
        (1, 0),    # overnight
        (4, 0),    # overnight
    ]
    for h, m in _NEWS_CRAWL_TIMES:
        sched.add_job(lambda: _safe_call("news_crawl_v2", _run_news_crawl),
                      CronTrigger(hour=h, minute=m, timezone=KST),
                      id=f"news_crawl_{h:02d}{m:02d}",
                      name=f"news_crawl_v2 {h:02d}:{m:02d}")

    # SPEC-014 — News intelligence: 2-step host CLI pipeline
    # Step 1: Export articles for host Claude CLI (5 min after each crawl)
    _NEWS_EXPORT_TIMES = [
        (8, 5),     # post crawl 08:00
        (11, 5),    # post crawl 11:00
        (14, 35),   # post crawl 14:30
        (22, 5),    # post crawl 22:00
        (1, 5),     # post crawl 01:00
        (4, 5),     # post crawl 04:00
    ]
    for h, m in _NEWS_EXPORT_TIMES:
        sched.add_job(lambda: _safe_call("news_export", _run_news_export),
                      CronTrigger(hour=h, minute=m, timezone=KST),
                      id=f"news_export_{h:02d}{m:02d}",
                      name=f"news_export {h:02d}:{m:02d}")

    # Step 2: Import host results + run pipeline (15 min after each crawl)
    # Allows 10 min for host cron to run claude CLI at :10/:40
    _NEWS_IMPORT_TIMES = [
        (8, 15),    # host analyzes at 08:10
        (11, 15),   # host analyzes at 11:10
        (14, 45),   # host analyzes at 14:40
        (22, 15),   # host analyzes at 22:10
        (1, 15),    # host analyzes at 01:10
        (4, 15),    # host analyzes at 04:10
    ]
    for h, m in _NEWS_IMPORT_TIMES:
        sched.add_job(lambda: _safe_call("news_import", _run_news_import),
                      CronTrigger(hour=h, minute=m, timezone=KST),
                      id=f"news_import_{h:02d}{m:02d}",
                      name=f"news_import {h:02d}:{m:02d}")

    # SPEC-007 — Static context builders (run regardless of trading day; cheap)
    # macro_context 06:00 — every day (uses cached data)
    sched.add_job(lambda: _safe_call("build_macro_context", build_macro_context.main),
                  CronTrigger(hour=6, minute=0, timezone=KST),
                  id="ctx_macro", name="build_macro_context 06:00")
    # micro_context 06:30 — every day
    sched.add_job(lambda: _safe_call("build_micro_context", build_micro_context.main),
                  CronTrigger(hour=6, minute=30, timezone=KST),
                  id="ctx_micro", name="build_micro_context 06:30")
    # micro_news 06:45 — trading days only
    sched.add_job(lambda: _wrap("build_micro_news", build_micro_news.main),
                  CronTrigger(day_of_week="mon-fri", hour=6, minute=45, timezone=KST),
                  id="ctx_micro_news", name="build_micro_news 06:45")
    # macro_news Friday 16:30 — single LLM call
    sched.add_job(lambda: _safe_call("build_macro_news", build_macro_news.main),
                  CronTrigger(day_of_week="fri", hour=16, minute=30, timezone=KST),
                  id="ctx_macro_news", name="build_macro_news Fri 16:30")

    # Phase 1: Mechanical filter + export pending_screen.json (06:30)
    # Phase 2: Host cron runs Claude CLI at 06:35 (scripts/daily_screen.sh)
    # Orchestrator reads screened_tickers.json at 07:30 (pre_market cycle)
    sched.add_job(lambda: _safe_call("daily_screen", daily_screen.run),
                  CronTrigger(day_of_week="mon-fri", hour=6, minute=30, timezone=KST),
                  id="daily_screen", name="daily_screen 06:30")

    # SPEC-FIX: Blocked tickers cache 07:25 (before 07:30 pre_market cycle)
    sched.add_job(lambda: _wrap("blocked_tickers_cache", refresh_blocked_tickers),
                  CronTrigger(day_of_week="mon-fri", hour=7, minute=25, timezone=KST),
                  id="blocked_cache", name="blocked_tickers 07:25")

    # Pre-market 07:30
    sched.add_job(lambda: _wrap("pre_market", orchestrator.run_pre_market_cycle),
                  CronTrigger(day_of_week="mon-fri", hour=7, minute=30, timezone=KST),
                  id="pre_market", name="pre_market 07:30")

    # Intraday 09:30, 11:00, 13:30, 14:30
    for h, m in [(9, 30), (11, 0), (13, 30), (14, 30)]:
        sched.add_job(lambda: _wrap("intraday", orchestrator.run_intraday_cycle),
                      CronTrigger(day_of_week="mon-fri", hour=h, minute=m, timezone=KST),
                      id=f"intraday_{h}_{m}", name=f"intraday {h:02d}:{m:02d}")

    # Daily report 16:00
    sched.add_job(lambda: _wrap("daily_report", daily_report.generate_and_send),
                  CronTrigger(day_of_week="mon-fri", hour=16, minute=0, timezone=KST),
                  id="daily_report", name="daily_report 16:00")

    # Weekly macro: Friday 17:00
    sched.add_job(lambda: _wrap("weekly_macro", orchestrator.run_weekly_macro),
                  CronTrigger(day_of_week="fri", hour=17, minute=0, timezone=KST),
                  id="weekly_macro", name="weekly_macro 17:00")

    # Retrospective: Sunday 18:00
    sched.add_job(lambda: _wrap("retrospective", retrospective.run),
                  CronTrigger(day_of_week="sun", hour=18, minute=0, timezone=KST),
                  id="retrospective", name="retrospective 18:00")

    LOG.info("trading scheduler starting (KST cron)")
    signal.signal(signal.SIGTERM, lambda *_: sched.shutdown(wait=False))
    sched.start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    main()
