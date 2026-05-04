"""Retrospective persona — Sonnet 4.6, weekly Sunday."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from trading.db.session import connection
from trading.personas.base import call_persona, render_prompt

MODEL = "claude-sonnet-4-6"
PERSONA = "retrospective"


def _gather_stats(start: date, end: date) -> dict[str, Any]:
    sql_runs = """
        SELECT persona_name, COUNT(*) AS n, SUM(cost_krw) AS cost
          FROM persona_runs
         WHERE ts::date BETWEEN %s AND %s
         GROUP BY persona_name
    """
    sql_risk = """
        SELECT verdict, COUNT(*) AS n
          FROM risk_reviews
         WHERE ts::date BETWEEN %s AND %s
         GROUP BY verdict
    """
    sql_orders = """
        SELECT status, COUNT(*) AS n
          FROM orders
         WHERE ts::date BETWEEN %s AND %s
         GROUP BY status
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql_runs, (start, end))
        runs = [dict(r) for r in cur.fetchall()]
        cur.execute(sql_risk, (start, end))
        risk = [dict(r) for r in cur.fetchall()]
        cur.execute(sql_orders, (start, end))
        orders = [dict(r) for r in cur.fetchall()]
    return {"runs": runs, "risk": risk, "orders": orders}


def run(today: date | None = None):
    end = today or date.today()
    start = end - timedelta(days=7)
    stats = _gather_stats(start, end)

    input_data = {
        "today": end.isoformat(),
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "run_stats": json.dumps(stats["runs"], ensure_ascii=False, default=str),
        "risk_distribution": json.dumps(stats["risk"], ensure_ascii=False, default=str),
        "trade_summary": json.dumps(stats["orders"], ensure_ascii=False, default=str),
    }
    system_prompt = render_prompt("retrospective.jinja", **input_data)
    user_msg = "지난 주 데이터를 검토하고 회고 리포트와 시스템 개선안을 JSON으로 제출하세요."
    res = call_persona(
        persona_name=PERSONA,
        model=MODEL,
        cycle_kind="weekly",
        system_prompt=system_prompt,
        user_message=user_msg,
        trigger_context={"week_start": start.isoformat(), "week_end": end.isoformat()},
        max_tokens=3000,
        expect_json=True,
    )
    # Persist a row in retrospectives table for traceability
    sql = """
        INSERT INTO retrospectives
            (week_start, week_end, persona_run_id, summary, improvements)
        VALUES (%s, %s, %s, %s, %s::jsonb)
        ON CONFLICT (week_start) DO NOTHING
    """
    with connection() as conn, conn.cursor() as cur:
        # Table may not yet exist before migration 005 — guard.
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='retrospectives')"
        )
        exists = bool(cur.fetchone()["exists"])
    if exists:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (
                start, end, res.persona_run_id,
                (res.response_json or {}).get("trade_review", {}).get("approved_overall", ""),
                json.dumps((res.response_json or {}).get("improvements", []), ensure_ascii=False),
            ))
    return res
