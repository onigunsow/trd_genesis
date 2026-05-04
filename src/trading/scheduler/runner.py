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
from trading.scheduler.calendar import is_trading_day, reason_if_closed

LOG = logging.getLogger(__name__)
KST = pytz.timezone("Asia/Seoul")


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
