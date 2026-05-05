"""Emergency commands handled by the Telegram bot listener.

Handlers receive the user message text and return a short Korean response.
Commands honoured (REQ-RISK-05-4):
- /halt    set halt_state=true
- /resume  set halt_state=false
- /status  print system_state + recent persona run count
- /pnl     print today's PnL (best-effort estimate)
- /verbose exit silent_mode (REQ-FATIGUE-05-10)
- /silent  enter silent_mode manually
- /tool-calling on|off  toggle tool-calling mode (REQ-COMPAT-04-7)
- /reflection on|off    toggle reflection loop (REQ-COMPAT-04-7)
- /jit on|off|ws|dart|news  toggle JIT pipeline (SPEC-011 REQ-MIGR-06-3)
- /prototype on|off     toggle prototype risk (SPEC-011 REQ-MIGR-06-3)
- /prototype-status     show ProtoHedge status (SPEC-011 REQ-DYNRISK-04-10)
- /help    list commands
"""

from __future__ import annotations

from datetime import date

from trading.db.session import audit, connection, get_system_state, update_system_state
from trading.risk import circuit_breaker as cb


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
            f"live_unlocked={state['live_unlocked']}"
        )
    if cmd == "/pnl":
        return _pnl_summary()
    if cmd == "/verbose":
        update_system_state(silent_mode=False, updated_by=actor)
        audit("SILENT_MODE_OFF", actor=actor, details={})
        return "✓ silent_mode=false. 모든 페르소나 브리핑 발송 재개."
    if cmd == "/silent":
        update_system_state(silent_mode=True, updated_by=actor)
        audit("SILENT_MODE_ON", actor=actor, details={"reason": "manual"})
        return "✓ silent_mode=true. 주요 이벤트만 발송."
    # SPEC-009 REQ-COMPAT-04-7: Tool-calling and reflection toggle commands
    if cmd == "/tool-calling":
        return _handle_tool_calling(text, actor)
    if cmd == "/reflection":
        return _handle_reflection(text, actor)
    # SPEC-011 REQ-MIGR-06-3: JIT pipeline and prototype toggle commands
    if cmd == "/jit":
        return _handle_jit(text, actor)
    if cmd == "/prototype":
        return _handle_prototype(text, actor)
    if cmd == "/prototype-status":
        return _handle_prototype_status()
    if cmd in ("/help", "/start"):
        return _help()
    return f"unknown command: {cmd or '(empty)'}\n{_help()}"


def _handle_tool_calling(text: str, actor: str) -> str:
    """Toggle tool-calling feature flag (REQ-COMPAT-04-7)."""
    parts = text.strip().split()
    if len(parts) < 2 or parts[1].lower() not in ("on", "off"):
        return "사용법: /tool-calling on|off"
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


def _help() -> str:
    return (
        "사용 가능한 명령어:\n"
        "/halt          매매 정지 (신규 주문 차단)\n"
        "/resume        매매 재개\n"
        "/status        현재 시스템 상태\n"
        "/pnl           오늘 손익\n"
        "/verbose       풀 브리핑 모드\n"
        "/silent        침묵 모드\n"
        "/tool-calling on|off  Tool-calling 전환\n"
        "/reflection on|off    Reflection Loop 전환\n"
        "/jit on|off           JIT Pipeline 전환\n"
        "/jit ws|dart|news on|off  개별 소스 전환\n"
        "/prototype on|off     ProtoHedge 전환\n"
        "/prototype-status     ProtoHedge 현황\n"
        "/help          이 메시지"
    )


def _pnl_summary() -> str:
    sql = """
        SELECT
            COUNT(*) FILTER (WHERE side='buy')  AS buys,
            COUNT(*) FILTER (WHERE side='sell') AS sells,
            COALESCE(SUM(CASE WHEN side='sell' AND status IN ('filled','partial')
                               THEN COALESCE(fill_price,0)*COALESCE(fill_qty,qty) ELSE 0 END), 0)
          - COALESCE(SUM(CASE WHEN side='buy'  AND status IN ('filled','partial')
                               THEN COALESCE(fill_price,0)*COALESCE(fill_qty,qty) ELSE 0 END), 0) AS pnl
          FROM orders WHERE ts::date = CURRENT_DATE
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    return (
        f"오늘({date.today()}) 매매: 매수 {row['buys']} / 매도 {row['sells']}\n"
        f"실현 손익(추정): {int(row['pnl'] or 0):,}원"
    )
