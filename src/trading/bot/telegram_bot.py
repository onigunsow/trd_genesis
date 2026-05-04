"""Telegram bot — long-poll loop handling /halt /resume /status /pnl /verbose /silent.

Authorisation: only chat_id from .env TELEGRAM_CHAT_ID is honoured. All other
chats are ignored (REQ-RISK-05-4 chat_id whitelist).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from trading.config import get_settings
from trading.db.session import audit
from trading.risk import emergency

LOG = logging.getLogger(__name__)
TG_BASE = "https://api.telegram.org"


def _get(path: str, **params: Any) -> dict[str, Any]:
    s = get_settings()
    token = s.telegram.bot_token.get_secret_value()
    with httpx.Client(timeout=70.0) as c:
        r = c.get(f"{TG_BASE}/bot{token}/{path}", params=params)
    r.raise_for_status()
    return r.json()


def _send(chat_id: str, text: str) -> None:
    s = get_settings()
    token = s.telegram.bot_token.get_secret_value()
    with httpx.Client(timeout=10.0) as c:
        c.post(
            f"{TG_BASE}/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text},
        )


def run(poll_timeout: int = 60) -> None:
    """Long-poll forever. Caller (process manager / scheduler) controls lifecycle."""
    s = get_settings()
    allowed_chat = str(s.telegram.chat_id)
    offset: int | None = None
    LOG.info("telegram bot starting (allowed chat_id=%s)", allowed_chat)
    while True:
        try:
            params: dict[str, Any] = {"timeout": poll_timeout}
            if offset is not None:
                params["offset"] = offset
            data = _get("getUpdates", **params)
        except Exception as e:  # noqa: BLE001
            LOG.exception("getUpdates failed: %s", e)
            time.sleep(5)
            continue

        for upd in data.get("result", []) or []:
            offset = int(upd["update_id"]) + 1
            msg = upd.get("message") or upd.get("edited_message")
            if not msg:
                continue
            chat = msg.get("chat", {}) or {}
            chat_id = str(chat.get("id", ""))
            text = msg.get("text", "") or ""
            if chat_id != allowed_chat:
                audit("UNAUTHORIZED_TG_MSG", actor="telegram",
                      details={"from_chat_id": chat_id, "text_prefix": text[:50]})
                LOG.warning("unauthorized chat_id=%s text=%r", chat_id, text[:50])
                continue
            if not text.startswith("/"):
                continue
            try:
                reply = emergency.handle(text, actor=f"chat:{chat_id}")
            except Exception as e:  # noqa: BLE001
                LOG.exception("command handler failed")
                reply = f"명령 처리 오류: {e}"
            try:
                _send(chat_id, reply)
            except Exception:  # noqa: BLE001
                LOG.exception("reply send failed")
