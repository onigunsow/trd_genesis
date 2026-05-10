"""Micro persona — Sonnet 4.6, pre-market 07:30 KST + intraday cache reuse.

SPEC-009 REQ-PTOOL-02-4: Supports tool-calling mode for active information retrieval.
SPEC-015 REQ-ORCH-04-1: CLI routing via cli_personas_enabled feature flag.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from trading.db.session import connection
from trading.personas.base import call_persona, call_persona_via_cli, is_cli_mode_active, render_prompt

MODEL = "claude-sonnet-4-6"
PERSONA = "micro"


# @MX:ANCHOR: Micro cache reuse boundary for intraday cycles
# @MX:REASON: SPEC-TRADING-016 REQ-016-1-1 — intraday must reuse morning's Micro
# without re-running the persona; this function is the sole read path for that cache.
# @MX:SPEC: SPEC-TRADING-016/REQ-016-1-1
def latest_cached(max_age_days: int = 1) -> dict[str, Any] | None:
    """Return the most recent micro run within max_age_days, or None.

    Mirrors macro_persona.latest_cached. Intended for intraday cycles that must
    reuse the morning pre-market Micro analysis instead of re-invoking the LLM.

    Args:
        max_age_days: Maximum age of cached row in days. Defaults to 1 (today only).

    Returns:
        Dict with id, ts, response, response_json columns, or None if no fresh row.
    """
    sql = """
        SELECT id, ts, response, response_json
          FROM persona_runs
         WHERE persona_name = 'micro'
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
    cycle_kind: str = "pre_market",
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
):
    """Invoke Micro persona.

    Args:
        input_data: Context data for the persona prompt.
        cycle_kind: Cycle type (pre_market, intraday, etc.).
        tools: Optional tool definitions for tool-calling mode (SPEC-009).
    """
    today = input_data.get("today") or date.today().isoformat()
    # SPEC-008: memory를 user_msg로 분리 (캐시 안정성)
    memory_block = input_data.get("memory")
    system_prompt = render_prompt("micro.jinja", **{**input_data, "today": today})

    user_parts = []
    if memory_block:
        user_parts.append(f"[활성 마이크로 메모리 (과거 인사이트, 참고용)]\n{memory_block}\n")
    user_parts.append(
        "위 입력 데이터를 바탕으로 오늘의 매수/매도/관망 후보를 JSON으로 제출하세요."
    )
    user_msg = "\n".join(user_parts)

    # SPEC-015 REQ-ORCH-04-1: CLI routing when enabled
    if is_cli_mode_active():
        # REQ-PRECOMP-05-6: Pre-compute for expanded watchlist tickers
        tickers = input_data.get("watchlist", [])
        return call_persona_via_cli(
            persona_name=PERSONA,
            model=model or MODEL,
            cycle_kind=cycle_kind,
            system_prompt=system_prompt,
            user_message=user_msg,
            trigger_context={"input_keys": list(input_data.keys())},
            expect_json=True,
            tickers=tickers,
            input_data=input_data,
        )

    return call_persona(
        persona_name=PERSONA,
        model=model or MODEL,
        cycle_kind=cycle_kind,
        system_prompt=system_prompt,
        user_message=user_msg,
        trigger_context={"input_keys": list(input_data.keys())},
        max_tokens=3000,
        expect_json=True,
        tools=tools,
    )
