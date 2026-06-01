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
from trading.kis.account import balance
from trading.kis.client import KisClient, KisError, KisResponse
from trading.kis.market import current_price

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


def _held_qty(client: KisClient, ticker: str) -> int:
    """Current held qty for `ticker` from a fresh ``balance()`` (over-sell guard).

    Mirrors ``position_watchdog._confirm_qty`` (SPEC-033): the held quantity is
    re-confirmed from KIS just before a synthetic exit so we never short-sell.
    """
    for h in balance(client).get("holdings", []) or []:
        if h.get("ticker") == ticker:
            return int(h.get("qty", 0) or 0)
    return 0


def _reference_price(
    client: KisClient,
    ticker: str,
    order_type: OrderType,
    limit_price: int | None,
) -> int:
    """REQ-039-3: market order → ``inquire-price`` current price; limit → limit_price.

    Raises whatever ``current_price`` raises (KisError) on quote failure so the
    caller can audit + skip the synthetic fill gracefully.
    """
    if order_type == "limit":
        return int(limit_price or 0)
    quote = current_price(client, ticker)
    return int(quote.get("price", 0) or 0)


# @MX:WARN: paper-only synthetic fill. This is a MONEY path: it marks an order
# 'filled' locally without a real fill confirmation from KIS.
# @MX:REASON: KIS paper does not report same-day SELL fills (SPEC-029 Layer 3),
# so paper SELL orders would otherwise stay 'submitted' forever (2026-06-01
# regression). The `mode == PAPER` gate is load-bearing — a leak into live would
# fabricate fills on real money. live records fills ONLY via SPEC-029 reconcile.
def _synthetic_fill(
    client: KisClient,
    *,
    order_id: int,
    ticker: str,
    qty: int,
    side: Side,
    order_type: OrderType,
    limit_price: int | None,
) -> None:
    """REQ-039-1..4: fill a *paper* order synthetically at submission time.

    Paper-only hard gate (REQ-039-2): a non-paper client is a no-op (audited),
    so the live path is byte-for-byte unchanged. On a SELL the qty is clamped to
    the held quantity (REQ-039-4, never short). Reference price is the
    ``inquire-price`` current price for market orders or ``limit_price`` for limit
    orders (REQ-039-3). On a quote failure the fill is skipped (audited) and the
    order is left 'submitted' for SPEC-029 reconcile to handle (REQ-039-3).

    positions are updated cohesively (buy → qty up / weighted avg_cost; sell →
    qty down, never below 0) so intra-cycle consumers see the exit immediately;
    reconcile_from_balance remains the source of truth and reconverges later.
    """
    # REQ-039-2: paper-only hard gate. Never fabricate fills on the live path.
    if client.mode != TradingMode.PAPER:
        audit(
            "ORDER_SYNTHETIC_BLOCKED_LIVE",
            actor="system",
            details={"order_id": order_id, "ticker": ticker,
                     "reason": "synthetic fill is paper-only (REQ-039-2)"},
        )
        return

    # REQ-039-3: reference price; graceful skip on quote failure (no crash).
    try:
        ref_price = _reference_price(client, ticker, order_type, limit_price)
    except Exception as e:  # noqa: BLE001 — any quote failure → skip, never crash
        audit(
            "ORDER_SYNTHETIC_SKIPPED",
            actor="kis",
            details={"order_id": order_id, "ticker": ticker, "side": side,
                     "reason": "reference price unavailable", "error": str(e)[:200]},
        )
        return
    if ref_price <= 0:
        audit(
            "ORDER_SYNTHETIC_SKIPPED",
            actor="kis",
            details={"order_id": order_id, "ticker": ticker, "side": side,
                     "reason": "reference price <= 0"},
        )
        return

    fill_qty = qty
    if side == "sell":
        # REQ-039-4: clamp to held; the dropped excess is audited, never shorted.
        held = _held_qty(client, ticker)
        fill_qty = min(qty, held)
        if qty > held:
            audit(
                "OVERSELL_CLAMPED",
                actor="risk",
                details={"order_id": order_id, "ticker": ticker,
                         "requested_qty": qty, "held_qty": held,
                         "dropped_excess": qty - held, "filled_qty": fill_qty},
            )
        if fill_qty <= 0:
            return  # nothing held to exit — never short-sell.

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE orders
               SET status = 'filled',
                   fill_qty = %s,
                   fill_price = %s,
                   filled_at = now(),
                   synthetic = TRUE
             WHERE id = %s
            """,
            (fill_qty, ref_price, order_id),
        )

        # Cohesive positions update (mirrors _mirror_positions UPSERT shape).
        cur.execute(
            "SELECT qty, avg_cost FROM positions WHERE ticker = %s FOR UPDATE",
            (ticker,),
        )
        prow = cur.fetchone() or {}
        cur_qty = int(prow.get("qty", 0) or 0)
        cur_avg = int(prow.get("avg_cost", 0) or 0)

        if side == "buy":
            new_qty = cur_qty + fill_qty
            # Weighted average cost over the existing lot + this synthetic fill.
            new_avg = (
                int((cur_qty * cur_avg + fill_qty * ref_price) / new_qty)
                if new_qty > 0
                else 0
            )
        else:
            new_qty = max(0, cur_qty - fill_qty)
            new_avg = cur_avg if new_qty > 0 else 0

        cur.execute(
            """
            INSERT INTO positions (ticker, qty, avg_cost, last_updated)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (ticker) DO UPDATE SET
                qty = EXCLUDED.qty,
                avg_cost = EXCLUDED.avg_cost,
                last_updated = now()
            """,
            (ticker, new_qty, new_avg),
        )

        cur.execute(
            "INSERT INTO audit_log (event_type, actor, details) "
            "VALUES (%s, %s, %s::jsonb)",
            (
                "ORDER_FILLED_SYNTHETIC",
                "kis",
                json.dumps({
                    "order_id": order_id,
                    "ticker": ticker,
                    "side": side,
                    "ord_qty": qty,
                    "fill_qty": fill_qty,
                    "fill_price": ref_price,
                    "source": "paper_synthetic",
                    # Honesty caveat (scorecard tone): paper synthetic fill price
                    # is the quote/limit price and excludes slippage & market
                    # impact — it is NOT a real execution price.
                    "caveat": "페이퍼 합성 체결가 ≠ 실거래 체결가 (slippage 없음)",
                }),
            ),
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
    # @MX:ANCHOR: single order chokepoint. fan_in >= 3 — every buy/sell path
    # (orchestrator._execute_signal, late_cycle, position_watchdog) reaches KIS
    # through here via buy()/sell().
    # @MX:REASON: the live_unlocked gate (_check_live_gate) and the SPEC-039
    # paper-only synthetic fill both hang off this one function; keeping all
    # order side effects (DB persist + audit + fill) routed through it preserves
    # the invariant that no order bypasses the mode gate.
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

    # ── Step 4 (SPEC-039): paper-only synthetic fill. ──
    # The order was accepted by KIS (kis_order_no assigned). In paper mode KIS
    # never reports same-day SELL fills, so we fill the order locally now. The
    # `mode == PAPER` hard gate inside `_synthetic_fill` keeps live untouched;
    # any failure here is contained (audited, never raised) so submission
    # remains successful and SPEC-029 reconcile is the fallback.
    if kis_order_no is not None:
        try:
            _synthetic_fill(
                client,
                order_id=order_id,
                ticker=ticker,
                qty=qty,
                side=side,
                order_type=order_type,
                limit_price=limit_price,
            )
            with connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT status FROM orders WHERE id = %s", (order_id,))
                srow = cur.fetchone() or {}
            new_status = str(srow.get("status") or new_status)
        except Exception:  # noqa: BLE001 — synthetic fill must never break submission
            LOG.exception("SPEC-039 synthetic fill failed (order_id=%s)", order_id)
            audit("ORDER_SYNTHETIC_ERROR", actor="kis",
                  details={"order_id": order_id, "ticker": ticker, "side": side})

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
