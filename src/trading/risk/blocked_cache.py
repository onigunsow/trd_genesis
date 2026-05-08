"""Cache blocked tickers for Decision persona.

Runs as cron at 08:50 KST (before market open).
Results cached to data/blocked_tickers.json.

Checks stat_cls via KIS current_price for watchlist tickers.
Blocked statuses: 51=관리, 52=투자위험, 53=투자경고, 54=거래정지, 55=단기과열.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from trading.config import get_settings, project_root
from trading.kis.client import KisClient
from trading.kis.market import current_price, stat_cls_label
from trading.personas.context import DEFAULT_WATCHLIST

LOG = logging.getLogger(__name__)
CACHE_FILE = project_root() / "data" / "blocked_tickers.json"


def refresh_blocked_tickers() -> dict[str, Any]:
    """Query KIS API for watchlist tickers' stat_cls status.

    Cache results to JSON file for intraday use by Decision persona.
    Called at 08:50 KST cron (before 09:00 market open).
    """
    s = get_settings()
    client = KisClient(s.trading_mode)

    # Gather tickers to check: base watchlist + any screened tickers
    tickers_to_check: list[str] = list(DEFAULT_WATCHLIST)

    # Also include screened tickers if available
    screened_file = project_root() / "data" / "screened_tickers.json"
    if screened_file.exists():
        try:
            screened = json.loads(screened_file.read_text())
            if isinstance(screened, dict) and "tickers" in screened:
                tickers_to_check.extend(screened["tickers"])
        except Exception:  # noqa: BLE001
            pass

    # Deduplicate
    tickers_to_check = list(dict.fromkeys(tickers_to_check))

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
    """Read cached blocked tickers. Returns empty if stale or missing."""
    try:
        if CACHE_FILE.exists():
            cache = json.loads(CACHE_FILE.read_text())
            if cache.get("date") == date.today().isoformat():
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
