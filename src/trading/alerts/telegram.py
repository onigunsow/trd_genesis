"""Telegram time-series briefing channel.

Implements REQ-BRIEF-04-8 (every persona invocation, every trade, every event
trigger sends a structured briefing within 5 seconds) and REQ-BRIEF-04-9
(channel functions as time-series log, not just alerts).

Message types:
- persona_briefing : after a persona response
- trade_briefing   : after a KIS order with asset status
- trigger_briefing : after an event trigger fires
- system_briefing  : healthcheck, halt, resume, circuit breaker
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from trading.config import get_settings

LOG = logging.getLogger(__name__)
TG_BASE = "https://api.telegram.org"
KST = ZoneInfo("Asia/Seoul")


def _now_kst() -> str:
    return datetime.now(KST).strftime("%H:%M:%S")


def _client() -> httpx.Client:
    return httpx.Client(timeout=5.0)  # REQ-BRIEF-04-8 SLA 5s


@retry(stop=stop_after_attempt(3), wait=wait_fixed(1), reraise=True)
def _send_raw(text: str, parse_mode: str = "HTML") -> dict[str, Any]:
    s = get_settings()
    token = s.telegram.bot_token.get_secret_value()
    chat_id = s.telegram.chat_id
    url = f"{TG_BASE}/bot{token}/sendMessage"
    body = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    with _client() as c:
        r = c.post(url, data=body)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        LOG.warning("telegram send failed: %s", data)
    return data


def _escape_html(text: str) -> str:
    """Minimal HTML escape for Telegram parse_mode=HTML."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def system_briefing(category: str, message: str) -> None:
    """Generic system message (healthcheck, halt, resume, circuit breaker, etc.)."""
    text = f"<b>[{_escape_html(category)} · {_now_kst()}]</b>\n{_escape_html(message)}"
    _send_raw(text)


def _system_flag(name: str, default: bool = False) -> bool:
    """Read a boolean feature flag from system_state (lazy import; fail-safe)."""
    try:
        from trading.db.session import get_system_state
        return bool(get_system_state().get(name, default))
    except Exception:
        return default


def _verbose_briefing_active() -> bool:
    """SPEC-027: when True, also emit the per-persona briefings (full detail).
    Default (concise) sends only the consolidated cycle-chain briefing."""
    return _system_flag("verbose_briefing", False)


def _briefing_silent() -> bool:
    """SPEC-027: when True (/silent), suppress non-critical briefings."""
    return _system_flag("silent_mode", False)


def persona_briefing(
    persona: str,
    model: str,
    summary: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_krw: float = 0.0,
) -> None:
    """REQ-BRIEF-04-8 persona briefing — per-persona detail.

    SPEC-027: only sent in verbose mode (/detail). In the default concise mode
    the consolidated cycle-chain briefing replaces these fragmented messages.
    """
    if not _verbose_briefing_active():
        return
    persona_e = _escape_html(persona)
    model_e = _escape_html(model)
    summary_e = _escape_html(summary)
    cost_str = f"{cost_krw:,.0f}원" if cost_krw > 0 else "—"
    text = (
        f"<b>[{persona_e} · {model_e} · {_now_kst()}]</b>\n"
        f"{summary_e}\n"
        f"<i>{input_tokens} in / {output_tokens} out / {cost_str}</i>"
    )
    _send_raw(text)


def cycle_briefing(cycle_kind: str, chain: str) -> None:
    """SPEC-027: one consolidated decision-chain summary per cycle.

    Shows Macro -> Micro -> Decision -> Risk -> outcome with each persona's own
    reasoning. Suppressed in silent mode.
    """
    if _briefing_silent():
        return
    label = {
        "pre_market": "장전 사이클",
        "intraday": "장중 사이클",
        "event": "이벤트 사이클",
    }.get(cycle_kind, cycle_kind)
    text = f"<b>[사이클 요약 · {_escape_html(label)} · {_now_kst()}]</b>\n{_escape_html(chain)}"
    _send_raw(text)


def compute_sell_pnl(
    *,
    fill_price: int | float | None,
    avg_cost: int | float | None,
    qty: int,
    fee: int,
) -> tuple[int, float] | None:
    """SPEC-TRADING-041 REQ-041-2a: average-cost realized P&L for a sell.

    Returns ``(amount_krw, pct)`` where
    ``amount = (fill_price - avg_cost) * qty - fee`` and
    ``pct = (fill_price - avg_cost) / avg_cost * 100``.

    Returns ``None`` (omit the line, REQ-041-4b) when the basis is unknowable:
    ``avg_cost`` missing / non-positive, or ``fill_price`` missing. The amount is
    rounded to whole KRW; the percentage is the net realized return on the cost
    basis (``amount / (avg_cost * qty)``), so the sign/magnitude of money and
    percent agree (matches AC-2.1: +49,340원 → +4.9%).
    """
    if fill_price is None or avg_cost is None or avg_cost <= 0:
        return None
    amount = int(round((fill_price - avg_cost) * qty - fee))
    basis = avg_cost * qty
    pct = (amount / basis * 100) if basis else 0.0
    return amount, pct


def trade_briefing(
    *,
    side: str,
    ticker: str,
    name: str | None,
    qty: int,
    fill_price: int | None,
    fee: int,
    mode: str,
    total_assets: int,
    cash_pct: float,
    equity_pct: float,
    note: str = "",
    avg_cost: int | float | None = None,
) -> None:
    """REQ-BRIEF-04-8 trade briefing — sent after a KIS order, includes asset status.

    SPEC-TRADING-041 REQ-041-2: for ``side='sell'`` with a valid ``avg_cost`` and a
    known ``fill_price``, append a realized-P&L line (sign + pct). Buy alerts and
    sells without an avg_cost basis are unchanged (additive-only).
    """
    side_label = "매수" if side == "buy" else "매도"
    px = f"{fill_price:,}원" if fill_price else "(시장가)"
    name_str = f" {_escape_html(name)}" if name else ""
    note_line = f"\n{_escape_html(note)}" if note else ""
    pnl_line = ""
    if side == "sell":
        pnl = compute_sell_pnl(
            fill_price=fill_price, avg_cost=avg_cost, qty=qty, fee=fee
        )
        if pnl is not None:
            amount, pct = pnl
            pnl_line = f"\n실현손익 {amount:+,}원 ({pct:+.1f}%)"
    text = (
        f"<b>[매매 · {mode} · {_now_kst()}]</b>\n"
        f"{ticker}{name_str} {qty}주 {side_label} @ {px}\n"
        f"수수료 {fee}원{note_line}{pnl_line}\n"
        f"자산: {total_assets:,}원 (현금 {cash_pct:.1f}% / 주식 {equity_pct:.1f}%)"
    )
    _send_raw(text)


def trigger_briefing(reason: str, context: str) -> None:
    """REQ-EVENT-04-6 event trigger notification."""
    text = (
        f"<b>[이벤트 트리거 · {_now_kst()}]</b>\n"
        f"{_escape_html(reason)}\n{_escape_html(context)}"
    )
    _send_raw(text)


def silent_mode_active() -> bool:
    """Stub for REQ-FATIGUE-05-9. Implemented in M5."""
    return os.environ.get("TRADING_SILENT_MODE", "0") == "1"


def system_error(component: str, error: BaseException, *, context: str = "") -> None:
    """REQ-OPS-05-20: System errors must NOT be silent.

    Always sends Telegram alert + audit_log. Bypasses silent_mode (errors are critical).
    Caller should still log via standard logger; this is the user-facing signal.
    """
    # Lazy-import audit to avoid circular deps in alert module.
    try:
        from trading.db.session import audit
    except Exception:
        audit = None
    err_type = type(error).__name__
    err_msg = str(error)[:300]
    text = (
        f"<b>[시스템 에러 · {_escape_html(component)} · {_now_kst()}]</b>\n"
        f"{_escape_html(err_type)}: {_escape_html(err_msg)}"
    )
    if context:
        text += f"\n<i>{_escape_html(context[:200])}</i>"
    try:
        _send_raw(text)
    except Exception:
        # Last-resort: cannot reach Telegram. At least audit.
        LOG.exception("system_error telegram delivery failed (component=%s)", component)
    if audit is not None:
        try:
            audit("SYSTEM_ERROR", actor=component, details={
                "error_type": err_type,
                "error_msg": err_msg,
                "context": context,
            })
        except Exception:
            LOG.exception("system_error audit insert failed (component=%s)", component)


# ---------------------------------------------------------------------------
# SPEC-TRADING-063: 주문 거부 알림
# ---------------------------------------------------------------------------

# 브로커측 고장(계좌 만료·권한 상실 등)은 사이클마다 같은 거부를 반복 생성한다.
# 8/4~8/6 사례에서는 사흘간 15건이었다. 쓰로틀이 없으면 알림이 도배되어 오히려
# 무시되므로, 같은 사유는 이 주기에 한 번만 알린다.
ORDER_REJECT_ALERT_COOLDOWN_SEC = 3600


def _reject_alert_throttled(key: str, cooldown_seconds: int) -> bool:
    """같은 ``key`` 로 쿨다운 안에 이미 알렸으면 True.

    상태를 audit_log(DB)에 두는 이유: 프로세스 메모리 dict 는 컨테이너 재시작에
    지워져 쓰로틀이 풀린다(position_watchdog._TOOK_PROFIT 가 같은 이유로 미해소
    결함으로 남아 있다). 조회가 실패하면 False 를 돌려 발송 쪽으로 열어 둔다 —
    알림 누락이 도배보다 위험하다.
    """
    try:
        from trading.db.session import connection

        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM audit_log "
                " WHERE event_type = 'ORDER_REJECT_ALERT' "
                "   AND details->>'key' = %s "
                "   AND ts > now() - (%s * interval '1 second') "
                " LIMIT 1",
                (key, cooldown_seconds),
            )
            return cur.fetchone() is not None
    except Exception:  # 조회 실패는 fail-open (발송 쪽으로 열어 둔다)
        LOG.exception("order_rejected throttle lookup failed (key=%s)", key)
        return False


def _record_reject_alert(key: str, details: dict[str, Any]) -> None:
    """발송 사실을 audit_log 에 남긴다(다음 호출의 쓰로틀 기준)."""
    from trading.db.session import audit

    audit("ORDER_REJECT_ALERT", actor="kis", details={"key": key, **details})


def order_rejected(
    *,
    order_id: int,
    ticker: str,
    side: str,
    qty: int,
    mode: str,
    reason: str,
    name: str | None = None,
    cooldown_seconds: int | None = None,
) -> bool:
    """주문 거부를 침묵시키지 않는다 (SPEC-TRADING-063).

    2026-08-04~06 모의투자 계좌 주문권한 만료로 주문 15건이 전부 거부됐으나
    거부 전용 알림 경로가 없어 나흘간 발견되지 않았다. 거부는 실행경로 고장이므로
    ``system_error`` 와 같은 등급으로 취급해 silent_mode 를 우회한다.

    종목명은 호출자가 이미 알고 있을 때만 ``name`` 으로 넘긴다. 이 함수는
    pykrx 조회를 하지 않는다 — 과거 pykrx 데드소켓이 26시간 블로킹을 만든 전력이
    있어 실패 경로에서 네트워크를 타면 안 된다.

    Returns:
        실제로 발송했으면 True. 쓰로틀됐거나 전송에 실패했으면 False.
    """
    cooldown = (
        ORDER_REJECT_ALERT_COOLDOWN_SEC if cooldown_seconds is None else cooldown_seconds
    )
    reason_text = (reason or "(사유 미기재)").strip()
    key = f"{mode}:{reason_text[:80]}"

    if _reject_alert_throttled(key, cooldown):
        LOG.info("order_rejected alert throttled (key=%s, order_id=%s)", key, order_id)
        return False

    side_label = "매수" if side == "buy" else "매도"
    name_str = f" {_escape_html(name)}" if name else ""
    text = (
        f"<b>[주문 거부 · {_escape_html(mode)} · {_now_kst()}]</b>\n"
        f"{_escape_html(ticker)}{name_str} {qty}주 {side_label} 거부됨\n"
        f"사유: {_escape_html(reason_text[:300])}\n"
        f"<i>order_id={order_id}</i>"
    )

    try:
        _send_raw(text)
    except Exception:  # 주문 경로를 깨뜨리지 않는다
        LOG.exception("order_rejected telegram delivery failed (order_id=%s)", order_id)
        return False

    try:
        _record_reject_alert(key, {
            "order_id": order_id,
            "ticker": ticker,
            "side": side,
            "qty": qty,
            "mode": mode,
            "reason": reason_text[:300],
        })
    except Exception:  # 이미 보낸 알림을 되돌리지는 않는다
        LOG.exception("order_rejected audit insert failed (order_id=%s)", order_id)

    return True
