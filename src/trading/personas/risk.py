"""Risk persona — SoD verifier on every Decision signal."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from trading.db.session import connection
from trading.personas.base import call_persona, render_prompt

MODEL = "claude-sonnet-4-6"
PERSONA = "risk"


def run(input_data: dict[str, Any], decision_id: int, cycle_kind: str = "pre_market"):
    today = input_data.get("today") or date.today().isoformat()
    system_prompt = render_prompt("risk.jinja", **{
        **input_data,
        "today": today,
        "cycle_kind": cycle_kind,
    })
    user_msg = "위 시그널을 검증한 결과를 JSON으로 제출하세요. APPROVE/HOLD/REJECT 중 하나로 결정하세요."
    res = call_persona(
        persona_name=PERSONA,
        model=MODEL,
        cycle_kind=cycle_kind,
        system_prompt=system_prompt,
        user_message=user_msg,
        trigger_context={"decision_id": decision_id, "cycle_kind": cycle_kind},
        max_tokens=2000,
        expect_json=True,
    )

    verdict = (res.response_json or {}).get("verdict", "HOLD")
    if verdict not in ("APPROVE", "HOLD", "REJECT"):
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
