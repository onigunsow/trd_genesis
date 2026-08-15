"""Risk persona — SoD verifier on every Decision signal.

SPEC-015 REQ-ORCH-04-1: CLI routing via cli_personas_enabled feature flag.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from trading.db.session import audit, connection, get_effective_regime, get_system_state
from trading.personas import regime_branch
from trading.personas.base import (
    call_persona,
    call_persona_via_cli,
    is_cli_mode_active,
    render_prompt,
)

LOG = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
PERSONA = "risk"


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

    Fails SAFE (bull OFF) on a system_state read error so a DB hiccup can never
    enable the aggressive profile in the independent risk verifier.
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


def run(
    input_data: dict[str, Any],
    decision_id: int,
    cycle_kind: str = "pre_market",
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
):
    """Invoke Risk persona.

    Args:
        input_data: Context data for the persona prompt.
        decision_id: Reference to persona_decisions row.
        cycle_kind: Cycle type.
        tools: Optional tool definitions for tool-calling mode (SPEC-009).
    """
    today = input_data.get("today") or date.today().isoformat()
    # SPEC-TRADING-035 REQ-035-2: regime-aware conservative verification context.
    if input_data.get("current_regime"):
        regime = regime_branch.regime_branch_applied(input_data.get("current_regime"))
        risk_appetite = input_data.get("current_risk_appetite") or "neutral"
    else:
        regime, risk_appetite = get_effective_regime()
    regime_ctx = regime_branch.prompt_context(regime, risk_appetite)
    # SPEC-TRADING-036 REQ-036-2: bull-mode ctx (3-AND gate). The risk verifier
    # sees the same aggressive thresholds the decision persona used so its SoD
    # check is mode-aware. No transition alert here — decision.run owns it.
    bull_ctx = _bull_mode_context(regime, input_data)
    system_prompt = render_prompt("risk.jinja", **{
        **input_data,
        **regime_ctx,
        **bull_ctx,
        "today": today,
        "cycle_kind": cycle_kind,
    })
    user_msg = "위 시그널을 검증한 결과를 JSON으로 제출하세요. APPROVE/HOLD/REJECT 중 하나로 결정하세요."

    # SPEC-015 REQ-ORCH-04-1: CLI routing when enabled
    if is_cli_mode_active():
        # Extract signal ticker(s) for pre-computation
        signals = input_data.get("decision_signals", [])
        tickers = [s.get("ticker") for s in signals if s.get("ticker")]

        res = call_persona_via_cli(
            persona_name=PERSONA,
            model=model or MODEL,
            cycle_kind=cycle_kind,
            system_prompt=system_prompt,
            user_message=user_msg,
            trigger_context={"decision_id": decision_id, "cycle_kind": cycle_kind},
            expect_json=True,
            tickers=tickers,
            input_data=input_data,
            run_context={"decision_id": decision_id},
        )
    else:
        res = call_persona(
            persona_name=PERSONA,
            model=model or MODEL,
            cycle_kind=cycle_kind,
            system_prompt=system_prompt,
            user_message=user_msg,
            trigger_context={"decision_id": decision_id, "cycle_kind": cycle_kind},
            max_tokens=2000,
            expect_json=True,
            tools=tools,
        )

    # SPEC-TRADING-035 REQ-035-2(f): tag + snapshot the regime branch.
    if res.response_json is not None:
        res.response_json["regime_branch_applied"] = regime
    _stamp_regime_at_decision(res.persona_run_id, regime)

    verdict = (res.response_json or {}).get("verdict", "HOLD")
    if verdict not in ("APPROVE", "HOLD", "REJECT"):
        LOG.warning("Invalid Risk verdict '%s' for decision_id=%s — defaulting to HOLD", verdict, decision_id)
        audit("INVALID_RISK_VERDICT", actor="risk", details={"verdict": verdict, "decision_id": decision_id})
        verdict = "HOLD"
    rationale = (res.response_json or {}).get("rationale", "")

    sql = """
        INSERT INTO risk_reviews
            (persona_run_id, decision_id, verdict, rationale, code_rules_passed, raw)
        VALUES (%s,%s,%s,%s,%s,%s::jsonb)
        RETURNING id
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (
            res.persona_run_id,
            decision_id,
            verdict,
            rationale,
            False,  # 코드룰은 이 뒤 orchestrator 에서 돈다 → record_code_rules_result 로 갱신
            json.dumps(res.response_json or {}),
        ))
        row = cur.fetchone()
        review_id = row["id"]
    return res, review_id, verdict


def record_code_rules_result(review_id: int | None, *, passed: bool, breaches: list[str]) -> None:
    """risk_reviews.code_rules_passed 를 실제 check_pre_order 결과로 갱신한다.

    2026-08-15: 이 컬럼이 482행 전부 False 였다 — INSERT 시점엔 코드룰이 아직
    안 돌아서 False 를 박고, 그 뒤 아무도 갱신하지 않았다(죽은 컬럼). 리스크
    페르소나(verdict) → 코드룰(check_pre_order) 순서이므로 코드룰 결과가 나온
    뒤 여기서 써넣는다. breaches 는 raw 에 병합해 사후 집계가 가능하게 한다.

    실패해도 raise 하지 않는다 — 관측성 갱신이 주문 경로를 막으면 안 된다.
    """
    if review_id is None:
        return
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE risk_reviews
                   SET code_rules_passed = %s,
                       raw = raw || %s::jsonb
                 WHERE id = %s
                """,
                (bool(passed), json.dumps({"code_rules_breaches": list(breaches)}), int(review_id)),
            )
    except Exception:
        LOG.warning("record_code_rules_result failed (review_id=%s)", review_id, exc_info=True)
