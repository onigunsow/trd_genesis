"""Daily report (REQ-REPORT-05-6) — generated at 16:00 KST every Korean trading day.

Even with zero trades, the report is generated and sent. If Anthropic API is
unavailable (no credits, network error), a plain-text fallback is used.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

from anthropic import Anthropic

from trading.alerts.telegram import system_briefing
from trading.config import get_settings, project_root
from trading.db.session import connection
from trading.kis.account import balance
from trading.kis.client import KisClient
from trading.personas.base import block_if_cli_only_mode, call_persona_via_cli

LOG = logging.getLogger(__name__)

# SPEC-TRADING-030 REQ-030-1(c): tunable digest caps. Macro file is small
# (~9KB) so we keep more stories; micro is large (~38KB) so we cap tighter and
# mark the omitted tail. Both are sorted by Impact (5..1) before truncation.
N_MACRO = 10
N_MICRO = 12

# REQ-030-9a: an intelligence_*.md is considered usable only when it actually
# contains parseable stories. ``_read_context_md`` itself returns a placeholder
# string for a missing file; a present-but-empty (or stale weekend cadence)
# file yields zero stories which we surface as a section placeholder.


# --------------------------------------------------------------------------- #
# SPEC-TRADING-030 — intelligence digest (REQ-030-1)                          #
# --------------------------------------------------------------------------- #

# Story header: ``### [투자 주목] <title> (Impact: N/5)``. The title itself may
# embed parenthetical text (including a literal "(Impact: 미정)"), so we anchor
# on the LAST ``(Impact: N/5)`` on the line and take everything before it as the
# title. ``$`` anchors to end-of-line so an embedded impact-like token is never
# mistaken for the real one.
_HEADER_RE = re.compile(r"^###\s*\[투자 주목\]\s*(.+?)\s*\(Impact:\s*(\d+)\s*/\s*5\)\s*$")
_META_RE = re.compile(r"Keywords:\s*(.+?)\s*_?\s*$")
_STRATEGY_RE = re.compile(r"^→\s*(.+?)\s*$")


def _read_context_md(name: str) -> str:
    """Read a ``data/contexts/*.md`` file (read-only reuse, REQ-030-8).

    Mirrors ``trading.personas.context._read_md``: returns a human-readable
    placeholder string when the file is absent rather than raising, so the
    daily report never crashes on a missing intelligence file.
    """
    p = project_root() / "data" / "contexts" / name
    if not p.exists():
        return f"_({name} 미생성 — cron 미실행 또는 첫 운영)_"
    try:
        return p.read_text(encoding="utf-8")
    except Exception as e:
        return f"_({name} 읽기 실패: {e})_"


def _parse_intel_stories(md: str) -> list[dict[str, Any]]:
    """Parse ``[투자 주목]`` stories from an intelligence_*.md document.

    REQ-030-1(b): each story preserves title / impact / keywords / strategy(→).
    Returns stories sorted by Impact descending (stable for equal impact).
    """
    lines = md.splitlines()
    stories: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        m = _HEADER_RE.match(lines[i].strip())
        if not m:
            i += 1
            continue
        title = m.group(1).strip()
        impact = int(m.group(2))
        keywords = ""
        strategy = ""
        # Scan the next few lines for the meta (Keywords) and strategy (→) rows,
        # stopping at the next story header or a blank gap.
        j = i + 1
        while j < len(lines) and j <= i + 4:
            stripped = lines[j].strip()
            if stripped.startswith("###"):
                break
            km = _META_RE.search(stripped)
            if km and not keywords:
                keywords = km.group(1).strip().rstrip("_").strip()
            sm = _STRATEGY_RE.match(stripped)
            if sm and not strategy:
                strategy = sm.group(1).strip()
            j += 1
        stories.append(
            {"title": title, "impact": impact, "keywords": keywords, "strategy": strategy}
        )
        i = j
    # REQ-030-1(a): Impact descending. ``sorted`` is stable, preserving source
    # order among equal-impact stories.
    stories.sort(key=lambda s: s["impact"], reverse=True)
    return stories


def _intel_digest_stories(md: str, top_n: int) -> tuple[list[dict[str, Any]], str]:
    """Return (top-N stories by Impact, truncation marker).

    REQ-030-1(a): when more than ``top_n`` stories exist, the surplus is dropped
    and a ``"(+M건 저영향 생략)"`` marker is produced. Marker is "" when nothing
    was dropped.
    """
    stories = _parse_intel_stories(md)
    if len(stories) <= top_n:
        return stories, ""
    omitted = len(stories) - top_n
    return stories[:top_n], f"(+{omitted}건 저영향 생략)"


def _gather_intelligence() -> dict[str, Any]:
    """Collect macro + micro intelligence digests (REQ-030-1).

    Read-only reuse of the pre-computed intelligence_*.md (REQ-030-8). A missing
    or empty file degrades that section to ``status='missing'`` with no stories
    (REQ-030-9a); the narrative generator marks the corresponding 총평 section.
    """
    out: dict[str, Any] = {}
    for key, fname, cap in (
        ("macro", "intelligence_macro.md", N_MACRO),
        ("micro", "intelligence_micro.md", N_MICRO),
    ):
        md = _read_context_md(fname)
        stories, marker = _intel_digest_stories(md, top_n=cap)
        status = "ok" if stories else "missing"
        out[key] = {"status": status, "stories": stories, "marker": marker}
    return out


# --------------------------------------------------------------------------- #
# SPEC-TRADING-030 — portfolio collection (REQ-030-2)                          #
# --------------------------------------------------------------------------- #

def _collect_portfolio() -> dict[str, Any]:
    """Collect KIS holdings + P&L via ``account.balance`` (REQ-030-2).

    REQ-030-9b: on any failure (``KisError`` or otherwise) returns a safe
    placeholder (``status='error'``, empty holdings) instead of propagating, so
    the 16:00 cron is never crashed by a live KIS outage.
    """
    try:
        client = KisClient(get_settings().trading_mode)
        bal = balance(client)
    except Exception as e:
        # REQ-030-9b: never crash the daily report on a live KIS outage.
        LOG.warning("daily report portfolio fetch failed: %s", e)
        return {"status": "error", "holdings": [], "error": str(e)[:200]}
    return {
        "status": "ok",
        "holdings": bal.get("holdings", []),
        "total_assets": bal.get("total_assets", 0),
        "cash_d2": bal.get("cash_d2", 0),
        "stock_eval": bal.get("stock_eval", 0),
        "invest_basis": bal.get("invest_basis", 0),
        "pnl_total": bal.get("pnl_total", 0),
    }


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
    # SPEC-023 REQ-023-6 (c, d): today's auto-expansion events.
    sql_auto_expansion = """
        SELECT ticker FROM dynamic_tickers
         WHERE first_seen_at::date = CURRENT_DATE
         ORDER BY ticker
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
        except Exception:
            tool_stats = {"total_calls": 0, "failures": 0, "persona_invocations": 0}
        # Reflection stats (graceful if table does not exist yet)
        try:
            cur.execute(sql_reflection)
            reflection_stats = dict(cur.fetchone() or {})
        except Exception:
            reflection_stats = {"total_rounds": 0, "approved": 0, "rejected": 0, "withdrawn": 0}
        # SPEC-010: Per-model breakdown (graceful if column does not exist yet)
        try:
            cur.execute(sql_model_breakdown)
            model_breakdown = [dict(r) for r in cur.fetchall()]
        except Exception:
            model_breakdown = []
        # SPEC-023: today's auto-expansion events (graceful if table missing).
        try:
            cur.execute(sql_auto_expansion)
            auto_expansion_tickers = [
                row["ticker"] if isinstance(row, dict) else row[0]
                for row in cur.fetchall()
            ]
        except Exception:
            auto_expansion_tickers = []
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
        "auto_expansion_tickers": auto_expansion_tickers,
        # SPEC-TRADING-030: qualitative-review source material.
        "intelligence": _gather_intelligence(),  # REQ-030-1
        "portfolio": _collect_portfolio(),        # REQ-030-2
    }


def _fallback_text(data: dict[str, Any], skip_reason: str | None = None) -> str:
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

    # SPEC-023 REQ-023-6 (c): auto-expansion line — emit only when non-empty.
    auto_expansion = sorted(data.get("auto_expansion_tickers") or [])
    auto_exp_line = ""
    if auto_expansion:
        joined = ", ".join(auto_expansion)
        auto_exp_line = f"오늘 auto-expansion: {len(auto_expansion)}건 (티커: {joined})\n"

    # SPEC-TRADING-030: the skip-reason line is part of the DEGRADE path only.
    # When ``_fallback_text`` is reused as the operational-metrics block beneath
    # a successfully generated narrative, ``skip_reason`` is None and we must NOT
    # print "(LLM 요약 생략)" — that would falsely imply the narrative failed.
    skip_line = f"\n({skip_reason})" if skip_reason else ""

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
        f"{auto_exp_line}"
        f"누적 (7D/30D): 매매 {week_orders}/{month_orders}건, 수수료 {week_fee:,}/{month_fee:,}원"
        f"{skip_line}"
    )


def format_benchmark_section(benchmark: object) -> str:
    """SPEC-TRADING-044 M4 — KOSPI 매수후보유 누적 초과수익 섹션 (REQ-044-B1, B2, B3).

    CLI-only 경로(비용 0)에서 일일 리포트에 포함된다 (SPEC-030 패턴).
    benchmark.py의 Benchmark 객체를 받아 텍스트를 생성한다 (병렬 경로 없음, REQ-044-B4).
    """
    from trading.edge.benchmark import Benchmark  # lazy import (circular 방지)

    if not isinstance(benchmark, Benchmark):
        return "전략 vs KOSPI: 데이터 없음 — 알파 미확인\n"

    if not benchmark.available:
        return "전략 vs KOSPI 매수후보유 누적 초과수익: 알파 미확인 (KOSPI 데이터 없음)\n"

    excess = benchmark.cumulative_excess_return_pct
    sign = "+" if excess >= 0 else ""
    basis = benchmark.comparison_basis or "money-weighted(원가기준 집계)"
    strat_pct = benchmark.strategy_return_pct
    kospi_pct = benchmark.kospi_return_pct
    return (
        f"전략 vs KOSPI 매수후보유 누적 초과수익: {sign}{excess:.1f}%p\n"
        f"  (기간: {benchmark.start} ~ {benchmark.end}, 기준: {basis})\n"
        f"  전략 {strat_pct:+.2f}% vs KOSPI {kospi_pct:+.2f}%\n"
    )


@block_if_cli_only_mode
def _llm_text(data: dict[str, Any]) -> str:
    """DEPRECATED (SPEC-TRADING-030): direct-API daily summary.

    Superseded by ``_narrative_text`` which runs over the zero-cost CLI
    subscription path and therefore works under ``cli_only_mode``.
    ``generate_and_send`` no longer calls this function. It is retained (still
    guarded by ``@block_if_cli_only_mode``) only so existing SPEC-016 tests and
    any non-cli-only deployment keep a working direct-API path; do NOT add new
    callers (REQ-030-7).

    SPEC-TRADING-016 REQ-016-1-3: This is one of two remaining direct API
    callers identified during the SPEC-015 audit. The
    ``@block_if_cli_only_mode`` decorator ensures the call fails loudly under
    cli_only_mode rather than silently burning the Anthropic budget. We chose
    the decorator over a CLI bridge migration because this function returns
    free-form Korean prose (not structured JSON) and writes nothing to
    ``persona_runs`` — it does not fit the persona-pipeline contract that
    ``call_persona_via_cli`` enforces.

    When ``cli_only_mode`` is active, the existing ``_fallback_text`` plain
    template (used in ``generate_and_send`` when this function raises) keeps
    the daily report flowing without LLM input.
    """
    s = get_settings()
    if s.anthropic.api_key is None:
        raise RuntimeError("ANTHROPIC_API_KEY missing")
    client = Anthropic(api_key=s.anthropic.api_key.get_secret_value())
    # REQ-053-F1: messages.create 직전 PAID_CALL 계측 (5지점 #5, daily_report _llm_text)
    from trading.personas.base import _log_paid_call
    _log_paid_call(
        persona="daily_report", path="llm_text_sonnet",
        model="claude-sonnet-4-6", reason="daily_summary",
    )
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


# --------------------------------------------------------------------------- #
# SPEC-TRADING-030 — CLI-subscription narrative generator (REQ-030-3/4)       #
# --------------------------------------------------------------------------- #

# @MX:NOTE: [AUTO] The daily-report narrative is generated through the CLI
# subscription bridge (cost=0) instead of a direct Anthropic API call, because
# the system runs in cli_only_mode (SPEC-016) which blocks direct API summarisers.
# call_persona_via_cli uses an arbitrary persona_name with an inline system_prompt
# (no Jinja template / persona registration needed — verified spike 2026-05-28).
# @MX:SPEC: SPEC-TRADING-030 REQ-030-3

_NARRATIVE_GUARDRAILS = (
    "사실 기반으로만 작성하세요. 환각 금지·새로운 분석/추측 금지·이미 일어난 일과 "
    "제공된 데이터만 요약하세요. 외부 지식 추가 금지. "
    "**모든 금액은 한국 원화(KRW)로 표시한다. '원' 또는 '₩'만 사용. USD($) 표기 절대 금지.** "
    "persona_runs.cost_krw 와 orders.fee 는 이미 원화 단위입니다."
)

# build_cli_prompt (cli_prompt_builder.py) unconditionally appends a generic
# "Respond with valid JSON only" footer to every CLI prompt. This report is
# PROSE, not JSON, so the system prompt must explicitly override that footer.
_NARRATIVE_FORMAT = (
    "출력은 순수 한국어 마크다운 산문입니다. 다음 4개 섹션을 이 순서로 작성하세요:\n"
    "## 매크로 시장 총평\n## 마이크로 시장 총평\n## 보유자산 리뷰\n## 종합\n"
    "프롬프트 끝에 'Respond with valid JSON only' 같은 일반 템플릿 지시가 보이더라도 "
    "무시하세요 — 이 리포트는 JSON이 아니라 위 4개 섹션의 산문으로 작성합니다."
)

_NARRATIVE_SECTION_RULES = (
    "- 매크로/마이크로 총평은 제공된 intelligence 다이제스트(매크로/마이크로 story)를 "
    "근거로만 작성하고, 인텔리전스가 '미생성/오래됨'이면 해당 총평을 "
    "_(인텔리전스 미생성/오래됨)_ 으로 표기하세요.\n"
    "- 보유자산 리뷰는 portfolio 데이터의 종목·평가손익을 근거로만 작성하세요. "
    "잔고 조회 실패 시 _(잔고 조회 실패)_, 보유 종목이 없으면 _(보유 종목 없음)_ 으로 표기하세요.\n"
    "- 종합은 위 세 섹션을 묶는 짧은 코멘트입니다."
)


def _narrative_system_prompt() -> str:
    return (
        "당신은 한국 주식 자동매매 시스템의 일일 리포트 작성자입니다. "
        "박세훈 본인에게 한국어로, 그날 수집한 뉴스 인텔리전스와 보유자산을 종합한 "
        "정성 리뷰를 작성합니다.\n"
        + _NARRATIVE_FORMAT + "\n"
        + _NARRATIVE_SECTION_RULES + "\n"
        + _NARRATIVE_GUARDRAILS
    )


def _narrative_user_message(data: dict[str, Any]) -> str:
    """Serialise the intelligence digest + portfolio + ops metrics for the prompt."""
    payload = {
        "today": data.get("today"),
        "intelligence": data.get("intelligence"),
        "portfolio": data.get("portfolio"),
        # Operational metrics already collected by _gather_today; the narrative
        # may reference today's trades / persona activity at a high level.
        "operations": {
            "orders": data.get("orders"),
            "risk": data.get("risk"),
            "cost": data.get("cost"),
            "cumulative": data.get("cumulative"),
        },
    }
    return (
        "아래 JSON 데이터를 근거로 4개 섹션(매크로/마이크로/보유자산/종합)을 작성하세요.\n"
        + json.dumps(payload, ensure_ascii=False, default=str)
    )


def _narrative_text(data: dict[str, Any]) -> str:
    """Generate the 3-section qualitative narrative via the CLI subscription path.

    REQ-030-3: routes through ``call_persona_via_cli`` (cost=0, works under
    cli_only_mode) rather than a direct Anthropic API call. Returns the
    free-form prose from ``PersonaResult.response_text``. May raise
    ``RuntimeError`` on CLI + Haiku double failure — the caller
    (``generate_and_send``) degrades gracefully (REQ-030-6).
    """
    result = call_persona_via_cli(
        persona_name="daily_report",
        model="cli-claude-max",
        cycle_kind="daily",
        system_prompt=_narrative_system_prompt(),
        user_message=_narrative_user_message(data),
        expect_json=False,
        apply_memory_ops=False,
        input_data=data,
    )
    return result.response_text


def _llm_skip_reason(exc: Exception) -> str:
    """SPEC-TRADING-026: human-accurate reason the LLM summary was skipped.

    The previous hardcoded "(Anthropic API 미구성 또는 호출 실패)" was misleading —
    in ``cli_only_mode`` the direct API call is blocked *by design* (the bot
    runs personas on the Claude CLI subscription), it did not fail. Surface the
    real reason so the daily report is not mistaken for a broken system.
    """
    msg = str(exc)
    if "cli_only_mode" in msg:
        return "CLI 전용 모드 — 직접 API 호출 차단(정상), 결정형 요약 사용"
    if "ANTHROPIC_API_KEY" in msg:
        return "ANTHROPIC_API_KEY 미설정 — LLM 요약 생략"
    return f"LLM 요약 생성 실패: {msg[:120]}"


def generate_and_send() -> str:
    """Build and dispatch the 16:00 daily report (REQ-030-5/6).

    SPEC-TRADING-030: composes the qualitative narrative (top) over the
    operational-metrics block (bottom). The narrative is generated via the CLI
    subscription path; on CLI + Haiku double failure it degrades to the
    metrics-only ``_fallback_text`` so the cron never crashes.

    SPEC-TRADING-055 D1 [CRITICAL]: resolver 헬스체크는 리포트 본문·전송을
    절대 중단할 수 없다.  evaluate_resolver_health() 와
    maybe_notify_resolver_anomaly() 를 각각 독립 try/except 로 감싸고,
    예외 발생 시 degraded 라인으로 폴백한다.
    """
    # SPEC-TRADING-055 D1: resolver 헬스 평가 — 실패해도 리포트 계속.
    _HEALTH_DEGRADE_PREFIX = "운영점검: 평가 실패"
    try:
        from trading.ops.resolver_health import evaluate_resolver_health, summary_line
        health = evaluate_resolver_health()
        health_line = summary_line(health)
    except Exception as _he:
        LOG.exception("resolver_health 평가 실패 — degraded 라인으로 폴백")
        health = None
        health_line = f"{_HEALTH_DEGRADE_PREFIX} — {_he}"

    data = _gather_today()
    try:
        narrative = _narrative_text(data)  # REQ-030-3
        # REQ-030-5: narrative headline on top, operational-metrics block below.
        text = f"{narrative}\n\n———\n{_fallback_text(data)}\n{health_line}"
    except Exception as e:
        reason = _llm_skip_reason(e)
        LOG.warning("daily report narrative skipped (%s)", reason)
        # REQ-030-6: degrade to metrics-only with a human-readable skip reason.
        text = _fallback_text(data, skip_reason=reason) + f"\n{health_line}"

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
    except Exception:
        LOG.exception("daily report telegram send failed")

    # SPEC-TRADING-055 D1: 이상 경고 — 리포트 전송 완료 후 별도 발송.
    # 실패해도 generate_and_send 반환값에 영향 없음.
    if health is not None:
        try:
            from trading.ops.resolver_health import maybe_notify_resolver_anomaly
            maybe_notify_resolver_anomaly(health)
        except Exception:
            LOG.exception("resolver_health 이상 경고 발송 실패 — 리포트 영향 없음")

    return text
