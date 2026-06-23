"""SPEC-TRADING-057 M1+M2 — 실 진입 피처 OOS 알파 측정 런 하니스.

REQ-057-M2-1 : 닫힌 측정 목록: RSI / PER / foreign_5d (score class A)
REQ-057-M2-2 : 모든 피처 추출은 as_of_date 기준 point-in-time — 미래 누출 금지
REQ-057-M2-3 : time-weighted equity-curve 알파 (engine.run 경유, measure_feature_alpha 재사용)
REQ-057-M2-3a: Bonferroni 다중검정 보정 (bonferroni_n=3 고정)
REQ-057-M2-3b: 표본 floor=30 (실제 월별 30회 이상)
REQ-057-M2-4 : LLM 레이어 백테스트 금지

설계 원칙:
- 모든 provider는 의존성 주입 인자 — 기본 provider는 lazy import (pykrx/DB)
- pykrx는 모듈 최상위에서 import하지 않는다 (KRX 세션 사이드이펙트 방지)
- measure_feature_alpha / reconstruct_universe / load_historical_ohlcv 를 호출만 함 (수정 금지)
- CLI 단독 실행: `python -m trading.backtest.entry_alpha_run [--start YYYY] [--end YYYY]`

point-in-time 정확성 논증:
- RSI: ohlcv_provider(ticker, start, end=as_of_date) 호출 → as_of_date 이후 종가 배제
- PER: fundamental_provider(ticker, as_of_date) 호출 → as_of_date 당일 PER (시장가xEPS)
- foreign: flows_provider(ticker, start=as_of-5d, end=as_of_date) → as_of_date 이후 수급 배제

# @MX:ANCHOR: [AUTO] 진입 피처 알파 런 하니스 — 외부 호출 진입점
# @MX:REASON: run_entry_alpha는 measure_feature_alpha를 3회 호출하는 오케스트레이터.
#             CLI + 단위 테스트 두 경로에서 호출됨 (fan_in >= 2).
# @MX:SPEC: SPEC-TRADING-057
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import pandas as pd

from trading.backtest.feature_alpha_measurer import (
    FeatureAlphaResult,
    measure_feature_alpha,
)

LOG = logging.getLogger(__name__)

# KOSPI200 인덱스 코드 (pykrx get_index_ohlcv_by_date)
_KOSPI200_CODE = "1028"

# RSI 기본 기간 (Wilder 14일)
_RSI_PERIOD = 14

# 외국인 순매수 집계 창 (5거래일)
_FOREIGN_WINDOW_DAYS = 5

# RSI 계산용 OHLCV 룩백 배율 (period * _RSI_LOOKBACK_MULT 일치 가져옴)
_RSI_LOOKBACK_MULT = 3


# ── RSI 계산 순수 함수 ─────────────────────────────────────────────────────

def _compute_rsi_from_closes(closes: list[float], *, period: int = 14) -> float | None:
    """Wilder 평활 RSI를 계산한다.

    Args:
        closes: 종가 시계열 (시간 오름차순).
        period: RSI 기간 (기본 14).

    Returns:
        RSI 값 [0, 100], 데이터 부족 시 None.

    point-in-time 보장: 이 함수 자체는 순수 함수이며 입력 closes가
    as_of_date 이전 데이터로만 채워지는 것은 호출자(build_rsi_extractor) 책임이다.
    """
    # period+1개 이상의 종가 필요 (변화량 period개 필요)
    if len(closes) < period + 1:
        return None

    # 전일 대비 변화량
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    # 초기 평균 gains/losses (첫 period 개)
    gains = [max(d, 0.0) for d in deltas[:period]]
    losses = [abs(min(d, 0.0)) for d in deltas[:period]]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    # Wilder 평활: 나머지 델타
    for delta in deltas[period:]:
        gain = max(delta, 0.0)
        loss = abs(min(delta, 0.0))
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0.0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


# ── 피처 추출기 팩토리 ─────────────────────────────────────────────────────

def build_rsi_extractor(
    *,
    ohlcv_provider: Callable[[str, date, date], list[dict[str, Any]]] | None = None,
    period: int = _RSI_PERIOD,
) -> Callable[[date, list[str]], dict[str, float | None]]:
    """RSI 피처 추출기를 빌드한다.

    Args:
        ohlcv_provider: (ticker, start, end) -> list[Bar].
            None이면 기본 pykrx_adapter + cache 경로 (컨테이너 전용).
        period: RSI 기간 (기본 14).

    Returns:
        (as_of_date, tickers) -> {ticker: rsi_value | None}

    point-in-time 정확성:
        extractor는 ohlcv_provider(ticker, start, end=as_of_date)를 호출한다.
        end=as_of_date이므로 as_of_date 이후 종가는 물리적으로 수신되지 않는다.
    """
    # @MX:NOTE: [AUTO] ohlcv_provider가 None이면 기본 pykrx_adapter 경로 (lazy import)
    def _default_ohlcv_provider(ticker: str, start: date, end: date) -> list[dict[str, Any]]:
        """기본 OHLCV provider — pykrx_adapter + DB 캐시 (컨테이너 런타임 전용)."""
        from trading.data import pykrx_adapter
        from trading.data.cache import cached_ohlcv

        pykrx_adapter.fetch_ohlcv(ticker, start, end)
        rows = cached_ohlcv(pykrx_adapter.SOURCE, ticker, start, end)
        return [
            {
                "ts": r["ts"],
                "close": float(r.get("close", 0) or 0),
            }
            for r in rows
        ]

    provider = ohlcv_provider if ohlcv_provider is not None else _default_ohlcv_provider

    def extractor(as_of_date: date, tickers: list[str]) -> dict[str, float | None]:
        """RSI 피처를 point-in-time으로 추출한다.

        end=as_of_date 엄수 → look-ahead 구조적 방지.
        """
        # 룩백 창: period * 3일 (충분한 Wilder 수렴 보장)
        lookback_start = as_of_date - timedelta(days=period * _RSI_LOOKBACK_MULT)
        result: dict[str, float | None] = {}

        for ticker in tickers:
            try:
                bars = provider(ticker, lookback_start, as_of_date)  # end = as_of_date
                # ts 기준 오름차순 정렬 후 종가 추출
                bars_sorted = sorted(bars, key=lambda b: b["ts"])
                closes = [float(b["close"]) for b in bars_sorted if b.get("close")]
                result[ticker] = _compute_rsi_from_closes(closes, period=period)
            except Exception as exc:
                LOG.warning("RSI 추출 실패: ticker=%s, %s", ticker, exc)
                result[ticker] = None

        return result

    return extractor


def build_per_extractor(
    *,
    fundamental_provider: Callable[[str, date], float | None] | None = None,
) -> Callable[[date, list[str]], dict[str, float | None]]:
    """PER 피처 추출기를 빌드한다.

    sign 규칙: 낮은 PER = 더 좋음 → score = -PER (높을수록 좋아짐).
    PER ≤ 0 또는 None → None (적자/데이터 없음).

    Args:
        fundamental_provider: (ticker, as_of_date) -> PER 값 | None.
            None이면 기본 pykrx_adapter.fetch_fundamentals 경로 (컨테이너 전용).

    Returns:
        (as_of_date, tickers) -> {ticker: score | None}
        score = -PER (높을수록 저평가)

    point-in-time 정확성:
        fundamental_provider는 as_of_date만을 기준으로 호출한다.
        pykrx get_market_fundamental_by_date(start, end=as_of_date)는
        as_of_date 당일의 시장가 PER를 반환하므로 미래 EPS/주가 사용 없음.
    """

    def _default_fundamental_provider(ticker: str, as_of_date: date) -> float | None:
        """기본 펀더멘털 provider — pykrx fundamentals 캐시 (컨테이너 전용)."""
        from pykrx import stock  # lazy import

        date_str = as_of_date.strftime("%Y%m%d")
        try:
            df = stock.get_market_fundamental_by_date(date_str, date_str, ticker)
            if df is None or df.empty:
                return None
            row = df.iloc[-1]
            per = row.get("PER")
            if per is None:
                return None
            return float(per)
        except Exception:
            return None

    provider = (
        fundamental_provider if fundamental_provider is not None
        else _default_fundamental_provider
    )

    def extractor(as_of_date: date, tickers: list[str]) -> dict[str, float | None]:
        """PER 피처를 point-in-time으로 추출한다.

        point-in-time 보장: provider는 as_of_date로만 호출됨.
        sign 변환: score = -PER (낮은 PER → 높은 score).
        """
        result: dict[str, float | None] = {}
        for ticker in tickers:
            try:
                per = provider(ticker, as_of_date)  # as_of_date 엄수
                if per is None or per <= 0.0:
                    result[ticker] = None
                else:
                    result[ticker] = -per  # 낮은 PER = 더 좋음 → 음수화
            except Exception as exc:
                LOG.warning("PER 추출 실패: ticker=%s, %s", ticker, exc)
                result[ticker] = None
        return result

    return extractor


def build_foreign_extractor(
    *,
    flows_provider: Callable[[str, date, date], list[dict[str, Any]]] | None = None,
    window_days: int = _FOREIGN_WINDOW_DAYS,
) -> Callable[[date, list[str]], dict[str, float | None]]:
    """외국인 순매수 피처 추출기를 빌드한다.

    Args:
        flows_provider: (ticker, start, end) -> list[{ts, foreign_net}].
            None이면 기본 pykrx_adapter.fetch_flows 경로 (컨테이너 전용).
        window_days: 누적 창 (캘린더일 기준, 기본 5).

    Returns:
        (as_of_date, tickers) -> {ticker: 5일 외국인 순매수 합계 | None}

    point-in-time 정확성:
        flows_provider(ticker, start=as_of-window_days, end=as_of_date) 호출.
        end=as_of_date → as_of_date 이후 수급 데이터 배제.
    """

    def _default_flows_provider(ticker: str, start: date, end: date) -> list[dict[str, Any]]:
        """기본 수급 provider — pykrx_adapter.fetch_flows + cache (컨테이너 전용)."""
        from pykrx import stock  # lazy import

        s = start.strftime("%Y%m%d")
        e = end.strftime("%Y%m%d")
        try:
            df = stock.get_market_trading_value_by_date(s, e, ticker)
            if df is None or df.empty:
                return []
            rows = []
            for ts, row in df.iterrows():
                d = ts.date() if hasattr(ts, "date") else ts
                rows.append({
                    "ts": d,
                    "foreign_net": int(row.get("외국인합계", row.get("외국인", 0)) or 0),
                })
            return rows
        except Exception:
            return []

    provider = (
        flows_provider if flows_provider is not None
        else _default_flows_provider
    )

    def extractor(as_of_date: date, tickers: list[str]) -> dict[str, float | None]:
        """외국인 순매수 피처를 point-in-time으로 추출한다.

        end=as_of_date 엄수 → look-ahead 구조적 방지.
        """
        lookback_start = as_of_date - timedelta(days=window_days * 2)  # 주말 여유
        result: dict[str, float | None] = {}

        for ticker in tickers:
            try:
                rows = provider(ticker, lookback_start, as_of_date)  # end = as_of_date
                if not rows:
                    result[ticker] = None
                    continue
                # as_of_date 이하 데이터만 사용 (구조적 이중 가드)
                valid = [r for r in rows if r["ts"] <= as_of_date]
                if not valid:
                    result[ticker] = None
                    continue
                # 최근 window_days 개 집계
                valid_sorted = sorted(valid, key=lambda r: r["ts"], reverse=True)
                recent = valid_sorted[:window_days]
                total = sum(r.get("foreign_net", 0) for r in recent)
                result[ticker] = float(total)
            except Exception as exc:
                LOG.warning("외국인 순매수 추출 실패: ticker=%s, %s", ticker, exc)
                result[ticker] = None

        return result

    return extractor


def build_kospi200_returns_provider(
    *,
    index_ohlcv_provider: Callable[[date, date], list[dict[str, Any]]] | None = None,
) -> Callable[[date, date], pd.Series]:
    """KOSPI200 벤치마크 일별 수익률 provider를 빌드한다.

    Args:
        index_ohlcv_provider: (start, end) -> list[{ts, close}].
            None이면 기본 pykrx get_index_ohlcv_by_date('1028') (컨테이너 전용).

    Returns:
        (start, end) -> pd.Series(index=date, values=pct_change) — time-weighted.

    point-in-time 정확성:
        index_ohlcv_provider(start, end)의 end는 호출 시점의 end이므로
        engine.run과 동일한 기간 범위를 사용한다.
    """

    def _default_index_ohlcv_provider(start: date, end: date) -> list[dict[str, Any]]:
        """기본 KOSPI200 지수 provider — pykrx (컨테이너 전용)."""
        from pykrx import stock  # lazy import

        s = start.strftime("%Y%m%d")
        e = end.strftime("%Y%m%d")
        try:
            df = stock.get_index_ohlcv_by_date(s, e, _KOSPI200_CODE)
            if df is None or df.empty:
                return []
            rows = []
            for ts, row in df.iterrows():
                d = ts.date() if hasattr(ts, "date") else ts
                close = row.get("종가") or row.get("Close")
                if close:
                    rows.append({"ts": d, "close": float(close)})
            return rows
        except Exception:
            return []

    provider = (
        index_ohlcv_provider if index_ohlcv_provider is not None
        else _default_index_ohlcv_provider
    )

    def kospi_returns(start: date, end: date) -> pd.Series:
        """KOSPI200 일별 수익률 시리즈 (pct_change, time-weighted)."""
        rows = provider(start, end)
        if not rows:
            return pd.Series(dtype=float)

        rows_sorted = sorted(rows, key=lambda r: r["ts"])
        dates = [r["ts"] for r in rows_sorted]
        closes = pd.Series([r["close"] for r in rows_sorted], index=dates)
        returns = closes.pct_change().dropna()
        return returns

    return kospi_returns


# ── 월별 리밸런싱 일정 생성 ───────────────────────────────────────────────

def build_rebalance_schedule(start: date, end: date) -> list[date]:
    """주어진 기간 안에서 매월 첫 평일(월~금)을 리밸런싱 날짜로 반환한다.

    Args:
        start: 시작일 (포함).
        end: 종료일 (포함).

    Returns:
        월별 첫 평일 날짜 목록 (오름차순, start~end 범위 내).

    Note:
        한국 공휴일은 별도 캘린더 없이 단순 주말 보정만 적용한다.
        백테스트 목적이므로 ±1일 오차는 허용된다.
    """
    dates: list[date] = []
    # 시작 연/월부터 종료 연/월까지 순회
    year, month = start.year, start.month

    while True:
        # 해당 월의 1일
        first_day = date(year, month, 1)
        if first_day > end:
            break

        # 첫 평일 탐색 (최대 7일)
        candidate = first_day
        for _ in range(7):
            if candidate.weekday() < 5:  # 월=0, 금=4
                break
            candidate += timedelta(days=1)

        # start~end 범위 확인
        if start <= candidate <= end:
            dates.append(candidate)

        # 다음 월로 이동
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1

    return dates


def _snap_to_trading_days(
    candidates: list[date],
    universe_provider: Callable[[date], Any],
    max_back: int = 7,
) -> list[date]:
    """각 후보 날짜를 직전 거래일(유니버스 achievable=True)로 snap한다.

    달력 기반 스케줄은 휴장일(신정·삼일절·근로자의날 등)을 만들 수 있고, 그 날짜의
    멤버십은 빈 목록(achievable=False)이라 measurer가 생존편향으로 오인한다. 이를
    방지하기 위해 휴장일은 ≤max_back일 직전 거래일로 보정한다. max_back 내 거래일을
    못 찾으면(진짜 불가) 해당 날짜는 제외 — measurer의 fail-closed가 실제 생존편향만
    다루도록 한다.
    """
    snapped: list[date] = []
    seen: set[date] = set()
    for c in candidates:
        d = c
        for _ in range(max_back + 1):
            u = universe_provider(d)
            if getattr(u, "achievable", False) and getattr(u, "tickers", None):
                if d not in seen:
                    seen.add(d)
                    snapped.append(d)
                break
            d = d - timedelta(days=1)
    return sorted(snapped)


# ── 메인 오케스트레이터 ───────────────────────────────────────────────────

# @MX:ANCHOR: [AUTO] 진입 피처 3종 OOS 알파 측정 오케스트레이터
# @MX:REASON: measure_feature_alpha를 3회 호출. CLI + 단위 테스트 두 경로(fan_in >= 2).
# @MX:SPEC: SPEC-TRADING-057
def run_entry_alpha(
    *,
    rebalance_dates: list[date],
    universe_provider: Callable[[date], Any],
    ohlcv_provider: Callable[[str, date, date], list[dict[str, Any]]] | None = None,
    fundamental_provider: Callable[[str, date], float | None] | None = None,
    flows_provider: Callable[[str, date, date], list[dict[str, Any]]] | None = None,
    index_ohlcv_provider: Callable[[date, date], list[dict[str, Any]]] | None = None,
    sample_floor: int = 30,
    bonferroni_n: int = 3,
    rsi_period: int = _RSI_PERIOD,
    foreign_window_days: int = _FOREIGN_WINDOW_DAYS,
) -> list[FeatureAlphaResult]:
    """RSI / PER / foreign_5d 3개 피처의 OOS 알파를 측정한다.

    Args:
        rebalance_dates: 월별 리밸런싱 날짜 목록.
        universe_provider: (date) -> UniverseResult.
        ohlcv_provider: (ticker, start, end) -> list[Bar] | None (기본 pykrx).
        fundamental_provider: (ticker, as_of_date) -> PER | None (기본 pykrx).
        flows_provider: (ticker, start, end) -> list[flows] | None (기본 pykrx).
        index_ohlcv_provider: (start, end) -> list[{ts, close}] | None (기본 pykrx).
        sample_floor: 최소 리밸런싱 횟수 (기본 30).
        bonferroni_n: 다중검정 수 (기본 3, REQ-057-M2-3a).
        rsi_period: RSI 기간 (기본 14).
        foreign_window_days: 외국인 순매수 집계 창 (기본 5).

    Returns:
        FeatureAlphaResult 3개 목록 [rsi, per, foreign].
    """
    # prices_provider는 load_historical_ohlcv를 통해 ohlcv_provider를 감싼다
    rsi_ext = build_rsi_extractor(ohlcv_provider=ohlcv_provider, period=rsi_period)
    per_ext = build_per_extractor(fundamental_provider=fundamental_provider)
    foreign_ext = build_foreign_extractor(
        flows_provider=flows_provider, window_days=foreign_window_days
    )
    kospi_returns = build_kospi200_returns_provider(
        index_ohlcv_provider=index_ohlcv_provider
    )

    # prices_provider: ohlcv_provider를 wrap해 DataFrame 반환
    # historical_loader를 직접 재사용
    from trading.backtest.historical_loader import load_historical_ohlcv

    def prices_provider(
        tickers: list[str], start: date, end: date
    ) -> pd.DataFrame:
        """종목별 종가 DataFrame 반환 (point-in-time: ts <= end)."""
        load_result = load_historical_ohlcv(
            tickers, start, end, cutoff=end,
            ohlcv_provider=ohlcv_provider,
        )
        return load_result.to_prices_dataframe()

    results: list[FeatureAlphaResult] = []

    for feature_name, extractor in [
        ("rsi", rsi_ext),
        ("per", per_ext),
        ("foreign", foreign_ext),
    ]:
        LOG.info(
            "entry_alpha_run: %s 피처 OOS 알파 측정 시작 (%d 리밸런싱)",
            feature_name, len(rebalance_dates),
        )
        result = measure_feature_alpha(
            feature_name=feature_name,
            rebalance_dates=rebalance_dates,
            universe_provider=universe_provider,
            feature_extractor=extractor,
            prices_provider=prices_provider,
            kospi_returns_provider=kospi_returns,
            sample_floor=sample_floor,
            bonferroni_n=bonferroni_n,
        )
        results.append(result)
        LOG.info(
            "entry_alpha_run: %s → label=%s, net_alpha=%s, rebalance_count=%d",
            feature_name, result.label,
            f"{result.net_alpha:+.4f}" if result.net_alpha is not None else "None",
            result.rebalance_count,
        )

    return results


# ── CLI 진입점 ────────────────────────────────────────────────────────────

def _cli_main(argv: list[str] | None = None) -> int:
    """CLI 진입점 — 실 KRX 데이터로 다년간 OOS 알파를 측정한다.

    사용법:
        docker exec trading-app /opt/venv/bin/python -m trading.backtest.entry_alpha_run \\
            [--start 2016-01-01] [--end 2025-12-31]

    출력:
        피처별 label, net_alpha, rebalance_count, 한줄 판정 문장.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="SPEC-057 M1+M2 진입 피처 OOS 알파 측정 (rsi/per/foreign)",
    )
    parser.add_argument("--start", default="2016-01-01", help="백테스트 시작일 YYYY-MM-DD")
    parser.add_argument("--end", default="2025-12-31", help="백테스트 종료일 YYYY-MM-DD")
    parser.add_argument("--sample-floor", type=int, default=30, help="최소 리밸런싱 횟수")
    args = parser.parse_args(argv)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    print("[SPEC-057] 진입 피처 OOS 알파 측정 시작")
    print(f"  기간: {start} ~ {end}")
    print(f"  sample_floor={args.sample_floor}, bonferroni_n=3")
    print()

    # 유니버스 provider: reconstruct_universe 기본 (pykrx)
    from trading.backtest.universe_reconstructor import reconstruct_universe

    def universe_provider(d: date):
        return reconstruct_universe(d)

    # 리밸런싱 일정 (달력 기반) → 휴장일을 직전 거래일로 snap
    raw_dates = build_rebalance_schedule(start, end)
    rebalance_dates = _snap_to_trading_days(raw_dates, universe_provider)
    if not rebalance_dates:
        print("  리밸런싱 가능한 거래일 없음 — 종료")
        return 1
    first_d, last_d = rebalance_dates[0], rebalance_dates[-1]
    print(
        f"  리밸런싱 횟수: {len(rebalance_dates)}개 ({first_d} ~ {last_d}; "
        f"휴장일 snap 적용, 원후보 {len(raw_dates)}개)"
    )
    print()

    # 기본 provider (pykrx / DB) — 인자 미전달 시 기본 경로 사용
    results = run_entry_alpha(
        rebalance_dates=rebalance_dates,
        universe_provider=universe_provider,
        sample_floor=args.sample_floor,
    )

    # 결과 출력
    print("=" * 60)
    print("SPEC-057 M1+M2 OOS 알파 측정 결과")
    print("=" * 60)
    for r in results:
        alpha_str = f"{r.net_alpha:+.4f}" if r.net_alpha is not None else "None (보고금지)"
        print(f"\n[{r.feature_name.upper()}]")
        print(f"  label         : {r.label}")
        print(f"  net_alpha     : {alpha_str}")
        if r.p_value is not None:
            print(f"  p_value       : {r.p_value:.4f}")
        else:
            print("  p_value       : None")
        print(f"  bonferroni_thr: {r.bonferroni_threshold:.4f}")
        print(f"  rebalance_cnt : {r.rebalance_count}")
        print(f"  detail        : {r.detail}")

        # 한 줄 판정
        if r.label == "PASS":
            verdict = (
                f"✓ {r.feature_name}: Bonferroni 유의 + 양의 알파"
                " → 기계적 진입 피처로서 OOS 알파 존재"
            )
        elif r.label == "NOT_PASS":
            verdict = f"✗ {r.feature_name}: 알파 불유의 또는 음수 → OOS 알파 미확인"
        elif r.label == "INCONCLUSIVE":
            verdict = (
                f"? {r.feature_name}: 표본 부족"
                f" (n={r.rebalance_count} < floor={r.sample_floor}) → 결론 보류"
            )
        else:  # SURVIVORSHIP_BOUND
            verdict = (
                f"⚠ {r.feature_name}: 생존편향 상한"
                " → as-of-date 유니버스 재구성 불가, 알파 보고 금지"
            )
        print(f"  판정          : {verdict}")

    print()
    print("=" * 60)
    print("주의: PASS ≠ 실거래 진입 신호. 비용/슬리피지 포함 결과이며 LLM 레이어 미포함.")
    print("데이터 백필 필요 시: docker exec trading-app trading kospi200-backfill")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli_main())
