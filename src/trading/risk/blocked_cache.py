"""Cache blocked tickers for Decision persona.

Runs as cron at 08:50 KST (before market open).
Results cached to data/blocked_tickers.json.

Checks stat_cls via KIS current_price for watchlist tickers.
Blocked statuses: 51=관리, 52=투자위험, 53=투자경고, 54=거래정지, 55=단기과열.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from trading.config import get_settings, project_root
from trading.data.universe import get_data_universe
from trading.kis.client import KisClient
from trading.kis.market import current_price, stat_cls_label

LOG = logging.getLogger(__name__)
CACHE_FILE = project_root() / "data" / "blocked_tickers.json"


# @MX:NOTE: SPEC-020 REQ-020-2 — universe source switched from DEFAULT_WATCHLIST
# to get_data_universe() so the 07:25 cron pre-flight check covers screened
# tickers too. Prevents 055550-style late-blocks (2026-05-12 07:33 incident).
# @MX:SPEC: SPEC-TRADING-020
def refresh_blocked_tickers() -> dict[str, Any]:
    """Query KIS API for universe tickers' stat_cls status.

    Cache results to JSON file for intraday use by Decision persona.
    Called at 07:25 KST cron (before 09:00 market open).

    SPEC-020 REQ-020-2: universe = get_data_universe() (screened U holdings U
    KOSPI200; falls back to DEFAULT on cold-start). Previously hardcoded to
    DEFAULT_WATCHLIST, which allowed screened-only tickers like 055550 to
    bypass the pre-flight check.
    """
    s = get_settings()
    client = KisClient(s.trading_mode)

    # SPEC-020 REQ-020-2: source universe from get_data_universe() instead of
    # hardcoded DEFAULT_WATCHLIST.
    tickers_to_check: list[str] = list(get_data_universe())

    blocked: dict[str, dict[str, str]] = {}
    for ticker in tickers_to_check[:50]:  # Limit to avoid API rate limits
        try:
            q = current_price(client, ticker)
            if not q["is_normal"]:
                label = stat_cls_label(q["stat_cls"])
                blocked[ticker] = {
                    "reason": f"{label} (stat_cls={q['stat_cls']})",
                    "stat_cls": q["stat_cls"],
                    "date": date.today().isoformat(),
                }
        except Exception as e:  # noqa: BLE001
            LOG.warning("blocked_cache: failed to check %s: %s", ticker, e)
            continue

    cache: dict[str, Any] = {
        "date": date.today().isoformat(),
        "blocked": blocked,
        "blocked_today_by_safety": [],
    }

    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
    LOG.info("Blocked tickers cache updated: %d blocked out of %d checked",
             len(blocked), len(tickers_to_check))
    return cache


def get_blocked_tickers() -> dict[str, Any]:
    """Read cached blocked tickers. Returns empty if stale or missing.

    Accepts today's or yesterday's cache because blocked status (management
    designation, trading halt, etc.) persists across trading days.  The cache
    is refreshed at 07:25 KST, but before that time we still want the
    previous day's blocked list to prevent trades on restricted stocks.
    """
    try:
        if CACHE_FILE.exists():
            cache = json.loads(CACHE_FILE.read_text())
            cache_date = cache.get("date", "")
            today = date.today().isoformat()
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            if cache_date in (today, yesterday):
                return cache
    except Exception:  # noqa: BLE001
        pass
    return {"date": date.today().isoformat(), "blocked": {}, "blocked_today_by_safety": []}


def record_blocked_by_safety(ticker: str, reason: str) -> None:
    """Called when trade safety blocks a ticker during trading.

    Adds to today's cache so Decision can see intraday blocks too.
    """
    cache = get_blocked_tickers()
    entry = {"ticker": ticker, "reason": reason}
    blocked_today = cache.get("blocked_today_by_safety", [])
    if entry not in blocked_today:
        blocked_today.append(entry)
        cache["blocked_today_by_safety"] = blocked_today
        cache["blocked"][ticker] = {
            "reason": reason,
            "date": date.today().isoformat(),
        }
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
        except Exception as e:  # noqa: BLE001
            LOG.warning("record_blocked_by_safety: write failed: %s", e)
