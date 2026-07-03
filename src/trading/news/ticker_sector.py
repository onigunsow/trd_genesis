"""SPEC-TRADING-060 REQ-060-1: 티커→news 섹터 단일 진실원천.

ticker_metadata DB 조회 + sector_taxonomy.yaml 정밀-우선 매핑으로
티커를 news 섹터 키로 해소한다. TICKER_SECTOR_MAP 하드코딩 대체.
"""

from __future__ import annotations

import logging

LOG = logging.getLogger(__name__)


# @MX:ANCHOR: [AUTO] resolve_ticker_sector — 티커→news 섹터 단일 진실원천.
# @MX:REASON: 이 함수가 오경보 억제의 핵심 불변식: 미매핑 시 반드시 None 반환
#             (가짜 캐치올 금지). context_builder·relevance·reporter 최소 3개소 소비.
# @MX:SPEC: SPEC-TRADING-060


def _lookup_ticker_metadata(ticker: str) -> dict | None:
    """DB 에서 ticker_metadata 행을 조회해 반환. 없으면 None.

    조회 컬럼: sector(업종명), name(회사명).
    """
    from trading.db.session import connection

    sql = """
        SELECT sector, name
          FROM ticker_metadata
         WHERE ticker = %s
         LIMIT 1
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (ticker,))
        row = cur.fetchone()
    return dict(row) if row else None


def resolve_ticker_sector(ticker: str, market: str | None = None) -> str | None:
    """티커를 news 섹터 키로 해소한다.

    (a) ticker_metadata 에서 업종명 lookup →
    (b) sector_taxonomy.news_sector(업종명) 으로 매핑.

    미존재·미매핑 → None (가짜 캐치올 절대 금지).

    Args:
        ticker: 종목코드 (예: '016140').
        market: 시장 코드. None 이면 active_market() 사용.

    Returns:
        news 섹터 키 (예: 'finance_banking') 또는 None.
    """
    try:
        row = _lookup_ticker_metadata(ticker)
        if row is None:
            return None
        raw_sector = row.get("sector") or ""
        if not str(raw_sector).strip():
            return None
        from trading.data.sector_taxonomy import news_sector

        return news_sector(raw_sector, market)
    except Exception:
        LOG.debug("resolve_ticker_sector(%s) 예외 → None 반환", ticker)
        return None
