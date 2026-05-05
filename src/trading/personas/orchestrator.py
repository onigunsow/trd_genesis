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

import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from trading.alerts import telegram as tg
from trading.config import TradingMode, get_settings
from trading.db.session import audit, connection, get_system_state, update_system_state
from trading.kis.account import balance
from trading.kis.client import KisClient
from trading.kis.order import buy as kis_buy
from trading.kis.order import sell as kis_sell
from trading.models.router import resolve_model
from trading.personas import decision as decision_persona
from trading.personas import macro as macro_persona
from trading.personas import micro as micro_persona
from trading.personas import risk as risk_persona
from trading.personas import context as ctx
from trading.risk import circuit_breaker
from trading.risk.limits import check_pre_order, record_breach
from trading.risk.market_safety import check_pre_order_safety
from trading.tools.registry import get_tools_for_persona

LOG = logging.getLogger(__name__)

CycleKind = Literal["pre_market", "intraday", "event", "weekly", "manual"]

# SPEC-009 REQ-REFL-03-2: Maximum reflection rounds (Decision re-invoke + Risk re-evaluate).
MAX_REFLECTION_ROUNDS: int = 2
# SPEC-009 REQ-REFL-03-10: Combined timeout per reflection round (seconds).
REFLECTION_ROUND_TIMEOUT: float = 30.0


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
        top = ""
        if buy:
            top = "\n매수 후보: " + ", ".join(b.get("ticker", "") for b in buy[:3])
        return line + top
    if name == "decision":
        sigs = response_json.get("signals", []) or []
        if not sigs:
            return "신규 시그널 없음"
        return "\n".join(
            f"- {s.get('ticker','')} {s.get('side','?')} {s.get('qty',0)}주: "
            f"{(s.get('rationale','') or '')[:80]}"
            for s in sigs[:3]
        )
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


def _build_micro_input(today: str, macro_summary: str | None) -> dict[str, Any]:
    """Build micro persona context from cached data (M5 정밀화)."""
    return ctx.assemble_micro_input(macro_summary=macro_summary)


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
        except Exception as e:  # noqa: BLE001
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
        except Exception as e:  # noqa: BLE001
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
    except Exception as e:  # noqa: BLE001
        LOG.warning("Failed to persist reflection_round: %s", e)


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
    try:
        result = fn(client, ticker=ticker, qty=qty,
                    order_type="market",
                    persona_decision_id=decision_id)
        return int(result["order_id"])
    except Exception as e:  # noqa: BLE001
        LOG.exception("execute signal failed: %s", sig)
        audit("EXEC_FAILED", actor="orchestrator",
              details={"signal": sig, "error": str(e), "decision_id": decision_id})
        return None


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
    except Exception as e:  # noqa: BLE001
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
    dec_input = {
        "today": today,
        "macro_guide": macro_summary or "(없음)",
        "micro_candidates": (micro_res.response_json or {}).get("candidates", {}),
        "assets": assets,
        "daily_order_count": 0,    # M5: actual count
        "daily_pnl_pct": 0.0,
        "event_trigger": None,     # only set on event-driven cycles
    }
    try:
        dec_res, sig_ids = decision_persona.run(
            dec_input,
            cycle_kind="pre_market",
            macro_run_id=res.macro_run_id,
            micro_run_id=res.micro_run_id,
            tools=decision_tools,
            model=decision_model,
        )
    except Exception as e:  # noqa: BLE001
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
    if state["halt_state"]:
        tg.system_briefing("매매 정지", "halt_state=true 이므로 매매 차단됨")
        return res

    risk_model = resolve_model("risk")
    risk_tools = _get_persona_tools("risk", state)
    client = KisClient(s.trading_mode)
    signals = (dec_res.response_json or {}).get("signals", [])
    micro_summary_text = _summarize_persona("micro", micro_res.response_json)
    for sig, decision_id in zip(signals, sig_ids, strict=False):
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
        except Exception as e:  # noqa: BLE001
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
            res.rejected.append(decision_id)
            continue

        # Use safety.quote.price as authoritative ref_price for limit check.
        ref_price = safety.quote["price"] if safety.quote else 100_000
        chk = check_pre_order(
            side=side_str,
            ticker=ticker,
            qty=qty,
            ref_price=ref_price,
            total_assets=int(assets["total_assets"]),
            holdings=assets["holdings"],
            mode=client.mode.value,
            market="KOSPI",
        )
        if not chk.passed:
            record_breach(chk, {"signal": sig, "decision_id": decision_id})
            tg.system_briefing(
                "한도 위반 차단",
                f"종목 {ticker} 매매 차단\n위반: {', '.join(chk.breaches)}",
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
                ca_pct = (bal_after["cash_d2"] / bal_after["total_assets"] * 100) if bal_after["total_assets"] else 0.0
                eq_pct = (bal_after["stock_eval"] / bal_after["total_assets"] * 100) if bal_after["total_assets"] else 0.0
                tg.trade_briefing(
                    side=sig["side"],
                    ticker=sig["ticker"],
                    name=None,
                    qty=int(sig.get("qty", 0)),
                    fill_price=None,    # KIS fill price arrives via separate inquiry
                    fee=0,
                    mode=client.mode.value,
                    total_assets=bal_after["total_assets"],
                    cash_pct=ca_pct,
                    equity_pct=eq_pct,
                    note=f"Decision {decision_id} → orders {order_id}",
                )
            except Exception as e:  # noqa: BLE001
                LOG.warning("post-trade briefing failed: %s", e)

    return res


def run_intraday_cycle(today: str | None = None) -> CycleResult:
    """Intraday cycle: skip Micro full analysis; Decision uses Micro cache."""
    today = today or date.today().isoformat()
    # Reuse most recent micro for today (or last cached). For brevity, full impl deferred to M5.
    # Here we treat intraday similarly to pre_market but mark cycle_kind appropriately.
    res = run_pre_market_cycle(today=today)
    res.cycle_kind = "intraday"
    return res


def run_weekly_macro(today: str | None = None) -> int:
    """Friday 17:00 KST: invoke Macro persona. Returns persona_run_id."""
    today_str = today or date.today().isoformat()
    state = get_system_state()
    macro_model = resolve_model("macro")
    macro_tools = _get_persona_tools("macro", state)
    macro_input = ctx.assemble_macro_input()
    res = macro_persona.run(macro_input, cycle_kind="weekly", tools=macro_tools, model=macro_model)
    tg.persona_briefing(
        persona="Macro",
        model=macro_model,
        summary=_summarize_persona("macro", res.response_json),
        input_tokens=res.input_tokens,
        output_tokens=res.output_tokens,
        cost_krw=res.cost_krw,
    )
    return res.persona_run_id
