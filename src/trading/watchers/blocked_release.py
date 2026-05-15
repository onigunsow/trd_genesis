"""SPEC-TRADING-024 REQ-024-4 — Blocked-release watcher.

Poll KIS `stat_cls` for each ticker in `get_data_universe()` (5-min cadence
during market hours). When a ticker transitions from blocked (stat_cls=51..55,
typically 55 단기과열) → released (stat_cls=00), fire a `blocked_release`
event and update the on-disk blocked_tickers.json cache.

The previous-state input is sourced from the SPEC-018 cache at
`data/blocked_tickers.json` (managed by `trading.risk.blocked_cache`). After
a successful poll, the cache is rewritten so subsequent watcher invocations
have an up-to-date baseline.

@MX:SPEC: SPEC-TRADING-024
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

LOG = logging.getLogger(__name__)


def _get_universe() -> list[str]:
    """Wrapper over `get_data_universe()` with a safe fallback."""
    try:
        from trading.data.universe import get_data_universe

        return list(get_data_universe())
    except Exception as exc:
        LOG.warning("blocked_release: universe fetch failed: %s", exc)
        return []


def _get_current_stat_cls(ticker: str) -> dict[str, Any] | None:
    """Return `{stat_cls, is_normal, price, ...}` or None on failure."""
    try:
        from trading.config import get_settings
        from trading.kis.client import KisClient
        from trading.kis.market import current_price

        s = get_settings()
        client = KisClient(s.trading_mode)
        return current_price(client, ticker)
    except Exception as exc:
        LOG.warning("blocked_release: KIS quote failed for %s: %s", ticker, exc)
        return None


def _load_previous_blocked() -> dict[str, dict[str, Any]]:
    """Load the previous blocked snapshot from SPEC-018 cache.

    Returns mapping `ticker -> {stat_cls, reason, date}`. Empty dict on
    missing/corrupt cache (graceful degradation: first poll of the day will
    treat every currently-released ticker as a no-op, not a spurious event).
    """
    try:
        from trading.risk.blocked_cache import get_blocked_tickers

        cache = get_blocked_tickers() or {}
        return {k: dict(v) for k, v in (cache.get("blocked") or {}).items()}
    except Exception as exc:
        LOG.warning("blocked_release: previous-cache load failed: %s", exc)
        return {}


def _persist_blocked_state(blocked: dict[str, dict[str, Any]]) -> None:
    """Write updated blocked dict back to data/blocked_tickers.json."""
    try:
        from trading.config import project_root

        cache_file = project_root() / "data" / "blocked_tickers.json"
        cache: dict[str, Any] = {
            "date": date.today().isoformat(),
            "blocked": blocked,
            "blocked_today_by_safety": [],
        }
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
    except Exception as exc:
        LOG.warning("blocked_release: persist failed: %s", exc)


def _fire_trigger_event(ticker: str, trigger_type: str, metadata: dict[str, Any]) -> None:
    from trading.watchers.event_handler import handle_trigger_event

    handle_trigger_event(ticker, trigger_type, metadata)


def _stat_label(code: str) -> str:
    try:
        from trading.kis.market import stat_cls_label

        return stat_cls_label(code)
    except Exception:
        return f"stat_cls={code}"


# @MX:ANCHOR: SPEC-TRADING-024 REQ-024-4 entry-point for blocked-release polling
# @MX:REASON: fan_in >= 3 (scheduler cron, manual smoke test, Stage 2 reuse)
# @MX:SPEC: SPEC-TRADING-024
def poll_blocked_release() -> dict[str, Any]:
    """Single poll iteration. Returns metrics dict for observability."""
    metrics = {
        "checked": 0,
        "released": 0,
        "still_blocked": 0,
        "skipped_no_quote": 0,
    }
    previous = _load_previous_blocked()
    next_blocked: dict[str, dict[str, Any]] = {}

    for ticker in _get_universe():
        metrics["checked"] += 1
        quote = _get_current_stat_cls(ticker)
        if quote is None:
            metrics["skipped_no_quote"] += 1
            # Preserve previous state so we don't lose history mid-poll
            if ticker in previous:
                next_blocked[ticker] = previous[ticker]
            continue

        current_code = str(quote.get("stat_cls", "00") or "00")
        is_normal = bool(quote.get("is_normal", current_code == "00"))

        if not is_normal:
            metrics["still_blocked"] += 1
            next_blocked[ticker] = {
                "stat_cls": current_code,
                "reason": _stat_label(current_code),
                "date": date.today().isoformat(),
            }
            continue

        # Currently released → was it previously blocked?
        prev_entry = previous.get(ticker)
        if prev_entry is not None:
            metadata = {
                "previous_stat_cls": prev_entry.get("stat_cls"),
                "current_stat_cls": current_code,
                "previous_reason": prev_entry.get("reason"),
                "detected_at": date.today().isoformat(),
            }
            _fire_trigger_event(ticker, "blocked_release", metadata)
            metrics["released"] += 1
            LOG.info(
                "blocked_release fired ticker=%s prev=%s current=%s",
                ticker,
                prev_entry.get("stat_cls"),
                current_code,
            )

    _persist_blocked_state(next_blocked)
    if metrics["checked"]:
        LOG.info("blocked_release poll: %s", json.dumps(metrics))
    return metrics
