"""Phase 0 — 일별 자산 스냅샷.

매 거래일 마감 후 KIS inquire-balance 를 한 번 호출해 총자산/주식평가/예수금/미실현손익을
``daily_equity_snapshot`` 에 UPSERT 한다. 같은 거래일 재실행은 멱등(ON CONFLICT DO UPDATE).

realized_pnl_cum 은 balance() 가 제공하지 않으므로 여기서는 건드리지 않는다(NULL 유지 또는
기존 값 보존). edge/roundtrips 의 누적 실현손익 백필이 그 컬럼을 채운다.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from trading.db.session import connection

LOG = logging.getLogger(__name__)


def _today_kst() -> date:
    """오늘(KST) 날짜. 스케줄러가 KST cron 으로 호출하므로 컨테이너 TZ 와 무관하게 일치."""
    from datetime import datetime

    import pytz

    return datetime.now(pytz.timezone("Asia/Seoul")).date()


def record_snapshot(client: Any | None = None, *, trading_day: date | None = None) -> dict[str, Any]:
    """balance() 한 번 호출 → daily_equity_snapshot UPSERT. 기록한 행 dict 반환.

    Parameters
    ----------
    client : KisClient | None
        미지정 시 ``KisClient(get_settings().trading_mode)`` 로 생성.
    trading_day : date | None
        미지정 시 오늘(KST).
    """
    # 무거운 KIS 스택은 호출 시점에만 로드 (스케줄러/CLI import 비용 절약).
    from trading.kis.account import balance

    if client is None:
        from trading.config import get_settings
        from trading.kis.client import KisClient

        client = KisClient(get_settings().trading_mode)

    day = trading_day or _today_kst()
    bal = balance(client)

    # @MX:WARN: ``cash`` 컬럼은 ``cash_d2``(D+2 예수금)다 — 당일 현금이 아니다.
    # @MX:REASON: 2026-09-12 실측 — ``total_assets - stock_eval - cash`` 가 매매가
    #   많은 날 ±2,234,640원까지 벌어진다(9/10). 미결제 정산분이 total_assets 에는
    #   들어가고 cash_d2 에는 아직 안 들어가기 때문이며 결함이 아니다. 다만
    #   **``cash / total_assets`` 를 현금비중으로 쓰면 틀린다.** 현금비중이 필요하면
    #   ``total_assets - stock_eval`` 를 쓰거나(현금+미결제), 잔고 %와 맞추려면
    #   ``invest_basis``(cash + stock_eval, REQ-029-10) 를 분모로 쓸 것.
    #   투자비중은 ``stock_eval / total_assets`` 라 이 함정과 무관하다
    #   (edge/benchmark.py invested_share).
    row = {
        "trading_day": day,
        "total_assets": int(bal.get("total_assets", 0) or 0),
        "stock_eval": int(bal.get("stock_eval", 0) or 0),
        "cash": int(bal.get("cash_d2", 0) or 0),
        "unrealized_pnl": int(bal.get("pnl_total", 0) or 0),
    }

    sql = """
        INSERT INTO daily_equity_snapshot
            (trading_day, total_assets, stock_eval, cash, unrealized_pnl)
        VALUES (%(trading_day)s, %(total_assets)s, %(stock_eval)s, %(cash)s, %(unrealized_pnl)s)
        ON CONFLICT (trading_day) DO UPDATE SET
            total_assets   = EXCLUDED.total_assets,
            stock_eval     = EXCLUDED.stock_eval,
            cash           = EXCLUDED.cash,
            unrealized_pnl = EXCLUDED.unrealized_pnl,
            created_at     = NOW()
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, row)

    LOG.info(
        "equity_snapshot %s total=%d stock=%d cash=%d unrealized=%d",
        day,
        row["total_assets"],
        row["stock_eval"],
        row["cash"],
        row["unrealized_pnl"],
    )
    return row
