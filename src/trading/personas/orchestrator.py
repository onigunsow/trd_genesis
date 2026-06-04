"""Persona orchestration — sequencing + telegram briefing + paper auto-execute.

Pre-market 07:30 cycle:
    Micro → Decision → Risk → (paper auto-execute on APPROVE)

Intraday cycle (09:30, 11:00, 13:30, 14:30):
    Decision (micro cache) → Risk → execute

Event-trigger cycle (price ±3%, new disclosure, VIX spike):
    Decision (with trigger context) → Risk → execute

SPEC-009: Tool-calling integration + Reflection Loop.
- REQ-PTOOL-02-3~6: Per-persona tool sets via registry.
- REQ-REFL-03-1~10: Risk REJECT Reflection Loop (max 2 rounds, 30s timeout).
"""

from __future__ import annotations

import functools
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from trading.alerts import telegram as tg
from trading.config import get_settings
from trading.data.ticker_names import ticker_name
from trading.db.session import NOW, audit, connection, get_system_state, update_system_state
from trading.kis.account import balance
from trading.kis.client import KisClient
from trading.kis.market import OVERHEAT_STAT_CLS
from trading.kis.order import buy as kis_buy
from trading.kis.order import sell as kis_sell
from trading.models.router import resolve_model
from trading.personas import context as ctx
from trading.personas import decision as decision_persona
from trading.personas import macro as macro_persona
from trading.personas import micro as micro_persona
from trading.personas import risk as risk_persona
from trading.personas.portfolio_gate import _apply_portfolio_adjustment
from trading.risk import circuit_breaker
from trading.risk.blocked_cache import get_blocked_tickers, record_blocked_by_safety
from trading.risk.limits import check_pre_order, record_breach
from trading.risk.market_safety import OVERHEAT_SIZE_FACTOR, check_pre_order_safety
from trading.screener.daily_screen import load_screened_tickers
from trading.scripts.refresh_market_data import (
    RECENT_OHLCV_DAYS,
    expand_universe_for_tickers,
)
from trading.strategy.car.filter import evaluate_event
from trading.strategy.car.models import FilterDecision
from trading.tools.registry import get_tools_for_persona

LOG = logging.getLogger(__name__)

CycleKind = Literal["pre_market", "intraday", "event", "weekly", "manual"]


# @MX:NOTE: SPEC-029 v0.2.0 REQ-029-10 — both trade-briefing percentages share a
# single invest_basis denominator (cash_d2 + stock_eval) so they sum to 100%.
# Using KIS tot_evlu_amt as the denominator (as v0.1.0 did) produced sums > 100%
# because tot_evlu_amt != dnca_tot_amt + scts_evlu_amt. The zero-basis guard
# avoids ZeroDivisionError on an empty account (AC-029-15).
def compute_balance_pcts(bal: dict[str, Any]) -> tuple[float, float]:
    """Return (cash_pct, equity_pct) on the invest_basis denominator."""
    basis = bal.get("invest_basis")
    if not basis:
        basis = int(bal.get("cash_d2", 0) or 0) + int(bal.get("stock_eval", 0) or 0)
    if basis <= 0:
        return (0.0, 0.0)
    cash_pct = int(bal.get("cash_d2", 0) or 0) / basis * 100
    equity_pct = int(bal.get("stock_eval", 0) or 0) / basis * 100
    return (cash_pct, equity_pct)


def _ticker_label(ticker: str) -> str:
    """SPEC-TRADING-041 REQ-041-1: '코드 이름' display label for system alerts.

    Presentation-layer name enrichment so the 한도 위반 차단 / 단기과열 비중 축소
    alerts read consistently with trade alerts. Degrades to the bare code when
    ``ticker_name`` returns None/empty or raises (REQ-041-4a) — never renders
    the literal "None".
    """
    try:
        name = ticker_name(ticker)
    except Exception:
        name = None
    return f"{ticker} {name}" if name else ticker


# ---------------------------------------------------------------------------
# SPEC-023: universe auto-expansion hook (micro -> decision)
# ---------------------------------------------------------------------------


def _has_recent_ohlcv(ticker: str) -> bool:
    """REQ-023-1 (b): True when the cache has OHLCV within the recency window.

    Wraps the cache-layer helper so it can be monkeypatched in unit tests
    without dragging the real Postgres connection into orchestrator tests.
    Defensive: when the DB lookup itself fails (test envs without Postgres,
    DB outage, etc.), returns True so we do NOT spuriously trigger an
    expansion call that would also fail. The real cycles call this from a
    process where the DB is reachable; tests that exercise the auto-expansion
    semantics monkeypatch this function directly.
    """
    from datetime import date as _date
    from datetime import timedelta as _td

    from trading.scripts.refresh_market_data import _get_latest_ohlcv_ts

    try:
        last_ts = _get_latest_ohlcv_ts(ticker)
    except Exception as exc:
        LOG.debug("_has_recent_ohlcv cache lookup failed for %s: %s", ticker, exc)
        return True  # fail closed — skip expansion rather than risk a crash
    if last_ts is None:
        return False
    return last_ts >= _date.today() - _td(days=RECENT_OHLCV_DAYS)


# @MX:NOTE: SPEC-TRADING-026 — split the blocked dict by stat_cls so the
# persona layer can hard-exclude genuine risk states (51~54 / unknown) while
# keeping 단기과열(55) as a cautioned, de-weighted candidate. Used for every
# blocked-aware exclusion path (watchlist + decision candidate filter).
# @MX:SPEC: SPEC-TRADING-026
def _split_blocked(
    blocked: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Partition a blocked-ticker dict into ``(hard_blocked, overheated)``.

    Entries with ``stat_cls == "55"`` (단기과열) are overheated (soft); every
    other entry — including those missing ``stat_cls`` (e.g. intraday
    safety-recorded blocks) — is hard-blocked (conservative default).
    """
    hard: dict[str, Any] = {}
    over: dict[str, Any] = {}
    for ticker, info in (blocked or {}).items():
        if isinstance(info, dict) and info.get("stat_cls") == OVERHEAT_STAT_CLS:
            over[ticker] = info
        else:
            hard[ticker] = info
    return hard, over


def _filter_and_expand_candidates(
    candidate_tickers: list[str],
    *,
    cycle_kind: str,
    blocked_tickers: dict[str, Any] | None = None,
) -> tuple[list[str], dict[str, Any] | None]:
    """SPEC-023 orchestrator hook — runs between micro and decision.

    1. REQ-023-1: identify candidates whose OHLCV is stale or missing.
    2. REQ-023-1 (f) + R-1: auto-expansion runs BEFORE the blocked filter so
       that blocked tickers still get their data fetched (their block status
       may clear in a future cycle).
    3. REQ-023-3: drop candidates whose expansion fetch failed.
    4. SPEC-018 blocked filter: drop blocked tickers AFTER expansion.

    Returns (filtered_candidates, expansion_metrics_or_None).
    """
    blocked_tickers = blocked_tickers or {}
    to_expand = [t for t in candidate_tickers if not _has_recent_ohlcv(t)]

    expansion_metrics: dict[str, Any] | None = None
    if to_expand:
        try:
            expansion_metrics = expand_universe_for_tickers(
                to_expand, cycle_kind=cycle_kind,
            )
        except Exception as exc:
            LOG.warning(
                "auto-expansion hook crashed (proceeding without fetch): %s", exc
            )
            expansion_metrics = None

    successful: set[str] = set()
    if expansion_metrics is not None:
        successful = set(expansion_metrics.get("successful_tickers") or [])

    # Drop tickers that needed expansion but failed.
    filtered = [
        t
        for t in candidate_tickers
        if (t not in to_expand) or (t in successful)
    ]
    # SPEC-018 blocked filter (applied AFTER expansion — R-1).
    filtered = [t for t in filtered if t not in blocked_tickers]
    return filtered, expansion_metrics


def _count_holds_today(ticker: str) -> int:
    """Count HOLD verdicts for a ticker today."""
    sql = """
        SELECT COUNT(*) FROM persona_runs pr
        JOIN persona_decisions pd ON pd.decision_run_id = pr.id
        WHERE pr.persona_name = 'risk'
          AND pr.ts::date = CURRENT_DATE
          AND pr.response_json->>'verdict' = 'HOLD'
          AND pd.ticker = %s
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (ticker,))
            return cur.fetchone()[0]
    except Exception:
        return 0


def _get_hold_feedback_today(tickers: list[str]) -> list[dict]:
    """Get Risk HOLD rationale for tickers from today."""
    if not tickers:
        return []
    sql = """
        SELECT pd.ticker, pr.response_json->>'rationale' as rationale,
               COUNT(*) OVER (PARTITION BY pd.ticker) as hold_count
        FROM persona_runs pr
        JOIN persona_decisions pd ON pd.decision_run_id = pr.id
        WHERE pr.persona_name = 'risk'
          AND pr.ts::date = CURRENT_DATE
          AND pr.response_json->>'verdict' = 'HOLD'
          AND pd.ticker = ANY(%s)
        ORDER BY pr.ts DESC
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (tickers,))
            rows = cur.fetchall()
            # Deduplicate by ticker, keep latest
            seen: set[str] = set()
            result: list[dict] = []
            for row in rows:
                if row["ticker"] not in seen:
                    seen.add(row["ticker"])
                    result.append({
                        "ticker": row["ticker"],
                        "rationale": (row["rationale"] or "")[:300],
                        "count": row["hold_count"],
                    })
            return result
    except Exception:
        return []

# SPEC-009 REQ-REFL-03-2: Maximum reflection rounds (Decision re-invoke + Risk re-evaluate).
MAX_REFLECTION_ROUNDS: int = 2
# SPEC-009 REQ-REFL-03-10: Combined timeout per reflection round (seconds).
REFLECTION_ROUND_TIMEOUT: float = 45.0


def _summarize_persona(name: str, response_json: dict[str, Any] | None, max_lines: int = 5) -> str:
    """Build a 3-5 line briefing summary from a persona JSON response."""
    if not response_json:
        return "(응답 파싱 실패 또는 비어있음)"
    if name == "macro":
        return (
            f"체제: {response_json.get('regime','?')} / "
            f"위험선호: {response_json.get('risk_appetite','?')}\n"
            f"{response_json.get('weekly_outlook','')[:300]}"
        )
    if name == "micro":
        c = response_json.get("candidates", {})
        buy = c.get("buy", []) or []
        sell = c.get("sell", []) or []
        hold = c.get("hold", []) or []
        line = f"매수 {len(buy)} / 매도 {len(sell)} / 관망 {len(hold)}"
        if buy:
            line += "\n매수 후보: " + ", ".join(b.get("ticker", "") for b in buy[:3])
        # SPEC-027: surface the market-tone summary the persona already produced.
        tone = (response_json.get("summary") or "").strip()
        if tone:
            line += f"\n톤: {tone[:150]}"
        return line
    if name == "decision":
        sigs = response_json.get("signals", []) or []
        # SPEC-027: the decision persona writes a 1-2 line intent summary even
        # when it proposes no trade — surface it so "왜 매매 안 했는지" is visible.
        intent = (response_json.get("summary") or "").strip()
        if not sigs:
            return "신규 시그널 없음" + (f" — {intent[:150]}" if intent else "")
        body = "\n".join(
            f"- {s.get('ticker','')} {s.get('side','?')} {s.get('qty',0)}주: "
            f"{(s.get('rationale','') or '')[:80]}"
            for s in sigs[:3]
        )
        return body + (f"\n의도: {intent[:120]}" if intent else "")
    if name == "risk":
        return (
            f"verdict: {response_json.get('verdict','?')}\n"
            f"{(response_json.get('rationale','') or '')[:300]}"
        )
    return str(response_json)[:300]


def _gather_assets() -> dict[str, Any]:
    """Snapshot KIS balance for the active mode."""
    s = get_settings()
    client = KisClient(s.trading_mode)
    return balance(client)


# SPEC-TRADING-037 REQ-037-5: a halt trip whose breaches are the daily-order
# COUNT limit (and nothing worse) lets risk-reducing SELLs through. The literal
# tokens mirror risk.limits (breach prefix "daily_count") and risk.auto_resume
# (reason "pre-order limit breach", loss prefix "daily_loss", manual prefix
# "manual"). Kept here so the persona sell path and auto_resume stay consistent.
_AUTO_LIMIT_REASON = "pre-order limit breach"
_DAILY_LOSS_PREFIX = "daily_loss"
_DAILY_COUNT_PREFIX = "daily_count"
_MANUAL_PREFIX = "manual"


def _count_halt_allows_sells(active_trip: dict[str, Any] | None) -> bool:
    """True iff a halt is a benign daily-order-COUNT trip that may pass SELLs.

    Pure (no I/O). ``active_trip`` is the ``details`` dict of the active
    ``CIRCUIT_BREAKER_TRIP`` audit row (see ``auto_resume._fetch_active_trip``).

    fail-safe: blocks on manual halt, daily-LOSS halt (even mixed with a count
    breach), any non-auto-limit reason, and any malformed / missing trip. Only an
    automatic limit breach whose breaches contain a ``daily_count`` token AND no
    ``daily_loss`` token returns True (REQ-037-5 b, S-3).
    """
    if not active_trip:
        return False
    reason = str(active_trip.get("reason", ""))
    if reason.startswith(_MANUAL_PREFIX):
        return False
    if reason != _AUTO_LIMIT_REASON:
        return False
    breaches = active_trip.get("breaches")
    if not isinstance(breaches, list) or not breaches:
        return False
    # Any real-loss breach -> capital-preservation hard gate, block even sells.
    if any(str(b).startswith(_DAILY_LOSS_PREFIX) for b in breaches):
        return False
    # At least one breach must be the order-count limit (else it is some other
    # benign limit we do not extend the sell bypass to — conservative).
    return any(str(b).startswith(_DAILY_COUNT_PREFIX) for b in breaches)


def _partition_signals_for_count_halt(
    signals: list[dict[str, Any]],
    sig_ids: list[int],
    *,
    holdings: list[dict[str, Any]],
    active_trip: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[int]]:
    """Keep only risk-reducing SELLs on existing holdings during a count-halt.

    Returns ``([], [])`` (full block) unless ``_count_halt_allows_sells`` is True,
    in which case it keeps SELL signals whose ticker is currently held (a sell of
    a non-held ticker is a short, not risk-reducing). BUYs are always dropped
    (REQ-037-5 a). Pairs each kept signal with its ``sig_ids`` entry.
    """
    if not _count_halt_allows_sells(active_trip):
        return ([], [])
    held = {str(h.get("ticker")) for h in holdings or []}
    kept_sig: list[dict[str, Any]] = []
    kept_ids: list[int] = []
    for sig, sid in zip(signals, sig_ids, strict=False):
        if str(sig.get("side", "")).lower() != "sell":
            continue
        if str(sig.get("ticker", "")) not in held:
            continue
        kept_sig.append(sig)
        kept_ids.append(sid)
    return (kept_sig, kept_ids)


def _maybe_count_halt_bypass(
    signals: list[dict[str, Any]],
    sig_ids: list[int],
    *,
    holdings: list[dict[str, Any]],
    cycle_kind: str,
) -> tuple[list[dict[str, Any]], list[int]]:
    """Halt-gate body shared by the cycle entry-points (REQ-037-5).

    Reads the active trip (audit_log, defensive — reuses ``auto_resume``), then:
      - count-halt + risk-reducing SELLs present -> log + Telegram + audit the
        bypass and return ONLY those SELLs (BUYs dropped) so the caller's
        risk/execute loop runs for them;
      - otherwise -> SPEC-031 throttled "매매 정지" briefing + log, and return
        ``([], [])`` so the caller skips the cycle as before.

    Double-sell guard (REQ-037-5 c): the SPEC-033 watchdog also exits on a stop;
    the per-signal ``_execute_signal`` re-reads live qty before ordering, so a
    same-ticker double exit cannot over-sell.
    """
    from trading.risk.auto_resume import _fetch_active_trip

    active_trip = _fetch_active_trip()
    bypass_sig, bypass_ids = _partition_signals_for_count_halt(
        signals, sig_ids, holdings=holdings, active_trip=active_trip
    )
    if bypass_ids:
        tickers = ", ".join(f"{s.get('ticker')} {s.get('qty')}주" for s in bypass_sig)
        LOG.info(
            "COUNT-HALT BYPASS SELL — %s cycle: %d risk-reducing sell(s) proceed (%s)",
            cycle_kind, len(bypass_ids), tickers,
        )
        try:
            tg.system_briefing(
                "COUNT-HALT BYPASS SELL",
                f"일일 주문수 정지 중 위험 축소 매도 진행: {tickers}",
            )
        except Exception:
            LOG.warning("count-halt bypass telegram briefing failed")
        audit("COUNT_HALT_BYPASS_SELL", actor="orchestrator", details={
            "cycle_kind": cycle_kind,
            "sells": [{"ticker": s.get("ticker"), "qty": s.get("qty")} for s in bypass_sig],
        })
        return (bypass_sig, bypass_ids)

    # Not eligible (loss / manual / unknown halt, or no risk-reducing sells):
    # SPEC-TRADING-031 REQ-031-1/2/4 — throttled "매매 정지" briefing + log.
    sent = circuit_breaker.maybe_notify_halt()
    LOG.info(
        "halt_state=true — skipping %s cycle (telegram briefing %s)",
        cycle_kind,
        "sent" if sent else "throttled",
    )
    return ([], [])


# @MX:ANCHOR: Cycle-wide entry for micro persona context — fan_in >= 3
# (run_pre_market_cycle, run_intraday_cycle, run_event_trigger_cycle reuse the
# resulting watchlist semantics indirectly via Decision input).
# @MX:SPEC: SPEC-TRADING-018/REQ-018-1, SPEC-TRADING-020/REQ-020-3
# @MX:REASON: Universe construction is the single chokepoint that decides
# whether the trading day produces signals. Bug on 2026-05-11 was here.
def _build_micro_input(today: str, macro_summary: str | None) -> dict[str, Any]:
    """Build micro persona context from cached data.

    SPEC-020 REQ-020-3 semantics (revised, 2026-05-12):

    1. If screened_tickers is non-empty -> watchlist = screened (filtered).
       DEFAULT_WATCHLIST is NOT merged in (no hardcoded bias).
    2. If screened_tickers is empty (cold-start) -> watchlist = DEFAULT
       (filtered). This automatically satisfies the SPEC-018 REQ-018-4
       fallback intent: when DEFAULT is fully blocked on cold-start, the
       resulting watchlist is empty and downstream code handles it.
    3. Blocked tickers (단기과열 etc.) are always filtered out (REQ-018-1).
    4. Forward the blocked-ticker dict to ``assemble_micro_input`` so the
       prompt layer can render the exclusion block (REQ-018-2/3).
    """
    screened = load_screened_tickers()
    blocked_cache = get_blocked_tickers()
    blocked_tickers = blocked_cache.get("blocked", {}) or {}
    # SPEC-026: only HARD blocks (51~54 / unknown) are excluded from the
    # watchlist. 단기과열(55) is kept so the micro persona can still consider it
    # (the prompt marks it as a reduce-weight caution; execution then size-caps
    # and forces a limit order).
    hard_blocked, _overheated = _split_blocked(blocked_tickers)
    hard_set = set(hard_blocked.keys())

    # SPEC-020 REQ-020-3: screened-first; DEFAULT only as cold-start fallback.
    base_universe: list[str] = list(screened) if screened else list(ctx.DEFAULT_WATCHLIST)
    expanded_watchlist = [t for t in base_universe if t not in hard_set]

    return ctx.assemble_micro_input(
        macro_summary=macro_summary,
        watchlist=expanded_watchlist,
        blocked_tickers=blocked_tickers,
    )


@dataclass
class CycleResult:
    cycle_kind: CycleKind
    macro_run_id: int | None = None
    micro_run_id: int | None = None
    decision_run_id: int | None = None
    risk_run_ids: list[int] = field(default_factory=list)
    decisions: list[int] = field(default_factory=list)
    executed_orders: list[int] = field(default_factory=list)
    rejected: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# SPEC-TRADING-027: consolidated per-cycle decision-chain briefing.
# ---------------------------------------------------------------------------

def _build_cycle_chain(
    cycle_kind: str,
    macro_json: dict[str, Any] | None,
    micro_json: dict[str, Any] | None,
    decision_json: dict[str, Any] | None,
    risk_json: dict[str, Any] | None,
    executed: int,
    rejected: int,
) -> str:
    """Build one consolidated decision-chain summary for a cycle.

    Compresses no-trade cycles to a few lines (leading with the decision's WHY);
    shows the full Macro -> Micro -> Decision -> Risk path + outcome when a trade
    is proposed. Surfaces each persona's own reasoning, not just verdicts.
    """
    sigs = (decision_json or {}).get("signals", []) or []
    traded = executed > 0 or bool(sigs)
    lines: list[str] = []

    if macro_json:
        lines.append(
            f"매크로: {macro_json.get('regime', '?')} / "
            f"위험선호 {macro_json.get('risk_appetite', '?')}"
        )

    if traded:
        if micro_json:
            lines.append("마이크로: " + _summarize_persona("micro", micro_json).replace("\n", " / "))
        if decision_json:
            lines.append("결정: " + _summarize_persona("decision", decision_json).replace("\n", " | "))
        if risk_json:
            lines.append("리스크: " + _summarize_persona("risk", risk_json).replace("\n", " "))
        lines.append(f"결과: 체결 {executed}건 / 거부 {rejected}건")
    else:
        dec = _summarize_persona("decision", decision_json) if decision_json else "결정 미실행"
        lines.append("결정: " + dec.replace("\n", " "))
        if micro_json:
            c = micro_json.get("candidates", {}) or {}
            lines.append(
                f"(마이크로 매수 {len(c.get('buy') or [])} / 관망 {len(c.get('hold') or [])})"
            )
    return "\n".join(lines)


def _fetch_persona_run(run_id: int | None) -> dict[str, Any] | None:
    """Fetch a persona run's response_json by id (for the cycle-chain briefing)."""
    if not run_id:
        return None
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT response_json FROM persona_runs WHERE id = %s", (run_id,))
            row = cur.fetchone()
            return row["response_json"] if row else None
    except Exception as e:
        LOG.warning("cycle chain: fetch persona_run %s failed: %s", run_id, e)
        return None


def _send_cycle_chain(res: CycleResult, cycle_kind: str) -> None:
    """Send the consolidated cycle-chain briefing (best-effort, never raises)."""
    try:
        chain = _build_cycle_chain(
            cycle_kind,
            _fetch_persona_run(res.macro_run_id),
            _fetch_persona_run(res.micro_run_id),
            _fetch_persona_run(res.decision_run_id),
            _fetch_persona_run(res.risk_run_ids[-1] if res.risk_run_ids else None),
            executed=len(res.executed_orders),
            rejected=len(res.rejected),
        )
        tg.cycle_briefing(cycle_kind, chain)
    except Exception as e:
        LOG.warning("cycle chain briefing failed: %s", e)


def _with_cycle_briefing(cycle_kind: str):
    """Decorator: after a cycle returns its CycleResult, emit one consolidated
    decision-chain briefing. Runs post-cycle and swallows errors so briefing can
    never affect the trading path."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any):
            res = fn(*args, **kwargs)
            if isinstance(res, CycleResult):
                _send_cycle_chain(res, cycle_kind)
            return res
        return wrapper
    return deco


def _maybe_enter_silent_mode(latest_signal_count: int) -> None:
    """REQ-FATIGUE-05-9: enter silent mode when 3 consecutive Decision returns no signal."""
    if latest_signal_count > 0:
        return
    sql = """
        SELECT id, response_json
          FROM persona_runs
         WHERE persona_name = 'decision' AND error IS NULL
         ORDER BY id DESC
         LIMIT 3
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = list(cur.fetchall())
    if len(rows) < 3:
        return
    all_empty = True
    for r in rows:
        rj = r["response_json"] or {}
        sigs = rj.get("signals") if isinstance(rj, dict) else None
        if sigs:
            all_empty = False
            break
    if all_empty:
        update_system_state(silent_mode=True, updated_by="orchestrator")
        audit("SILENT_MODE_ON", actor="orchestrator",
              details={"reason": "3 consecutive no-signal decisions"})


def _get_persona_tools(persona_name: str, state: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Return tool definitions for a persona if tool_calling_enabled, else None.

    REQ-COMPAT-04-1: Respects the tool_calling_enabled feature flag.
    """
    if not state.get("tool_calling_enabled"):
        return None
    tools = get_tools_for_persona(persona_name)
    return tools if tools else None


def _run_reflection_loop(
    *,
    original_signal: dict[str, Any],
    risk_response_json: dict[str, Any],
    dec_input: dict[str, Any],
    cycle_kind: str,
    decision_id: int,
    macro_run_id: int | None,
    micro_run_id: int | None,
    assets: dict[str, Any],
    cash_pct: float,
    macro_summary: str | None,
    micro_summary: str,
    today: str,
    state: dict[str, Any],
) -> tuple[str, dict[str, Any] | None, int | None]:
    """Execute Reflection Loop when Risk returns REJECT.

    REQ-REFL-03-1~10: Extract rejection feedback, re-invoke Decision with context,
    re-invoke Risk on revised signal. Max 2 rounds with 30s timeout per round.

    Returns:
        Tuple of (final_verdict, revised_signal_or_none, revised_risk_run_id_or_none).
    """
    rationale = risk_response_json.get("rationale", "")
    concerns = risk_response_json.get("concerns", [])

    decision_tools = _get_persona_tools("decision", state)
    risk_tools = _get_persona_tools("risk", state)

    for round_num in range(1, MAX_REFLECTION_ROUNDS + 1):
        round_start = time.time()

        # REQ-REFL-03-3: Build rejection_feedback context for Decision re-invoke
        rejection_feedback = {
            "round": round_num,
            "risk_verdict": "REJECT",
            "risk_rationale": rationale,
            "risk_concerns": concerns,
            "original_signal": original_signal,
            "instruction": (
                "위 거부 사유를 반영하여 시그널을 수정하거나, 철회(withdraw)하세요. "
                "새 시그널은 Risk가 제기한 모든 우려를 해소해야 합니다."
            ),
        }

        # Build Decision re-invoke input with feedback
        revised_dec_input = {**dec_input, "rejection_feedback": rejection_feedback}

        try:
            dec_res, revised_sig_ids = decision_persona.run(
                revised_dec_input,
                cycle_kind=cycle_kind,
                macro_run_id=macro_run_id,
                micro_run_id=micro_run_id,
                tools=decision_tools,
            )
        except Exception as e:
            LOG.exception("Reflection round %d Decision re-invoke failed: %s", round_num, e)
            _persist_reflection_round(
                cycle_kind=cycle_kind,
                original_decision_id=decision_id,
                round_number=round_num,
                risk_persona_run_id=None,
                risk_rationale=rationale,
                revised_decision_run_id=None,
                revised_risk_run_id=None,
                final_verdict="REJECT",
            )
            return "REJECT", None, None

        # Check timeout
        elapsed = time.time() - round_start
        if elapsed > REFLECTION_ROUND_TIMEOUT:
            LOG.warning("REFLECTION_TIMEOUT round=%d elapsed=%.1fs", round_num, elapsed)
            audit("REFLECTION_TIMEOUT", actor="orchestrator", details={
                "round": round_num, "decision_id": decision_id, "elapsed_s": elapsed,
            })
            tg.system_briefing(
                "Reflection Timeout",
                f"Round {round_num} timeout ({elapsed:.1f}s > {REFLECTION_ROUND_TIMEOUT}s). "
                f"원래 REJECT 유지.",
            )
            _persist_reflection_round(
                cycle_kind=cycle_kind,
                original_decision_id=decision_id,
                round_number=round_num,
                risk_persona_run_id=None,
                risk_rationale=rationale,
                revised_decision_run_id=dec_res.persona_run_id,
                revised_risk_run_id=None,
                final_verdict="REJECT",
            )
            return "REJECT", None, None

        # REQ-REFL-03-4: Check for withdrawal
        revised_json = dec_res.response_json or {}
        revised_signals = revised_json.get("signals", [])
        is_withdrawn = revised_json.get("withdraw", False) or not revised_signals

        if is_withdrawn:
            LOG.info("Reflection round %d: Decision withdrew signal", round_num)
            audit("REFLECTION_WITHDRAWN", actor="orchestrator", details={
                "round": round_num, "decision_id": decision_id,
            })
            _persist_reflection_round(
                cycle_kind=cycle_kind,
                original_decision_id=decision_id,
                round_number=round_num,
                risk_persona_run_id=None,
                risk_rationale=rationale,
                revised_decision_run_id=dec_res.persona_run_id,
                revised_risk_run_id=None,
                final_verdict="WITHDRAWN",
            )
            return "WITHDRAWN", None, None

        # Re-invoke Risk on revised signal (REQ-REFL-03-9: Risk is unaware of reflection)
        revised_sig = revised_signals[0] if revised_signals else original_signal
        rk_input = {
            "today": today,
            "decision_signals": [revised_sig],
            "assets": assets,
            "cash_pct": cash_pct,
            "daily_order_count": 0,
            "daily_pnl_pct": 0.0,
            "macro_summary": macro_summary or "(없음)",
            "micro_summary": micro_summary,
        }

        try:
            rk_res, review_id, revised_verdict = risk_persona.run(
                rk_input,
                decision_id=revised_sig_ids[0] if revised_sig_ids else decision_id,
                cycle_kind=cycle_kind,
                tools=risk_tools,
            )
        except Exception as e:
            LOG.exception("Reflection round %d Risk re-invoke failed: %s", round_num, e)
            _persist_reflection_round(
                cycle_kind=cycle_kind,
                original_decision_id=decision_id,
                round_number=round_num,
                risk_persona_run_id=None,
                risk_rationale=rationale,
                revised_decision_run_id=dec_res.persona_run_id,
                revised_risk_run_id=None,
                final_verdict="REJECT",
            )
            return "REJECT", None, None

        # Check timeout again after Risk call
        elapsed = time.time() - round_start
        if elapsed > REFLECTION_ROUND_TIMEOUT:
            LOG.warning("REFLECTION_TIMEOUT after Risk round=%d elapsed=%.1fs", round_num, elapsed)
            audit("REFLECTION_TIMEOUT", actor="orchestrator", details={
                "round": round_num, "decision_id": decision_id, "elapsed_s": elapsed,
            })
            _persist_reflection_round(
                cycle_kind=cycle_kind,
                original_decision_id=decision_id,
                round_number=round_num,
                risk_persona_run_id=rk_res.persona_run_id,
                risk_rationale=rationale,
                revised_decision_run_id=dec_res.persona_run_id,
                revised_risk_run_id=rk_res.persona_run_id,
                final_verdict="REJECT",
            )
            return "REJECT", None, None

        # REQ-REFL-03-8: Telegram briefing for reflection outcome
        tg.persona_briefing(
            persona=f"Risk (Reflection R{round_num}) -> {revised_verdict}",
            model="claude-sonnet-4-6",
            summary=f"[Risk -> REJECT -> Reflection Round {round_num} -> {revised_verdict}]",
            input_tokens=rk_res.input_tokens,
            output_tokens=rk_res.output_tokens,
            cost_krw=rk_res.cost_krw,
        )

        # Persist reflection round (REQ-REFL-03-6)
        _persist_reflection_round(
            cycle_kind=cycle_kind,
            original_decision_id=decision_id,
            round_number=round_num,
            risk_persona_run_id=rk_res.persona_run_id,
            risk_rationale=rationale,
            revised_decision_run_id=dec_res.persona_run_id,
            revised_risk_run_id=rk_res.persona_run_id,
            final_verdict=revised_verdict,
        )

        if revised_verdict == "APPROVE":
            return "APPROVE", revised_sig, rk_res.persona_run_id

        if revised_verdict != "REJECT":
            # HOLD or unknown verdict => treat as final rejection
            return revised_verdict, None, rk_res.persona_run_id

        # REJECT again: update rationale/concerns for next round
        revised_risk_json = rk_res.response_json or {}
        rationale = revised_risk_json.get("rationale", rationale)
        concerns = revised_risk_json.get("concerns", concerns)

    # All rounds exhausted, final REJECT
    return "REJECT", None, None


def _persist_reflection_round(
    *,
    cycle_kind: str,
    original_decision_id: int,
    round_number: int,
    risk_persona_run_id: int | None,
    risk_rationale: str,
    revised_decision_run_id: int | None,
    revised_risk_run_id: int | None,
    final_verdict: str,
) -> None:
    """Persist a reflection round to reflection_rounds table (REQ-REFL-03-6)."""
    try:
        sql = """
            INSERT INTO reflection_rounds
                (cycle_kind, original_decision_id, round_number,
                 risk_persona_run_id, risk_rationale,
                 revised_decision_run_id, revised_risk_run_id, final_verdict)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (
                cycle_kind,
                original_decision_id,
                round_number,
                risk_persona_run_id,
                risk_rationale,
                revised_decision_run_id,
                revised_risk_run_id,
                final_verdict,
            ))
    except Exception as e:
        LOG.warning("Failed to persist reflection_round: %s", e)


# @MX:NOTE: SPEC-TRADING-026 — execution policy for 단기과열(55). Size-caps BUY
# orders and forces a limit order at the reference price (single-price auction).
# Sells / non-overheated orders are left as market orders so risk exits are
# never throttled. Mutates ``sig`` in place; returns (new_qty, capped).
# @MX:SPEC: SPEC-TRADING-026
def _apply_overheat_order_policy(
    sig: dict[str, Any],
    *,
    qty: int,
    side: str,
    ref_price: int,
    overheated: bool,
) -> tuple[int, bool]:
    """Apply SPEC-026 size-cap + limit-only to overheated BUY orders.

    Returns ``(new_qty, capped)``. When ``capped`` is True the caller should
    surface a briefing; ``sig`` has been updated with the reduced qty, a
    ``limit`` order_type, and ``limit_price = ref_price``.
    """
    if overheated and side == "buy" and qty > 0:
        new_qty = max(1, int(qty * OVERHEAT_SIZE_FACTOR))
        sig["qty"] = new_qty
        sig["order_type"] = "limit"
        sig["limit_price"] = ref_price
        return new_qty, True
    return qty, False


def _execute_signal(client: KisClient, sig: dict[str, Any], decision_id: int) -> int | None:
    """Submit a buy/sell order based on a decision signal. Returns DB orders.id or None."""
    side = sig.get("side", "hold")
    if side == "hold":
        return None
    ticker = sig.get("ticker", "")
    qty = int(sig.get("qty", 0) or 0)
    if not ticker or qty <= 0:
        return None
    fn = kis_buy if side == "buy" else kis_sell
    # SPEC-026: honour a per-signal order_type / limit_price so the orchestrator
    # can force a limit order on 단기과열(55) entries (single-price auction).
    # Defaults preserve the prior market-order behaviour.
    order_type = sig.get("order_type", "market")
    limit_price = sig.get("limit_price") if order_type == "limit" else None
    try:
        result = fn(client, ticker=ticker, qty=qty,
                    order_type=order_type,
                    limit_price=limit_price,
                    persona_decision_id=decision_id)
        return int(result["order_id"])
    except Exception as e:
        LOG.exception("execute signal failed: %s", sig)
        audit("EXEC_FAILED", actor="orchestrator",
              details={"signal": sig, "error": str(e), "decision_id": decision_id})
        return None


@_with_cycle_briefing("pre_market")
def run_pre_market_cycle(today: str | None = None) -> CycleResult:
    """Pre-market 07:30 sequence: Micro → Decision → Risk → paper auto-execute.

    Macro is read from cache (run separately on Friday 17:00 KST).
    """
    today = today or date.today().isoformat()
    res = CycleResult(cycle_kind="pre_market")

    # 1. Macro from cache
    cached_macro = macro_persona.latest_cached(max_age_days=7)
    macro_summary = None
    if cached_macro:
        res.macro_run_id = int(cached_macro["id"])
        macro_summary = (cached_macro["response"] or "")[:500]

    # SPEC-009: Check tool_calling_enabled and reflection_loop_enabled feature flags
    state = get_system_state()
    micro_tools = _get_persona_tools("micro", state)

    # 2. Micro — SPEC-010 REQ-ROUTER-01-4: Use Model Router for model resolution
    micro_model = resolve_model("micro")
    micro_input = _build_micro_input(today, macro_summary)
    try:
        micro_res = micro_persona.run(
            micro_input, cycle_kind="pre_market", tools=micro_tools, model=micro_model,
        )
    except Exception as e:
        tg.system_error("Micro persona", e, context=f"cycle=pre_market today={today}")
        raise
    res.micro_run_id = micro_res.persona_run_id
    tg.persona_briefing(
        persona="Micro",
        model=micro_model,
        summary=_summarize_persona("micro", micro_res.response_json),
        input_tokens=micro_res.input_tokens,
        output_tokens=micro_res.output_tokens,
        cost_krw=micro_res.cost_krw,
    )

    # 3. Decision — SPEC-010: Model Router resolves model
    decision_model = resolve_model("decision")
    decision_tools = _get_persona_tools("decision", state)
    assets = _gather_assets()
    cash_pct = (assets["cash_d2"] / assets["total_assets"] * 100) if assets["total_assets"] else 100.0
    # Inject blocked tickers for Decision awareness
    blocked_cache = get_blocked_tickers()
    blocked_tickers = blocked_cache.get("blocked", {})

    dec_input = {
        "today": today,
        "macro_guide": macro_summary or "(없음)",
        "micro_candidates": (micro_res.response_json or {}).get("candidates", {}),
        "assets": assets,
        "daily_order_count": 0,    # M5: actual count
        "daily_pnl_pct": 0.0,
        "event_trigger": None,     # only set on event-driven cycles
        # SPEC-012: CAR context and dynamic thresholds flag
        "car_context": None,
        "dynamic_thresholds_enabled": state.get("dynamic_thresholds_enabled", False),
        # Blocked tickers (단기과열/거래정지) for Decision filtering
        "blocked_tickers": blocked_tickers,
    }
    # Inject HOLD feedback from today
    candidate_tickers = [
        c.get("ticker") for c in
        (dec_input.get("micro_candidates") or {}).get("buy", [])
        if c.get("ticker")
    ]
    # SPEC-023 REQ-023-1: auto-expand universe-out candidates before decision.
    # The helper applies blocked filter AFTER expansion (R-1) so data is still
    # fetched for blocked tickers (they may be unblocked later).
    # SPEC-026: only HARD blocks (51~54 / unknown) drop a candidate here;
    # 단기과열(55) stays (the prompt marks it as a reduce-weight caution).
    _hard_blocked, _ = _split_blocked(blocked_tickers)
    candidate_tickers, _expansion_metrics = _filter_and_expand_candidates(
        candidate_tickers,
        cycle_kind="pre_market",
        blocked_tickers=_hard_blocked,
    )
    # Reflect filtered candidates back into the Decision input.
    if _expansion_metrics is not None:
        kept = set(candidate_tickers)
        buy_list = (dec_input.get("micro_candidates") or {}).get("buy", []) or []
        dec_input["micro_candidates"]["buy"] = [
            c for c in buy_list if c.get("ticker") in kept
        ]
    hold_warnings = _get_hold_feedback_today(candidate_tickers)
    dec_input["hold_warnings"] = hold_warnings if hold_warnings else []
    try:
        dec_res, sig_ids = decision_persona.run(
            dec_input,
            cycle_kind="pre_market",
            macro_run_id=res.macro_run_id,
            micro_run_id=res.micro_run_id,
            tools=decision_tools,
            model=decision_model,
        )
    except Exception as e:
        tg.system_error("Decision persona", e, context=f"cycle=pre_market today={today}")
        raise
    res.decision_run_id = dec_res.persona_run_id
    res.decisions = sig_ids
    tg.persona_briefing(
        persona="Decision · 박세훈",
        model=decision_model,
        summary=_summarize_persona("decision", dec_res.response_json),
        input_tokens=dec_res.input_tokens,
        output_tokens=dec_res.output_tokens,
        cost_krw=dec_res.cost_krw,
    )

    # 4. Risk + execute per signal
    if not sig_ids:
        _maybe_enter_silent_mode(0)
        return res
    s = get_settings()
    signals = (dec_res.response_json or {}).get("signals", [])
    if state["halt_state"]:
        # SPEC-TRADING-037 REQ-037-5: a daily-order-COUNT halt still lets
        # risk-reducing SELLs on existing holdings through (BUYs blocked). Any
        # other halt (loss / manual / unknown) blocks the whole cycle.
        signals, sig_ids = _maybe_count_halt_bypass(
            signals, sig_ids, holdings=assets["holdings"], cycle_kind=res.cycle_kind
        )
        if not sig_ids:
            return res

    risk_model = resolve_model("risk")
    risk_tools = _get_persona_tools("risk", state)
    client = KisClient(s.trading_mode)
    micro_summary_text = _summarize_persona("micro", micro_res.response_json)
    # SPEC-TRADING-034: portfolio sizing gate (buy-only, binding) between decision
    # and risk/execute. holdings<5 or no-buys -> no-op; failure -> unadjusted.
    signals, sig_ids = _apply_portfolio_adjustment(
        signals, sig_ids,
        holdings=assets["holdings"],
        holdings_count=len(assets["holdings"]),
        total_assets=assets["total_assets"],
        cash_pct=compute_balance_pcts(assets)[0],
        today=today, cycle_kind=res.cycle_kind,
        res_rejected=res.rejected,
    )
    for sig, decision_id in zip(signals, sig_ids, strict=False):
        # Issue 3: Skip Risk for qty=0 signals (save API cost)
        qty_raw = int(sig.get("qty", 0) or 0)
        if qty_raw == 0:
            LOG.info("Skipping Risk for qty=0 signal: %s %s",
                     sig.get("ticker"), sig.get("side"))
            continue

        # Auto-block tickers with 3+ HOLDs today
        ticker = sig.get("ticker")
        if ticker and _count_holds_today(ticker) >= 3:
            LOG.info("Ticker %s blocked — 3+ HOLDs today", ticker)
            audit("TICKER_BLOCKED_BY_HOLDS", actor="orchestrator", details={
                "ticker": ticker, "hold_count": _count_holds_today(ticker),
            })
            res.rejected.append(decision_id)
            continue

        rk_input = {
            "today": today,
            "decision_signals": [sig],
            "assets": assets,
            "cash_pct": cash_pct,
            "daily_order_count": 0,
            "daily_pnl_pct": 0.0,
            "macro_summary": macro_summary or "(없음)",
            "micro_summary": micro_summary_text,
        }
        rk_res, review_id, verdict = risk_persona.run(
            rk_input, decision_id=decision_id, cycle_kind="pre_market",
            tools=risk_tools,
            model=risk_model,
        )
        res.risk_run_ids.append(rk_res.persona_run_id)
        tg.persona_briefing(
            persona=f"Risk -> {verdict}",
            model=risk_model,
            summary=_summarize_persona("risk", rk_res.response_json),
            input_tokens=rk_res.input_tokens,
            output_tokens=rk_res.output_tokens,
            cost_krw=rk_res.cost_krw,
        )

        # SPEC-009 REQ-REFL-03-1: Reflection Loop on REJECT
        if verdict == "REJECT" and state.get("reflection_loop_enabled"):
            final_verdict, revised_sig, revised_risk_run_id = _run_reflection_loop(
                original_signal=sig,
                risk_response_json=rk_res.response_json or {},
                dec_input=dec_input,
                cycle_kind="pre_market",
                decision_id=decision_id,
                macro_run_id=res.macro_run_id,
                micro_run_id=res.micro_run_id,
                assets=assets,
                cash_pct=cash_pct,
                macro_summary=macro_summary,
                micro_summary=micro_summary_text,
                today=today,
                state=state,
            )
            if revised_risk_run_id:
                res.risk_run_ids.append(revised_risk_run_id)

            if final_verdict == "APPROVE" and revised_sig:
                # Use revised signal for execution
                sig = revised_sig
                verdict = "APPROVE"
            else:
                # REJECT, WITHDRAWN, or HOLD — reject signal
                res.rejected.append(decision_id)
                continue
        elif verdict != "APPROVE":
            res.rejected.append(decision_id)
            continue

        # REQ-RISK-04-7 SoD: code-rule check IN ADDITION to Risk persona APPROVE.
        side_str = sig.get("side", "hold")
        if side_str not in ("buy", "sell"):
            res.rejected.append(decision_id)
            continue
        ticker = sig.get("ticker", "")
        qty = int(sig.get("qty", 0) or 0)

        # Phase 2 — REQ-KIS-02-11/12: market safety check (live quote, stat_cls, buyable).
        try:
            safety = check_pre_order_safety(client, ticker=ticker, side=side_str,
                                            qty=qty, notional=qty * (assets["total_assets"] // 100))
        except Exception as e:
            tg.system_briefing("safety_check_error",
                               f"{ticker} 매매 안전성 검증 중 예외: {e}")
            res.rejected.append(decision_id)
            continue
        if not safety.passed:
            tg.system_briefing(
                "거래 안전성 차단",
                f"{ticker} {side_str} 차단\n사유: {', '.join(safety.blockers)}",
            )
            audit("ORDER_BLOCKED_SAFETY", actor="orchestrator", details={
                "decision_id": decision_id, "ticker": ticker, "side": side_str,
                "blockers": safety.blockers,
            })
            # Issue 4: Record blocked ticker so Decision avoids it in future cycles
            for blocker in safety.blockers:
                if "stat_cls" in blocker:
                    record_blocked_by_safety(ticker, blocker)
                    break
            res.rejected.append(decision_id)
            continue

        # Use safety.quote.price as authoritative ref_price for limit check.
        ref_price = safety.quote["price"] if safety.quote else 100_000

        # SPEC-026: 단기과열(55) BUYs → size-cap + limit-only (single-price
        # auction). Applied BEFORE the limit check so the reduced qty governs it.
        _orig_qty = qty
        qty, _capped = _apply_overheat_order_policy(
            sig, qty=qty, side=side_str, ref_price=ref_price,
            overheated=getattr(safety, "overheated", False),
        )
        if _capped:
            tg.system_briefing(
                "단기과열 비중 축소",
                f"{_ticker_label(ticker)} 단기과열(55) — 수량 {_orig_qty}→{qty}, "
                f"지정가 {ref_price:,}원 (단일가매매 대응)",
            )

        # SPEC-TRADING-040 M3/M4: pass 단기과열 + the held P&L so check_pre_order can
        # apply the sell-budget separation, the 단기과열 1-buy/day cap, and the
        # no-averaging-down-on-a-loss guard. Buy-affecting only; a SELL is never
        # newly blocked (risk-reducing exits always allowed).
        _held = next((h for h in assets["holdings"] if h.get("ticker") == ticker), None)
        chk = check_pre_order(
            side=side_str,
            ticker=ticker,
            qty=qty,
            ref_price=ref_price,
            total_assets=int(assets["total_assets"]),
            holdings=assets["holdings"],
            mode=client.mode.value,
            market="KOSPI",
            overheated=bool(getattr(safety, "overheated", False)),
            held_pnl_pct=(float(_held["pnl_pct"]) if _held else None),
        )
        if not chk.passed:
            record_breach(chk, {"signal": sig, "decision_id": decision_id})
            tg.system_briefing(
                "한도 위반 차단",
                f"종목 {_ticker_label(ticker)} 매매 차단\n위반: {', '.join(chk.breaches)}",
            )
            circuit_breaker.trip(reason="pre-order limit breach", details={"breaches": chk.breaches})
            res.rejected.append(decision_id)
            continue

        # Paper auto-execute (live blocked by order.py live_unlocked gate)
        order_id = _execute_signal(client, sig, decision_id)
        if order_id:
            res.executed_orders.append(order_id)
            try:
                bal_after = balance(client)
                ca_pct, eq_pct = compute_balance_pcts(bal_after)
                # SPEC-TRADING-041 REQ-041-2: for a SELL, surface the realized
                # P&L. The fill price is the executed ref (paper synthetic fills
                # at ref_price; KIS market BUY price still arrives via a separate
                # inquiry so buys keep fill_price=None, unchanged).
                _sell_fill = ref_price if sig["side"] == "sell" else None
                tg.trade_briefing(
                    side=sig["side"],
                    ticker=sig["ticker"],
                    name=ticker_name(sig["ticker"]),
                    qty=int(sig.get("qty", 0)),
                    fill_price=_sell_fill,
                    fee=0,
                    mode=client.mode.value,
                    total_assets=bal_after["total_assets"],
                    cash_pct=ca_pct,
                    equity_pct=eq_pct,
                    note=f"Decision {decision_id} → orders {order_id}",
                    # SPEC-TRADING-041 OQ#1: capture avg_cost from the PRE-sell
                    # holdings snapshot — a FULL sell drops the ticker from the
                    # POST-fill balance, so it must come from _held (pre-order).
                    avg_cost=(_held.get("avg_cost") if _held else None),
                )
            except Exception as e:
                LOG.warning("post-trade briefing failed: %s", e)

    return res


@_with_cycle_briefing("event")
def run_event_trigger_cycle(
    today: str | None = None,
    *,
    ticker: str,
    event_type: str,
    event_subtype: str | None = None,
    event_magnitude: float | None = None,
    event_context: str = "",
    is_safety_critical: bool = False,
) -> CycleResult | None:
    """Event-trigger cycle with SPEC-012 CAR filter integration.

    REQ-FILTER-03-1: CAR filter sits between event trigger and Decision invocation.
    REQ-FILTER-03-4: Blocked events skip Decision (token savings).
    REQ-FILTER-03-5: Passed events inject CAR context into Decision input.

    Returns CycleResult if Decision invoked, None if event was filtered out.
    """
    today = today or date.today().isoformat()
    state = get_system_state()

    # SPEC-012: Apply CAR filter if enabled (REQ-MIGR-07-4)
    if state.get("car_filter_enabled", False):
        filter_result = evaluate_event(
            ticker=ticker,
            event_type=event_type,
            event_subtype=event_subtype,
            event_magnitude=event_magnitude,
            is_safety_critical=is_safety_critical,
        )

        if filter_result.decision == FilterDecision.BLOCK:
            LOG.info(
                "CAR filter BLOCKED event: %s/%s for %s (predicted_car_5d=%.4f, threshold=%.4f)",
                event_type, event_subtype, ticker,
                filter_result.predicted_car_5d or 0.0,
                filter_result.threshold,
            )
            tg.system_briefing(
                "Event-CAR Filtered",
                f"{ticker} {event_type}/{event_subtype or ''} blocked. "
                f"|CAR|={abs(filter_result.predicted_car_5d or 0):.2%} < threshold={filter_result.threshold:.2%}",
            )
            return None

        # PASS or PASS_LOW_CONFIDENCE: proceed with CAR context
        car_context = filter_result.car_context
    else:
        car_context = None

    # Build event trigger context
    trigger_text = event_context or f"{event_type}/{event_subtype or ''} for {ticker} (magnitude: {event_magnitude})"

    # Run pre_market-style cycle with event context injected
    res = CycleResult(cycle_kind="event")
    cached_macro = macro_persona.latest_cached(max_age_days=7)
    macro_summary = (cached_macro["response"] or "")[:500] if cached_macro else None

    decision_model = resolve_model("decision")
    decision_tools = _get_persona_tools("decision", state)
    assets = _gather_assets()
    cash_pct = (assets["cash_d2"] / assets["total_assets"] * 100) if assets["total_assets"] else 100.0

    # Blocked tickers for event-trigger Decision
    blocked_cache_ev = get_blocked_tickers()
    dec_input = {
        "today": today,
        "macro_guide": macro_summary or "(없음)",
        "micro_candidates": {},
        "assets": assets,
        "daily_order_count": 0,
        "daily_pnl_pct": 0.0,
        "event_trigger": trigger_text,
        "car_context": car_context,
        "dynamic_thresholds_enabled": state.get("dynamic_thresholds_enabled", False),
        "hold_warnings": [],
        "blocked_tickers": blocked_cache_ev.get("blocked", {}),
    }

    try:
        dec_res, sig_ids = decision_persona.run(
            dec_input,
            cycle_kind="event",
            macro_run_id=int(cached_macro["id"]) if cached_macro else None,
            micro_run_id=None,
            tools=decision_tools,
            model=decision_model,
        )
    except Exception as e:
        tg.system_error("Decision persona (event)", e, context=f"ticker={ticker} event={event_type}")
        raise

    res.decision_run_id = dec_res.persona_run_id
    res.decisions = sig_ids
    tg.persona_briefing(
        persona="Decision · 박세훈 (Event)",
        model=decision_model,
        summary=_summarize_persona("decision", dec_res.response_json),
        input_tokens=dec_res.input_tokens,
        output_tokens=dec_res.output_tokens,
        cost_krw=dec_res.cost_krw,
    )

    # Risk + execute follows same pattern as pre_market
    if not sig_ids:
        return res

    if state["halt_state"]:
        # SPEC-TRADING-031 REQ-031-1/2/4: throttle the per-cycle "매매 정지"
        # briefing to once per cooldown (helper decides + sends), but log every
        # halted cycle's skip so the operator log records all skips even when the
        # Telegram message is throttled.
        sent = circuit_breaker.maybe_notify_halt()
        LOG.info(
            "halt_state=true — skipping %s cycle (telegram briefing %s)",
            res.cycle_kind,
            "sent" if sent else "throttled",
        )
        return res

    s = get_settings()
    risk_model = resolve_model("risk")
    risk_tools = _get_persona_tools("risk", state)
    client = KisClient(s.trading_mode)
    signals = (dec_res.response_json or {}).get("signals", [])
    # SPEC-TRADING-034: portfolio sizing gate (buy-only, binding) — event cycle.
    signals, sig_ids = _apply_portfolio_adjustment(
        signals, sig_ids,
        holdings=assets["holdings"],
        holdings_count=len(assets["holdings"]),
        total_assets=assets["total_assets"],
        cash_pct=compute_balance_pcts(assets)[0],
        today=today, cycle_kind=res.cycle_kind,
        res_rejected=res.rejected,
    )
    for sig, decision_id in zip(signals, sig_ids, strict=False):
        rk_input = {
            "today": today,
            "decision_signals": [sig],
            "assets": assets,
            "cash_pct": cash_pct,
            "daily_order_count": 0,
            "daily_pnl_pct": 0.0,
            "macro_summary": macro_summary or "(없음)",
            "micro_summary": "(event trigger)",
        }
        rk_res, review_id, verdict = risk_persona.run(
            rk_input, decision_id=decision_id, cycle_kind="event",
            tools=risk_tools, model=risk_model,
        )
        res.risk_run_ids.append(rk_res.persona_run_id)

        if verdict == "APPROVE":
            order_id = _execute_signal(client, sig, decision_id)
            if order_id:
                res.executed_orders.append(order_id)
        else:
            res.rejected.append(decision_id)

    return res


# @MX:ANCHOR: Intraday cycle entry point — implements SPEC-TRADING-016 REQ-016-1-1.
# @MX:REASON: Replaces deferred-to-M5 stub that incorrectly delegated to pre_market
# and overwrote cycle_kind AFTER DB writes. This function now sets cycle_kind=
# "intraday" BEFORE persona records are persisted, reuses the morning Micro cache,
# and runs Decision/Risk fresh.
# @MX:SPEC: SPEC-TRADING-016/REQ-016-1-1
@_with_cycle_briefing("intraday")
def run_intraday_cycle(today: str | None = None) -> CycleResult:
    """Intraday cycle (매시 정각 09~15): reuse cached Micro; fresh Decision/Risk.

    SPEC-TRADING-016 REQ-016-1-1:
    - cycle_kind="intraday" is set BEFORE any persona DB inserts (correctness).
    - Micro is NOT re-executed; the morning's cached result is reused.
    - Decision and Risk run fresh against current market data with cycle_kind="intraday".
    - On no Micro cache: log warning and proceed with empty candidates.
    """
    today = today or date.today().isoformat()
    res = CycleResult(cycle_kind="intraday")
    state = get_system_state()

    # 1. Macro from cache (same 7-day window as pre_market)
    cached_macro = macro_persona.latest_cached(max_age_days=7)
    macro_summary: str | None = None
    if cached_macro:
        res.macro_run_id = int(cached_macro["id"])
        macro_summary = (cached_macro["response"] or "")[:500]

    # 2. Reuse today's cached Micro (skip Micro re-execution)
    cached_micro = micro_persona.latest_cached(max_age_days=1)
    micro_candidates: dict[str, Any] = {}
    micro_summary_text = "(no micro cache)"
    if cached_micro and cached_micro.get("response_json"):
        res.micro_run_id = int(cached_micro["id"])
        micro_response_json = cached_micro["response_json"] or {}
        micro_candidates = micro_response_json.get("candidates", {}) or {}
        micro_summary_text = _summarize_persona("micro", micro_response_json)
    else:
        LOG.warning(
            "intraday: no cached micro found within 1 day; proceeding with empty candidates"
        )

    # 3. Decision — fresh call with cycle_kind="intraday"
    decision_model = resolve_model("decision")
    decision_tools = _get_persona_tools("decision", state)
    assets = _gather_assets()
    cash_pct = (
        assets["cash_d2"] / assets["total_assets"] * 100
        if assets["total_assets"] else 100.0
    )
    blocked_cache = get_blocked_tickers()
    blocked_tickers = blocked_cache.get("blocked", {})

    dec_input: dict[str, Any] = {
        "today": today,
        "macro_guide": macro_summary or "(없음)",
        "micro_candidates": micro_candidates,
        "assets": assets,
        "daily_order_count": 0,
        "daily_pnl_pct": 0.0,
        "event_trigger": None,
        "car_context": None,
        "dynamic_thresholds_enabled": state.get("dynamic_thresholds_enabled", False),
        "blocked_tickers": blocked_tickers,
    }
    candidate_tickers = [
        c.get("ticker")
        for c in (micro_candidates.get("buy") or [])
        if c.get("ticker")
    ]
    # SPEC-023 REQ-023-1: same hook as pre_market — auto-expansion runs
    # before blocked filter so universe-out candidates fetch data.
    # SPEC-026: only HARD blocks drop a candidate; 단기과열(55) stays (cautioned).
    _hard_blocked, _ = _split_blocked(blocked_tickers)
    candidate_tickers, _expansion_metrics = _filter_and_expand_candidates(
        candidate_tickers,
        cycle_kind="intraday",
        blocked_tickers=_hard_blocked,
    )
    if _expansion_metrics is not None:
        kept = set(candidate_tickers)
        buy_list = (micro_candidates.get("buy") or []) or []
        dec_input["micro_candidates"]["buy"] = [
            c for c in buy_list if c.get("ticker") in kept
        ]
    hold_warnings = _get_hold_feedback_today(candidate_tickers)
    dec_input["hold_warnings"] = hold_warnings if hold_warnings else []

    try:
        dec_res, sig_ids = decision_persona.run(
            dec_input,
            cycle_kind="intraday",
            macro_run_id=res.macro_run_id,
            micro_run_id=res.micro_run_id,
            tools=decision_tools,
            model=decision_model,
        )
    except Exception as e:
        tg.system_error("Decision persona", e, context=f"cycle=intraday today={today}")
        raise
    res.decision_run_id = dec_res.persona_run_id
    res.decisions = sig_ids
    tg.persona_briefing(
        persona="Decision · 박세훈 (intraday)",
        model=decision_model,
        summary=_summarize_persona("decision", dec_res.response_json),
        input_tokens=dec_res.input_tokens,
        output_tokens=dec_res.output_tokens,
        cost_krw=dec_res.cost_krw,
    )

    # 4. Risk + execute per signal — same gate pattern as pre_market
    if not sig_ids:
        return res
    signals = (dec_res.response_json or {}).get("signals", [])
    if state["halt_state"]:
        # SPEC-TRADING-037 REQ-037-5: daily-order-COUNT halt lets risk-reducing
        # SELLs through (BUYs blocked); any other halt skips the cycle.
        signals, sig_ids = _maybe_count_halt_bypass(
            signals, sig_ids, holdings=assets["holdings"], cycle_kind=res.cycle_kind
        )
        if not sig_ids:
            return res

    s = get_settings()
    risk_model = resolve_model("risk")
    risk_tools = _get_persona_tools("risk", state)
    client = KisClient(s.trading_mode)
    # SPEC-TRADING-034: portfolio sizing gate (buy-only, binding) — intraday cycle.
    signals, sig_ids = _apply_portfolio_adjustment(
        signals, sig_ids,
        holdings=assets["holdings"],
        holdings_count=len(assets["holdings"]),
        total_assets=assets["total_assets"],
        cash_pct=compute_balance_pcts(assets)[0],
        today=today, cycle_kind=res.cycle_kind,
        res_rejected=res.rejected,
    )

    for sig, decision_id in zip(signals, sig_ids, strict=False):
        qty_raw = int(sig.get("qty", 0) or 0)
        if qty_raw == 0:
            LOG.info(
                "Skipping Risk for qty=0 signal: %s %s",
                sig.get("ticker"), sig.get("side"),
            )
            continue

        ticker = sig.get("ticker")
        if ticker and _count_holds_today(ticker) >= 3:
            LOG.info("Ticker %s blocked — 3+ HOLDs today", ticker)
            audit("TICKER_BLOCKED_BY_HOLDS", actor="orchestrator", details={
                "ticker": ticker, "hold_count": _count_holds_today(ticker),
            })
            res.rejected.append(decision_id)
            continue

        rk_input = {
            "today": today,
            "decision_signals": [sig],
            "assets": assets,
            "cash_pct": cash_pct,
            "daily_order_count": 0,
            "daily_pnl_pct": 0.0,
            "macro_summary": macro_summary or "(없음)",
            "micro_summary": micro_summary_text,
        }
        rk_res, review_id, verdict = risk_persona.run(
            rk_input,
            decision_id=decision_id,
            cycle_kind="intraday",
            tools=risk_tools,
            model=risk_model,
        )
        res.risk_run_ids.append(rk_res.persona_run_id)
        tg.persona_briefing(
            persona=f"Risk -> {verdict}",
            model=risk_model,
            summary=_summarize_persona("risk", rk_res.response_json),
            input_tokens=rk_res.input_tokens,
            output_tokens=rk_res.output_tokens,
            cost_krw=rk_res.cost_krw,
        )

        if verdict != "APPROVE":
            res.rejected.append(decision_id)
            continue

        side_str = sig.get("side", "hold")
        if side_str not in ("buy", "sell"):
            res.rejected.append(decision_id)
            continue
        qty = int(sig.get("qty", 0) or 0)

        try:
            safety = check_pre_order_safety(
                client,
                ticker=ticker or "",
                side=side_str,
                qty=qty,
                notional=qty * (assets["total_assets"] // 100),
            )
        except Exception as e:
            tg.system_briefing(
                "safety_check_error",
                f"{ticker} 매매 안전성 검증 중 예외: {e}",
            )
            res.rejected.append(decision_id)
            continue
        if not safety.passed:
            tg.system_briefing(
                "거래 안전성 차단",
                f"{ticker} {side_str} 차단\n사유: {', '.join(safety.blockers)}",
            )
            audit("ORDER_BLOCKED_SAFETY", actor="orchestrator", details={
                "decision_id": decision_id, "ticker": ticker, "side": side_str,
                "blockers": safety.blockers,
            })
            for blocker in safety.blockers:
                if "stat_cls" in blocker:
                    record_blocked_by_safety(ticker or "", blocker)
                    break
            res.rejected.append(decision_id)
            continue

        ref_price = safety.quote["price"] if safety.quote else 100_000

        # SPEC-026: 단기과열(55) BUYs → size-cap + limit-only (single-price
        # auction). Applied BEFORE the limit check so the reduced qty governs it.
        _orig_qty = qty
        qty, _capped = _apply_overheat_order_policy(
            sig, qty=qty, side=side_str, ref_price=ref_price,
            overheated=getattr(safety, "overheated", False),
        )
        if _capped:
            tg.system_briefing(
                "단기과열 비중 축소",
                f"{_ticker_label(ticker)} 단기과열(55) — 수량 {_orig_qty}→{qty}, "
                f"지정가 {ref_price:,}원 (단일가매매 대응)",
            )

        # SPEC-TRADING-040 M3/M4: 단기과열 + held P&L for sell-budget separation,
        # the 1-buy/day cap and the no-averaging-down guard (buy-affecting only).
        _held = next((h for h in assets["holdings"] if h.get("ticker") == ticker), None)
        chk = check_pre_order(
            side=side_str,
            ticker=ticker or "",
            qty=qty,
            ref_price=ref_price,
            total_assets=int(assets["total_assets"]),
            holdings=assets["holdings"],
            mode=client.mode.value,
            market="KOSPI",
            overheated=bool(getattr(safety, "overheated", False)),
            held_pnl_pct=(float(_held["pnl_pct"]) if _held else None),
        )
        if not chk.passed:
            record_breach(chk, {"signal": sig, "decision_id": decision_id})
            tg.system_briefing(
                "한도 위반 차단",
                f"종목 {_ticker_label(ticker)} 매매 차단\n위반: {', '.join(chk.breaches)}",
            )
            circuit_breaker.trip(
                reason="pre-order limit breach",
                details={"breaches": chk.breaches},
            )
            res.rejected.append(decision_id)
            continue

        order_id = _execute_signal(client, sig, decision_id)
        if order_id:
            res.executed_orders.append(order_id)
            try:
                bal_after = balance(client)
                ca_pct, eq_pct = compute_balance_pcts(bal_after)
                # SPEC-TRADING-041 REQ-041-2: realized P&L for SELLs (see
                # pre_market site for the rationale; buys keep fill_price=None).
                _sell_fill = ref_price if sig["side"] == "sell" else None
                tg.trade_briefing(
                    side=sig["side"],
                    ticker=sig["ticker"],
                    name=ticker_name(sig["ticker"]),
                    qty=int(sig.get("qty", 0)),
                    fill_price=_sell_fill,
                    fee=0,
                    mode=client.mode.value,
                    total_assets=bal_after["total_assets"],
                    cash_pct=ca_pct,
                    equity_pct=eq_pct,
                    note=f"Decision {decision_id} → orders {order_id} (intraday)",
                    # SPEC-TRADING-041 OQ#1: pre-sell avg_cost from _held snapshot.
                    avg_cost=(_held.get("avg_cost") if _held else None),
                )
            except Exception as e:
                LOG.warning("post-trade briefing failed: %s", e)

    return res


def persist_macro_regime(res: Any) -> None:
    """SPEC-TRADING-035 REQ-035-1(b/d): cache Macro's regime/risk_appetite.

    On a successful macro run, extract ``regime`` and ``risk_appetite`` from the
    response JSON and promote them to the ``system_state`` regime columns
    (``regime_updated_at = NOW()``, ``regime_source_run_id`` = this run id), so
    Decision/Risk/Portfolio can branch off a structured value instead of parsing
    the 500-char text blob.

    If either key is missing (or the response is empty), this is a schema
    regression — both keys are already emitted by ``macro.jinja`` — so the cache
    is left UNCHANGED (previous value preserved) and the operator is notified via
    Telegram. The notify failure is swallowed so a macro run never crashes here.
    """
    pj = getattr(res, "response_json", None)
    regime = (pj or {}).get("regime") if isinstance(pj, dict) else None
    risk = (pj or {}).get("risk_appetite") if isinstance(pj, dict) else None
    if not regime or not risk:
        # REQ-035-1d: schema guard — do NOT update the cache; notify.
        try:
            tg.system_error(
                "Macro regime cache",
                ValueError("macro response missing regime/risk_appetite"),
                context="cache not updated (previous value preserved)",
            )
        except Exception:
            LOG.warning("regime schema-error notify failed (swallowed)", exc_info=True)
        LOG.warning(
            "macro regime cache skipped — missing keys (regime=%r risk_appetite=%r)",
            regime, risk,
        )
        return

    update_system_state(
        current_regime=regime,
        current_risk_appetite=risk,
        regime_source_run_id=getattr(res, "persona_run_id", None),
        regime_updated_at=NOW,
        updated_by="macro",
    )


def run_weekly_macro(today: str | None = None) -> int:
    """Friday 17:00 KST (+ weekday 06:10 KST, SPEC-035 REQ-035-3): invoke Macro
    persona. Returns persona_run_id."""
    today_str = today or date.today().isoformat()
    state = get_system_state()
    macro_model = resolve_model("macro")
    macro_tools = _get_persona_tools("macro", state)
    macro_input = ctx.assemble_macro_input()
    res = macro_persona.run(macro_input, cycle_kind="weekly", tools=macro_tools, model=macro_model)
    # SPEC-TRADING-035 REQ-035-1(b): promote regime/risk_appetite to system_state.
    persist_macro_regime(res)
    tg.persona_briefing(
        persona="Macro",
        model=macro_model,
        summary=_summarize_persona("macro", res.response_json),
        input_tokens=res.input_tokens,
        output_tokens=res.output_tokens,
        cost_krw=res.cost_krw,
    )
    return res.persona_run_id
