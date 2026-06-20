"""SPEC-TRADING-054 ADR-002: ticker_metadata 로더.

KRX/pykrx 에서 종목→업종 매핑을 가져와 ticker_metadata 테이블에 upsert 한다.
트레이딩 결정 경로와 완전히 분리된 별도 유틸이다(REQ-054-A7).

사용법:
    trading sector-load              # 현재 보유 종목 + positions 테이블 종목 적재
    trading sector-load --all        # KRX 전체 종목 적재 (시간 소요)
    trading sector-load 005930 000660  # 지정 종목만 적재

KRX 크레덴셜 / pykrx 미설치 환경에서는 graceful skip 후 로그만 남긴다.
대시보드는 매핑 없는 종목을 "미분류" 로 자동 폴백(REQ-054-G1).
"""

from __future__ import annotations

import logging
from typing import Any

LOG = logging.getLogger(__name__)

# pykrx 업종 분류 시도 — 미설치 환경에서 graceful skip
try:
    from pykrx import stock as _pykrx_stock  # type: ignore[import]
    _PYKRX_AVAILABLE = True
except ImportError:
    _pykrx_stock = None
    _PYKRX_AVAILABLE = False


def _fetch_sector_map(tickers: list[str]) -> dict[str, tuple[str, str]]:
    """pykrx 로 종목별 업종 조회.

    Returns:
        {ticker: (sector, industry)} 딕셔너리.
        조회 실패 종목은 포함하지 않는다.
    """
    if not _PYKRX_AVAILABLE:
        LOG.warning("pykrx 미설치 — 업종 조회 불가. ticker_metadata 적재 건너뜀.")
        return {}

    result: dict[str, tuple[str, str]] = {}
    for ticker in tickers:
        try:
            # pykrx 업종 조회: get_market_sector_classifications 는 날짜 필요
            import datetime
            today = datetime.date.today().strftime("%Y%m%d")
            # KOSPI 업종 시도
            df = _pykrx_stock.get_market_sector_classifications(today, market="KOSPI")
            if ticker in df.index:
                sector = str(df.loc[ticker, "GICS섹터"] or "미분류")
                industry = str(df.loc[ticker, "GICS산업군"] or "")
                result[ticker] = (sector, industry)
                continue
            # KOSDAQ 업종 시도
            df2 = _pykrx_stock.get_market_sector_classifications(today, market="KOSDAQ")
            if ticker in df2.index:
                sector = str(df2.loc[ticker, "GICS섹터"] or "미분류")
                industry = str(df2.loc[ticker, "GICS산업군"] or "")
                result[ticker] = (sector, industry)
        except Exception as exc:
            LOG.warning("ticker %s 업종 조회 실패 (건너뜀): %s", ticker, exc)

    return result


def _tickers_from_db() -> list[str]:
    """positions 테이블 + orders 테이블(최근 90일 paper 체결)에서 종목코드 수집."""
    from trading.db.session import connection

    sql = """
        SELECT DISTINCT ticker FROM (
            SELECT ticker FROM positions WHERE qty > 0
            UNION
            SELECT ticker FROM orders
             WHERE mode = 'paper'
               AND status IN ('filled', 'partial')
               AND ts >= NOW() - INTERVAL '90 days'
        ) sub
        WHERE ticker IS NOT NULL AND ticker != ''
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
        return [r["ticker"] for r in rows]
    except Exception as exc:
        LOG.error("DB 에서 종목 목록 조회 실패: %s", exc)
        return []


def _upsert_ticker_metadata(sector_map: dict[str, tuple[str, str]]) -> int:
    """ticker_metadata 에 sector_map 을 upsert. 반환값: upsert 행 수."""
    if not sector_map:
        return 0

    from trading.db.session import connection

    _SQL = """
        INSERT INTO ticker_metadata (ticker, sector, industry, updated_at)
        VALUES (%s, %s, %s, NOW())
        ON CONFLICT (ticker) DO UPDATE SET
            sector     = EXCLUDED.sector,
            industry   = EXCLUDED.industry,
            updated_at = NOW()
    """
    count = 0
    try:
        with connection() as conn, conn.cursor() as cur:
            for ticker, (sector, industry) in sector_map.items():
                cur.execute(_SQL, (ticker, sector, industry))
                count += 1
        LOG.info("ticker_metadata upsert: %d 행", count)
    except Exception as exc:
        LOG.error("ticker_metadata upsert 실패: %s", exc)
    return count


def load_sector_metadata(tickers: list[str] | None = None) -> dict[str, Any]:
    """ticker_metadata 를 적재하는 메인 진입점.

    Args:
        tickers: None 이면 DB 에서 자동 수집. 리스트면 지정 종목만.

    Returns:
        {"attempted": N, "upserted": M, "pykrx_available": bool}
    """
    target = tickers if tickers is not None else _tickers_from_db()
    LOG.info("sector_loader: 대상 종목 %d 개", len(target))

    sector_map = _fetch_sector_map(target)
    upserted = _upsert_ticker_metadata(sector_map)

    return {
        "attempted": len(target),
        "upserted": upserted,
        "pykrx_available": _PYKRX_AVAILABLE,
    }
