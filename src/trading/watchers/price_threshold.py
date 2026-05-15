"""SPEC-TRADING-024 REQ-024-2 — ATR-based price-threshold watcher.

For each target ticker (holdings union dynamic_tickers union today's micro
candidates), compute today's ATR-derived threshold =
`atr_multiplier * atr_14 / close_price`
(default multiplier 1.5x, per resolved Open Question Q-4). If the KIS-reported
`change_pct` (signed, absolute value used) exceeds the threshold, fire a
`price_threshold` event.

Throttling: shared `TickerThrottle` (300s cooldown, 20/day cap shared across
all three Stage 1 watchers).

Event handling: delegates to `trading.watchers.event_handler.handle_trigger_event`
which invokes the standard `orchestrator.run_intraday_cycle`. Stage 1 does NOT
narrow the cycle to the specific ticker — Stage 2 will introduce multi-tier
dispatch.

@MX:SPEC: SPEC-TRADING-024
"""

from __future__ import annotations

import json
import logging
from typing import Any

from trading.strategy.volatility.atr import compute_atr
from trading.watchers.throttle import TickerThrottle

LOG = logging.getLogger(__name__)

# Stage 1 defaults — overridable via scheduler.yaml (resolved Q-4: ATR-based
# threshold, multiplier 1.5x).
DEFAULT_ATR_MULTIPLIER: float = 1.5
DEFAULT_COOLDOWN_SECONDS: int = 300
DEFAULT_DAILY_CAP: int = 20

# Module-level shared throttle. _get_shared_throttle() is the test seam.
_SHARED_THROTTLE: TickerThrottle | None = None


def _get_shared_throttle() -> TickerThrottle:
    """Return the process-global throttle (lazy-initialised)."""
    global _SHARED_THROTTLE
    if _SHARED_THROTTLE is None:
        _SHARED_THROTTLE = TickerThrottle(
            min_interval_sec=DEFAULT_COOLDOWN_SECONDS,
            daily_cap=DEFAULT_DAILY_CAP,
        )
    return _SHARED_THROTTLE


def _get_target_tickers() -> list[str]:
    """holdings union dynamic_tickers union today's micro buy candidates.

    Falls back gracefully when sources are unavailable so a stale watcher
    poll cannot crash the scheduler.
    """
    try:
        from trading.data.universe import _read_active_holdings, _read_dynamic_tickers
    except Exception:
        return []

    seen: set[str] = set()
    out: list[str] = []
    for src_fn, label in (
        (_read_active_holdings, "holdings"),
        (_read_dynamic_tickers, "dynamic_tickers"),
        (_read_micro_candidate_tickers, "micro_candidates"),
    ):
        try:
            for t in src_fn() or []:
                if isinstance(t, str) and t and t not in seen:
                    seen.add(t)
                    out.append(t)
        except Exception as exc:
            LOG.warning("price_threshold: source %s failed: %s", label, exc)
    return out


def _read_micro_candidate_tickers() -> list[str]:
    """Today's micro candidate tickers (buy list) from latest cached run."""
    try:
        from trading.personas import micro as micro_persona
    except Exception:
        return []
    try:
        row = micro_persona.latest_cached(max_age_days=1)
    except Exception as exc:
        LOG.warning("price_threshold: micro.latest_cached failed: %s", exc)
        return []
    if not row:
        return []
    response_json = row.get("response_json") or {}
    candidates = response_json.get("candidates", {}) or {}
    buy = candidates.get("buy") or []
    return [c.get("ticker") for c in buy if isinstance(c, dict) and c.get("ticker")]


def _get_kis_quote(ticker: str) -> dict[str, Any] | None:
    """Fetch current KIS quote for `ticker`; returns None on failure."""
    try:
        from trading.config import get_settings
        from trading.kis.client import KisClient
        from trading.kis.market import current_price

        s = get_settings()
        client = KisClient(s.trading_mode)
        return current_price(client, ticker)
    except Exception as exc:
        LOG.warning("price_threshold: KIS quote failed for %s: %s", ticker, exc)
        return None


def _fire_trigger_event(ticker: str, trigger_type: str, metadata: dict[str, Any]) -> None:
    """Record event + invoke shared event handler."""
    from trading.watchers.event_handler import handle_trigger_event

    handle_trigger_event(ticker, trigger_type, metadata)


# @MX:ANCHOR: SPEC-TRADING-024 REQ-024-2 entry-point for adaptive price polling
# @MX:REASON: fan_in >= 3 (scheduler cron, manual CLI smoke test,
#             future Stage 2 reuse)
# @MX:SPEC: SPEC-TRADING-024
def poll_price_threshold(
    atr_multiplier: float = DEFAULT_ATR_MULTIPLIER,
) -> dict[str, Any]:
    """Single poll iteration. Returns metrics dict for observability."""
    metrics = {
        "checked": 0,
        "fired": 0,
        "skipped_no_atr": 0,
        "skipped_no_quote": 0,
        "throttled": 0,
    }
    throttle = _get_shared_throttle()
    for ticker in _get_target_tickers():
        metrics["checked"] += 1

        quote = _get_kis_quote(ticker)
        if quote is None:
            metrics["skipped_no_quote"] += 1
            continue

        atr_row = compute_atr(ticker)
        if atr_row is None:
            metrics["skipped_no_atr"] += 1
            continue

        close_price = float(atr_row.get("close_price") or 0)
        atr_14 = float(atr_row.get("atr_14") or 0)
        if close_price <= 0 or atr_14 <= 0:
            metrics["skipped_no_atr"] += 1
            continue

        # ATR-relative threshold percentage. atr_14 is absolute KRW units;
        # divide by close_price to get a fractional move, multiply by 100 for
        # pct, then by `atr_multiplier` (default 1.5x).
        threshold_pct = atr_multiplier * (atr_14 / close_price) * 100.0
        price_change_pct = abs(float(quote.get("change_pct") or 0))

        if price_change_pct < threshold_pct:
            continue

        if not throttle.can_fire(ticker):
            metrics["throttled"] += 1
            continue

        throttle.record(ticker)
        metadata = {
            "atr_14": atr_14,
            "close_price": close_price,
            "price_change_pct": price_change_pct,
            "atr_threshold_pct": threshold_pct,
            "atr_multiplier": atr_multiplier,
        }
        _fire_trigger_event(ticker, "price_threshold", metadata)
        metrics["fired"] += 1
        LOG.info(
            "price_threshold fired ticker=%s change_pct=%.2f threshold_pct=%.2f",
            ticker,
            price_change_pct,
            threshold_pct,
        )

    if metrics["checked"]:
        LOG.info("price_threshold poll: %s", json.dumps(metrics))
    return metrics
