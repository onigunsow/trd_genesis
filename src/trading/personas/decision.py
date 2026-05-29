"""Decision persona (박세훈 페르소나) — Sonnet 4.6.

Synthesizes Macro guide + Micro candidates + current portfolio + risk limits
into trade signals. Persists signals to persona_decisions.

SPEC-015 REQ-ORCH-04-1: CLI routing via cli_personas_enabled feature flag.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from trading.db.session import connection, get_effective_regime, get_system_state
from trading.personas import regime_branch
from trading.personas.base import (
    call_persona,
    call_persona_via_cli,
    is_cli_mode_active,
    render_prompt,
)

MODEL = "claude-sonnet-4-6"
PERSONA = "decision"


def _stamp_regime_at_decision(persona_run_id: int | None, regime: str) -> None:
    """SPEC-TRADING-035 REQ-035-2(f): snapshot the regime onto persona_runs."""
    if persona_run_id is None:
        return
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE persona_runs SET regime_at_decision = %s WHERE id = %s",
            (regime, persona_run_id),
        )


def _bull_mode_context(regime: str, input_data: dict[str, Any]) -> dict[str, Any]:
    """SPEC-TRADING-036 REQ-036-2: derive bull-mode ctx via the 3-AND gate (S-4).

    Reads ``trading_mode`` + ``late_cycle_defense_active`` from system_state
    (explicit input overrides for tests/CLI). A state-read failure fails SAFE —
    bull mode is treated as inactive — so a DB hiccup can never silently enable
    the aggressive profile (capital-preservation hard rule).
    """
    try:
        if "trading_mode" in input_data or "late_cycle_defense_active" in input_data:
            trading_mode = input_data.get("trading_mode", "paper")
            late_cycle = bool(input_data.get("late_cycle_defense_active", False))
        else:
            state = get_system_state()
            trading_mode = state.get("trading_mode", "paper")
            late_cycle = bool(state.get("late_cycle_defense_active", False))
    except Exception:
        trading_mode, late_cycle = "live", True  # fail safe -> bull OFF
    active = regime_branch.bull_mode_active(regime, late_cycle, trading_mode)
    return regime_branch.bull_prompt_context(active)


def run(input_data: dict[str, Any],
        cycle_kind: str = "pre_market",
        macro_run_id: int | None = None,
        micro_run_id: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None):
    """Invoke Decision persona.

    Args:
        input_data: Context data for the persona prompt.
        cycle_kind: Cycle type.
        macro_run_id: Reference to macro persona run.
        micro_run_id: Reference to micro persona run.
        tools: Optional tool definitions for tool-calling mode (SPEC-009).
    """
    today = input_data.get("today") or date.today().isoformat()
    # SPEC-TRADING-035 REQ-035-2: resolve the macro regime (explicit input wins;
    # else the single TTL-aware read helper). Inject the conservative adjusted
    # numbers into the prompt context so the LLM sees the branch.
    if input_data.get("current_regime"):
        regime = regime_branch.regime_branch_applied(input_data.get("current_regime"))
        risk_appetite = input_data.get("current_risk_appetite") or "neutral"
    else:
        regime, risk_appetite = get_effective_regime()
    regime_ctx = regime_branch.prompt_context(regime, risk_appetite)
    # SPEC-TRADING-036 REQ-036-2: derive the aggressive bull profile (3-AND gate)
    # and inject it. The conservative regime_ctx (SPEC-035) is the live/late-cycle
    # fallback — bull_ctx only loosens it when bull_mode_active is True.
    bull_ctx = _bull_mode_context(regime, input_data)
    regime_branch.maybe_notify_bull_transition(bull_ctx["bull_mode_active"])
    system_prompt = render_prompt("decision.jinja", **{
        **input_data,
        **regime_ctx,
        **bull_ctx,
        "today": today,
        "cycle_kind": cycle_kind,
    })
    user_msg = (
        "위 입력을 바탕으로 박세훈 페르소나의 매매 시그널을 JSON으로 제출하세요. "
        "시그널이 없으면 빈 리스트를 반환하세요."
    )

    # SPEC-015 REQ-ORCH-04-1: CLI routing when enabled
    if is_cli_mode_active():
        # REQ-PRECOMP-05-7: Pre-compute for candidate tickers from Micro result
        candidates = input_data.get("micro_candidates", {})
        tickers = []
        for side in ("buy", "sell"):
            for c in (candidates.get(side) or []):
                t = c.get("ticker")
                if t and t not in tickers:
                    tickers.append(t)

        res = call_persona_via_cli(
            persona_name=PERSONA,
            model=model or MODEL,
            cycle_kind=cycle_kind,
            system_prompt=system_prompt,
            user_message=user_msg,
            trigger_context={
                "macro_run_id": macro_run_id,
                "micro_run_id": micro_run_id,
                "cycle_kind": cycle_kind,
            },
            expect_json=True,
            tickers=tickers,
            input_data=input_data,
            run_context={
                "macro_run_id": macro_run_id,
                "micro_run_id": micro_run_id,
            },
        )
    else:
        res = call_persona(
            persona_name=PERSONA,
            model=model or MODEL,
            cycle_kind=cycle_kind,
            system_prompt=system_prompt,
            user_message=user_msg,
            trigger_context={
                "macro_run_id": macro_run_id,
                "micro_run_id": micro_run_id,
                "cycle_kind": cycle_kind,
            },
            max_tokens=3000,
            expect_json=True,
            tools=tools,
        )

    # SPEC-TRADING-035 REQ-035-2(f): tag the regime branch onto the response JSON
    # and snapshot it on persona_runs for audit.
    if res.response_json is not None:
        res.response_json["regime_branch_applied"] = regime
    _stamp_regime_at_decision(res.persona_run_id, regime)

    # Persist each signal as a row in persona_decisions.
    sig_ids: list[int] = []
    if res.response_json and isinstance(res.response_json.get("signals"), list):
        for sig in res.response_json["signals"]:
            sql = """
                INSERT INTO persona_decisions
                    (persona_run_id, macro_run_id, micro_run_id, cycle_kind,
                     ticker, side, qty, rationale, confidence, raw)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                RETURNING id
            """
            with connection() as conn, conn.cursor() as cur:
                cur.execute(sql, (
                    res.persona_run_id,
                    macro_run_id,
                    micro_run_id,
                    cycle_kind,
                    sig.get("ticker", ""),
                    sig.get("side", "hold"),
                    int(sig.get("qty", 0) or 0),
                    sig.get("rationale", ""),
                    float(sig.get("confidence")) if sig.get("confidence") is not None else None,
                    json.dumps(sig),
                ))
                row = cur.fetchone()
                sig_ids.append(row["id"])
    return res, sig_ids
