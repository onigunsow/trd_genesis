"""SPEC-TRADING-042 D1/D6 — 유령 합성매수 append-only 교정 및 orders-positions 패리티.

**문제 (D1)**:
  `_synthetic_fill` 이 paper 모드에서 KIS 미확인 매수를 `status='filled'` 로
  영속화한다. orders 순매수 집계(buy filled - sell filled)가 실제 KIS 잔고보다
  부풀려진 채로 남아 FIFO 원가 순서를 오염시킨다(D6).

**설계 (append-only, [HARD])**:
  과거 `status='filled'` 행은 절대 UPDATE/DELETE 하지 않는다.
  초과분을 닫기 위한 교정 SELL 행을 `correction=TRUE` 로 INSERT 한다.
  `build_roundtrips` 는 `correction=TRUE` 매도를 원장정리(FIFO lot pop, RoundTrip
  미생성)로 처리한다 → open_qty 가 KIS 진실로 수렴, 실현손익 오염 없음.

**D2 parity**:
  `orders_positions_divergence()` 가 orders-agg net 과 positions.qty 를 비교.
  smoke_gate `ledger_parity` 계산에 AND 조건으로 배선된다(cli.py).

모든 쓰기 경로는 `mode == PAPER` 가드. live 는 no-op 요약 반환.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from trading.config import TradingMode
from trading.db.session import connection

LOG = logging.getLogger(__name__)

# # @MX:WARN: [AUTO] 돈 경로 — append-only 교정 SELL INSERT. live 절대 실행 금지.
# # @MX:REASON: D1 유령 합성매수 open_qty 부풀림. 교정 lot 은 paper/synthetic/
# #   correction=TRUE 만 INSERT, 기존 filled 행 불변 보장([HARD]).


def _orders_net_by_ticker(cur: Any) -> dict[str, int]:
    """paper filled/partial 주문의 ticker 별 순매수 수량(교정 매도 포함).

    status IN ('filled','partial') 필터 — _FILL_SQL / M2 정의와 동일.
    correction=TRUE 매도도 매도로 집계해 orders_net 이 교정 후에 0 이 되도록 한다.
    """
    cur.execute(
        """
        SELECT ticker,
               COALESCE(SUM(
                   CASE WHEN side = 'buy' THEN fill_qty ELSE -fill_qty END
               ), 0) AS net_qty
          FROM orders
         WHERE mode = 'paper'
           AND status IN ('filled', 'partial')
           AND fill_qty IS NOT NULL AND fill_qty > 0
         GROUP BY ticker
        """
    )
    rows = cur.fetchall() or []
    return {
        str(r.get("ticker") or r["ticker"]): int(r.get("net_qty") or r["net_qty"])
        for r in rows
    }


def _positions_qty_by_ticker(cur: Any) -> dict[str, dict[str, Any]]:
    """KIS reconcile 결과 positions 테이블에서 ticker 별 qty / avg_cost 반환."""
    cur.execute(
        """
        SELECT ticker, qty, avg_cost
          FROM positions
         WHERE mode = 'paper'
        """
    )
    rows = cur.fetchall() or []
    result: dict[str, dict[str, Any]] = {}
    for r in rows:
        ticker = str(r.get("ticker") or r["ticker"])
        result[ticker] = {
            "qty": int(r.get("qty") or r["qty"] or 0),
            "avg_cost": float(r.get("avg_cost") or r["avg_cost"] or 0.0),
        }
    return result


def _vwap_open_lots(cur: Any, ticker: str) -> float:
    """해당 ticker 의 미청산 매수 lot 평단가 (positions.avg_cost 없을 때 폴백).

    buy filled rows 의 fill_price VWAP(fill_qty 가중평균).
    """
    cur.execute(
        """
        SELECT COALESCE(
                   SUM(fill_price * fill_qty) / NULLIF(SUM(fill_qty), 0),
                   0
               ) AS vwap
          FROM orders
         WHERE mode = 'paper'
           AND side = 'buy'
           AND status IN ('filled', 'partial')
           AND ticker = %s
           AND fill_qty IS NOT NULL AND fill_qty > 0
           AND fill_price IS NOT NULL
        """,
        (ticker,),
    )
    row = cur.fetchone() or {}
    return float(row.get("vwap") or 0.0)


def _insert_correction_sell(
    cur: Any,
    *,
    ticker: str,
    qty: int,
    fill_price: float,
    now_ts: datetime,
) -> None:
    """교정 SELL 행 INSERT (append-only, paper/synthetic/correction=TRUE).

    persona_decision_id=NULL — 교정 행은 LLM 결정이 아님.
    fee=0 — 가상 교정이므로 수수료 없음.
    """
    cur.execute(
        """
        INSERT INTO orders (
            ticker, side, qty, fill_qty, fill_price, fee,
            status, mode, synthetic, correction,
            ts, filled_at, persona_decision_id
        ) VALUES (
            %s, 'sell', %s, %s, %s, 0,
            'filled', 'paper', TRUE, TRUE,
            %s, %s, NULL
        )
        """,
        (ticker, qty, qty, fill_price, now_ts, now_ts),
    )


def _audit_convergence(
    cur: Any,
    *,
    ticker: str,
    orders_net_before: int,
    kis_held: int,
    excess: int,
    fill_price: float,
    dry_run: bool,
) -> None:
    """audit_log 에 GHOST_BUY_CONVERGED 이벤트 기록 (dry_run 시 skip)."""
    if dry_run:
        return
    details = {
        "ticker": ticker,
        "orders_net_before": orders_net_before,
        "kis_held": kis_held,
        "excess": excess,
        "fill_price": fill_price,
        "spec": "SPEC-TRADING-042-D1",
    }
    cur.execute(
        "INSERT INTO audit_log (event_type, actor, details) VALUES (%s, %s, %s::jsonb)",
        ("GHOST_BUY_CONVERGED", "ghost_convergence", json.dumps(details)),
    )


# @MX:WARN: [AUTO] 돈 경로 — paper DB 에 교정 SELL lot 을 INSERT 한다.
# @MX:REASON: D1 유령 합성매수 교정. live 절대 실행 금지(paper-only 가드 첫 줄).
#   append-only — 기존 filled 행 UPDATE/DELETE 없음([HARD] SPEC-042).
def converge_ghost_buys(client: Any, *, dry_run: bool = False) -> dict[str, Any]:
    """KIS 확인 잔고 대비 초과 synthetic 매수를 교정 SELL lot 으로 수렴.

    Parameters
    ----------
    client:
        KisClient 인스턴스. `client.mode` 로 TradingMode 를 읽는다.
    dry_run:
        True 이면 SELECT 만 수행, INSERT/audit 없음.

    Returns
    -------
    dict with keys:
        scanned_tickers, converged, total_excess, dry_run
    """
    # paper-only 가드 — live 에선 no-op 요약 반환, audit 없음.
    if client.mode != TradingMode.PAPER:
        return {
            "scanned_tickers": 0,
            "converged": 0,
            "total_excess": 0,
            "dry_run": dry_run,
            "skipped_live": True,
        }

    summary: dict[str, Any] = {
        "scanned_tickers": 0,
        "converged": 0,
        "total_excess": 0,
        "dry_run": dry_run,
        "skipped_live": False,
    }

    now_ts = datetime.now(UTC)

    with connection() as conn, conn.cursor() as cur:
        orders_net = _orders_net_by_ticker(cur)
        positions_map = _positions_qty_by_ticker(cur)

        all_tickers = set(orders_net.keys()) | set(positions_map.keys())
        summary["scanned_tickers"] = len(all_tickers)

        for ticker in sorted(all_tickers):
            net = orders_net.get(ticker, 0)
            pos_info = positions_map.get(ticker, {})
            kis_held = pos_info.get("qty", 0)
            excess = net - kis_held

            if excess <= 0:
                # 정합 또는 positions > orders(양방향 divergence 는 D2 parity 에서 감지)
                continue

            # fill_price 결정: positions.avg_cost 우선, 없으면 VWAP 폴백.
            fill_price = pos_info.get("avg_cost", 0.0)
            if not fill_price:
                fill_price = _vwap_open_lots(cur, ticker)
            # 여전히 0 이면 1 로 방어 (가격 0 의 교정 매도는 의미 없음).
            if not fill_price:
                fill_price = 1.0

            if not dry_run:
                _insert_correction_sell(
                    cur,
                    ticker=ticker,
                    qty=excess,
                    fill_price=fill_price,
                    now_ts=now_ts,
                )
            _audit_convergence(
                cur,
                ticker=ticker,
                orders_net_before=net,
                kis_held=kis_held,
                excess=excess,
                fill_price=fill_price,
                dry_run=dry_run,
            )

            summary["converged"] += 1
            summary["total_excess"] += excess
            LOG.info(
                "SPEC-042 ghost_convergence: ticker=%s orders_net=%d"
                " kis_held=%d excess=%d price=%.2f dry_run=%s",
                ticker,
                net,
                kis_held,
                excess,
                fill_price,
                dry_run,
            )

        if not dry_run:
            conn.commit()

    return summary


# @MX:NOTE: [AUTO] D2 parity 함수 — smoke_gate ledger_parity 의 AND 조건.
def orders_positions_divergence() -> dict[str, Any]:
    """orders-agg net 과 positions.qty 의 ticker 별 괴리 계산(D2).

    Returns
    -------
    dict:
        {
            "by_ticker": {
                "<ticker>": {
                    "orders_net": int,
                    "positions_qty": int,
                    "diff": int,     # orders_net - positions_qty (양수=orders 초과)
                }
            },
            "parity": bool,          # True = 모든 ticker diff==0
        }

    status IN ('filled','partial') 필터 — M2 / _FILL_SQL 동일.
    교정 매도(correction=TRUE) 도 매도로 집계해 수렴 후 parity==True 가 되도록.
    """
    with connection() as conn, conn.cursor() as cur:
        orders_net = _orders_net_by_ticker(cur)
        positions_map = _positions_qty_by_ticker(cur)

    all_tickers = set(orders_net.keys()) | set(positions_map.keys())

    by_ticker: dict[str, dict[str, int]] = {}
    parity = True
    for ticker in sorted(all_tickers):
        net = orders_net.get(ticker, 0)
        pos_qty = positions_map.get(ticker, {}).get("qty", 0)
        diff = net - pos_qty
        by_ticker[ticker] = {
            "orders_net": net,
            "positions_qty": pos_qty,
            "diff": diff,
        }
        if diff != 0:
            parity = False

    return {"by_ticker": by_ticker, "parity": parity}
