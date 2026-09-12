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
from trading.db.session import connection
from trading.edge.roundtrips import RoundTrip

LOG = logging.getLogger(__name__)

_SOURCE = "pykrx"
# KRX 코스피 종합지수 코드. korea_momentum.KOSPI_CODE 와 동일하나, 그 모듈을 import 하면
# ecos_adapter/httpx 등 무거운 체인을 끌어오므로 상수만 로컬 정의(pykrx get_index_ohlcv 용).
KOSPI_CODE = "1001"


# 지수(1001)는 갱신 잡이 없다 — refresh_ohlcv 는 get_data_universe()(매매 종목)만
# 돌고, 지수는 kospi200_backfill 일회성 백필로만 채워진다. 2026-09-12 실측: 캐시가
# 2026-08-20 에서 멈춰 있었고, 그 결과 한 달짜리 전략 수익률을 9일짜리 지수 수익률과
# 비교하고 있었다(구간 8/11~9/11, 실제 지수 8/11~8/20, KOSPI +7.99%). 소비하는 쪽에서
# 꼬리를 스스로 채운다 — 채운 뒤 upsert 하므로 다음 호출은 캐시에 걸린다.
_TAIL_GAP_TOLERANCE_DAYS = 3


def kospi_closes(start: date, end: date) -> list[tuple[date, float]]:
    """[start, end] KOSPI 종가 (date, close) 오름차순. 실패/없음 시 [].

    캐시가 ``end`` 근처까지 오지 못하면 모자란 꼬리만 pykrx 로 받아 적재한다.
    휴장 때문에 마지막 거래일이 ``end`` 보다 며칠 이를 수 있으므로
    ``_TAIL_GAP_TOLERANCE_DAYS`` 만큼은 정상으로 본다.
    """
    try:
        rows = cached_ohlcv(_SOURCE, KOSPI_CODE, start, end)
    except Exception:  # noqa: BLE001 — 캐시 조회 실패는 graceful
        rows = []
    if rows:
        cached = [(r["ts"], float(r["close"])) for r in rows if r.get("close")]
        if cached and (end - cached[-1][0]).days <= _TAIL_GAP_TOLERANCE_DAYS:
            return cached
        # 꼬리가 비었다 — 모자란 구간만 받아 채운 뒤 다시 캐시에서 읽는다.
        tail_start = cached[-1][0] if cached else start
        LOG.info(
            "benchmark: KOSPI 캐시가 %s 에서 멈춤 (요청 end=%s) — 꼬리 보충 시도",
            tail_start, end,
        )
        if _fetch_and_cache(tail_start, end):
            try:
                rows = cached_ohlcv(_SOURCE, KOSPI_CODE, start, end)
                refreshed = [
                    (r["ts"], float(r["close"])) for r in rows if r.get("close")
                ]
                if refreshed:
                    return refreshed
            except Exception:
                LOG.info("benchmark: 보충 후 캐시 재조회 실패 — 기존 캐시로 진행")
        return cached  # 보충 실패 — 있는 데까지. compute 가 그 사실을 밝힌다.

    # 캐시 미스 → pykrx 인덱스 폴백 후 캐시 적재.
    return _fetch_and_cache(start, end)


def _fetch_and_cache(start: date, end: date) -> list[tuple[date, float]]:
    """pykrx 지수 OHLCV 를 받아 캐시에 적재하고 종가 목록을 돌려준다. 실패 시 []."""
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



def invested_share(start: date, end: date) -> tuple[float, int] | None:
    """[start, end] 구간 일별 (주식평가액 / 총자산) 평균과 표본일수. 없으면 None.

    알파의 분모가 무엇이었는지를 숫자로 남기기 위한 값이다. 전략 수익률은 "돈이
    들어가 있던 구간의 투입원가" 기준이라 대기 현금이 빠지는데, KOSPI 쪽은 전 구간
    full-invested 다. 이 비율이 낮을수록 두 수익률은 다른 것을 재고 있다.
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT stock_eval, total_assets FROM daily_equity_snapshot "
                "WHERE trading_day >= %s AND trading_day <= %s",
                (start, end),
            )
            rows = cur.fetchall()
    except Exception:  # 조회 실패는 기준 미표기일 뿐 — 알파는 그대로 낸다.
        LOG.info("benchmark: 투자비중 조회 실패 — 비교 기준에 수치 생략")
        return None
    shares = [
        float(r["stock_eval"]) / float(r["total_assets"])
        for r in rows
        if r.get("total_assets") and float(r["total_assets"]) > 0
    ]
    if not shares:
        return None
    return sum(shares) / len(shares) * 100.0, len(shares)


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

        # 2026-09-05: 비교 기준을 산문이 아니라 수치로 남긴다. AgenticTrading 의
        # baseline_resolver 가 외부 run 마다 같은 창·같은 초기자본의 베이스라인 run 을
        # 행에 못 박는 것과 같은 취지 — 알파를 나중에 재해석할 수 있게 한다.
        # invested_share_pct = 구간 일평균 주식평가액/총자산 (0.0 = 미측정)
        self.invested_share_pct: float = 0.0
        self.invested_days: int = 0
        # 지수 데이터가 실제로 덮은 구간. 전략 구간(start~end)보다 짧으면
        # 두 수익률은 서로 다른 기간을 재고 있다.
        self.kospi_start: date | None = None
        self.kospi_end: date | None = None
        self.n_roundtrips: int = 0
        self.total_cost_basis: float = 0.0
        self.total_fees: float = 0.0


def compute(
    roundtrips: Sequence[RoundTrip],
    *,
    closes: list[tuple[date, float]] | None = None,
    invested: tuple[float, int] | None = None,
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
    b.kospi_start, b.kospi_end = closes[0][0], closes[-1][0]
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
    #
    # 2026-09-05: 그 문장에 실측치를 붙인다. "현금 비중이 높을수록 부풀려진다" 는
    # 경고는 얼마나 부풀려졌는지 답하지 않아서, 8/23 의 +12.69p 를 여전히 손으로
    # 환산해야 했다. 투자비중을 같이 실으면 알파 옆에 분모가 남는다.
    b.n_roundtrips = len(roundtrips)
    b.total_cost_basis = total_cost
    b.total_fees = sum(r.fees for r in roundtrips)
    if invested is None:
        invested = invested_share(start, end)
    if invested is not None:
        b.invested_share_pct, b.invested_days = invested

    basis = (
        f"투입원가 기준 초과수익 ({start}~{end}, 라운드트립 {b.n_roundtrips}건, "
        f"투입원가 {total_cost:,.0f}원): "
        "전략=Σ순손익/Σ투입원가(보유 구간만, 대기 현금 제외) − KOSPI=전 구간 매수후보유."
    )
    if b.invested_days:
        basis += (
            f" 이 구간 일평균 투자비중은 {b.invested_share_pct:.1f}%"
            f"(스냅샷 {b.invested_days}일) — 나머지 {100.0 - b.invested_share_pct:.1f}%는"
            " 현금·미결제라 KOSPI 쪽 분모에만 들어간다. 비중이 낮을수록 하락장에서"
            " 알파가 부풀려진다(종목 선택이 아니라 미투자 효과)."
        )
    else:
        basis += (
            " 투자비중 미측정(일별 자산 스냅샷 없음) —"
            " 현금 비중에 의한 왜곡 크기를 알 수 없다."
        )

    # 2026-09-05: 알파는 net_pnl(gross 에서 orders.fee 를 뺀 값) 기준인데 페이퍼 체결은 fee 를
    # 기록하지 않아 실측 수수료가 0 이다. analytics 는 별도로 슬리피지·거래세를
    # 모델링한 *_adj 지표를 내지만 알파는 그 경로를 타지 않는다. 여기 알파는
    # 비용 반영 전 값이라고 화면에 밝힌다(정의는 유지, 오독만 막는다).
    if b.n_roundtrips and b.total_fees == 0:
        basis += (
            " 실측 수수료 0원(페이퍼 체결은 fee 미기록) — 이 알파는 비용 반영 전이다."
            " 비용보정 수익률은 analytics 의 *_adj 지표를 볼 것."
        )

    # 2026-09-12: 지수 구간이 전략 구간을 못 덮으면 두 수익률은 다른 기간을 잰다.
    # 지수(1001)에는 갱신 잡이 없어 캐시가 2026-08-20 에서 멈춰 있었고, 그 결과
    # 한 달 전략 수익률 vs 9일 지수 수익률을 알파라고 불렀다. 숫자는 그대로 내되
    # 어긋난 사실을 화면이 말하게 한다 — 조용히 맞는 것처럼 보이는 게 최악이다.
    if b.kospi_end is not None and (end - b.kospi_end).days > _TAIL_GAP_TOLERANCE_DAYS:
        basis += (
            f" 【경고】 지수 데이터가 {b.kospi_end} 까지뿐이라 KOSPI 쪽은"
            f" {b.kospi_start}~{b.kospi_end} 구간만 잰 값이다 —"
            f" 전략 구간({start}~{end})과 다르므로 이 알파는 같은 기간 비교가 아니다."
        )

    b.comparison_basis = basis
    return b
