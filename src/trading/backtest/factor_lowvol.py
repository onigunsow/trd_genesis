"""SPEC-TRADING-058 M1 — 저변동성 팩터 신호 계산 (순수 함수).

REQ-058-M1-1 : 낮은 변동성 = 낮은 랭킹 (선택 우선순위 높음)
REQ-058-M1-2 : 결정성 — 동일 입력 → 동일 랭킹
REQ-058-M1-3 : point-in-time — as_of_date 이후 데이터 사용 금지
REQ-058-M1-4 : 이력 부족 종목 명시적 제외 (impute 금지)

설계 원칙:
- 순수 함수: 주입된 DataFrame 외 I/O 없음. DB/pykrx 직접 호출 금지.
- lookback=120 거래일 고정 기본값 (단일 config 소스).
- 이력 부족 종목은 excluded_tickers에 기록, rankings에서 제외.
- 반환 rankings: pd.Series (index=ticker, values=rank int 1~N, 오름차순 = 저변동).

# @MX:ANCHOR: [AUTO] 저변동성 팩터 신호 계산 — point-in-time 순수 함수
# @MX:REASON: REQ-058-M1-1/M1-2/M1-3; SPEC-058 M2 포트폴리오 구성이 호출(fan_in >= 2).
#             look-ahead 없음 불변식을 깨면 백테스트 전체가 미래 누출 상태가 된다.
# @MX:SPEC: SPEC-TRADING-058
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

LOG = logging.getLogger(__name__)

# 저변동성 lookback 기본값 — 단일 config 소스 (REQ-058-M1-1)
DEFAULT_LOOKBACK = 120  # 거래일


@dataclass
class LowVolResult:
    """저변동성 팩터 신호 계산 결과.

    rankings:
        pd.Series(index=ticker, values=rank int 1~N).
        rank=1이 가장 낮은 변동성 (선택 우선순위 최고).
    excluded_tickers:
        이력 부족으로 제외된 종목 목록 (REQ-058-M1-4).
    """

    rankings: pd.Series = field(default_factory=lambda: pd.Series(dtype=int))
    excluded_tickers: list[str] = field(default_factory=list)


def compute_low_vol_signal(
    prices_df: pd.DataFrame,
    as_of_date: date,
    *,
    lookback: int = DEFAULT_LOOKBACK,
) -> LowVolResult:
    """as_of_date 기준 point-in-time 저변동성 팩터 신호를 계산한다.

    Args:
        prices_df: DataFrame(index=date, columns=ticker, values=close).
                   인덱스는 python date 또는 pandas Timestamp 허용.
        as_of_date: 팩터 계산 기준일 (이 날짜 이후 데이터는 사용하지 않음).
        lookback: 변동성 계산에 사용할 거래일 수 (기본값 120).

    Returns:
        LowVolResult:
            rankings: pd.Series(index=ticker, values=rank 1~N, 낮을수록 저변동성).
            excluded_tickers: 이력 부족(<lookback 유효 bar) 종목.

    Point-in-time 불변식 (REQ-058-M1-3):
        as_of_date 이후 데이터는 절대 사용하지 않는다.
        이 함수를 우회하는 직접 슬라이싱은 금지된다.
    """
    # 1. as_of_date 이전 데이터만 슬라이스 (REQ-058-M1-3: point-in-time)
    idx = prices_df.index
    # pandas Timestamp / python date 모두 처리
    mask = pd.Series(idx).apply(
        lambda d: (d.date() if hasattr(d, "date") else d) <= as_of_date
    ).values
    prices_pit = prices_df.iloc[mask]

    # 2. 변동성 계산: 최근 lookback개의 유효 종가로 일간 수익률 표준편차
    vols: dict[str, float] = {}
    excluded: list[str] = []

    for ticker in prices_pit.columns:
        series = prices_pit[ticker].dropna()
        if len(series) < lookback:
            # 이력 부족 → 제외 (REQ-058-M1-4: impute 금지)
            excluded.append(ticker)
            LOG.debug(
                "factor_lowvol: %s 이력 부족 (%d < %d) — 제외",
                ticker, len(series), lookback,
            )
            continue

        # 최근 lookback개의 바만 사용 (rolling window)
        recent = series.iloc[-lookback:]
        daily_returns = recent.pct_change().dropna()

        if len(daily_returns) < 2:
            excluded.append(ticker)
            continue

        vols[ticker] = float(daily_returns.std())

    if not vols:
        return LowVolResult(
            rankings=pd.Series(dtype=int),
            excluded_tickers=sorted(excluded),
        )

    # 3. 변동성 오름차순으로 랭킹 부여 (낮은 변동성 = rank 1, REQ-058-M1-1)
    vol_series = pd.Series(vols)
    # rank(ascending=True): 가장 낮은 값이 rank=1
    rankings = vol_series.rank(ascending=True, method="first").astype(int)
    # 결정성 보장: 인덱스 정렬 (REQ-058-M1-2)
    rankings = rankings.sort_index()

    LOG.info(
        "factor_lowvol: as_of=%s lookback=%d → %d종목 랭킹, %d종목 제외",
        as_of_date.isoformat(), lookback, len(rankings), len(excluded),
    )

    return LowVolResult(
        rankings=rankings,
        excluded_tickers=sorted(excluded),
    )
