"""페이퍼 매도 오만료(false-expire) 원장 소급 교정.

근원 (2026-08-14 확정, 최초 발생 2026-06-01):
``submit_order`` Step 4 의 ``_synthetic_fill`` 이 KIS POST *이후* 에
``_held_qty`` 로 잔고를 다시 읽었다. 매도가 이미 KIS 에 접수되어 잔고가 차감된
뒤이므로 held=0 을 보고 자기 주문을 오버셀로 오판, 합성 체결을 건너뛰었다.
주문은 'submitted' 로 방치되다 ``order_resolver`` 가 'expired' 로 닫았고,
실체결 매도가 원장에서 사라져 FIFO 라운드트립이 만들어지지 않았다.
``balance()`` 의 2초 TTL 캐시(SPEC-043) 덕에 POST~재조회가 2초 안에 끝난 날은
매도 전 잔고가 캐시에 걸려 우연히 정상 동작했다 — KIS 응답이 느린 날에만 터진
간헐 결함이라 두 달 넘게 누적됐다.

전방 수정은 ``order.py`` 의 ``pre_submit_held`` (POST 전 보유수량 확정). 이
모듈은 그 이전에 쌓인 원장을 되돌린다.

안전 불변식 (가짜 체결 날조 금지)
----------------------------------
교정 대상은 **KIS 잔고가 매도수량만큼 정확히 줄었음이 실측으로 증명된** 행뿐이다.
``POSITION_SYNCED`` 는 ``fills.reconcile_from_balance`` 가 KIS ``balance()`` 를
읽어 종목별로 남기는 감사 행이므로, 로컬 캐시가 아닌 브로커 진실이다.

    pre_qty  = 주문 직전 마지막 POSITION_SYNCED.qty  (10분 이내)
    post_qty = 주문 직후 첫    POSITION_SYNCED.qty  (10분 이내)
    대상 조건: pre_qty - post_qty == order.qty

이 조건은 두 반례를 자동으로 배제한다 (ID 하드코딩 없음):

- 잔고가 줄지 않은 행(pre == post) → 진짜 미체결이므로 'expired' 가 옳다.
- 전후 POSITION_SYNCED 가 없는 행 → 근거 부족이므로 손대지 않는다.

또한 이 조건은 RC-1(유령 포지션 — 합성매수가 로컬 positions 만 만들고 KIS 엔
잔고가 없어 매도가 유령이 되는 건, ``broker_truth`` 참조)과도 갈린다. 유령이면
KIS 잔고가 애초에 없어 pre_qty 가 0/NULL 이므로 대상에서 빠진다.

체결가 (정직성 명시)
--------------------
실제 체결가는 복원할 수 없다. 우선순위대로 근사한다:

1. ``position_eval_snapshot.eval_price`` — KIS 평가가. 매도 후에는 보유목록에서
   빠져 갱신이 멈추므로 **보유 중 마지막 동기화 시점의 시장가**가 남는다.
2. ``ohlcv.close`` — 스냅샷이 없는 초기 구간(2026-06 상순) 폴백. 일봉 종가.

어느 소스를 썼는지 행마다 감사에 기록한다. 교정 행은 ``synthetic=TRUE`` 로
표시해 실현손익 리포트의 "페이퍼 합성 체결가 ≠ 실거래 체결가" 경고 카운트에
정직하게 잡히게 한다.

``correction`` 은 FALSE 로 둔다 — SPEC-042 D1/D6 의 ``correction=TRUE`` 는
"원장 정리 전용, 실현손익 미발생" 규약이고(``roundtrips.build_roundtrips``),
여기 교정 대상은 **실제로 체결된 매도**라 라운드트립을 생성해야 한다.

멱등성: ``WHERE status='expired'`` 가드로 갱신하므로 재실행해도 이미 교정된
행은 건드리지 않는다.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from trading.config import estimate_fee
from trading.db.session import connection

LOG = logging.getLogger(__name__)

# submit_order 의 수수료 추정과 동일한 기본값. ticker_metadata 에 시장 구분
# 컬럼이 없어 원 주문 경로(order.py market_guess)와 같은 가정을 쓴다.
_MARKET_DEFAULT = "KOSPI"

# POSITION_SYNCED 탐색 창은 주문 시각 전후 10분 고정. fill_sync 가 장중 60초
# 주기라 정상 운영에서 전후 각 1건 이상이 반드시 잡히고, 창을 넓히면 같은 날
# 재매수/재매도의 동기화 행을 잘못 물 수 있어 좁게 유지한다.
_CANDIDATE_SQL = """
    SELECT o.id, o.ts, o.ticker, o.qty, o.mode,
           (SELECT (a.details->>'qty')::int FROM audit_log a
             WHERE a.event_type = 'POSITION_SYNCED'
               AND a.details->>'ticker' = o.ticker
               AND a.ts < o.ts AND a.ts > o.ts - interval '10 min'
             ORDER BY a.ts DESC LIMIT 1) AS pre_qty,
           (SELECT (a.details->>'qty')::int FROM audit_log a
             WHERE a.event_type = 'POSITION_SYNCED'
               AND a.details->>'ticker' = o.ticker
               AND a.ts > o.ts AND a.ts < o.ts + interval '10 min'
             ORDER BY a.ts ASC LIMIT 1) AS post_qty,
           (SELECT s.eval_price FROM position_eval_snapshot s
             WHERE s.trading_day = o.ts::date AND s.ticker = o.ticker) AS snap_px,
           (SELECT k.close::int FROM ohlcv k
             WHERE k.symbol = o.ticker AND k.ts = o.ts::date LIMIT 1) AS ohlcv_px
      FROM orders o
     WHERE o.side = 'sell'
       AND o.status = 'expired'
       AND o.mode = 'paper'
     ORDER BY o.ts ASC
"""


def _resolve_price(row: dict[str, Any]) -> tuple[int | None, str]:
    """체결가 근사값과 그 출처 라벨. 둘 다 없으면 ``(None, "unavailable")``."""
    snap = row.get("snap_px")
    if snap:
        return int(snap), "position_eval_snapshot.eval_price"
    ohlcv = row.get("ohlcv_px")
    if ohlcv:
        return int(ohlcv), "ohlcv.close"
    return None, "unavailable"


def _classify(row: dict[str, Any]) -> tuple[bool, str]:
    """대상 여부 + 사유. 안전 불변식(모듈 독스트링) 판정 지점."""
    pre, post = row.get("pre_qty"), row.get("post_qty")
    if pre is None or post is None:
        return False, "no POSITION_SYNCED evidence around order"
    if int(pre) - int(post) != int(row["qty"]):
        return False, (
            f"KIS balance delta {int(pre) - int(post)} != order qty {row['qty']} "
            "— genuinely unfilled"
        )
    if _resolve_price(row)[0] is None:
        return False, "no price source (snapshot/ohlcv both missing)"
    return True, "KIS balance dropped by exactly the sold quantity"


def repair_expired_sells(*, dry_run: bool = True) -> dict[str, Any]:
    """오만료된 페이퍼 매도를 체결로 되돌린다.

    Returns 요약 dict: ``repaired`` / ``skipped`` 건수와 행별 ``details``.
    ``dry_run=True`` (기본) 면 SELECT + 판정만 하고 UPDATE/audit 을 하지 않는다.
    """
    summary: dict[str, Any] = {
        "dry_run": dry_run,
        "candidates": 0,
        "repaired": 0,
        "skipped": 0,
        "details": [],
    }

    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_CANDIDATE_SQL)
            rows = [dict(r) for r in cur.fetchall()]

        summary["candidates"] = len(rows)

        for row in rows:
            eligible, reason = _classify(row)
            price, price_source = _resolve_price(row)
            qty = int(row["qty"])
            entry: dict[str, Any] = {
                "order_id": int(row["id"]),
                "ticker": row["ticker"],
                "qty": qty,
                "pre_qty": row.get("pre_qty"),
                "post_qty": row.get("post_qty"),
                "eligible": eligible,
                "reason": reason,
                "fill_price": price,
                "price_source": price_source,
            }

            if not eligible:
                summary["skipped"] += 1
                summary["details"].append(entry)
                LOG.info(
                    "expired_sell_repair SKIP order_id=%s %s — %s",
                    row["id"], row["ticker"], reason,
                )
                continue

            if price is None:  # _classify 가 이미 걸러내지만 타입 내로잉용
                summary["skipped"] += 1
                summary["details"].append(entry)
                continue

            fee = estimate_fee(
                mode=str(row["mode"]),
                side="sell",
                market=_MARKET_DEFAULT,
                notional=price * qty,
            )
            entry["fee"] = fee
            summary["repaired"] += 1
            summary["details"].append(entry)

            if dry_run:
                LOG.info(
                    "expired_sell_repair DRY order_id=%s %s qty=%d px=%d(%s) fee=%d",
                    row["id"], row["ticker"], qty, price, price_source, fee,
                )
                continue

            with conn.cursor() as cur:
                # status='expired' 가드 = 멱등성. 이미 교정된 행은 0 rows.
                cur.execute(
                    """
                    UPDATE orders
                       SET status = 'filled',
                           fill_qty = %s,
                           fill_price = %s,
                           fee = %s,
                           filled_at = ts,
                           synthetic = TRUE
                     WHERE id = %s AND status = 'expired'
                    """,
                    (qty, price, fee, int(row["id"])),
                )
                cur.execute(
                    "INSERT INTO audit_log (event_type, actor, details) "
                    "VALUES (%s, %s, %s::jsonb)",
                    (
                        "EXPIRED_SELL_REPAIRED",
                        "ledger_repair",
                        json.dumps({
                            **entry,
                            "caveat": "fill_price 는 근사값 — 실체결가 복원 불가",
                            "root_cause": "_synthetic_fill read held qty AFTER "
                                          "KIS POST (fixed by pre_submit_held)",
                            "decision_scope": "ledger_repair",
                        }),
                    ),
                )
            LOG.info(
                "expired_sell_repair OK order_id=%s %s qty=%d px=%d(%s) fee=%d",
                row["id"], row["ticker"], qty, price, price_source, fee,
            )

    return summary
