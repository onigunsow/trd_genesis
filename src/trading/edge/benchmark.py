"""Phase 1 — KOSPI 매수후보유 대비 알파.

전략의 "실투입 원가 대비 집계 수익률" 을 같은 기간 KOSPI 지수 매수후보유 수익률과 비교한다.
이는 시간가중이 아닌 money-weighted 근사이며(원가 기준 집계), 리포트에 그렇게 라벨링한다.

KOSPI 종가는 캐시(`cached_ohlcv("pykrx","1001",...)`) 우선, 미스 시 pykrx
``get_index_ohlcv`` 로 폴백 후 캐시에 적재한다(korea_momentum.KOSPI_CODE 재사용). 데이터가
없으면 알파를 produce 하지 않는다(graceful, available=False).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Sequence

from trading.data.cache import cached_ohlcv, upsert_ohlcv
from trading.edge.roundtrips import RoundTrip

LOG = logging.getLogger(__name__)

_SOURCE = "pykrx"
# KRX 코스피 종합지수 코드. korea_momentum.KOSPI_CODE 와 동일하나, 그 모듈을 import 하면
# ecos_adapter/httpx 등 무거운 체인을 끌어오므로 상수만 로컬 정의(pykrx get_index_ohlcv 용).
KOSPI_CODE = "1001"


def kospi_closes(start: date, end: date) -> list[tuple[date, float]]:
    """[start, end] KOSPI 종가 (date, close) 오름차순. 실패/없음 시 []."""
    try:
        rows = cached_ohlcv(_SOURCE, KOSPI_CODE, start, end)
    except Exception:  # noqa: BLE001 — 캐시 조회 실패는 graceful
        rows = []
    if rows:
        return [(r["ts"], float(r["close"])) for r in rows if r.get("close")]

    # 캐시 미스 → pykrx 인덱스 폴백 후 캐시 적재.
    try:
        from pykrx import stock  # lazy import (heavy)

        df = stock.get_index_ohlcv(
            start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), KOSPI_CODE
        )
    except Exception:  # noqa: BLE001
        LOG.info("benchmark: KOSPI(%s) fetch 실패 — 알파 unavailable", KOSPI_CODE)
        return []
    if df is None or df.empty:
        return []

    out: list[tuple[date, float]] = []
    cache_rows = []
    for ts, row in df.iterrows():
        d = ts.date() if hasattr(ts, "date") else ts
        close = float(row.get("종가", row.get("Close", 0)) or 0)
        if close:
            out.append((d, close))
            cache_rows.append({
                "ts": d,
                "open": float(row.get("시가", row.get("Open", close)) or close),
                "high": float(row.get("고가", row.get("High", close)) or close),
                "low": float(row.get("저가", row.get("Low", close)) or close),
                "close": close,
                "volume": int(row.get("거래량", row.get("Volume", 0)) or 0),
            })
    try:
        upsert_ohlcv(_SOURCE, KOSPI_CODE, cache_rows)
    except Exception:  # noqa: BLE001 — 적재 실패해도 비교는 진행
        pass
    return sorted(out, key=lambda t: t[0])


class Benchmark:
    # @MX:NOTE: [AUTO] SPEC-TRADING-044 M4 — KOSPI 누적 초과수익 surface.
    # money-weighted(원가기준 집계) vs time-weighted 라벨링.
    # available=False 시 cumulative_excess_return_pct=0.0, comparison_basis="".
    def __init__(self) -> None:
        self.available: bool = False
        self.start: date | None = None
        self.end: date | None = None
        self.kospi_start_close: float = 0.0
        self.kospi_end_close: float = 0.0
        self.kospi_return_pct: float = 0.0
        self.strategy_return_pct: float = 0.0
        self.alpha_pct: float = 0.0

        # SPEC-TRADING-044 M4: 누적 초과수익 + 비교 기준 라벨 (REQ-044-B1, B2)
        self.cumulative_excess_return_pct: float = 0.0
        self.comparison_basis: str = ""


def compute(
    roundtrips: Sequence[RoundTrip],
    *,
    closes: list[tuple[date, float]] | None = None,
) -> Benchmark:
    """라운드트립 기간의 KOSPI 매수후보유 대비 전략 알파.

    ``closes`` 미지정 시 라운드트립 기간으로 KOSPI 종가를 로드한다(테스트는 직접 주입).
    """
    b = Benchmark()
    if not roundtrips:
        return b

    start = min(r.entry_date for r in roundtrips)
    end = max(r.exit_date for r in roundtrips)
    b.start, b.end = start, end

    if closes is None:
        closes = kospi_closes(start, end)
    if len(closes) < 2:
        return b  # available=False

    closes = sorted(closes, key=lambda t: t[0])
    b.kospi_start_close = closes[0][1]
    b.kospi_end_close = closes[-1][1]
    if not b.kospi_start_close:
        return b
    b.kospi_return_pct = (b.kospi_end_close / b.kospi_start_close - 1.0) * 100.0

    # 전략: 실투입 원가 대비 집계 순손익률 (money-weighted 근사).
    total_cost = sum(r.cost_basis for r in roundtrips)
    total_net = sum(r.net_pnl for r in roundtrips)
    b.strategy_return_pct = (total_net / total_cost * 100.0) if total_cost else 0.0

    b.alpha_pct = b.strategy_return_pct - b.kospi_return_pct
    b.available = True

    # SPEC-TRADING-044 M4: 누적 초과수익 surface (REQ-044-B1, B2)
    # alpha_pct = 전략 - KOSPI = 누적 초과수익 (동일 기간, money-weighted 근사)
    b.cumulative_excess_return_pct = b.alpha_pct
    # 2026-08-23: 이 알파가 무엇인지 화면이 밝힐 수 있도록 한계까지 문장에 담는다.
    # 전략 쪽은 "실제로 돈이 들어가 있던 구간의 투입원가 대비" 수익률이라 대기 현금이
    # 분모에서 빠지고, KOSPI 쪽은 전 구간 full-invested 다. 2026-07 처럼 시장이 크게
    # 빠질 때 대부분 현금이었다면 "덜 잃었다" 가 +알파로 잡힌다 — 종목 선택 능력이
    # 아니라 미투자 효과다. 정의 자체는 유지하고(운영자 결정), 오독만 막는다.
    b.comparison_basis = (
        "투입원가 기준 초과수익: 전략=Σ순손익/Σ투입원가(보유 구간만, 대기 현금 제외) "
        "− KOSPI=전 구간 매수후보유. 현금 비중이 높을수록 하락장에서 알파가 부풀려진다."
    )
    return b
