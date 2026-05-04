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
        SELECT persona_name, COUNT(*) AS n, SUM(cost_krw) AS cost
          FROM persona_runs WHERE ts::date = CURRENT_DATE GROUP BY persona_name
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
    return {
        "today": today.isoformat(),
        "orders": orders,
        "runs": runs,
        "risk": risk,
        "cost": cost,
        "cumulative": cum,
    }


def _fallback_text(data: dict[str, Any]) -> str:
    orders = data["orders"]
    runs = data["runs"]
    risk = data["risk"]
    cost = data.get("cost") or {}
    cum = data.get("cumulative") or {}
    n_buys = sum(1 for o in orders if o["side"] == "buy")
    n_sells = sum(1 for o in orders if o["side"] == "sell")
    persona_cost_total = sum(float(r.get("cost") or 0) for r in runs)
    risk_str = ", ".join(f"{r['verdict']}={r['n']}" for r in risk) or "—"

    exec_fee = int(cost.get("exec_fee_total") or 0)
    attempted_fee = int(cost.get("attempted_fee_total") or 0)
    week_orders = int(cum.get("week_orders") or 0)
    week_fee = int(cum.get("week_fee") or 0)
    month_orders = int(cum.get("month_orders") or 0)
    month_fee = int(cum.get("month_fee") or 0)

    return (
        f"[일일 리포트 · {data['today']}]\n"
        f"매매: 매수 {n_buys} / 매도 {n_sells} (총 {len(orders)}건, 체결 {cost.get('executed_count', 0)}건)\n"
        f"매매 비용 추정: 체결분 {exec_fee:,}원 (시도 합계 {attempted_fee:,}원)\n"
        f"페르소나 비용: {persona_cost_total:,.0f}원\n"
        f"Risk verdict: {risk_str}\n"
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
