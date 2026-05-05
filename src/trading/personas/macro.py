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


def run(
    input_data: dict[str, Any],
    cycle_kind: str = "weekly",
    tools: list[dict[str, Any]] | None = None,
):
    """Invoke the macro persona. input_data fields are passed to the Jinja template.

    Args:
        input_data: Context data for the persona prompt.
        cycle_kind: Cycle type (weekly, manual, etc.).
        tools: Optional tool definitions for tool-calling mode (SPEC-009).
    """
    today = input_data.get("today") or date.today().isoformat()
    # SPEC-008: memory는 system_prompt에서 제외하고 user_msg에 prepend (캐시 안정성).
    memory_block = input_data.pop("memory", None) if "memory" in input_data else input_data.get("memory")
    system_prompt = render_prompt("macro.jinja", **{**input_data, "today": today})

    user_parts = []
    if memory_block:
        user_parts.append(f"[활성 매크로 메모리 (과거 인사이트, 참고용)]\n{memory_block}\n")
    user_parts.append(
        "위 입력 데이터를 바탕으로 향후 1주 한국 주식시장에 대한 매크로 분석을 JSON으로 제출하세요."
    )
    user_msg = "\n".join(user_parts)
    return call_persona(
        persona_name=PERSONA,
        model=MODEL,
        cycle_kind=cycle_kind,
        system_prompt=system_prompt,
        user_message=user_msg,
        trigger_context={"input_keys": list(input_data.keys())},
        max_tokens=3000,
        expect_json=True,
        tools=tools,
    )
