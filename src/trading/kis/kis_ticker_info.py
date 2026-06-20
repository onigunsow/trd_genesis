"""SPEC-TRADING-054 follow-up: KIS 기반 KRX 독립 종목명·업종 캐시 resolver.

KRX/pykrx 없이 KIS API 만으로 name + sector 를 조회한다.
- 종목명 (hts_kor_isnm): /uapi/domestic-stock/v1/quotations/search-info (TR FHKST03010100)
- 업종 (bstp_kor_isnm): /uapi/domestic-stock/v1/quotations/inquire-price (TR FHKST01010100)

ticker_metadata 테이블 upsert 진입점:
  from trading.kis.kis_ticker_info import resolve_and_cache
  resolve_and_cache(client, ['055550', '005930'])  # 배치

백필 진입점:
  from trading.kis.kis_ticker_info import backfill
  print(backfill())

대시보드 읽기 전용 resolver:
  from trading.kis.kis_ticker_info import lookup_names_from_db
  lookup_names_from_db(['055550', '005930'])  # -> {'055550': '신한지주', ...}
"""

from __future__ import annotations

import logging
from typing import Any

LOG = logging.getLogger(__name__)

# KIS search-info 엔드포인트: paper/live 공용 TR_ID
_SEARCH_INFO_PATH = "/uapi/domestic-stock/v1/quotations/search-info"
_SEARCH_INFO_TR_ID = "FHKST03010100"  # paper/live 동일 (조회 전용)

# KIS inquire-price 엔드포인트: 업종 (bstp_kor_isnm) 소스
_INQUIRE_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
_INQUIRE_PRICE_TR_ID = "FHKST01010100"

# name 폴백 체인 (fetch_ticker_name 실패 시)
_STATIC_FALLBACK: dict[str, str] = {
    # 기존 personas.context.TICKER_NAMES 의 소규모 목록
}

# sector 폴백 (업종 조회 실패 시)
_DEFAULT_SECTOR = "미분류"


# ---------------------------------------------------------------------------
# KIS 단건 조회 (per-ticker try/except — 네트워크 오류가 배치 전체를 죽이지 않음)
# ---------------------------------------------------------------------------

def _fetch_ticker_name(client: Any, ticker: str) -> str | None:
    """KIS search-info 로 종목명(hts_kor_isnm) 조회.

    Args:
        client: KisClient 인스턴스.
        ticker: 6자리 종목코드.

    Returns:
        한국어 종목명 문자열, 또는 조회 실패 시 None.
    """
    try:
        resp = client.get(
            _SEARCH_INFO_PATH,
            tr_id=_SEARCH_INFO_TR_ID,
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
                # search-info 는 날짜 파라미터 불필요이나
                # 일봉 파라미터는 무시됨 — hts_kor_isnm 은 output1 에 항상 포함.
                "FID_INPUT_DATE_1": "20240101",
                "FID_INPUT_DATE_2": "20260101",
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            },
        )
        if resp.rt_cd != "0":
            LOG.warning(
                "KIS search-info 실패 ticker=%s rt_cd=%s", ticker, resp.rt_cd
            )
            return None
        raw = getattr(resp, "raw", {})
        # output1 또는 output 중 dict 를 우선 선택
        output = raw.get("output1", raw.get("output", {}))
        if isinstance(output, list):
            output = output[0] if output else {}
        name = output.get("hts_kor_isnm", "")
        return str(name).strip() if name else None
    except Exception as exc:
        LOG.warning("KIS search-info 예외 ticker=%s: %s", ticker, exc)
        return None


def _fetch_ticker_sector(client: Any, ticker: str) -> str | None:
    """KIS inquire-price 로 업종명(bstp_kor_isnm) 조회.

    Args:
        client: KisClient 인스턴스.
        ticker: 6자리 종목코드.

    Returns:
        한국어 업종명 문자열, 또는 조회 실패 시 None.
    """
    try:
        resp = client.get(
            _INQUIRE_PRICE_PATH,
            tr_id=_INQUIRE_PRICE_TR_ID,
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
        )
        if resp.rt_cd != "0":
            LOG.warning(
                "KIS inquire-price 실패 ticker=%s rt_cd=%s", ticker, resp.rt_cd
            )
            return None
        raw = getattr(resp, "raw", {})
        output = raw.get("output", {})
        if isinstance(output, list):
            output = output[0] if output else {}
        sector = output.get("bstp_kor_isnm", "")
        return str(sector).strip() if sector else None
    except Exception as exc:
        LOG.warning("KIS inquire-price 예외 ticker=%s: %s", ticker, exc)
        return None


# ---------------------------------------------------------------------------
# DB upsert 헬퍼
# ---------------------------------------------------------------------------

_UPSERT_SQL = """
    INSERT INTO ticker_metadata (ticker, name, sector, industry, updated_at)
    VALUES (%s, %s, %s, %s, NOW())
    ON CONFLICT (ticker) DO UPDATE SET
        name       = CASE WHEN EXCLUDED.name != '' THEN EXCLUDED.name ELSE ticker_metadata.name END,
        sector     = CASE WHEN EXCLUDED.sector != '' AND EXCLUDED.sector != '미분류'
                          THEN EXCLUDED.sector ELSE ticker_metadata.sector END,
        industry   = EXCLUDED.industry,
        updated_at = NOW()
"""


def _upsert_ticker_info(
    entries: list[tuple[str, str, str]],
) -> int:
    """ticker_metadata 에 (ticker, name, sector) 배치 upsert.

    기존 name/sector 가 있고 새 값이 비어있으면 덮어쓰지 않는다 (CASE 절).

    Args:
        entries: [(ticker, name, sector), ...]

    Returns:
        upsert 행 수.
    """
    if not entries:
        return 0

    from trading.db.session import connection

    count = 0
    try:
        with connection() as conn, conn.cursor() as cur:
            for ticker, name, sector in entries:
                cur.execute(_UPSERT_SQL, (ticker, name, sector, ""))
                count += 1
        LOG.info("ticker_metadata upsert: %d 행", count)
    except Exception as exc:
        LOG.error("ticker_metadata upsert 실패: %s", exc)
    return count


# ---------------------------------------------------------------------------
# 배치 resolver (공개 API)
# ---------------------------------------------------------------------------

# @MX:ANCHOR: [AUTO] resolve_and_cache — ticker name+sector 일괄 조회·캐시 단일 진입점.
# @MX:REASON: backfill / fills.py reconcile upkeep / 테스트가 이 함수를 소비
#   (fan_in >= 3). 종목별 try/except 격리로 부분 장애가 배치 전체를 죽이지 않는다.
def resolve_and_cache(client: Any, tickers: list[str]) -> dict[str, Any]:
    """KIS 로 종목명·업종 조회 후 ticker_metadata 에 upsert.

    종목별 try/except 격리: KIS 타임아웃·오류가 발생해도 해당 종목만 건너뛰고
    나머지를 계속 처리한다.

    Args:
        client: KisClient 인스턴스.
        tickers: 6자리 종목코드 목록.

    Returns:
        {"attempted": N, "upserted": M, "failed": K,
         "results": {ticker: {"name": ..., "sector": ...}}}
    """
    if not tickers:
        return {"attempted": 0, "upserted": 0, "failed": 0, "results": {}}

    # 중복 제거
    unique = list(dict.fromkeys(t for t in tickers if t))
    entries: list[tuple[str, str, str]] = []
    results: dict[str, dict[str, str]] = {}
    failed = 0

    for ticker in unique:
        try:
            name = _fetch_ticker_name(client, ticker) or ""
            sector = _fetch_ticker_sector(client, ticker) or _DEFAULT_SECTOR

            # 정적 폴백 (KIS 종목명 조회 실패 시)
            if not name and ticker in _STATIC_FALLBACK:
                name = _STATIC_FALLBACK[ticker]

            entries.append((ticker, name, sector))
            results[ticker] = {"name": name, "sector": sector}
            LOG.debug("resolved ticker=%s name=%r sector=%r", ticker, name, sector)
        except Exception as exc:
            # 이중 안전망: _fetch_* 내부에서도 except 하지만 최외각에서도 보호
            LOG.error("resolve_and_cache: ticker=%s 예외 (건너뜀): %s", ticker, exc)
            failed += 1

    upserted = _upsert_ticker_info(entries)

    return {
        "attempted": len(unique),
        "upserted": upserted,
        "failed": failed,
        "results": results,
    }


# ---------------------------------------------------------------------------
# 백필 진입점
# ---------------------------------------------------------------------------

def _collect_backfill_tickers() -> list[str]:
    """백필 대상 종목 수집.

    orders (paper, 최근 180일 체결) + positions (round-trip 원장 종목).
    """
    from trading.db.session import connection

    sql = """
        SELECT DISTINCT ticker FROM (
            SELECT ticker FROM orders
             WHERE status IN ('filled', 'partial')
               AND ts >= NOW() - INTERVAL '180 days'
            UNION
            SELECT ticker FROM positions
             WHERE ticker IS NOT NULL AND ticker != ''
        ) sub
        WHERE ticker IS NOT NULL AND ticker != ''
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql)
            return [r["ticker"] for r in cur.fetchall()]
    except Exception as exc:
        LOG.error("백필 대상 종목 조회 실패: %s", exc)
        return []


def backfill(client: Any | None = None) -> dict[str, Any]:
    """ticker_metadata 백필: DB 내 모든 거래 종목에 대해 name+sector 적재.

    CLI 사용법:
        docker exec trading-app python -c \\
            "from trading.kis.kis_ticker_info import backfill; print(backfill())"

    Args:
        client: KisClient 인스턴스. None 이면 내부에서 생성.

    Returns:
        resolve_and_cache 결과 dict.
    """
    if client is None:
        from trading.config import get_settings
        from trading.kis.client import KisClient

        client = KisClient(get_settings().trading_mode)

    tickers = _collect_backfill_tickers()
    LOG.info("백필 대상 종목 %d 개: %s", len(tickers), tickers)

    if not tickers:
        return {"attempted": 0, "upserted": 0, "failed": 0, "results": {}}

    result = resolve_and_cache(client, tickers)
    LOG.info(
        "백필 완료: attempted=%d upserted=%d failed=%d",
        result["attempted"], result["upserted"], result["failed"],
    )
    return result


# ---------------------------------------------------------------------------
# 대시보드 읽기 전용 조회 (DB 전용, KIS/pykrx 호출 없음)
# ---------------------------------------------------------------------------

# @MX:ANCHOR: [AUTO] lookup_names_from_db — 대시보드 요청 경로의 단일 name 조회 진입점.
# @MX:REASON: fetch_roundtrips / fetch_portfolio / fetch_holdings 등 N개 함수가 호출.
#   KIS/pykrx를 절대 호출하지 않으므로 대시보드 요청 지연에 영향 없음(REQ-054).
def lookup_names_from_db(tickers: list[str]) -> dict[str, str]:
    """ticker_metadata 에서 종목명 일괄 조회 (읽기 전용, 즉시 반환).

    대시보드 요청 경로에서 호출. KIS/pykrx 절대 호출 안 함.

    Args:
        tickers: 조회할 종목코드 목록 (중복 포함 가능).

    Returns:
        {ticker: name} 딕셔너리. 미등록 종목은 포함되지 않는다.
    """
    from trading.dashboard.db import ro_connection

    unique = list(dict.fromkeys(t for t in tickers if t))
    if not unique:
        return {}

    try:
        with ro_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT ticker, name FROM ticker_metadata WHERE ticker = ANY(%s)",
                (unique,),
            )
            return {r["ticker"]: r["name"] for r in cur.fetchall() if r.get("name")}
    except Exception as exc:
        LOG.warning("lookup_names_from_db 실패 (빈 dict 반환): %s", exc)
        return {}


def resolve_ticker_name(ticker: str, *, db_names: dict[str, str]) -> str:
    """단일 종목의 표시명 결정 (우선순위: DB캐시 > 정적폴백 > 코드).

    Args:
        ticker: 6자리 종목코드.
        db_names: lookup_names_from_db() 결과.

    Returns:
        표시용 문자열 (항상 비어 있지 않음).
    """
    if ticker in db_names and db_names[ticker]:
        return db_names[ticker]
    if ticker in _STATIC_FALLBACK:
        return _STATIC_FALLBACK[ticker]
    return ticker  # 최후 폴백: 코드 그대로 반환
