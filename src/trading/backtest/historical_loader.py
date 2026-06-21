"""SPEC-TRADING-057 M1 — point-in-time historical OHLCV 로더.

REQ-057-M1-1  : pykrx_adapter를 감싸 하니스에 bars를 공급 (어댑터 미변경)
REQ-057-M1-2  : 결정성 — 동일 입력 → 바이트 동일 바 시퀀스
REQ-057-M1-3  : ts <= cutoff 슬라이스 불변식 (_slice_bars 재사용 + 명시적 필터)
REQ-057-M1-4  : 커버리지 갭 명시적 보고 (조용히 partial data 반환 금지)
REQ-057-M1-5  : 미래 바 / 생존편향 유니버스 / 소급 펀더멘털 주입 금지

설계 원칙:
- ohlcv_provider 콜백으로 의존성 주입 — 단위 테스트는 픽스처를 주입한다.
- 기본 provider는 pykrx_adapter.fetch_ohlcv + 캐시에서 읽기 (컨테이너 전용).
- 종목별 provider 오류는 CoverageGap으로 기록하고 나머지 로드를 계속한다.
- 반환 bars는 ts 오름차순 정렬 — engine.run DataFrame 변환 전제 충족.
- ADR-057-3: 어댑터 미변경, 래핑만. ADR-057-4: SPEC-058 재사용 가능 표면.

# @MX:ANCHOR: [AUTO] point-in-time OHLCV 로더 — look-ahead 불변식
# @MX:REASON: REQ-057-M1-3; walk_forward._slice_bars 불변식과 동일 규율.
#             ts > cutoff 바가 누출되면 백테스트 전체가 미래 누출 상태가 된다.
# @MX:SPEC: SPEC-TRADING-057
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

LOG = logging.getLogger(__name__)

Bar = dict[str, Any]


@dataclass
class CoverageGap:
    """데이터 커버리지 갭 정보 (REQ-057-M1-4).

    로더가 요청했지만 완전히 반환받지 못한 종목/기간을 기록한다.
    """

    ticker: str
    requested_start: date
    requested_end: date
    actual_bar_count: int
    reason: str = ""


@dataclass
class LoadResult:
    """historical OHLCV 로드 결과.

    bars: {ticker: [Bar]} — ts <= cutoff 인 바만 포함, ts 오름차순 정렬.
    coverage_gaps: 갭이 발생한 종목 목록.
    """

    bars: dict[str, list[Bar]] = field(default_factory=dict)
    coverage_gaps: list[CoverageGap] = field(default_factory=list)

    def to_prices_dataframe(self) -> pd.DataFrame:
        """bars → engine.run 호환 prices DataFrame.

        Returns:
            DataFrame(index=date, columns=ticker, values=close).
        """
        prices: dict[str, dict[date, float]] = {}
        for ticker, bar_list in self.bars.items():
            if bar_list:
                prices[ticker] = {b["ts"]: float(b["close"]) for b in bar_list}

        if not prices:
            return pd.DataFrame()

        df = pd.DataFrame(prices)
        # 인덱스를 python date 객체로 정규화 (engine.run 호환)
        df.index = [d if isinstance(d, date) else d.date() for d in df.index]  # type: ignore[assignment]
        return df.sort_index()


def _default_ohlcv_provider(ticker: str, start: date, end: date) -> list[Bar]:
    """기본 OHLCV provider — pykrx_adapter + DB 캐시 경유 (컨테이너 런타임 전용).

    pykrx_adapter.fetch_ohlcv는 DB에 적재 후 행 수(int)를 반환한다.
    로더는 적재된 행을 cache에서 직접 읽어야 한다.

    주의: 이 provider는 KRX 네트워크 + DB 연결이 필요하다.
    단위 테스트는 픽스처 provider를 주입해 이 경로를 우회한다.
    """
    # lazy import: 테스트 컬렉션 시점에 pykrx_adapter/cache가 로드되지 않도록 함
    from trading.data import pykrx_adapter
    from trading.data.cache import cached_ohlcv

    # 캐시 적재 (이미 있으면 멱등 upsert)
    pykrx_adapter.fetch_ohlcv(ticker, start, end)

    # 캐시에서 읽기 (cached_ohlcv: source, symbol, start, end → list[dict])
    rows = cached_ohlcv(pykrx_adapter.SOURCE, ticker, start, end)
    return [
        {
            "ts": r["ts"],
            "open": float(r.get("open", 0) or 0),
            "high": float(r.get("high", 0) or 0),
            "low": float(r.get("low", 0) or 0),
            "close": float(r.get("close", 0) or 0),
            "volume": int(r.get("volume", 0) or 0),
        }
        for r in rows
    ]


def _slice_by_cutoff(bars: list[Bar], cutoff: date) -> list[Bar]:
    """ts <= cutoff 인 바만 반환 (REQ-057-M1-3 point-in-time 불변식).

    walk_forward._slice_bars 와 동일한 규율을 공유한다.
    이 함수를 우회하는 직접 인덱싱은 금지된다.
    """
    return [b for b in bars if b["ts"] <= cutoff]


def load_historical_ohlcv(
    tickers: list[str],
    start: date,
    end: date,
    cutoff: date,
    *,
    ohlcv_provider: Callable[[str, date, date], list[Bar]] | None = None,
) -> LoadResult:
    """point-in-time historical OHLCV를 로드하고 ts <= cutoff 슬라이스를 반환한다.

    Args:
        tickers: 로드할 종목 코드 목록.
        start: 요청 시작일.
        end: 요청 종료일 (provider 호출 범위).
        cutoff: look-ahead 차단 기준일 (ts > cutoff 바는 제거됨, REQ-057-M1-3).
        ohlcv_provider: (ticker, start, end) -> list[Bar] 콜백.
            None 이면 기본 pykrx_adapter + cache 구현 사용 (컨테이너 전용).
            단위 테스트는 픽스처 provider를 주입한다.

    Returns:
        LoadResult:
            bars: 종목별 ts <= cutoff 인 bars (ts 오름차순).
            coverage_gaps: 갭 발생 종목 목록.
    """
    # # @MX:NOTE: [AUTO] provider 미주입 시 기본 pykrx + cache 경로 — 네트워크/DB 필요
    provider = ohlcv_provider if ohlcv_provider is not None else _default_ohlcv_provider

    result = LoadResult()

    for ticker in tickers:
        try:
            raw_bars = provider(ticker, start, end)
        except Exception as exc:
            # 종목별 오류는 CoverageGap으로 기록, 나머지 로드 계속 (REQ-057-M1-4)
            reason = f"provider 오류: {exc!r}"
            LOG.warning(
                "historical_loader: ticker=%s 로드 실패 → 갭 기록: %s",
                ticker, reason,
            )
            result.coverage_gaps.append(CoverageGap(
                ticker=ticker,
                requested_start=start,
                requested_end=end,
                actual_bar_count=0,
                reason=reason,
            ))
            continue

        # point-in-time 슬라이스: ts <= cutoff (REQ-057-M1-3)
        sliced = _slice_by_cutoff(raw_bars, cutoff)

        # 결정성 보장: ts 오름차순 정렬 (REQ-057-M1-2)
        sliced.sort(key=lambda b: b["ts"])

        result.bars[ticker] = sliced

        # 커버리지 갭 감지 (REQ-057-M1-4)
        _check_coverage_gap(ticker, start, end, cutoff, raw_bars, sliced, result)

    return result


def _check_coverage_gap(
    ticker: str,
    start: date,
    end: date,
    cutoff: date,
    raw_bars: list[Bar],
    sliced: list[Bar],
    result: LoadResult,
) -> None:
    """커버리지 갭을 감지하고 result.coverage_gaps에 추가한다.

    갭 판단 기준:
    1. provider가 빈 목록을 반환한 경우 (데이터 없음).
    2. sliced bars의 최소/최대 날짜가 [start, min(end,cutoff)] 범위를 완전히 커버하지 못하는 경우.
    """
    effective_end = min(end, cutoff)

    # 데이터 없음
    if not raw_bars:
        result.coverage_gaps.append(CoverageGap(
            ticker=ticker,
            requested_start=start,
            requested_end=effective_end,
            actual_bar_count=0,
            reason="데이터 없음",
        ))
        return

    # 부분 데이터: 마지막 bar가 요청 종료일에 훨씬 못 미치는 경우
    # 영업일 기준이 아닌 단순 날짜 비교 (보수적 — false positive 가능하나 REQ-057-M1-4 준수)
    if sliced:
        last_ts = max(b["ts"] for b in sliced)
        first_ts = min(b["ts"] for b in sliced)
        if first_ts > start or last_ts < effective_end:
            result.coverage_gaps.append(CoverageGap(
                ticker=ticker,
                requested_start=start,
                requested_end=effective_end,
                actual_bar_count=len(sliced),
                reason=(
                    f"부분 커버리지: 실제 [{first_ts}~{last_ts}], "
                    f"요청 [{start}~{effective_end}]"
                ),
            ))
