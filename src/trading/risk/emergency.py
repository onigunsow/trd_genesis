"""Emergency commands handled by the Telegram bot listener.

Handlers receive the user message text and return a short Korean response.
Commands honoured (REQ-RISK-05-4):
- /halt    set halt_state=true
- /resume  set halt_state=false
- /status  print system_state + recent persona run count
- /pnl     print today's PnL (best-effort estimate)
- /verbose exit silent_mode (REQ-FATIGUE-05-10)
- /silent  enter silent_mode manually
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
    if cmd in ("/help", "/start"):
        return _help()
    return f"unknown command: {cmd or '(empty)'}\n{_help()}"


def _help() -> str:
    return (
        "사용 가능한 명령어:\n"
        "/halt    매매 정지 (신규 주문 차단)\n"
        "/resume  매매 재개\n"
        "/status  현재 시스템 상태\n"
        "/pnl     오늘 손익\n"
        "/verbose 풀 브리핑 모드\n"
        "/silent  침묵 모드\n"
        "/help    이 메시지"
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
