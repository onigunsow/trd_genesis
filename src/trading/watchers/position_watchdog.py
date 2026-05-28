"""SPEC-TRADING-033 — auto stop-loss / take-profit position watchdog.

Realises SPEC-024 REQ-024-7 (deferred Stage 2 position watchdog). Closes a
capital-preservation gap: today a holding is only sold when the decision
persona chooses to sell during a cycle, so a crashing position is never
auto-exited.

Each market-hours (09-15 KST, mon-fri) `*/5` poll iterates the KIS
``balance()`` holdings and compares each holding's KIS-reported ``pnl_pct``
(``evlu_pfls_rt`` — never recomputed) against the ATR dynamic thresholds from
``get_dynamic_thresholds`` (SPEC-012):

- ``pnl_pct <= effective_stop`` → full-qty stop-loss exit.
- ``pnl_pct >= effective_take`` and not yet taken today → half-qty take-profit
  exit, guarded to at most once per ticker per KST day (in-memory marker).

Exits call ``kis_sell`` DIRECTLY, so they do NOT pass through the orchestrator
cycle halt gate or the daily-order-count pre-check. Risk-reducing exits must
never be blocked by buy-oriented gates (capital-preservation hard rule,
REQ-033-4). Real market rejections (lower-limit / locked) are tolerated and
absorbed by per-ticker error isolation.

@MX:SPEC: SPEC-TRADING-033
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

import pytz

from trading.alerts.telegram import system_briefing
from trading.db.session import audit
from trading.kis.account import balance
from trading.kis.order import sell as kis_sell
from trading.strategy.volatility.thresholds import get_dynamic_thresholds

LOG = logging.getLogger(__name__)
KST = pytz.timezone("Asia/Seoul")

# In-memory per-ticker take-profit guard: ticker -> KST date the ticker was last
# taken. A new KST day naturally clears the guard (stored date != today).
# Single-scheduler process makes this sufficient (A-3); a container restart
# resets it, an accepted same-day limitation (SPEC-024 TickerThrottle precedent).
_TOOK_PROFIT: dict[str, date] = {}


def _today_kst() -> date:
    """Current KST calendar date (test seam — patch to fix 'today')."""
    return datetime.now(KST).date()


def _took_profit_today(ticker: str) -> bool:
    """True if `ticker` was already take-profit-exited on the current KST day."""
    marked = _TOOK_PROFIT.get(ticker)
    return marked is not None and marked == _today_kst()


def _mark_took_profit(ticker: str) -> None:
    """Mark `ticker` as take-profit-exited for the current KST day."""
    _TOOK_PROFIT[ticker] = _today_kst()


def _reset_took_profit() -> None:
    """Clear the in-memory take-profit guard (test seam)."""
    _TOOK_PROFIT.clear()


def _build_client() -> Any:
    """Build a KIS client for the active trading mode (test seam)."""
    from trading.config import get_settings
    from trading.kis.client import KisClient

    return KisClient(get_settings().trading_mode)


def _read_holdings(client: Any) -> list[dict[str, Any]]:
    """Return the current ``balance()`` holdings list (test seam)."""
    return balance(client).get("holdings", []) or []


def _confirm_qty(client: Any, ticker: str) -> int:
    """Re-read `ticker`'s live qty from a fresh ``balance()`` (double-sell guard).

    Q-4: the decision persona may have sold the same ticker in this `*/5`
    window. Re-confirming the qty just before exiting avoids a double-sell. On
    any error the per-ticker try/except in the caller absorbs it.
    """
    for h in balance(client).get("holdings", []) or []:
        if h.get("ticker") == ticker:
            return int(h.get("qty", 0) or 0)
    return 0


# @MX:NOTE: pure decision helper — stop is evaluated before take so the two can
# never both fire for one holding (effective_stop < 0 < effective_take).
def classify_holding(
    pnl_pct: float,
    eff_stop: float | None,
    eff_take: float | None,
    took_profit_today: bool,
    qty: int,
) -> tuple[str, int]:
    """Classify a holding into an exit action and the qty to sell.

    Returns one of:
    - ("stop", qty)             when ``pnl_pct <= eff_stop`` (full exit).
    - ("take", max(1, qty//2))  when ``pnl_pct >= eff_take`` and not taken today.
    - ("skip", 0)               otherwise, or when thresholds are unavailable.

    Defensive: ``None`` thresholds (real ATR fallback) classify as skip so the
    watchdog never crashes on an incomplete threshold dict (A-2).
    """
    if eff_stop is None or eff_take is None:
        return ("skip", 0)
    if pnl_pct <= eff_stop:
        return ("stop", qty)
    if pnl_pct >= eff_take and not took_profit_today:
        return ("take", max(1, qty // 2))
    return ("skip", 0)


def _notify_and_audit(kind: str, ticker: str, pnl_pct: float, threshold: float, qty: int) -> None:
    """Emit a Telegram briefing (best-effort) + an audit_log entry for an exit."""
    category = "자동 손절" if kind == "stop" else "자동 익절"
    message = (
        f"{ticker} {category} 실행 — pnl {pnl_pct:+.2f}% (임계 {threshold:+.2f}%), 매도 {qty}주"
    )
    # Telegram failures must not abort the exit/sweep (REQ-033-6).
    try:
        system_briefing(category, message)
    except Exception:
        LOG.warning("position_watchdog: telegram briefing failed for %s", ticker)

    audit(
        "POSITION_WATCHDOG_EXIT",
        actor="position_watchdog",
        details={
            "kind": kind,
            "ticker": ticker,
            "pnl_pct": pnl_pct,
            "threshold": threshold,
            "qty": qty,
        },
    )


# @MX:ANCHOR: SPEC-TRADING-033 REQ-033-1 entry-point for the position watchdog
# @MX:REASON: fan_in >= 3 (scheduler cron, manual CLI smoke test, future reuse)
# @MX:SPEC: SPEC-TRADING-033
def poll_position_watchdog() -> dict[str, Any]:
    """Single watchdog poll. Returns a metrics dict for observability.

    metrics: {"checked","stop_exits","take_exits","skipped","errors"}.
    """
    metrics = {
        "checked": 0,
        "stop_exits": 0,
        "take_exits": 0,
        "skipped": 0,
        "errors": 0,
    }

    try:
        client = _build_client()
        holdings = _read_holdings(client)
    except Exception:
        LOG.exception("position_watchdog: could not read balance — skipping poll")
        metrics["errors"] += 1
        return metrics

    for holding in holdings:
        metrics["checked"] += 1
        ticker = holding.get("ticker", "")
        try:
            qty = int(holding.get("qty", 0) or 0)
            pnl_pct = float(holding.get("pnl_pct", 0) or 0)

            th = get_dynamic_thresholds(ticker)
            eff_stop = th.get("effective_stop")
            eff_take = th.get("effective_take")

            action, sell_qty = classify_holding(
                pnl_pct=pnl_pct,
                eff_stop=eff_stop,
                eff_take=eff_take,
                took_profit_today=_took_profit_today(ticker),
                qty=qty,
            )

            # classify returns a non-skip action only when both thresholds are
            # present, so this narrows eff_stop/eff_take away from None.
            if action == "skip" or sell_qty <= 0 or eff_stop is None or eff_take is None:
                metrics["skipped"] += 1
                continue

            # Double-sell guard (Q-4): re-confirm live qty just before exiting.
            live_qty = _confirm_qty(client, ticker)
            if live_qty <= 0:
                LOG.info("position_watchdog: %s already flat — skip exit", ticker)
                metrics["skipped"] += 1
                continue
            sell_qty = min(sell_qty, live_qty) if action == "take" else live_qty

            threshold = float(eff_stop if action == "stop" else eff_take)

            # Direct kis_sell — bypasses the orchestrator cycle halt gate and
            # the daily-order-count pre-check limit (REQ-033-4). Real market
            # rejection (lower-limit/locked) raises and is absorbed below.
            kis_sell(
                client,
                ticker=ticker,
                qty=sell_qty,
                order_type="market",
                persona_decision_id=None,
            )

            if action == "take":
                _mark_took_profit(ticker)
                metrics["take_exits"] += 1
            else:
                metrics["stop_exits"] += 1

            _notify_and_audit(action, ticker, pnl_pct, threshold, sell_qty)
            LOG.info(
                "position_watchdog %s ticker=%s pnl=%.2f threshold=%.2f qty=%d",
                action,
                ticker,
                pnl_pct,
                threshold,
                sell_qty,
            )
        except Exception:
            # Per-ticker isolation (REQ-033-6a): one bad ticker (quote/threshold/
            # order/market rejection) must not abort the sweep.
            LOG.exception("position_watchdog: error processing %s", ticker)
            metrics["errors"] += 1

    if metrics["checked"]:
        LOG.info("position_watchdog poll: %s", json.dumps(metrics))
    return metrics
