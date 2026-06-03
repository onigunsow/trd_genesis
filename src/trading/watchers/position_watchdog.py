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
import math
from datetime import date, datetime
from typing import Any

import pytz

from trading.alerts.telegram import system_briefing
from trading.config import (
    RISK_CONCENTRATION_CAP_LATE_CYCLE_PCT,
    RISK_CONCENTRATION_CAP_PCT,
    STAGNATION_DAYS,
    STAGNATION_PNL_BAND_PCT,
    STAGNATION_RSI_HIGH,
    STAGNATION_RSI_LOW,
    STAGNATION_TRIM_FRACTION,
)
from trading.db.session import audit, connection
from trading.kis.account import balance
from trading.kis.order import sell as kis_sell
from trading.strategy.volatility.rsi import compute_rsi
from trading.strategy.volatility.thresholds import get_dynamic_thresholds

LOG = logging.getLogger(__name__)
KST = pytz.timezone("Asia/Seoul")

# SPEC-TRADING-038 REQ-038-2: the per-ticker take-profit guard is persisted to the
# ``position_action_markers`` table (action='take_profit') instead of an in-memory
# dict. The DB is the single source of truth, so the "already took profit today"
# marker survives a container restart and cannot drive a double half-sell. The
# marker is keyed by the KST trading day, so a new day is naturally a fresh state.
_TAKE_PROFIT_ACTION = "take_profit"
# SPEC-TRADING-040 M2 (REQ-040-2b): the concentration-trim idempotency marker
# reuses ``position_action_markers`` with a new action value — no migration
# (the table's ``action`` column is free-form TEXT, SPEC Q-7).
_TRIM_ACTION = "trim"


def _today_kst() -> date:
    """Current KST calendar date (test seam — patch to fix 'today')."""
    return datetime.now(KST).date()


def _action_done_today(ticker: str, action: str) -> bool:
    """True if (KST day, ticker, action) already has a marker row.

    Backed by ``position_action_markers`` so the guard survives a restart. A read
    failure is propagated to the caller's per-ticker try/except (isolation).
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM position_action_markers "
            "WHERE trading_day = %s AND ticker = %s AND action = %s LIMIT 1",
            (_today_kst(), ticker, action),
        )
        return cur.fetchone() is not None


def _mark_action(ticker: str, action: str) -> None:
    """Mark (KST day, ticker, action) idempotently (``ON CONFLICT DO NOTHING``)."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO position_action_markers (trading_day, ticker, action) "
            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (_today_kst(), ticker, action),
        )


def _took_profit_today(ticker: str) -> bool:
    """True if `ticker` was already take-profit-exited on the current KST day.

    SPEC-TRADING-038 REQ-038-2 (signature preserved): thin wrapper over
    ``_action_done_today`` with the take-profit action.
    """
    return _action_done_today(ticker, _TAKE_PROFIT_ACTION)


def _mark_took_profit(ticker: str) -> None:
    """Mark `ticker` as take-profit-exited for the current KST day (preserved)."""
    _mark_action(ticker, _TAKE_PROFIT_ACTION)


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


# @MX:NOTE: SPEC-TRADING-040 M2 — pure concentration-trim helper. Trims an
# over-weight ticker back to ~cap. Risk/rebalance-motivated → EV-exempt (ADR-1),
# so it is NOT gated by the backtest expectancy check that profit-taking is.
# @MX:SPEC: SPEC-TRADING-040
def classify_concentration(
    eval_amount: int,
    qty: int,
    total_portfolio_value: int,
    cap_pct: float,
) -> tuple[str, int]:
    """Classify a holding's concentration into a trim action + qty to sell.

    Returns ``("trim", n)`` when the holding's value exceeds ``cap_pct`` of the
    portfolio, where ``n`` is the share count that brings it back to ~cap; else
    ``("skip", 0)``.

    The trim qty is ``ceil(excess_value / value_per_share)`` so the post-trim
    value lands at or just below the cap. Clamped to ``qty`` so the watchdog can
    never over-sell or short (REQ-040-2d). Defensive: a non-positive portfolio
    value or qty classifies as skip (never crashes / never divides by zero).
    """
    if total_portfolio_value <= 0 or qty <= 0 or eval_amount <= 0:
        return ("skip", 0)
    cap_value = total_portfolio_value * cap_pct
    if eval_amount <= cap_value:
        return ("skip", 0)
    value_per_share = eval_amount / qty
    if value_per_share <= 0:
        return ("skip", 0)
    excess_value = eval_amount - cap_value
    trim_qty = math.ceil(excess_value / value_per_share)
    trim_qty = max(1, min(trim_qty, qty))  # over-sell clamp + at least 1 share
    return ("trim", trim_qty)


# @MX:NOTE: SPEC-TRADING-040 M1c — pure stagnation-rotation predicate. A long-held
# flat-P&L, neutral-RSI position is rotated out (risk/rebalance → EV-exempt).
# Defensive on missing data (holding_days / rsi None → not stagnant).
# @MX:SPEC: SPEC-TRADING-040
def is_stagnant(
    holding_days: int | None,
    pnl_pct: float,
    rsi: float | None,
) -> bool:
    """True when a holding is stagnant: held long, flat P&L, neutral RSI.

    Conditions (all required): held ``STAGNATION_DAYS``+ days, ``|pnl| <``
    ``STAGNATION_PNL_BAND_PCT``, and RSI within the neutral band
    [``STAGNATION_RSI_LOW``, ``STAGNATION_RSI_HIGH``]. Missing holding_days or
    RSI → not stagnant (the extreme stop/take rules still apply).
    """
    if holding_days is None or rsi is None:
        return False
    if holding_days < STAGNATION_DAYS:
        return False
    if abs(pnl_pct) >= STAGNATION_PNL_BAND_PCT:
        return False
    return STAGNATION_RSI_LOW <= rsi <= STAGNATION_RSI_HIGH


def _portfolio_value(client: Any) -> int:
    """Total portfolio value used as the concentration-cap denominator (seam).

    Uses ``invest_basis`` (cash + stock eval, the same 100%-summing denominator
    the balance % uses, REQ-029-10); falls back to ``total_assets``.
    """
    bal = balance(client)
    return int(bal.get("invest_basis") or bal.get("total_assets") or 0)


# @MX:NOTE: SPEC-TRADING-040 M1c — holding_days source. Days since the FIRST buy
# fill for a ticker (from the orders table). No buy fills -> None (defensive
# skip, so is_stagnant cannot fire on a holding we cannot date).
# @MX:SPEC: SPEC-TRADING-040
def _holding_days(ticker: str) -> int | None:
    """Days held = today (KST) - MIN(first buy fill date) for `ticker`.

    Uses ``filled_at`` when present, else ``ts`` (paper synthetic fills set both).
    Returns None when the ticker has no filled/partial buy on record, or on a DB
    error — both absorbed by the caller's per-ticker isolation.
    """
    sql = """
        SELECT MIN(COALESCE(filled_at, ts)) AS first_buy
          FROM orders
         WHERE ticker = %s
           AND side = 'buy'
           AND status IN ('filled','partial')
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (ticker,))
            row = cur.fetchone()
    except Exception as e:
        LOG.warning("position_watchdog: holding_days DB error for %s: %s", ticker, e)
        return None
    first_buy = row.get("first_buy") if row else None
    if first_buy is None:
        return None
    first_date = first_buy.date() if isinstance(first_buy, datetime) else first_buy
    return (_today_kst() - first_date).days


# @MX:NOTE: SPEC-TRADING-040 M1c — RSI source. Reuses the shared compute_rsi
# (extracted from screener.daily_screen) — NOT reimplemented. None on
# unavailable/insufficient data -> defensive skip.
# @MX:SPEC: SPEC-TRADING-040
def _ticker_rsi(ticker: str) -> float | None:
    """Current RSI(14) for `ticker` via the shared ``compute_rsi`` (or None)."""
    try:
        return compute_rsi(ticker)
    except Exception as e:
        LOG.warning("position_watchdog: rsi compute error for %s: %s", ticker, e)
        return None


def _late_cycle_active() -> bool:
    """True when SPEC-036 late-cycle defence is active (seam).

    A failure to read system_state is treated as inactive (conservative — the
    normal cap applies) and absorbed by the caller's per-ticker isolation.
    """
    try:
        from trading.db.session import get_system_state

        return bool(get_system_state().get("late_cycle_defense_active", False))
    except Exception:
        LOG.warning("position_watchdog: late-cycle state read failed — assume inactive")
        return False


_EXIT_CATEGORY = {
    "stop": "자동 손절",
    "take": "자동 익절",
    "trim": "집중 트림",       # SPEC-040 M2
    "rotate": "정체 로테이션",  # SPEC-040 M1c
}


def _notify_and_audit(kind: str, ticker: str, pnl_pct: float, threshold: float, qty: int) -> None:
    """Emit a Telegram briefing (best-effort) + an audit_log entry for an exit."""
    category = _EXIT_CATEGORY.get(kind, "자동 매도")
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


# @MX:WARN: SPEC-TRADING-040 — direct gate-free SELL. Like the stop/take exit it
# bypasses the orchestrator halt gate + daily-count pre-check (risk-reducing
# exits must never be blocked by buy-side gates). Re-confirms live qty for an
# over-sell clamp (REQ-040-2d) and marks the day so it cannot trim twice.
# @MX:REASON: a bug here could over-sell (short) or repeat-trim a position.
# @MX:SPEC: SPEC-TRADING-040
def _execute_trim(
    client: Any,
    ticker: str,
    trim_qty: int,
    pnl_pct: float,
    cap_pct: float,
    *,
    kind: str = "trim",
) -> bool:
    """Execute one partial trim (concentration ``trim`` or stagnation ``rotate``).

    Re-confirms live qty (double-sell + over-sell guard), clamps the trim qty to
    it, sells directly via ``kis_sell`` (market), marks the SHARED action='trim'
    guard for the KST day (so concentration and stagnation cannot both sell the
    same ticker today), and audits with the given ``kind``. A zero live qty
    (already flat) is a no-op skip.
    """
    live_qty = _confirm_qty(client, ticker)
    if live_qty <= 0:
        LOG.info("position_watchdog: %s already flat — skip %s", ticker, kind)
        return False
    sell_qty = min(trim_qty, live_qty)  # over-sell clamp (never short)

    kis_sell(
        client,
        ticker=ticker,
        qty=sell_qty,
        order_type="market",
        persona_decision_id=None,
    )
    _mark_action(ticker, _TRIM_ACTION)  # shared marker — one trim per ticker/day
    # threshold field reused to carry the cap% that drove a concentration trim
    # (0.0 for a stagnation rotation, which is not cap-driven).
    _notify_and_audit(kind, ticker, pnl_pct, cap_pct * 100.0, sell_qty)
    LOG.info(
        "position_watchdog %s ticker=%s pnl=%.2f cap=%.1f%% qty=%d",
        kind, ticker, pnl_pct, cap_pct * 100.0, sell_qty,
    )
    return True


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
        "trim_exits": 0,     # SPEC-040 M2 concentration trim
        "rotate_exits": 0,   # SPEC-040 M1c stagnation rotation trim
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

    # SPEC-040 M2/M2c: concentration-cap denominator + late-cycle tightening.
    # Read once per poll. A failure here must not abort the whole sweep, so the
    # cap is disabled (value 0 → classify_concentration always skips) on error.
    try:
        portfolio_value = _portfolio_value(client)
    except Exception:
        LOG.warning("position_watchdog: portfolio value read failed — trim disabled this poll")
        portfolio_value = 0
    cap_pct = (
        RISK_CONCENTRATION_CAP_LATE_CYCLE_PCT
        if _late_cycle_active()
        else RISK_CONCENTRATION_CAP_PCT
    )

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

            # SPEC-040 M2 + M1c: when the extreme stop/take rules say skip, the
            # holding may still warrant a RISK-motivated trim (EV-exempt, ADR-1),
            # code-enforced here because the decision persona effectively never
            # sells. Both trims share the SAME idempotent action='trim' marker, so
            # at most ONE trim fires per ticker per KST day (concentration is
            # evaluated first; if it does not trim, stagnation rotation is tried).
            if action == "skip" and not _action_done_today(ticker, _TRIM_ACTION):
                eval_amount = int(holding.get("eval_amount", 0) or 0)
                c_action, trim_qty = classify_concentration(
                    eval_amount=eval_amount,
                    qty=qty,
                    total_portfolio_value=portfolio_value,
                    cap_pct=cap_pct,
                )
                if c_action == "trim" and trim_qty > 0:
                    if _execute_trim(client, ticker, trim_qty, pnl_pct, cap_pct, kind="trim"):
                        metrics["trim_exits"] += 1
                    else:
                        metrics["skipped"] += 1
                    continue

                # M1c stagnation rotation: a long-held, flat-P&L, neutral-RSI
                # position is rotated out (partial trim). holding_days + RSI are
                # fetched lazily so a non-stagnant holding pays no DB cost.
                if is_stagnant(
                    holding_days=_holding_days(ticker),
                    pnl_pct=pnl_pct,
                    rsi=_ticker_rsi(ticker),
                ):
                    rotate_qty = max(1, int(qty * STAGNATION_TRIM_FRACTION))
                    if _execute_trim(client, ticker, rotate_qty, pnl_pct, 0.0, kind="rotate"):
                        metrics["rotate_exits"] += 1
                    else:
                        metrics["skipped"] += 1
                    continue

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
