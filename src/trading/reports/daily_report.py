"""Daily report (REQ-REPORT-05-6) — generated at 16:00 KST every Korean trading day.

Even with zero trades, the report is generated and sent. If Anthropic API is
unavailable (no credits, network error), a plain-text fallback is used.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

import httpx
from anthropic import Anthropic

from trading.alerts.telegram import system_briefing
from trading.config import get_settings
from trading.db.session import connection

LOG = logging.getLogger(__name__)


def _gather_today() -> dict[str, Any]:
    today = date.today()
    sql_orders = """
        SELECT id, ticker, side, qty, status, fill_price, fill_qty, fee, mode
          FROM orders WHERE ts::date = CURRENT_DATE ORDER BY id
    """
    sql_runs = """
        SELECT persona_name, COUNT(*) AS n, SUM(cost_krw) AS cost,
               SUM(input_tokens) AS in_tok,
               SUM(cache_read_tokens) AS cache_read,
               SUM(cache_creation_tokens) AS cache_create
          FROM persona_runs WHERE ts::date = CURRENT_DATE GROUP BY persona_name
    """
    # SPEC-010 REQ-COST-04-1: Per-model cost breakdown
    sql_model_breakdown = """
        SELECT model, COUNT(*) AS n,
               SUM(cost_krw) AS cost,
               SUM(input_tokens) AS in_tok,
               SUM(output_tokens) AS out_tok
          FROM persona_runs WHERE ts::date = CURRENT_DATE GROUP BY model
    """
    sql_risk = """
        SELECT verdict, COUNT(*) AS n
          FROM risk_reviews WHERE ts::date = CURRENT_DATE GROUP BY verdict
    """
    sql_cost = """
        SELECT
            COUNT(*) FILTER (WHERE status IN ('submitted','filled','partial')) AS executed_count,
            COALESCE(SUM(fee) FILTER (WHERE status IN ('submitted','filled','partial')), 0) AS exec_fee_total,
            COALESCE(SUM(fee), 0) AS attempted_fee_total
          FROM orders WHERE ts::date = CURRENT_DATE
    """
    # Weekly + monthly cumulative for context
    sql_cum = """
        SELECT
            (SELECT COUNT(*) FROM orders WHERE ts >= CURRENT_DATE - INTERVAL '7 days')   AS week_orders,
            (SELECT COUNT(*) FROM orders WHERE ts >= CURRENT_DATE - INTERVAL '30 days')  AS month_orders,
            (SELECT COALESCE(SUM(fee), 0) FROM orders WHERE ts >= CURRENT_DATE - INTERVAL '7 days')  AS week_fee,
            (SELECT COALESCE(SUM(fee), 0) FROM orders WHERE ts >= CURRENT_DATE - INTERVAL '30 days') AS month_fee
    """
    # SPEC-009 REQ-PTOOL-02-9: Tool usage stats
    sql_tools = """
        SELECT
            COUNT(*) AS total_calls,
            COUNT(*) FILTER (WHERE success = false) AS failures,
            COUNT(DISTINCT persona_run_id) AS persona_invocations
          FROM tool_call_log WHERE created_at::date = CURRENT_DATE
    """
    # SPEC-009 REQ-REFL-03-7: Reflection stats
    sql_reflection = """
        SELECT
            COUNT(*) AS total_rounds,
            COUNT(*) FILTER (WHERE final_verdict = 'APPROVE') AS approved,
            COUNT(*) FILTER (WHERE final_verdict = 'REJECT') AS rejected,
            COUNT(*) FILTER (WHERE final_verdict = 'WITHDRAWN') AS withdrawn
          FROM reflection_rounds WHERE created_at::date = CURRENT_DATE
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql_orders)
        orders = [dict(r) for r in cur.fetchall()]
        cur.execute(sql_runs)
        runs = [dict(r) for r in cur.fetchall()]
        cur.execute(sql_risk)
        risk = [dict(r) for r in cur.fetchall()]
        cur.execute(sql_cost)
        cost = dict(cur.fetchone() or {})
        cur.execute(sql_cum)
        cum = dict(cur.fetchone() or {})
        # Tool stats (graceful if table does not exist yet)
        try:
            cur.execute(sql_tools)
            tool_stats = dict(cur.fetchone() or {})
        except Exception:  # noqa: BLE001
            tool_stats = {"total_calls": 0, "failures": 0, "persona_invocations": 0}
        # Reflection stats (graceful if table does not exist yet)
        try:
            cur.execute(sql_reflection)
            reflection_stats = dict(cur.fetchone() or {})
        except Exception:  # noqa: BLE001
            reflection_stats = {"total_rounds": 0, "approved": 0, "rejected": 0, "withdrawn": 0}
        # SPEC-010: Per-model breakdown (graceful if column does not exist yet)
        try:
            cur.execute(sql_model_breakdown)
            model_breakdown = [dict(r) for r in cur.fetchall()]
        except Exception:  # noqa: BLE001
            model_breakdown = []
    return {
        "today": today.isoformat(),
        "orders": orders,
        "runs": runs,
        "risk": risk,
        "cost": cost,
        "cumulative": cum,
        "tool_stats": tool_stats,
        "reflection_stats": reflection_stats,
        "model_breakdown": model_breakdown,
    }


def _fallback_text(data: dict[str, Any]) -> str:
    orders = data["orders"]
    runs = data["runs"]
    risk = data["risk"]
    cost = data.get("cost") or {}
    cum = data.get("cumulative") or {}
    tool_stats = data.get("tool_stats") or {}
    reflection_stats = data.get("reflection_stats") or {}
    model_breakdown = data.get("model_breakdown") or []
    n_buys = sum(1 for o in orders if o["side"] == "buy")
    n_sells = sum(1 for o in orders if o["side"] == "sell")
    persona_cost_total = sum(float(r.get("cost") or 0) for r in runs)
    in_tok_total = sum(int(r.get("in_tok") or 0) for r in runs)
    cache_read_total = sum(int(r.get("cache_read") or 0) for r in runs)
    cache_create_total = sum(int(r.get("cache_create") or 0) for r in runs)
    cache_hit_pct = (cache_read_total / in_tok_total * 100) if in_tok_total else 0.0
    risk_str = ", ".join(f"{r['verdict']}={r['n']}" for r in risk) or "—"

    exec_fee = int(cost.get("exec_fee_total") or 0)
    attempted_fee = int(cost.get("attempted_fee_total") or 0)
    week_orders = int(cum.get("week_orders") or 0)
    week_fee = int(cum.get("week_fee") or 0)
    month_orders = int(cum.get("month_orders") or 0)
    month_fee = int(cum.get("month_fee") or 0)

    # SPEC-009 REQ-PTOOL-02-9: Tool usage summary
    tool_total = int(tool_stats.get("total_calls") or 0)
    tool_failures = int(tool_stats.get("failures") or 0)
    tool_invocations = int(tool_stats.get("persona_invocations") or 0)
    tool_avg = (tool_total / tool_invocations) if tool_invocations else 0.0
    tool_line = f"Tool 호출: 총 {tool_total}회, 평균 {tool_avg:.1f}회/페르소나, 실패 {tool_failures}건"

    # SPEC-009 REQ-REFL-03-7: Reflection summary
    refl_total = int(reflection_stats.get("total_rounds") or 0)
    refl_approved = int(reflection_stats.get("approved") or 0)
    refl_rejected = int(reflection_stats.get("rejected") or 0)
    refl_withdrawn = int(reflection_stats.get("withdrawn") or 0)
    refl_line = (
        f"Reflection: 시도 {refl_total}건, 성공(APPROVE) {refl_approved}건, "
        f"최종 REJECT {refl_rejected}건, 철회 {refl_withdrawn}건"
    )

    # REQ-NFR-09-4: Observability metrics
    refl_success_rate = (refl_approved / refl_total * 100) if refl_total else 0.0

    # SPEC-010 REQ-COST-04-1: Per-model cost breakdown
    model_lines: list[str] = []
    for mb in model_breakdown:
        model_name = (mb.get("model") or "unknown").split("-")[-1]  # e.g., "haiku-4-5" -> "4-5"
        short_name = mb.get("model", "unknown")
        if "opus" in short_name:
            short_name = "Opus"
        elif "sonnet" in short_name:
            short_name = "Sonnet"
        elif "haiku" in short_name:
            short_name = "Haiku"
        n = int(mb.get("n") or 0)
        mc = float(mb.get("cost") or 0)
        model_lines.append(f"  {short_name}: {n}건 × {mc/n:,.0f}원 = {mc:,.0f}원" if n else "")
    model_section = "\n".join(l for l in model_lines if l)
    if not model_section:
        model_section = "  (모델별 내역 없음)"

    return (
        f"[일일 리포트 · {data['today']}]\n"
        f"매매: 매수 {n_buys} / 매도 {n_sells} (총 {len(orders)}건, 체결 {cost.get('executed_count', 0)}건)\n"
        f"매매 비용 추정: 체결분 {exec_fee:,}원 (시도 합계 {attempted_fee:,}원)\n"
        f"페르소나 비용: {persona_cost_total:,.0f}원\n"
        f"[모델별 내역]\n{model_section}\n"
        f"캐시 적중률: {cache_hit_pct:.1f}% (read {cache_read_total:,} / total {in_tok_total:,} 토큰, SPEC-008)\n"
        f"Risk verdict: {risk_str}\n"
        f"{tool_line}\n"
        f"{refl_line}\n"
        f"Observability: tool_calls_total={tool_total}, tool_failures={tool_failures}, "
        f"reflection_rounds={refl_total}, reflection_success_rate={refl_success_rate:.1f}%\n"
        f"누적 (7D/30D): 매매 {week_orders}/{month_orders}건, 수수료 {week_fee:,}/{month_fee:,}원\n"
        "(Anthropic API 미구성 또는 호출 실패로 LLM 요약 생략)"
    )


def _llm_text(data: dict[str, Any]) -> str:
    s = get_settings()
    if s.anthropic.api_key is None:
        raise RuntimeError("ANTHROPIC_API_KEY missing")
    client = Anthropic(api_key=s.anthropic.api_key.get_secret_value())
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=(
            "당신은 한국 주식 자동매매 시스템의 일일 리포트 작성자입니다. "
            "박세훈 본인에게 한국어로 5~8줄, 사실 기반으로 요약하세요. "
            "오늘 매매·PnL 추정·페르소나 활동·SoD 통계를 포함하고, "
            "환각 금지·새로운 분석 금지·이미 일어난 일만 요약하세요. "
            "**모든 금액은 한국 원화(KRW)로 표시한다. '원' 또는 '₩'만 사용. "
            "USD($) 표기 절대 금지.** persona_runs.cost_krw 와 orders.fee 모두 이미 원화 단위. "
            "주문 거부 사유는 데이터의 rejected_reason 필드에 명시되어 있으니 추측하지 말고 그대로 인용. "
            "거부 사유가 '모의투자 영업일이 아닙니다' 또는 '장시작전' 또는 '장종료'면 시스템 점검 필요 없음."
        ),
        messages=[{"role": "user", "content": json.dumps(data, ensure_ascii=False, default=str)}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def generate_and_send() -> str:
    data = _gather_today()
    try:
        text = _llm_text(data)
    except Exception as e:  # noqa: BLE001
        LOG.warning("daily report LLM failed (using fallback): %s", e)
        text = _fallback_text(data)

    # Persist
    sql = """
        INSERT INTO daily_reports (trading_day, summary, details)
        VALUES (CURRENT_DATE, %s, %s::jsonb)
        ON CONFLICT (trading_day) DO UPDATE SET summary=EXCLUDED.summary, details=EXCLUDED.details
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (text, json.dumps(data, default=str)))

    try:
        system_briefing("일일 리포트", text)
    except Exception:  # noqa: BLE001
        LOG.exception("daily report telegram send failed")
    return text
