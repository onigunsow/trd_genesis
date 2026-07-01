"""SPEC-TRADING-054 ADR-002: ticker_metadata 로더.

KRX/pykrx 에서 종목→업종 매핑을 가져와 ticker_metadata 테이블에 upsert 한다.
트레이딩 결정 경로와 완전히 분리된 별도 유틸이다(REQ-054-A7).

사용법:
    trading sector-load              # 유니버스 + positions 테이블 종목 적재
    trading sector-load --all        # KRX 전체 종목 적재 (시간 소요)
    trading sector-load 005930 000660  # 지정 종목만 적재

KRX 크레덴셜 / pykrx 미설치 환경에서는 graceful skip 후 로그만 남긴다.
대시보드는 매핑 없는 종목을 "미분류" 로 자동 폴백(REQ-054-G1).

CHANGE C (2026-07-01): _fetch_sector_map 배치 fetch — N 종목에도 pykrx
get_market_sector_classifications 를 KOSPI/KOSDAQ 각 1회만 호출한다.
_quiet_pykrx + 서킷브레이커 가드를 적용해 소음·hammer 방지.

CHANGE D (2026-07-01): load_sector_metadata 기본 타겟을
get_data_universe() + _tickers_from_db() 합집합으로 확장해 후보 종목 섹터를
사전 적재한다.
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

# _quiet_pykrx 와 _get_shared_breaker 는 모듈 수준에서 임포트해 테스트에서
# patch('trading.dashboard.sector_loader._quiet_pykrx') 등으로 교체 가능하게 한다.
try:
    from trading.data.krx_circuit_breaker import _get_shared_breaker
    from trading.data.pykrx_adapter import _quiet_pykrx
except ImportError:
    # 테스트 환경 등에서 의존성 미설치 시 graceful skip
    _quiet_pykrx = None  # type: ignore[assignment]
    _get_shared_breaker = None  # type: ignore[assignment]


def _fetch_sector_map(tickers: list[str]) -> dict[str, tuple[str, str]]:
    """pykrx 로 종목별 업종 조회 (배치 — KOSPI/KOSDAQ 각 1회).

    기존 구현은 N 종목에 대해 per-ticker 루프 안에서 전체 시장 분류표를 최대
    2N 회 내려받는 버그가 있었다. 이 구현은 전체 분류표를 KOSPI/KOSDAQ 각 1회
    페치한 뒤 requested tickers 를 일괄 조회한다.

    Returns:
        {ticker: (sector, industry)} 딕셔너리.
        조회 실패·서킷 OPEN·pykrx 미설치 시 {} 반환.
    """
    if not _PYKRX_AVAILABLE:
        LOG.warning("pykrx 미설치 — 업종 조회 불가. ticker_metadata 적재 건너뜀.")
        return {}

    if not tickers:
        return {}

    from trading.data.krx_circuit_breaker import KrxCircuitOpen

    breaker = _get_shared_breaker()
    try:
        breaker.check_or_raise()
    except KrxCircuitOpen as exc:
        LOG.warning("sector_loader: 서킷 OPEN — pykrx 호출 생략: %s", exc)
        return {}

    import datetime
    today = datetime.date.today().strftime("%Y%m%d")

    # KOSPI / KOSDAQ 분류표 각 1회 페치 (배치)
    try:
        with _quiet_pykrx():
            df_kospi = _pykrx_stock.get_market_sector_classifications(today, market="KOSPI")
        with _quiet_pykrx():
            df_kosdaq = _pykrx_stock.get_market_sector_classifications(today, market="KOSDAQ")
    except Exception as exc:
        LOG.warning("sector_loader: pykrx 분류표 조회 실패: %s", exc)
        breaker.record_failure()
        return {}

    breaker.record_success()

    result: dict[str, tuple[str, str]] = {}
    for ticker in tickers:
        # KOSPI 우선, 없으면 KOSDAQ
        if df_kospi is not None and len(df_kospi) > 0 and ticker in df_kospi.index:
            sector = str(df_kospi.loc[ticker, "GICS섹터"] or "미분류")
            industry = str(df_kospi.loc[ticker, "GICS산업군"] or "")
            result[ticker] = (sector, industry)
        elif df_kosdaq is not None and len(df_kosdaq) > 0 and ticker in df_kosdaq.index:
            sector = str(df_kosdaq.loc[ticker, "GICS섹터"] or "미분류")
            industry = str(df_kosdaq.loc[ticker, "GICS산업군"] or "")
            result[ticker] = (sector, industry)

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


def _get_universe() -> list[str]:
    """get_data_universe() 를 호출해 후보 유니버스를 반환한다.

    별도 함수로 분리해 테스트에서 patch('...sector_loader._get_universe') 로
    교체 가능하게 한다.
    """
    from trading.data.universe import get_data_universe
    return get_data_universe()


def load_sector_metadata(tickers: list[str] | None = None) -> dict[str, Any]:
    """ticker_metadata 를 적재하는 메인 진입점.

    Args:
        tickers: None 이면 _get_universe() 와 _tickers_from_db() 합집합 자동 수집.
                 리스트면 지정 종목만.

    Returns:
        {"attempted": N, "upserted": M, "pykrx_available": bool}

    CHANGE D: tickers=None 시 기본 타겟을 유니버스(후보 종목 포함) + DB 보유
    종목의 합집합으로 확장한다. 섹터 집중 가드가 신규 BUY 후보의 섹터를
    알아야 하므로 사전 적재가 필수다. 각 소스는 독립 try/except 로 보호돼
    한쪽 실패가 나머지 적재를 막지 않는다.
    """
    if tickers is not None:
        target = tickers
    else:
        # 소스별 독립 수집 — 한쪽 실패가 전체를 막지 않는다
        universe: list[str] = []
        try:
            universe = _get_universe()
        except Exception as exc:
            LOG.warning("sector_loader: get_data_universe 실패 (건너뜀): %s", exc)

        db_tickers: list[str] = []
        try:
            db_tickers = _tickers_from_db()
        except Exception as exc:
            LOG.warning("sector_loader: _tickers_from_db 실패 (건너뜀): %s", exc)

        # 중복 제거 후 정렬 (결정론적 순서)
        target = sorted(set(universe) | set(db_tickers))

    LOG.info("sector_loader: 대상 종목 %d 개", len(target))

    sector_map = _fetch_sector_map(target)
    upserted = _upsert_ticker_metadata(sector_map)

    return {
        "attempted": len(target),
        "upserted": upserted,
        "pykrx_available": _PYKRX_AVAILABLE,
    }
