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


def persona_briefing(
    persona: str,
    model: str,
    summary: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_krw: float = 0.0,
) -> None:
    """REQ-BRIEF-04-8 persona briefing — sent immediately after a persona response."""
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
) -> None:
    """REQ-BRIEF-04-8 trade briefing — sent after a KIS order, includes asset status."""
    side_label = "매수" if side == "buy" else "매도"
    px = f"{fill_price:,}원" if fill_price else "(시장가)"
    name_str = f" {_escape_html(name)}" if name else ""
    note_line = f"\n{_escape_html(note)}" if note else ""
    text = (
        f"<b>[매매 · {mode} · {_now_kst()}]</b>\n"
        f"{ticker}{name_str} {qty}주 {side_label} @ {px}\n"
        f"수수료 {fee}원{note_line}\n"
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
        from trading.db.session import audit  # noqa: WPS433 (intentional lazy)
    except Exception:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
        # Last-resort: cannot reach Telegram. At least audit.
        LOG.exception("system_error telegram delivery failed (component=%s)", component)
    if audit is not None:
        try:
            audit("SYSTEM_ERROR", actor=component, details={
                "error_type": err_type,
                "error_msg": err_msg,
                "context": context,
            })
        except Exception:  # noqa: BLE001
            LOG.exception("system_error audit insert failed (component=%s)", component)
