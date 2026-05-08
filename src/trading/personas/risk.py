"""Risk persona — SoD verifier on every Decision signal.

SPEC-015 REQ-ORCH-04-1: CLI routing via cli_personas_enabled feature flag.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from trading.db.session import audit, connection
from trading.personas.base import call_persona, call_persona_via_cli, is_cli_mode_active, render_prompt

LOG = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
PERSONA = "risk"


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
    system_prompt = render_prompt("risk.jinja", **{
        **input_data,
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
            False,                                # code rules check happens externally
            json.dumps(res.response_json or {}),
        ))
        row = cur.fetchone()
        review_id = row["id"]
    return res, review_id, verdict
