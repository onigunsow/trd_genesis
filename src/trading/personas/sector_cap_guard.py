"""SPEC-TRADING-059(저변동+퀄리티) 섹터 집중 코드 가드.

decision.jinja:15 "단일 섹터 40% 초과 금지" 규칙을 Python 코드 레벨에서 강제한다.
enforce_cash_floor와 동일한 설계 원칙을 따른다:

- 순수 함수(pure): DB/네트워크 의존성은 호출자가 주입 (price_map, sector_map)
- fail-open: 미분류(unknown) 섹터는 차단하지 않고 WARNING 로그만 남긴다.
- 매도 불간섭: SELL/HOLD 신호는 절대 건드리지 않는다.
- regime 연동: sector_cap_pct는 호출자가 adjust_for_regime()에서 읽어서 주입한다.

# @MX:ANCHOR: [AUTO] enforce_sector_cap — 섹터 cap 강제 단일 진입점.
# @MX:REASON: fan_in >= 3 (portfolio_gate, 테스트, 미래 watchdog 경로).
#             이 함수가 없으면 섹터 분산 규칙은 프롬프트에만 존재해 우회 가능.
"""

from __future__ import annotations

import logging
from typing import Any

from trading.db.session import connection

LOG = logging.getLogger(__name__)

# 섹터 미상 마커 (ticker_metadata 미적재 / sector 컬럼 NULL 또는 '미분류')
_UNKNOWN_SECTOR = "미분류"


def get_sectors_from_db(tickers: list[str]) -> dict[str, str]:
    """ticker_metadata 테이블에서 종목별 섹터 조회 (읽기 전용).

    Returns:
        {ticker: sector} 딕셔너리. 조회 실패·빈 목록은 {} 반환 (fail-safe).
    """
    if not tickers:
        return {}

    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT ticker, sector FROM ticker_metadata WHERE ticker = ANY(%s)",
                (tickers,),
            )
            rows = cur.fetchall()
        # sector가 NULL이거나 빈 문자열이면 미분류로 처리
        return {
            row["ticker"]: (row["sector"] or _UNKNOWN_SECTOR).strip() or _UNKNOWN_SECTOR
            for row in rows
        }
    except Exception as exc:
        LOG.warning("섹터 DB 조회 실패 (fail-open): %s", exc)
        return {}


def enforce_sector_cap(
    signals: list[dict[str, Any]],
    *,
    holdings: list[dict[str, Any]],
    total_portfolio: int,
    sector_cap_pct: float,
    price_map: dict[str, int],
    sector_map: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """섹터 집중 cap을 초과하는 신규 BUY를 차단한다.

    매수 신호를 순서대로 처리하면서 (이미 차단된 신호가 없다고 가정하고)
    그 매수가 해당 섹터 비중을 sector_cap_pct 이상으로 만들면 차단한다.
    여러 BUY가 같은 섹터일 경우 순서대로 누적 계산한다.

    Args:
        signals: Decision 신호 목록 (buy/sell/hold 혼합 가능).
        holdings: 현재 보유 목록 (assets["holdings"] 형식).
                  각 항목에 "sector"·"eval_amount" 키가 있으면 사용한다.
        total_portfolio: 포트폴리오 총 평가액 (원). 0이면 cap 적용 스킵.
        sector_cap_pct: 단일 섹터 최대 비중 (%). regime에 따라 35/40/45.
        price_map: {ticker: 현재가(원)} — 신규 BUY의 매입금액 추정에 사용.
                   가격 정보 없는 종목은 섹터 미상과 동일하게 fail-open 처리.
        sector_map: {ticker: 섹터명} — 호출자가 DB 조회 결과를 주입.
                    없는 종목은 섹터 미상으로 처리 (차단 안 함 + WARNING).

    Returns:
        (kept, dropped_info) 튜플.
        - kept: 통과한 신호 목록 (SELL/HOLD 포함).
        - dropped_info: 차단된 신호 정보 목록 ({ticker, reason}).
    """
    if total_portfolio <= 0:
        # 총자산 0이면 비율 계산 불가 → fail-open
        LOG.warning("enforce_sector_cap: total_portfolio=%d ≤ 0 → cap 미적용", total_portfolio)
        return list(signals), []

    # 1. 보유 종목별 섹터 평가액 합산
    sector_eval: dict[str, int] = {}
    for h in holdings:
        sector = (h.get("sector") or _UNKNOWN_SECTOR).strip() or _UNKNOWN_SECTOR
        if sector == _UNKNOWN_SECTOR:
            continue  # 미분류 보유는 섹터 합산에서 제외 (보수적)
        amount = int(h.get("eval_amount", 0) or 0)
        sector_eval[sector] = sector_eval.get(sector, 0) + amount

    # 2. 신호별 순차 처리
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    unknown_count = 0

    for sig in signals:
        side = (sig.get("side") or "hold").lower()

        # SELL/HOLD는 무조건 통과 (자본 보전 우선)
        if side != "buy":
            kept.append(sig)
            continue

        ticker = sig.get("ticker", "")
        qty = int(sig.get("qty", 0) or 0)

        # 가격 정보 없으면 fail-open
        price = price_map.get(ticker, 0)
        if price <= 0:
            LOG.warning(
                "enforce_sector_cap: %s 가격 정보 없음 → cap 미적용 (fail-open)", ticker
            )
            unknown_count += 1
            kept.append(sig)
            continue

        # 섹터 정보 없으면 fail-open + WARNING
        sector = sector_map.get(ticker, "").strip()
        if not sector or sector == _UNKNOWN_SECTOR:
            LOG.warning(
                "enforce_sector_cap: %s 섹터 정보 없음(미상) → cap 미적용 (fail-open)", ticker
            )
            unknown_count += 1
            kept.append(sig)
            continue

        # 3. 이 BUY가 섹터 비중을 얼마나 올리는지 계산
        buy_amount = price * qty
        current_sector_amount = sector_eval.get(sector, 0)
        new_sector_amount = current_sector_amount + buy_amount
        new_sector_pct = new_sector_amount / total_portfolio * 100.0

        if new_sector_pct > sector_cap_pct:
            LOG.info(
                "섹터 cap 초과 — BUY 차단: ticker=%s 섹터=%s "
                "현재%.1f%% + 신규%.1f%% = %.1f%% > cap=%.1f%%",
                ticker, sector,
                current_sector_amount / total_portfolio * 100.0,
                buy_amount / total_portfolio * 100.0,
                new_sector_pct, sector_cap_pct,
            )
            dropped.append({
                "ticker": ticker,
                "sector": sector,
                "reason": (
                    f"섹터 집중도 초과: {sector} {new_sector_pct:.1f}% > cap {sector_cap_pct:.1f}%"
                ),
            })
        else:
            # 통과 → 누적 섹터 평가액 갱신 (동일 섹터 다음 BUY 계산에 반영)
            sector_eval[sector] = new_sector_amount
            kept.append(sig)

    if unknown_count > 0:
        LOG.warning(
            "enforce_sector_cap: 섹터 미상으로 cap 미적용 %d건 (fail-open)", unknown_count
        )

    return kept, dropped
