"""Macro persona — Opus 4.7, weekly Friday 17:00 KST.

Output is cached for 7 days; downstream personas reference the latest valid run.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from trading.db.session import connection
from trading.personas.base import call_persona, render_prompt

MODEL = "claude-opus-4-7"
PERSONA = "macro"


def latest_cached(max_age_days: int = 7) -> dict[str, Any] | None:
    """Return the most recent macro run within max_age_days, or None."""
    sql = """
        SELECT id, ts, response, response_json
          FROM persona_runs
         WHERE persona_name = 'macro'
           AND error IS NULL
           AND ts >= NOW() - %s::interval
         ORDER BY ts DESC
         LIMIT 1
    """
    interval = f"{max_age_days} days"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (interval,))
        row = cur.fetchone()
    return dict(row) if row else None


def run(input_data: dict[str, Any], cycle_kind: str = "weekly"):
    """Invoke the macro persona. input_data fields are passed to the Jinja template."""
    today = input_data.get("today") or date.today().isoformat()
    system_prompt = render_prompt("macro.jinja", **{**input_data, "today": today})
    user_msg = (
        "위 입력 데이터를 바탕으로 향후 1주 한국 주식시장에 대한 매크로 분석을 JSON으로 제출하세요."
    )
    return call_persona(
        persona_name=PERSONA,
        model=MODEL,
        cycle_kind=cycle_kind,
        system_prompt=system_prompt,
        user_message=user_msg,
        trigger_context={"input_keys": list(input_data.keys())},
        max_tokens=3000,
        expect_json=True,
    )
