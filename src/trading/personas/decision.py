"""Decision persona (박세훈 페르소나) — Sonnet 4.6.

Synthesizes Macro guide + Micro candidates + current portfolio + risk limits
into trade signals. Persists signals to persona_decisions.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from trading.db.session import connection
from trading.personas.base import call_persona, render_prompt

MODEL = "claude-sonnet-4-6"
PERSONA = "decision"


def run(input_data: dict[str, Any],
        cycle_kind: str = "pre_market",
        macro_run_id: int | None = None,
        micro_run_id: int | None = None):
    today = input_data.get("today") or date.today().isoformat()
    system_prompt = render_prompt("decision.jinja", **{
        **input_data,
        "today": today,
        "cycle_kind": cycle_kind,
    })
    user_msg = (
        "위 입력을 바탕으로 박세훈 페르소나의 매매 시그널을 JSON으로 제출하세요. "
        "시그널이 없으면 빈 리스트를 반환하세요."
    )
    res = call_persona(
        persona_name=PERSONA,
        model=MODEL,
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
    )

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
