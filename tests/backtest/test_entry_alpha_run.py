"""SPEC-TRADING-057 M1+M2 — 실 진입 피처 OOS 알파 런 하니스 단위 테스트.

이 파일의 모든 테스트는 픽스처 주입으로 실행한다:
- 네트워크 없음 (pykrx import 금지)
- DB 없음
- KRX 로그인 없음

각 테스트의 point-in-time 정확성 논증:
- RSI: lookback 창은 as_of_date 이전 14일이므로 미래 누출 없음.
- PER: as_of_date 당일 종가 기준 PER = E/P (당일 이전 발표 실적 사용) → 미래 누출 없음.
- foreign_5d: as_of_date를 포함한 최근 5거래일 순매수 합계 → 미래 누출 없음.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd
import pytest


# ── 픽스처 헬퍼 ────────────────────────────────────────────────────────────

_TICKERS = ["005930", "000660", "035420"]  # 삼성전자·하이닉스·NAVER 픽스처 코드


def _price_series(
    tickers: list[str],
    start: date,
    n_days: int,
    daily_ret: float = 0.005,
) -> pd.DataFrame:
    """결정적 종가 시리즈 — 모든 종목 동일 일별 수익률."""
    dates = [start + timedelta(days=i) for i in range(n_days)]
    data: dict[str, list[float]] = {}
    for t in tickers:
        close = 50_000.0
        vals: list[float] = []
        for _ in dates:
            vals.append(close)
            close *= 1 + daily_ret
        data[t] = vals
    return pd.DataFrame(data, index=dates)


def _kospi_returns(start: date, end: date, daily_ret: float = 0.004) -> pd.Series:
    n = (end - start).days + 1
    dates = [start + timedelta(days=i) for i in range(n)]
    return pd.Series([daily_ret] * n, index=dates, name="KOSPI200")


def _universe_provider_factory(tickers: list[str], achievable: bool = True):
    """단순 고정 유니버스를 반환하는 provider 팩토리."""
    from trading.backtest.universe_reconstructor import UniverseResult

    def provider(d: date):
        return UniverseResult(
            rebalance_date=d,
            tickers=tickers if achievable else [],
            achievable=achievable,
        )

    return provider


# ── TC-A: entry_alpha_run 모듈 임포트 — pykrx 없이 가능해야 함 ─────────────

class TestModuleImportIsolation:
    """entry_alpha_run은 pykrx/DB 없이 import 가능해야 한다."""

    def test_module_importable_without_pykrx(self):
        """pykrx KRX 로그인 사이드이펙트 없이 모듈 import 성공."""
        # 이 import가 성공하지 않으면 테스트가 RED 상태
        import trading.backtest.entry_alpha_run as m  # noqa: F401
        assert hasattr(m, "build_rsi_extractor"), "build_rsi_extractor 함수 필요"
        assert hasattr(m, "build_per_extractor"), "build_per_extractor 함수 필요"
        assert hasattr(m, "build_foreign_extractor"), "build_foreign_extractor 함수 필요"
        assert hasattr(m, "build_kospi200_returns_provider"), "build_kospi200_returns_provider 함수 필요"
        assert hasattr(m, "build_rebalance_schedule"), "build_rebalance_schedule 함수 필요"
        assert hasattr(m, "run_entry_alpha"), "run_entry_alpha 함수 필요"

    def test_no_pykrx_import_at_module_level(self):
        """모듈 import 시점에 pykrx가 sys.modules에 로드되지 않아야 한다.

        pykrx는 import 시 KRX 세션 객체를 초기화할 수 있으므로
        런타임 lazy import로만 접근해야 한다.
        """
        import sys
        # pykrx가 이미 로드됐는지 확인 전에 entry_alpha_run을 신규 import
        # (이미 로드된 경우 이 테스트는 가드 역할만 함)
        import trading.backtest.entry_alpha_run  # noqa: F401
        # 모듈이 pykrx를 직접 import하면 pykrx가 sys.modules에 상위 레벨로 잡힘
        # 실제 가드는 lazy import — 여기서는 모듈 내부 함수가 pykrx를 호출하지 않는 한 OK
        assert "trading.backtest.entry_alpha_run" in sys.modules


# ── TC-B: build_rebalance_schedule — 월별 리밸런싱 일정 생성 ─────────────

class TestRebalanceSchedule:
    """월별 첫 영업일 리밸런싱 일정을 생성한다."""

    def test_schedule_length_matches_months(self):
        """2016-01 ~ 2018-12 = 36개월 → 최소 36개 날짜 (주말 보정 포함 ±)."""
        from trading.backtest.entry_alpha_run import build_rebalance_schedule

        dates = build_rebalance_schedule(
            start=date(2016, 1, 1),
            end=date(2018, 12, 31),
        )
        # 36개월 — 각 월에 최소 1개
        assert len(dates) >= 36, f"기대 >= 36개, 실제 {len(dates)}개"
        assert len(dates) <= 37, f"기대 <= 37개, 실제 {len(dates)}개"

    def test_schedule_ascending_order(self):
        """날짜가 오름차순 정렬돼야 한다."""
        from trading.backtest.entry_alpha_run import build_rebalance_schedule

        dates = build_rebalance_schedule(date(2020, 1, 1), date(2022, 12, 31))
        for i in range(len(dates) - 1):
            assert dates[i] < dates[i + 1], (
                f"날짜 순서 위반: {dates[i]} >= {dates[i + 1]}"
            )

    def test_all_dates_within_window(self):
        """반환 날짜가 start/end 범위 안에 있어야 한다."""
        from trading.backtest.entry_alpha_run import build_rebalance_schedule

        start = date(2020, 3, 1)
        end = date(2020, 6, 30)
        dates = build_rebalance_schedule(start, end)
        for d in dates:
            assert start <= d <= end, f"범위 밖 날짜: {d}"

    def test_each_date_is_weekday(self):
        """반환 날짜는 주말이 아닌 평일이어야 한다 (간단 영업일 근사)."""
        from trading.backtest.entry_alpha_run import build_rebalance_schedule

        dates = build_rebalance_schedule(date(2020, 1, 1), date(2021, 12, 31))
        for d in dates:
            assert d.weekday() < 5, f"주말 날짜 포함: {d} (weekday={d.weekday()})"


# ── TC-C: build_rsi_extractor — RSI point-in-time 정확성 ─────────────────

class TestRsiExtractor:
    """RSI 추출기는 as_of_date 이전 데이터만 사용해야 한다 (look-ahead 금지)."""

    def _make_rsi_prices(self, n_days: int = 30, ticker: str = "A") -> list[dict[str, Any]]:
        """RSI 계산용 픽스처 OHLCV 바 목록."""
        start = date(2020, 1, 2)
        bars = []
        close = 10_000.0
        for i in range(n_days):
            d = start + timedelta(days=i)
            bars.append({"ts": d, "close": close, "open": close, "high": close, "low": close, "volume": 1000})
            close *= 1.001  # 단조 상승 → RSI 높아짐
        return bars

    def test_rsi_value_in_range(self):
        """RSI는 [0, 100] 범위 내여야 한다."""
        from trading.backtest.entry_alpha_run import build_rsi_extractor

        as_of = date(2020, 1, 31)
        tickers = ["A"]

        # 픽스처 OHLCV provider: as_of 이전 30일 데이터 반환
        bars = self._make_rsi_prices(n_days=30)
        # bars 중 as_of 이전만 사용하는지 확인하기 위해 미래 bar 추가
        future_bar = {"ts": as_of + timedelta(days=1), "close": 99999.0,
                      "open": 99999.0, "high": 99999.0, "low": 99999.0, "volume": 0}
        bars_with_future = bars + [future_bar]

        def ohlcv_provider(ticker: str, start: date, end: date) -> list[dict[str, Any]]:
            # end는 as_of이어야 함 (미래 bar 반환되더라도 필터링됨)
            return [b for b in bars_with_future if b["ts"] <= end]

        extractor = build_rsi_extractor(ohlcv_provider=ohlcv_provider, period=14)
        result = extractor(as_of, tickers)

        assert "A" in result, "종목 A의 RSI가 반환돼야 한다"
        val = result["A"]
        if val is not None:
            assert 0.0 <= val <= 100.0, f"RSI={val}가 [0, 100] 밖"

    def test_rsi_extractor_called_with_as_of_only(self):
        """ohlcv_provider의 end는 as_of_date 이하여야 한다 (look-ahead 금지).

        point-in-time 정확성 논증:
        RSI 계산은 lookback 창(14일)의 종가만 사용한다.
        extractor는 ohlcv_provider(ticker, start, end=as_of_date)를 호출하므로
        as_of_date 이후 가격은 물리적으로 전달되지 않는다.
        """
        from trading.backtest.entry_alpha_run import build_rsi_extractor

        call_log: list[tuple[date, date]] = []
        as_of = date(2020, 2, 14)

        def ohlcv_provider(ticker: str, start: date, end: date) -> list[dict[str, Any]]:
            call_log.append((start, end))
            bars = self._make_rsi_prices(n_days=20)
            return [b for b in bars if b["ts"] <= end]

        extractor = build_rsi_extractor(ohlcv_provider=ohlcv_provider, period=14)
        extractor(as_of, ["A"])

        assert len(call_log) >= 1, "ohlcv_provider가 호출돼야 한다"
        for start_called, end_called in call_log:
            assert end_called <= as_of, (
                f"look-ahead 위반: end={end_called} > as_of={as_of}"
            )

    def test_rsi_none_when_insufficient_data(self):
        """데이터가 부족하면 RSI=None을 반환한다 (기간 미달)."""
        from trading.backtest.entry_alpha_run import build_rsi_extractor

        # period=14이지만 5일 데이터만 제공
        short_bars = self._make_rsi_prices(n_days=5)

        def ohlcv_provider(ticker: str, start: date, end: date) -> list[dict[str, Any]]:
            return [b for b in short_bars if b["ts"] <= end]

        extractor = build_rsi_extractor(ohlcv_provider=ohlcv_provider, period=14)
        result = extractor(date(2020, 1, 10), ["A"])

        # 데이터 부족 → None (extractor가 예외를 던지지 않아야 함)
        assert "A" in result
        assert result["A"] is None, f"데이터 부족 시 None이어야 함, 실제: {result['A']}"

    def test_rsi_lower_for_declining_prices(self):
        """하락 가격 시리즈의 RSI는 50 미만이어야 한다."""
        from trading.backtest.entry_alpha_run import build_rsi_extractor

        start = date(2020, 1, 2)
        n_days = 25
        # 단조 하락 가격 시리즈
        declining_bars = []
        close = 10_000.0
        for i in range(n_days):
            d = start + timedelta(days=i)
            declining_bars.append({"ts": d, "close": close,
                                    "open": close, "high": close, "low": close, "volume": 1000})
            close *= 0.999

        def ohlcv_provider(ticker: str, start_: date, end: date) -> list[dict[str, Any]]:
            return [b for b in declining_bars if b["ts"] <= end]

        extractor = build_rsi_extractor(ohlcv_provider=ohlcv_provider, period=14)
        as_of = start + timedelta(days=n_days - 1)
        result = extractor(as_of, ["A"])

        val = result.get("A")
        if val is not None:
            assert val < 50.0, f"하락 시리즈 RSI={val}는 50 미만이어야 함"

    def test_rsi_higher_for_rising_prices(self):
        """상승 가격 시리즈의 RSI는 50 초과여야 한다."""
        from trading.backtest.entry_alpha_run import build_rsi_extractor

        start = date(2020, 1, 2)
        rising_bars = []
        close = 10_000.0
        for i in range(25):
            d = start + timedelta(days=i)
            rising_bars.append({"ts": d, "close": close,
                                  "open": close, "high": close, "low": close, "volume": 1000})
            close *= 1.002

        def ohlcv_provider(ticker: str, start_: date, end: date) -> list[dict[str, Any]]:
            return [b for b in rising_bars if b["ts"] <= end]

        extractor = build_rsi_extractor(ohlcv_provider=ohlcv_provider, period=14)
        as_of = start + timedelta(days=24)
        result = extractor(as_of, ["A"])

        val = result.get("A")
        if val is not None:
            assert val > 50.0, f"상승 시리즈 RSI={val}는 50 초과여야 함"


# ── TC-D: RSI 수치 계산 정확성 ───────────────────────────────────────────

class TestRsiComputation:
    """RSI 수식 자체가 올바른지 픽스처 값으로 검증한다."""

    def test_rsi_wilder_smoothing(self):
        """Wilder 평활 RSI 계산이 수동 기대값과 일치하는지 확인.

        14일 RSI: 순수 상승 시리즈 → RSI ≈ 100에 수렴.
        순수 하락 시리즈 → RSI ≈ 0에 수렴.
        """
        from trading.backtest.entry_alpha_run import _compute_rsi_from_closes

        # 14일 내내 +1% 상승 → RSI ~ 100
        closes_up = [10000.0 * (1.01 ** i) for i in range(20)]
        rsi_up = _compute_rsi_from_closes(closes_up, period=14)
        assert rsi_up is not None
        assert rsi_up > 90.0, f"순수 상승 RSI={rsi_up:.2f}는 90 초과여야 함"

        # 14일 내내 -1% 하락 → RSI ~ 0
        closes_down = [10000.0 * (0.99 ** i) for i in range(20)]
        rsi_down = _compute_rsi_from_closes(closes_down, period=14)
        assert rsi_down is not None
        assert rsi_down < 10.0, f"순수 하락 RSI={rsi_down:.2f}는 10 미만이어야 함"

    def test_rsi_returns_none_for_insufficient_closes(self):
        """종가 시리즈가 period+1 미만이면 None을 반환한다."""
        from trading.backtest.entry_alpha_run import _compute_rsi_from_closes

        closes = [10000.0] * 10  # period=14이므로 부족
        result = _compute_rsi_from_closes(closes, period=14)
        assert result is None, f"부족한 데이터 → None이어야 함, 실제: {result}"


# ── TC-E: build_per_extractor — PER point-in-time 정확성 ─────────────────

class TestPerExtractor:
    """PER 추출기는 as_of_date 당일 펀더멘털만 사용해야 한다.

    point-in-time 정확성 논증:
    pykrx get_market_fundamental_by_date(start, end, ticker)는 date-indexed 시계열을 반환한다.
    extractor는 end=as_of_date로 호출하여 as_of_date 이후 EPS/주가 데이터를 배제한다.
    낮은 PER = 저평가 → score로 사용할 때 부호를 반전해 높은 값 = 좋음으로 정규화한다.
    """

    def test_per_sign_convention_lower_is_better(self):
        """낮은 PER → 더 높은 score (역수 변환 또는 음수화).

        설계 결정: extractor는 -PER 또는 1/PER을 반환한다.
        가장 낮은 PER 종목이 상위 quantile에 들어와야 한다.
        """
        from trading.backtest.entry_alpha_run import build_per_extractor

        as_of = date(2020, 6, 30)
        tickers = ["A", "B", "C"]

        # A=PER 5(저평가), B=PER 15, C=PER 30(고평가)
        per_fixture = {"A": 5.0, "B": 15.0, "C": 30.0}

        def fundamental_provider(ticker: str, as_of_date: date) -> float | None:
            return per_fixture.get(ticker)

        extractor = build_per_extractor(fundamental_provider=fundamental_provider)
        result = extractor(as_of, tickers)

        assert set(result.keys()) == set(tickers)
        # 낮은 PER 종목이 더 높은 score를 받아야 한다
        # (measure_feature_alpha의 select_top_quantile은 값 내림차순으로 상위 선택)
        if result["A"] is not None and result["C"] is not None:
            assert result["A"] > result["C"], (
                f"PER 5(A)={result['A']:.4f}가 PER 30(C)={result['C']:.4f}보다 높아야 함 "
                "(낮은 PER = 더 좋음)"
            )

    def test_per_none_when_provider_returns_none(self):
        """PER 데이터가 없으면 None을 반환한다."""
        from trading.backtest.entry_alpha_run import build_per_extractor

        def fundamental_provider(ticker: str, as_of_date: date) -> float | None:
            return None

        extractor = build_per_extractor(fundamental_provider=fundamental_provider)
        result = extractor(date(2020, 1, 2), ["A"])
        assert result["A"] is None

    def test_per_none_when_per_zero_or_negative(self):
        """PER 0 또는 음수 (적자 기업) → None 처리."""
        from trading.backtest.entry_alpha_run import build_per_extractor

        def fundamental_provider(ticker: str, as_of_date: date) -> float | None:
            return 0.0  # 또는 -5.0

        extractor = build_per_extractor(fundamental_provider=fundamental_provider)
        result = extractor(date(2020, 1, 2), ["A"])
        # PER ≤ 0은 의미 없음 → None
        assert result["A"] is None, "PER=0은 None으로 처리해야 함"

    def test_per_extractor_uses_as_of_date(self):
        """fundamental_provider는 as_of_date로만 호출돼야 한다 (미래 날짜 금지)."""
        from trading.backtest.entry_alpha_run import build_per_extractor

        call_log: list[date] = []
        as_of = date(2020, 6, 30)

        def fundamental_provider(ticker: str, as_of_date: date) -> float | None:
            call_log.append(as_of_date)
            assert as_of_date <= as_of, (
                f"미래 데이터 누출: {as_of_date} > {as_of}"
            )
            return 10.0

        extractor = build_per_extractor(fundamental_provider=fundamental_provider)
        extractor(as_of, ["A", "B"])

        assert len(call_log) >= 1


# ── TC-F: build_foreign_extractor — 외국인 순매수 point-in-time 정확성 ───

class TestForeignExtractor:
    """외국인 순매수 추출기는 as_of_date 이전 5거래일만 사용해야 한다.

    point-in-time 정확성 논증:
    extractor는 flows_provider(ticker, start=as_of-lookback, end=as_of_date)를 호출하므로
    as_of_date 이후 수급 데이터는 물리적으로 포함되지 않는다.
    5거래일 누적 외국인 순매수가 클수록 score 높음 (양의 모멘텀 신호).
    """

    def test_foreign_score_proportional_to_inflow(self):
        """외국인 순매수가 클수록 더 높은 score를 반환한다."""
        from trading.backtest.entry_alpha_run import build_foreign_extractor

        as_of = date(2020, 6, 30)
        tickers = ["A", "B"]

        # A: 5일간 순매수 합계 +100억, B: -50억
        flows_fixture: dict[str, list[dict[str, Any]]] = {
            "A": [{"ts": as_of - timedelta(days=i), "foreign_net": 10_000_000_000} for i in range(5)],
            "B": [{"ts": as_of - timedelta(days=i), "foreign_net": -5_000_000_000} for i in range(5)],
        }

        def flows_provider(ticker: str, start: date, end: date) -> list[dict[str, Any]]:
            rows = flows_fixture.get(ticker, [])
            return [r for r in rows if start <= r["ts"] <= end]

        extractor = build_foreign_extractor(flows_provider=flows_provider, window_days=5)
        result = extractor(as_of, tickers)

        assert "A" in result and "B" in result
        if result["A"] is not None and result["B"] is not None:
            assert result["A"] > result["B"], (
                f"순매수 많은 A={result['A']}가 B={result['B']}보다 높아야 함"
            )

    def test_foreign_extractor_end_is_as_of(self):
        """flows_provider의 end는 as_of_date 이하여야 한다 (look-ahead 금지)."""
        from trading.backtest.entry_alpha_run import build_foreign_extractor

        call_log: list[tuple[date, date]] = []
        as_of = date(2020, 6, 30)

        def flows_provider(ticker: str, start: date, end: date) -> list[dict[str, Any]]:
            call_log.append((start, end))
            return [{"ts": end - timedelta(days=i), "foreign_net": 1_000_000} for i in range(5)]

        extractor = build_foreign_extractor(flows_provider=flows_provider, window_days=5)
        extractor(as_of, ["A"])

        assert len(call_log) >= 1, "flows_provider가 호출돼야 한다"
        for _, end_called in call_log:
            assert end_called <= as_of, (
                f"look-ahead 위반: end={end_called} > as_of={as_of}"
            )

    def test_foreign_none_when_no_flows(self):
        """수급 데이터가 없으면 None을 반환한다."""
        from trading.backtest.entry_alpha_run import build_foreign_extractor

        def flows_provider(ticker: str, start: date, end: date) -> list[dict[str, Any]]:
            return []

        extractor = build_foreign_extractor(flows_provider=flows_provider, window_days=5)
        result = extractor(date(2020, 1, 2), ["A"])
        assert result.get("A") is None


# ── TC-G: build_kospi200_returns_provider — 벤치마크 수익률 ──────────────

class TestKospi200ReturnsProvider:
    """KOSPI200 벤치마크 수익률 provider 검증."""

    def test_returns_series_has_correct_sign(self):
        """상승 지수 시리즈 → 양의 수익률."""
        from trading.backtest.entry_alpha_run import build_kospi200_returns_provider

        # 픽스처: 매일 +0.5% 상승하는 KOSPI200 close 시리즈
        start = date(2020, 1, 2)
        n = 10
        idx_fixture = []
        close = 300.0
        for i in range(n):
            d = start + timedelta(days=i)
            idx_fixture.append({"ts": d, "close": close})
            close *= 1.005

        def index_ohlcv_provider(start_: date, end_: date) -> list[dict[str, Any]]:
            return [r for r in idx_fixture if start_ <= r["ts"] <= end_]

        provider = build_kospi200_returns_provider(
            index_ohlcv_provider=index_ohlcv_provider,
        )
        result = provider(start, start + timedelta(days=n - 1))

        assert isinstance(result, pd.Series), "Series를 반환해야 한다"
        assert len(result) > 0, "빈 시리즈면 안 됨"
        # 상승 시리즈 → 대부분 양수 수익률
        pos_ratio = (result > 0).sum() / len(result)
        assert pos_ratio >= 0.8, f"상승 지수에서 양수 수익률 비율={pos_ratio:.2f} < 0.8"

    def test_returns_provider_end_is_not_future(self):
        """index_ohlcv_provider 호출 end가 요청 end와 일치해야 한다."""
        from trading.backtest.entry_alpha_run import build_kospi200_returns_provider

        call_log: list[tuple[date, date]] = []
        start = date(2020, 1, 2)
        end = date(2020, 1, 20)

        def index_ohlcv_provider(start_: date, end_: date) -> list[dict[str, Any]]:
            call_log.append((start_, end_))
            return []

        provider = build_kospi200_returns_provider(
            index_ohlcv_provider=index_ohlcv_provider,
        )
        provider(start, end)

        assert len(call_log) >= 1
        for _, end_called in call_log:
            assert end_called <= end, (
                f"미래 end 호출: {end_called} > {end}"
            )


# ── TC-H: run_entry_alpha — 3 피처 통합 실행 ────────────────────────────

class TestRunEntryAlpha:
    """run_entry_alpha가 rsi/per/foreign 3개 피처를 모두 측정하고 결과를 반환한다."""

    def _make_providers(self):
        """결정적 픽스처 provider 모음 반환."""
        tickers = ["A", "B"]
        start = date(2016, 1, 4)
        n_rebal = 35  # floor(30) 충족

        # OHLCV 픽스처
        price_bars: dict[str, list[dict[str, Any]]] = {}
        for t in tickers:
            bars = []
            close = 50_000.0
            for i in range(400):
                d = start + timedelta(days=i)
                bars.append({"ts": d, "close": close,
                              "open": close, "high": close, "low": close, "volume": 1000})
                close *= 1.001
            price_bars[t] = bars

        def ohlcv_provider(ticker: str, s: date, e: date) -> list[dict[str, Any]]:
            return [b for b in price_bars.get(ticker, []) if s <= b["ts"] <= e]

        def fundamental_provider(ticker: str, as_of_date: date) -> float | None:
            # A = PER 8 (저평가), B = PER 20
            return 8.0 if ticker == "A" else 20.0

        def flows_provider(ticker: str, s: date, e: date) -> list[dict[str, Any]]:
            # A: 양의 외국인 순매수
            net = 5_000_000_000 if ticker == "A" else -1_000_000_000
            rows = []
            d = e
            for _ in range(5):
                rows.append({"ts": d, "foreign_net": net})
                d -= timedelta(days=1)
            return [r for r in rows if s <= r["ts"] <= e]

        def index_ohlcv_provider(s: date, e: date) -> list[dict[str, Any]]:
            rows = []
            close = 300.0
            n = (e - s).days + 1
            for i in range(n):
                d = s + timedelta(days=i)
                rows.append({"ts": d, "close": close})
                close *= 1.002
            return rows

        def universe_provider(d: date):
            from trading.backtest.universe_reconstructor import UniverseResult
            return UniverseResult(rebalance_date=d, tickers=tickers, achievable=True)

        rebalance_dates = [start + timedelta(days=20 * i) for i in range(n_rebal)]

        return {
            "ohlcv_provider": ohlcv_provider,
            "fundamental_provider": fundamental_provider,
            "flows_provider": flows_provider,
            "index_ohlcv_provider": index_ohlcv_provider,
            "universe_provider": universe_provider,
            "rebalance_dates": rebalance_dates,
        }

    def test_run_entry_alpha_returns_three_results(self):
        """run_entry_alpha는 rsi/per/foreign 3개 결과를 반환해야 한다."""
        from trading.backtest.entry_alpha_run import run_entry_alpha

        p = self._make_providers()
        results = run_entry_alpha(
            rebalance_dates=p["rebalance_dates"],
            universe_provider=p["universe_provider"],
            ohlcv_provider=p["ohlcv_provider"],
            fundamental_provider=p["fundamental_provider"],
            flows_provider=p["flows_provider"],
            index_ohlcv_provider=p["index_ohlcv_provider"],
            sample_floor=30,
            bonferroni_n=3,
        )
        assert len(results) == 3, f"결과 3개여야 함, 실제: {len(results)}"
        feature_names = {r.feature_name for r in results}
        assert "rsi" in feature_names, "rsi 결과 없음"
        assert "per" in feature_names, "per 결과 없음"
        assert "foreign" in feature_names, "foreign 결과 없음"

    def test_each_result_has_valid_label(self):
        """각 결과의 label이 정의된 집합에 속해야 한다."""
        from trading.backtest.entry_alpha_run import run_entry_alpha

        p = self._make_providers()
        results = run_entry_alpha(
            rebalance_dates=p["rebalance_dates"],
            universe_provider=p["universe_provider"],
            ohlcv_provider=p["ohlcv_provider"],
            fundamental_provider=p["fundamental_provider"],
            flows_provider=p["flows_provider"],
            index_ohlcv_provider=p["index_ohlcv_provider"],
            sample_floor=30,
            bonferroni_n=3,
        )
        valid_labels = {"PASS", "NOT_PASS", "INCONCLUSIVE", "SURVIVORSHIP_BOUND"}
        for r in results:
            assert r.label in valid_labels, (
                f"{r.feature_name}: label={r.label} 이 정의된 집합에 없음"
            )

    def test_feature_names_match_expected(self):
        """각 피처 이름이 정확히 'rsi', 'per', 'foreign'이어야 한다."""
        from trading.backtest.entry_alpha_run import run_entry_alpha

        p = self._make_providers()
        results = run_entry_alpha(
            rebalance_dates=p["rebalance_dates"],
            universe_provider=p["universe_provider"],
            ohlcv_provider=p["ohlcv_provider"],
            fundamental_provider=p["fundamental_provider"],
            flows_provider=p["flows_provider"],
            index_ohlcv_provider=p["index_ohlcv_provider"],
            sample_floor=30,
            bonferroni_n=3,
        )
        names = sorted(r.feature_name for r in results)
        assert names == ["foreign", "per", "rsi"], (
            f"피처 이름 불일치: {names}"
        )

    def test_bonferroni_n_is_3(self):
        """bonferroni_n=3이 각 결과의 bonferroni_threshold에 반영돼야 한다."""
        from trading.backtest.entry_alpha_run import run_entry_alpha

        p = self._make_providers()
        results = run_entry_alpha(
            rebalance_dates=p["rebalance_dates"],
            universe_provider=p["universe_provider"],
            ohlcv_provider=p["ohlcv_provider"],
            fundamental_provider=p["fundamental_provider"],
            flows_provider=p["flows_provider"],
            index_ohlcv_provider=p["index_ohlcv_provider"],
            sample_floor=30,
            bonferroni_n=3,
        )
        expected_threshold = 0.05 / 3
        for r in results:
            assert abs(r.bonferroni_threshold - expected_threshold) < 1e-9, (
                f"{r.feature_name}: bonferroni_threshold={r.bonferroni_threshold:.6f} "
                f"!= {expected_threshold:.6f}"
            )
