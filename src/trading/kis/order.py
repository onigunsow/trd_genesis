"""KIS order submission with live_unlocked gate (REQ-MODE-02-6) and audit (REQ-KIS-02-4).

All orders go through `submit_order()`. Live mode is BLOCKED unless
system_state.live_unlocked is true (REQ-FUTURE-08-2 safety guard).

Order persistence: every submission inserts into orders table AND audit_log,
regardless of success/failure.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from trading.config import TradingMode, estimate_fee, get_settings
from trading.db.session import audit, connection, get_system_state
from trading.kis.client import KisClient, KisError, KisResponse

LOG = logging.getLogger(__name__)

Side = Literal["buy", "sell"]
OrderType = Literal["market", "limit"]


class LiveLockedError(RuntimeError):
    """Live order rejected because live_unlocked=false (REQ-MODE-02-6, REQ-FUTURE-08-2)."""


def _check_live_gate(client: KisClient) -> None:
    """REQ-MODE-02-6: While TRADING_MODE=live AND live_unlocked=false, reject all orders."""
    if client.mode != TradingMode.LIVE:
        return
    state = get_system_state()
    if not state["live_unlocked"]:
        # Audit the rejection BEFORE raising.
        audit(
            "ORDER_BLOCKED_LIVE_LOCKED",
            actor="system",
            details={"reason": "LIVE_LOCKED", "live_unlocked": False},
        )
        raise LiveLockedError(
            "Live trading is blocked: live_unlocked=false. "
            "Manual SQL update with audit_log entry required (REQ-FUTURE-08-2)."
        )


def submit_order(
    client: KisClient,
    *,
    ticker: str,
    qty: int,
    side: Side,
    order_type: OrderType = "market",
    limit_price: int | None = None,
    persona_decision_id: int | None = None,
) -> dict[str, Any]:
    """Submit a buy/sell order and persist to DB + audit_log.

    Returns: dict with order_id (DB row id), kis_order_no, status, response.
    """
    _check_live_gate(client)

    if order_type == "limit" and limit_price is None:
        raise ValueError("limit_price required for limit orders")
    if order_type == "market" and limit_price is not None:
        LOG.warning("market order ignores limit_price=%s", limit_price)
        limit_price = None

    # KIS endpoint + tr_id
    path = "/uapi/domestic-stock/v1/trading/order-cash"
    if side == "buy":
        tr_id = client.tr_id(paper_id="VTTC0802U", live_id="TTTC0802U")
    else:
        tr_id = client.tr_id(paper_id="VTTC0801U", live_id="TTTC0801U")

    body = {
        "CANO": client.account_prefix,
        "ACNT_PRDT_CD": client.account_suffix,
        "PDNO": ticker,
        "ORD_DVSN": "01" if order_type == "market" else "00",  # 01=시장가, 00=지정가
        "ORD_QTY": str(qty),
        "ORD_UNPR": "0" if order_type == "market" else str(limit_price),
    }

    # ── Step 1: Pre-create order row in its own transaction so we have an id to reference. ──
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO orders (mode, side, ticker, qty, order_type, limit_price,
                                request, status, persona_decision_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            RETURNING id
            """,
            (
                client.mode.value,
                side,
                ticker,
                qty,
                order_type,
                limit_price,
                json.dumps(body),
                "submitted",
                persona_decision_id,
            ),
        )
        row = cur.fetchone()
        order_id = row["id"]

    # ── Step 2: External KIS call (no DB transaction held during external IO). ──
    try:
        resp: KisResponse = client.post(path, tr_id=tr_id, body=body)
    except Exception as e:  # noqa: BLE001
        # Transport error: persist 'error' status + audit in single transaction.
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE orders SET status='error', response=%s::jsonb, rejected_reason=%s "
                "WHERE id=%s",
                (json.dumps({"transport_error": str(e)}), str(e)[:200], order_id),
            )
            cur.execute(
                "INSERT INTO audit_log (event_type, actor, details) VALUES (%s, %s, %s::jsonb)",
                ("ORDER_TRANSPORT_ERROR", "kis",
                 json.dumps({"order_id": order_id, "error": str(e)})),
            )
        raise

    # ── Step 3: Atomic persistence — UPDATE orders + INSERT audit_log in single transaction. ──
    # If either fails, both roll back and the caller can retry. UNIQUE constraint on
    # kis_order_no prevents double-write of the same KIS order on retry.
    if resp.rt_cd == "0":
        out = resp.output if isinstance(resp.output, dict) else (resp.output[0] if resp.output else {})
        raw_kis_order_no = out.get("ODNO", out.get("KRX_FWDG_ORD_ORGNO", ""))
        # Normalise empty string to NULL so partial UNIQUE index (WHERE NOT NULL) honours it.
        kis_order_no = raw_kis_order_no or None
        new_status = "submitted"
    else:
        kis_order_no = None
        new_status = "rejected"

    market_guess = "KOSPI"  # M2 default; M3+ may pass explicit market via context
    notional_est = qty * (limit_price or 0)
    estimated_fee = estimate_fee(
        mode=client.mode.value,
        side=side,
        market=market_guess,
        notional=notional_est,
    )
    audit_event = "ORDER_SUBMITTED" if resp.rt_cd == "0" else "ORDER_REJECTED"
    audit_details = {
        "order_id": order_id,
        "kis_order_no": kis_order_no,
        "ticker": ticker,
        "side": side,
        "qty": qty,
        "mode": client.mode.value,
        "estimated_fee": estimated_fee,
        "rt_cd": resp.rt_cd,
        "msg": resp.msg,
    }

    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE orders
                   SET response = %s::jsonb,
                       kis_order_no = %s,
                       status = %s,
                       rejected_reason = %s,
                       fee = %s
                 WHERE id = %s
                """,
                (
                    json.dumps(resp.raw),
                    kis_order_no,
                    new_status,
                    None if resp.rt_cd == "0" else f"{resp.msg_cd}:{resp.msg}",
                    estimated_fee,
                    order_id,
                ),
            )
            cur.execute(
                "INSERT INTO audit_log (event_type, actor, details) VALUES (%s, %s, %s::jsonb)",
                (audit_event, "kis", json.dumps(audit_details)),
            )
            # Both statements succeed → conn.commit() runs at context-exit.
    except Exception as db_err:  # noqa: BLE001
        # Best-effort flag to surface inconsistency. The transaction itself rolled back,
        # so the prior 'submitted' row remains stale — log via a fresh connection.
        try:
            audit("ORDER_PERSIST_ERROR", actor="kis", details={
                "order_id": order_id, "db_error": str(db_err),
                "kis_rt_cd": resp.rt_cd, "kis_order_no": kis_order_no,
            })
        except Exception:  # noqa: BLE001
            LOG.exception("audit fallback also failed (order_id=%s)", order_id)
        raise

    if resp.rt_cd != "0":
        raise KisError(resp)

    return {
        "order_id": order_id,
        "kis_order_no": kis_order_no,
        "status": new_status,
        "rt_cd": resp.rt_cd,
        "msg": resp.msg,
        "raw": resp.raw,
    }


def buy(
    client: KisClient,
    *,
    ticker: str,
    qty: int,
    order_type: OrderType = "market",
    limit_price: int | None = None,
    persona_decision_id: int | None = None,
) -> dict[str, Any]:
    return submit_order(
        client,
        ticker=ticker,
        qty=qty,
        side="buy",
        order_type=order_type,
        limit_price=limit_price,
        persona_decision_id=persona_decision_id,
    )


def sell(
    client: KisClient,
    *,
    ticker: str,
    qty: int,
    order_type: OrderType = "market",
    limit_price: int | None = None,
    persona_decision_id: int | None = None,
) -> dict[str, Any]:
    return submit_order(
        client,
        ticker=ticker,
        qty=qty,
        side="sell",
        order_type=order_type,
        limit_price=limit_price,
        persona_decision_id=persona_decision_id,
    )
