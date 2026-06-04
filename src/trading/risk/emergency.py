"""Emergency commands handled by the Telegram bot listener.

Handlers receive the user message text and return a short Korean response.
Commands honoured (REQ-RISK-05-4):
- /halt    set halt_state=true
- /resume  set halt_state=false
- /status  print system_state + recent persona run count
- /pnl     print today's PnL (best-effort estimate)
- /verbose exit silent_mode (REQ-FATIGUE-05-10)
- /silent  enter silent_mode manually
- /tool_calling on|off (alias /tool-calling)  toggle tool-calling mode (REQ-COMPAT-04-7)
- /reflection on|off    toggle reflection loop (REQ-COMPAT-04-7)
- /car_filter on|off (alias /car-filter)      toggle Event-CAR filter (SPEC-012 REQ-MIGR-07-3)
- /dyn_threshold on|off (alias /dyn-threshold)  toggle dynamic thresholds (SPEC-012 REQ-MIGR-07-3)
- /jit on|off|ws|dart|news  toggle JIT pipeline (SPEC-011 REQ-MIGR-06-3)
- /prototype on|off     toggle prototype risk (SPEC-011 REQ-MIGR-06-3)
- /prototype_status (alias /prototype-status)  show ProtoHedge status (SPEC-011 REQ-DYNRISK-04-10)
- /cli on|off           toggle CLI persona mode (SPEC-015 REQ-FALLBACK-06-5)
- /help    list commands
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from trading.config import get_settings
from trading.db.session import audit, connection, get_system_state, update_system_state
from trading.kis.account import balance
from trading.kis.client import KisClient
from trading.risk import circuit_breaker as cb

LOG = logging.getLogger(__name__)


def handle(text: str, actor: str = "telegram") -> str:
    cmd = (text or "").strip().split()[0].lower() if text else ""
    if cmd == "/halt":
        cb.trip(reason="manual /halt", details={"actor": actor})
        return "✓ halt_state=true. 신규 주문 차단됨."
    if cmd == "/resume":
        cb.reset(actor=actor)
        return "✓ halt_state=false. 매매 재개 가능."
    if cmd == "/status":
        state = get_system_state()
        return (
            f"trading_mode={state['trading_mode']} | "
            f"halt={state['halt_state']} | silent={state['silent_mode']} | "
            f"live_unlocked={state['live_unlocked']} | "
            f"cli={state.get('cli_personas_enabled', False)}"
        )
    if cmd == "/pnl":
        return _pnl_summary()
    if cmd == "/holdings":
        return _holdings_summary()
    if cmd == "/verbose":
        update_system_state(silent_mode=False, updated_by=actor)
        audit("SILENT_MODE_OFF", actor=actor, details={})
        return "✓ silent_mode=false. 모든 페르소나 브리핑 발송 재개."
    if cmd == "/silent":
        update_system_state(silent_mode=True, updated_by=actor)
        audit("SILENT_MODE_ON", actor=actor, details={"reason": "manual"})
        return "✓ silent_mode=true. 주요 이벤트만 발송."
    # SPEC-027: cycle-chain briefing detail toggle (separate from silent_mode).
    if cmd == "/detail":
        update_system_state(verbose_briefing=True, updated_by=actor)
        audit("VERBOSE_BRIEFING_ON", actor=actor, details={})
        return "✓ verbose_briefing=true. 사이클 요약 + 페르소나별 상세 브리핑 발송."
    if cmd == "/brief":
        update_system_state(verbose_briefing=False, updated_by=actor)
        audit("VERBOSE_BRIEFING_OFF", actor=actor, details={})
        return "✓ verbose_briefing=false. 사이클 요약(통합)만 발송."
    # SPEC-009 REQ-COMPAT-04-7: Tool-calling and reflection toggle commands.
    # Underscore aliases let these be registered in Telegram's command menu
    # (which requires [a-z0-9_]); the hyphen forms remain valid (non-breaking).
    if cmd in ("/tool-calling", "/tool_calling"):
        return _handle_tool_calling(text, actor)
    if cmd == "/reflection":
        return _handle_reflection(text, actor)
    # SPEC-012 REQ-MIGR-07-3: CAR filter and dynamic thresholds toggle commands
    if cmd in ("/car-filter", "/car_filter"):
        return _handle_car_filter(text, actor)
    if cmd in ("/dyn-threshold", "/dyn_threshold"):
        return _handle_dyn_threshold(text, actor)
    # SPEC-011 REQ-MIGR-06-3: JIT pipeline and prototype toggle commands
    if cmd == "/jit":
        return _handle_jit(text, actor)
    if cmd == "/prototype":
        return _handle_prototype(text, actor)
    if cmd in ("/prototype-status", "/prototype_status"):
        return _handle_prototype_status()
    # SPEC-015 REQ-FALLBACK-06-5: CLI persona mode toggle
    if cmd == "/cli":
        return _handle_cli_toggle(text, actor)
    if cmd in ("/help", "/start"):
        return _help()
    return f"unknown command: {cmd or '(empty)'}\n{_help()}"


def _handle_car_filter(text: str, actor: str) -> str:
    """Toggle CAR filter feature flag (SPEC-012 REQ-MIGR-07-3)."""
    parts = text.strip().split()
    if len(parts) < 2 or parts[1].lower() not in ("on", "off"):
        return "사용법: /car_filter on|off"
    enable = parts[1].lower() == "on"
    update_system_state(car_filter_enabled=enable, updated_by=actor)
    event = "CAR_FILTER_ENABLED" if enable else "CAR_FILTER_DISABLED"
    audit(event, actor=actor, details={"enabled": enable})
    status = "활성화" if enable else "비활성화"
    return f"✓ car_filter_enabled={enable}. Event-CAR Filter {status}됨."


def _handle_dyn_threshold(text: str, actor: str) -> str:
    """Toggle dynamic thresholds feature flag (SPEC-012 REQ-MIGR-07-3)."""
    parts = text.strip().split()
    if len(parts) < 2 or parts[1].lower() not in ("on", "off"):
        return "사용법: /dyn_threshold on|off"
    enable = parts[1].lower() == "on"
    update_system_state(dynamic_thresholds_enabled=enable, updated_by=actor)
    event = "DYNAMIC_THRESHOLDS_ENABLED" if enable else "DYNAMIC_THRESHOLDS_DISABLED"
    audit(event, actor=actor, details={"enabled": enable})
    status = "활성화" if enable else "비활성화"
    return f"✓ dynamic_thresholds_enabled={enable}. Dynamic Thresholds {status}됨."


def _handle_tool_calling(text: str, actor: str) -> str:
    """Toggle tool-calling feature flag (REQ-COMPAT-04-7)."""
    parts = text.strip().split()
    if len(parts) < 2 or parts[1].lower() not in ("on", "off"):
        return "사용법: /tool_calling on|off"
    enable = parts[1].lower() == "on"
    update_system_state(tool_calling_enabled=enable, updated_by=actor)
    event = "TOOL_CALLING_ACTIVATED" if enable else "TOOL_CALLING_DEACTIVATED"
    audit(event, actor=actor, details={"enabled": enable})
    status = "활성화" if enable else "비활성화"
    return f"✓ tool_calling_enabled={enable}. Tool-calling {status}됨."


def _handle_reflection(text: str, actor: str) -> str:
    """Toggle reflection loop feature flag (REQ-COMPAT-04-7)."""
    parts = text.strip().split()
    if len(parts) < 2 or parts[1].lower() not in ("on", "off"):
        return "사용법: /reflection on|off"
    enable = parts[1].lower() == "on"
    update_system_state(reflection_loop_enabled=enable, updated_by=actor)
    event = "REFLECTION_LOOP_ACTIVATED" if enable else "REFLECTION_LOOP_DEACTIVATED"
    audit(event, actor=actor, details={"enabled": enable})
    status = "활성화" if enable else "비활성화"
    return f"✓ reflection_loop_enabled={enable}. Reflection Loop {status}됨."


def _handle_jit(text: str, actor: str) -> str:
    """Toggle JIT pipeline feature flags (SPEC-011 REQ-MIGR-06-3)."""
    parts = text.strip().split()
    if len(parts) < 2:
        return "사용법: /jit on|off | /jit ws|dart|news on|off"

    sub = parts[1].lower()

    # Master toggle: /jit on|off
    if sub in ("on", "off"):
        enable = sub == "on"
        update_system_state(jit_pipeline_enabled=enable, updated_by=actor)
        event = "JIT_PIPELINE_ENABLED" if enable else "JIT_PIPELINE_DISABLED"
        audit(event, actor=actor, details={"enabled": enable})
        if not enable:
            # Stop pipeline if running
            try:
                from trading.jit.pipeline import get_pipeline
                get_pipeline().stop()
            except Exception:
                pass
        status = "활성화" if enable else "비활성화"
        return f"JIT Pipeline {status}됨. WebSocket은 다음 장 개시 시 연결됩니다."

    # Sub-toggle: /jit ws|dart|news on|off
    if len(parts) < 3 or parts[2].lower() not in ("on", "off"):
        return "사용법: /jit ws|dart|news on|off"

    enable = parts[2].lower() == "on"

    if sub == "ws":
        update_system_state(jit_websocket_enabled=enable, updated_by=actor)
        audit("JIT_WEBSOCKET_TOGGLED", actor=actor, details={"enabled": enable})
        if not enable:
            try:
                from trading.jit.pipeline import get_pipeline
                get_pipeline().stop_websocket()
            except Exception:
                pass
        status = "활성화" if enable else "비활성화"
        return f"WebSocket {status}됨. DART/News 폴링은 유지됩니다."

    elif sub == "dart":
        update_system_state(jit_dart_polling_enabled=enable, updated_by=actor)
        audit("JIT_DART_TOGGLED", actor=actor, details={"enabled": enable})
        if not enable:
            try:
                from trading.jit.pipeline import get_pipeline
                get_pipeline().stop_dart()
            except Exception:
                pass
        status = "활성화" if enable else "비활성화"
        return f"DART 폴링 {status}됨."

    elif sub == "news":
        update_system_state(jit_news_polling_enabled=enable, updated_by=actor)
        audit("JIT_NEWS_TOGGLED", actor=actor, details={"enabled": enable})
        if not enable:
            try:
                from trading.jit.pipeline import get_pipeline
                get_pipeline().stop_news()
            except Exception:
                pass
        status = "활성화" if enable else "비활성화"
        return f"News 폴링 {status}됨."

    return "사용법: /jit on|off | /jit ws|dart|news on|off"


def _handle_prototype(text: str, actor: str) -> str:
    """Toggle prototype risk feature flag (SPEC-011 REQ-MIGR-06-3)."""
    parts = text.strip().split()
    if len(parts) < 2 or parts[1].lower() not in ("on", "off"):
        return "사용법: /prototype on|off"
    enable = parts[1].lower() == "on"
    update_system_state(prototype_risk_enabled=enable, updated_by=actor)
    event = "PROTOTYPE_RISK_ENABLED" if enable else "PROTOTYPE_RISK_DISABLED"
    audit(event, actor=actor, details={"enabled": enable})
    status = "활성화" if enable else "비활성화"
    return f"Prototype Risk {status}됨."


def _handle_prototype_status() -> str:
    """Show ProtoHedge status (SPEC-011 REQ-DYNRISK-04-10)."""
    try:
        state = get_system_state()
        if not state.get("prototype_risk_enabled", False):
            return "[ProtoHedge] 비활성화 상태. /prototype on 으로 활성화하세요."

        from trading.prototypes.exposure import format_prototype_status
        from trading.prototypes.similarity import build_current_state_text, compute_similarity

        state_text = build_current_state_text()
        matches = compute_similarity(state_text, cycle_kind="intraday")
        return format_prototype_status(matches)
    except Exception as e:
        return f"[ProtoHedge] 상태 조회 실패: {e}"


def _handle_cli_toggle(text: str, actor: str) -> str:
    """Toggle CLI persona mode (SPEC-015 REQ-FALLBACK-06-5)."""
    parts = text.strip().split()
    if len(parts) < 2 or parts[1].lower() not in ("on", "off"):
        state = get_system_state()
        current = state.get("cli_personas_enabled", False)
        return f"현재 CLI 모드: {'ON' if current else 'OFF'}\n사용법: /cli on|off"
    enable = parts[1].lower() == "on"
    update_system_state(cli_personas_enabled=enable, updated_by=actor)
    event = "CLI_PERSONAS_ENABLED" if enable else "CLI_PERSONAS_DISABLED"
    audit(event, actor=actor, details={"enabled": enable})
    status = "활성화" if enable else "비활성화"
    return f"CLI Persona Mode {status}됨. (cli_personas_enabled={enable})"


def _help() -> str:
    return (
        "사용 가능한 명령어:\n"
        "/halt          매매 정지 (신규 주문 차단)\n"
        "/resume        매매 재개\n"
        "/status        현재 시스템 상태\n"
        "/pnl           오늘 손익\n"
        "/holdings      보유 현황·평가손익\n"
        "/verbose       풀 브리핑 모드\n"
        "/silent        침묵 모드\n"
        "/tool_calling on|off  Tool-calling 전환\n"
        "/reflection on|off    Reflection Loop 전환\n"
        "/car_filter on|off    Event-CAR Filter 전환\n"
        "/dyn_threshold on|off Dynamic Thresholds 전환\n"
        "/jit on|off           JIT Pipeline 전환\n"
        "/jit ws|dart|news on|off  개별 소스 전환\n"
        "/prototype on|off     ProtoHedge 전환\n"
        "/prototype_status     ProtoHedge 현황\n"
        "/cli on|off           CLI Persona 전환\n"
        "/help          이 메시지"
    )


def _pnl_summary() -> str:
    """SPEC-TRADING-041 REQ-041-3b: same-day cash-flow gross, NET of fees.

    ``gross`` = sell amount − buy amount (filled/partial, CURRENT_DATE); the
    reported figure subtracts ``SUM(fee)`` so it is net of execution fees. This
    is a fast same-day-flow estimate, NOT a precise FIFO per-lot realized P&L —
    the label says so to avoid misreading.
    """
    sql = """
        SELECT
            COUNT(*) FILTER (WHERE side='buy')  AS buys,
            COUNT(*) FILTER (WHERE side='sell') AS sells,
            COALESCE(SUM(CASE WHEN side='sell' AND status IN ('filled','partial')
                      THEN COALESCE(fill_price,0)*COALESCE(fill_qty,qty) ELSE 0 END), 0)
          - COALESCE(SUM(CASE WHEN side='buy'  AND status IN ('filled','partial')
                      THEN COALESCE(fill_price,0)*COALESCE(fill_qty,qty) ELSE 0 END), 0) AS gross,
            COALESCE(SUM(CASE WHEN status IN ('filled','partial')
                      THEN COALESCE(fee,0) ELSE 0 END), 0) AS fee
          FROM orders WHERE ts::date = CURRENT_DATE
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    gross = int(row["gross"] or 0)
    fee = int(row["fee"] or 0)
    net = gross - fee
    return (
        f"오늘({date.today()}) 매매: 매수 {row['buys']} / 매도 {row['sells']}\n"
        f"실현 손익(추정·당일 현금흐름, 수수료 차감): {net:+,}원\n"
        f"(gross {gross:+,}원 − 수수료 {fee:,}원 / FIFO 정밀 손익 아님)"
    )


def _format_holdings(
    holdings: list[dict[str, Any]],
    *,
    stock_eval: int | None = None,
    cash: int | None = None,
    total: int | None = None,
) -> str:
    """SPEC-TRADING-041 REQ-041-3a: render KIS holdings + TOTAL eval P&L.

    Pure (no I/O) so the format is unit-testable independent of KIS. Each line:
    name (or ticker fallback), qty, avg_cost, current_price, signed eval P&L (KRW
    and %). Empty holdings → '보유 종목 없음'.

    SPEC-041 follow-on: when ``stock_eval`` / ``cash`` / ``total`` are provided
    (from ``balance()``), append an asset-summary block after the TOTAL line:
    주식 평가금 (stock_eval), 보유 현금 (cash_d2), 합산(총자산) (invest_basis =
    cash_d2 + stock_eval, the consistent system-wide denominator). Each summary
    value is omitted gracefully when None, preserving backward behavior for
    callers that pass holdings only. ``invest_basis`` is used for 합산 rather than
    ``total_assets``/``tot_evlu_amt`` (which does not equal cash+stock due to D+2
    settlement timing).
    """
    if not holdings:
        return "보유 종목 없음"
    lines = ["보유 현황:"]
    total_pnl = 0
    for h in holdings:
        name = h.get("name") or h.get("ticker", "")
        qty = int(h.get("qty", 0) or 0)
        avg_cost = int(h.get("avg_cost", 0) or 0)
        cur = int(h.get("current_price", 0) or 0)
        pnl_amt = int(h.get("pnl_amount", 0) or 0)
        pnl_pct = float(h.get("pnl_pct", 0) or 0)
        total_pnl += pnl_amt
        lines.append(
            f"· {name} {qty}주 | 평단 {avg_cost:,} → 현재 {cur:,} | "
            f"평가손익 {pnl_amt:+,}원 ({pnl_pct:+.1f}%)"
        )
    lines.append(f"TOTAL 평가손익: {total_pnl:+,}원")

    # Asset summary block (each value optional → omit gracefully when None).
    summary: list[str] = []
    if stock_eval is not None:
        summary.append(f"주식 평가금: {int(stock_eval):,}원")
    if cash is not None:
        summary.append(f"보유 현금: {int(cash):,}원")
    if total is not None:
        summary.append(f"합산(총자산): {int(total):,}원")
    if summary:
        lines.append("─────")
        lines.extend(summary)

    return "\n".join(lines)


def _holdings_summary() -> str:
    """REQ-041-3a / REQ-041-4c: /holdings via the daily_report client wiring.

    Reuses ``KisClient(get_settings().trading_mode)`` → ``balance`` → render,
    with the same try/except safe-degrade pattern as
    ``daily_report._collect_portfolio`` so a KIS outage never crashes the bot.
    """
    try:
        client = KisClient(get_settings().trading_mode)
        bal = balance(client)
    except Exception as e:
        LOG.warning("/holdings balance fetch failed: %s", e)
        return "잔고 조회 실패 — 잠시 후 다시 시도해 주세요."
    return _format_holdings(
        bal.get("holdings", []),
        stock_eval=bal.get("stock_eval"),
        cash=bal.get("cash_d2"),
        total=bal.get("invest_basis"),
    )
