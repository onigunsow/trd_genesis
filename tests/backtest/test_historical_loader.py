"""SPEC-TRADING-057 M1 — point-in-time historical OHLCV 로더 단위 테스트.

REQ-057-M1-1  : pykrx_adapter를 감싸 하니스에 bars를 공급
REQ-057-M1-2  : 결정성 — 동일 입력 → 바이트 동일 바 시퀀스
REQ-057-M1-3  : ts <= cutoff 슬라이스 불변식 (미래 바 제외)
REQ-057-M1-4  : 커버리지 갭 명시적 보고
REQ-057-M1-5  : 미래 바 / 생존편향 유니버스 / 소급 펀더멘털 주입 금지

설계 원칙:
- 모든 테스트는 픽스처 ohlcv_provider 주입으로 실행 — 네트워크/DB 불필요.
- engine.run 입력 포맷(prices DataFrame: index=date, cols=ticker)과 호환.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest


Bar = dict[str, Any]

# ── 픽스처 헬퍼 ────────────────────────────────────────────────────────────

def _make_bar(d: date, close: float) -> Bar:
    """단순 일봉 생성."""
    return {
        "ts": d,
        "open": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": 1_000,
    }


def _make_bars(start: date, n: int, base_price: float = 10_000.0) -> list[Bar]:
    """start 날짜부터 n개의 연속 일봉 생성."""
    return [_make_bar(start + timedelta(days=i), base_price + i * 10) for i in range(n)]


# 픽스처 구간: 22영업일 (2018-01-02 시작, 종료일은 마지막 bar 날짜에 맞춤)
# 연속 22일 → 마지막 날짜 = 2018-01-23
_START_2018 = date(2018, 1, 2)
_BARS_A_2018 = _make_bars(_START_2018, 22, base_price=5_000.0)  # 종목 A
_BARS_B_2018 = _make_bars(_START_2018, 15, base_price=3_000.0)  # 종목 B (짧음 → 갭)
# 종료일을 A 픽스처 마지막 날짜로 맞춰 "완전한 데이터" 케이스 성립
_END_2018 = _BARS_A_2018[-1]["ts"]   # 2018-01-23


def _ohlcv_provider_full(ticker: str, start: date, end: date) -> list[Bar]:
    """A/B 모두 완전한 바를 제공하는 픽스처."""
    if ticker == "000001":
        return [b for b in _BARS_A_2018 if start <= b["ts"] <= end]
    if ticker == "000002":
        return [b for b in _BARS_B_2018 if start <= b["ts"] <= end]
    return []


def _ohlcv_provider_partial(ticker: str, start: date, end: date) -> list[Bar]:
    """B는 첫 5일치만 반환 → 커버리지 갭 발생."""
    if ticker == "000001":
        return [b for b in _BARS_A_2018 if start <= b["ts"] <= end]
    if ticker == "000002":
        all_b = [b for b in _BARS_B_2018 if start <= b["ts"] <= end]
        return all_b[:5]  # 5일치만
    return []


def _ohlcv_provider_with_future(ticker: str, start: date, end: date) -> list[Bar]:
    """미래 바가 섞인 픽스처 — 로더가 걸러내야 한다."""
    bars = [b for b in _BARS_A_2018 if start <= b["ts"] <= end]
    # cutoff(2018-01-15) 이후 미래 바 추가
    future_bar = _make_bar(date(2018, 1, 20), 99_999.0)
    bars.append(future_bar)
    return bars


def _ohlcv_provider_fail(ticker: str, start: date, end: date) -> list[Bar]:
    """항상 예외를 던지는 픽스처 — 오류 격리 테스트용."""
    raise OSError(f"네트워크 실패: {ticker}")


# ── TC-1: 정상 로딩 ──────────────────────────────────────────────────────

class TestLoadNormal:
    """REQ-057-M1-1: 바를 하니스에 공급."""

    def test_returns_bars_for_all_tickers(self):
        """요청한 모든 종목의 bars를 반환한다."""
        from trading.backtest.historical_loader import load_historical_ohlcv

        result = load_historical_ohlcv(
            tickers=["000001", "000002"],
            start=_START_2018,
            end=_END_2018,
            cutoff=_END_2018,
            ohlcv_provider=_ohlcv_provider_full,
        )

        assert "000001" in result.bars
        assert "000002" in result.bars

    def test_bars_non_empty_for_available_ticker(self):
        """데이터가 있는 종목은 bars가 비어있지 않다."""
        from trading.backtest.historical_loader import load_historical_ohlcv

        result = load_historical_ohlcv(
            tickers=["000001"],
            start=_START_2018,
            end=_END_2018,
            cutoff=_END_2018,
            ohlcv_provider=_ohlcv_provider_full,
        )

        assert len(result.bars["000001"]) > 0

    def test_bars_contain_required_fields(self):
        """각 bar는 ts/close 필드를 가져야 한다 (engine.run 호환)."""
        from trading.backtest.historical_loader import load_historical_ohlcv

        result = load_historical_ohlcv(
            tickers=["000001"],
            start=_START_2018,
            end=_END_2018,
            cutoff=_END_2018,
            ohlcv_provider=_ohlcv_provider_full,
        )

        for bar in result.bars["000001"]:
            assert "ts" in bar, "ts 필드 필수"
            assert "close" in bar, "close 필드 필수"

    def test_no_coverage_gaps_when_data_complete(self):
        """완전한 데이터 제공 시 coverage_gaps가 없다."""
        from trading.backtest.historical_loader import load_historical_ohlcv

        result = load_historical_ohlcv(
            tickers=["000001"],
            start=_START_2018,
            end=_END_2018,
            cutoff=_END_2018,
            ohlcv_provider=_ohlcv_provider_full,
        )

        assert result.coverage_gaps == [], f"갭이 없어야 하는데: {result.coverage_gaps}"


# ── TC-2: point-in-time 슬라이스 불변식 ─────────────────────────────────

class TestPointInTimeSlice:
    """REQ-057-M1-3: ts <= cutoff 불변식 — 미래 바 제외."""

    def test_all_bars_ts_lte_cutoff(self):
        """반환된 모든 bar의 ts가 cutoff를 초과하지 않는다."""
        cutoff = date(2018, 1, 10)

        from trading.backtest.historical_loader import load_historical_ohlcv

        result = load_historical_ohlcv(
            tickers=["000001"],
            start=_START_2018,
            end=_END_2018,
            cutoff=cutoff,
            ohlcv_provider=_ohlcv_provider_full,
        )

        for bar in result.bars["000001"]:
            assert bar["ts"] <= cutoff, (
                f"미래 바 누출: ts={bar['ts']} > cutoff={cutoff}"
            )

    def test_future_bars_from_provider_are_filtered(self):
        """provider가 미래 바를 포함해 반환해도 로더가 걸러낸다."""
        cutoff = date(2018, 1, 15)

        from trading.backtest.historical_loader import load_historical_ohlcv

        result = load_historical_ohlcv(
            tickers=["000001"],
            start=_START_2018,
            end=_END_2018,
            cutoff=cutoff,
            ohlcv_provider=_ohlcv_provider_with_future,
        )

        for bar in result.bars["000001"]:
            assert bar["ts"] <= cutoff, (
                f"미래 바가 걸러지지 않음: ts={bar['ts']}"
            )

    def test_cutoff_date_bar_is_included(self):
        """cutoff 당일 바는 포함된다 (경계 포함)."""
        cutoff = date(2018, 1, 5)

        from trading.backtest.historical_loader import load_historical_ohlcv

        result = load_historical_ohlcv(
            tickers=["000001"],
            start=_START_2018,
            end=_END_2018,
            cutoff=cutoff,
            ohlcv_provider=_ohlcv_provider_full,
        )

        dates = [b["ts"] for b in result.bars["000001"]]
        assert cutoff in dates, f"cutoff 당일 바가 포함돼야 한다: {cutoff}"

    def test_bars_sorted_by_ts(self):
        """반환된 bars는 ts 오름차순 정렬돼야 한다 (engine.run 전제)."""
        from trading.backtest.historical_loader import load_historical_ohlcv

        result = load_historical_ohlcv(
            tickers=["000001"],
            start=_START_2018,
            end=_END_2018,
            cutoff=_END_2018,
            ohlcv_provider=_ohlcv_provider_full,
        )

        dates = [b["ts"] for b in result.bars["000001"]]
        assert dates == sorted(dates), "bars가 ts 오름차순이 아니다"


# ── TC-3: 커버리지 갭 명시 ───────────────────────────────────────────────

class TestCoverageGap:
    """REQ-057-M1-4: 커버리지 갭을 명시적으로 보고."""

    def test_gap_reported_for_partial_ticker(self):
        """B는 부분 데이터만 있으므로 갭이 보고돼야 한다."""
        from trading.backtest.historical_loader import load_historical_ohlcv

        result = load_historical_ohlcv(
            tickers=["000001", "000002"],
            start=_START_2018,
            end=_END_2018,
            cutoff=_END_2018,
            ohlcv_provider=_ohlcv_provider_partial,
        )

        gap_tickers = [g.ticker for g in result.coverage_gaps]
        assert "000002" in gap_tickers, "부분 데이터 종목 갭이 보고돼야 한다"

    def test_gap_contains_ticker_info(self):
        """갭 객체에 ticker 필드가 있다."""
        from trading.backtest.historical_loader import load_historical_ohlcv

        result = load_historical_ohlcv(
            tickers=["000002"],
            start=_START_2018,
            end=_END_2018,
            cutoff=_END_2018,
            ohlcv_provider=_ohlcv_provider_partial,
        )

        if result.coverage_gaps:
            gap = result.coverage_gaps[0]
            assert hasattr(gap, "ticker")
            assert gap.ticker == "000002"

    def test_no_data_ticker_reported_as_gap(self):
        """데이터가 전혀 없는 종목도 갭으로 보고된다."""
        from trading.backtest.historical_loader import load_historical_ohlcv

        result = load_historical_ohlcv(
            tickers=["UNKNOWN"],
            start=_START_2018,
            end=_END_2018,
            cutoff=_END_2018,
            ohlcv_provider=_ohlcv_provider_full,  # UNKNOWN은 반환값 없음
        )

        gap_tickers = [g.ticker for g in result.coverage_gaps]
        assert "UNKNOWN" in gap_tickers, "데이터 없는 종목이 갭에 있어야 한다"

    def test_gap_not_silent_partial_data_not_silently_dropped(self):
        """갭을 조용히 무시하지 않고 명시적으로 기록한다 (REQ-057-M1-4)."""
        from trading.backtest.historical_loader import load_historical_ohlcv

        result = load_historical_ohlcv(
            tickers=["000001", "000002"],
            start=_START_2018,
            end=_END_2018,
            cutoff=_END_2018,
            ohlcv_provider=_ohlcv_provider_partial,
        )

        # 부분 데이터는 존재하되, 갭도 보고된다
        assert len(result.bars.get("000002", [])) > 0, "부분 바는 있어야 한다"
        assert any(g.ticker == "000002" for g in result.coverage_gaps), "갭도 보고돼야 한다"


# ── TC-4: 결정성 ─────────────────────────────────────────────────────────

class TestDeterminism:
    """REQ-057-M1-2: 동일 입력 → 바이트 동일 출력."""

    def test_repeated_calls_produce_identical_bars(self):
        """동일 인자로 반복 호출해도 bars가 같다."""
        from trading.backtest.historical_loader import load_historical_ohlcv

        kwargs = dict(
            tickers=["000001", "000002"],
            start=_START_2018,
            end=_END_2018,
            cutoff=_END_2018,
            ohlcv_provider=_ohlcv_provider_full,
        )

        r1 = load_historical_ohlcv(**kwargs)
        r2 = load_historical_ohlcv(**kwargs)

        for ticker in ["000001", "000002"]:
            assert r1.bars[ticker] == r2.bars[ticker], (
                f"종목 {ticker}: 반복 호출 결과 불일치"
            )

    def test_bars_ts_ordering_is_stable(self):
        """bars 정렬 순서가 호출마다 동일하다."""
        from trading.backtest.historical_loader import load_historical_ohlcv

        r1 = load_historical_ohlcv(
            tickers=["000001"],
            start=_START_2018,
            end=_END_2018,
            cutoff=_END_2018,
            ohlcv_provider=_ohlcv_provider_full,
        )
        r2 = load_historical_ohlcv(
            tickers=["000001"],
            start=_START_2018,
            end=_END_2018,
            cutoff=_END_2018,
            ohlcv_provider=_ohlcv_provider_full,
        )

        assert [b["ts"] for b in r1.bars["000001"]] == [b["ts"] for b in r2.bars["000001"]]


# ── TC-5: 오류 격리 ──────────────────────────────────────────────────────

class TestErrorIsolation:
    """한 종목 provider 실패가 다른 종목에 영향을 주지 않는다."""

    def test_failed_ticker_reported_as_gap_not_exception(self):
        """provider 예외는 갭으로 기록하되 전체 로드는 계속된다."""
        from trading.backtest.historical_loader import load_historical_ohlcv

        call_record: list[str] = []

        def mixed_provider(ticker: str, start: date, end: date) -> list[Bar]:
            call_record.append(ticker)
            if ticker == "FAIL":
                raise OSError("강제 실패")
            return _ohlcv_provider_full(ticker, start, end)

        result = load_historical_ohlcv(
            tickers=["000001", "FAIL"],
            start=_START_2018,
            end=_END_2018,
            cutoff=_END_2018,
            ohlcv_provider=mixed_provider,
        )

        # 정상 종목은 데이터가 있어야 함
        assert len(result.bars.get("000001", [])) > 0, "정상 종목 데이터가 있어야 한다"
        # 실패 종목은 갭으로 기록
        gap_tickers = [g.ticker for g in result.coverage_gaps]
        assert "FAIL" in gap_tickers, "실패 종목이 갭으로 기록돼야 한다"

    def test_all_failed_tickers_report_gaps(self):
        """모든 종목 실패 시에도 예외를 전파하지 않고 빈 bars + 전체 갭 보고."""
        from trading.backtest.historical_loader import load_historical_ohlcv

        result = load_historical_ohlcv(
            tickers=["000001", "000002"],
            start=_START_2018,
            end=_END_2018,
            cutoff=_END_2018,
            ohlcv_provider=_ohlcv_provider_fail,
        )

        gap_tickers = {g.ticker for g in result.coverage_gaps}
        assert "000001" in gap_tickers
        assert "000002" in gap_tickers


# ── TC-6: engine.run 호환성 확인 ─────────────────────────────────────────

class TestEngineCompatibility:
    """반환된 bars가 engine.run의 prices DataFrame 변환에 사용 가능하다."""

    def test_bars_convertible_to_prices_dataframe(self):
        """bars → DataFrame 변환이 engine.run 서명 형태로 가능하다."""
        import pandas as pd
        from trading.backtest.historical_loader import load_historical_ohlcv

        result = load_historical_ohlcv(
            tickers=["000001", "000002"],
            start=_START_2018,
            end=_END_2018,
            cutoff=_END_2018,
            ohlcv_provider=_ohlcv_provider_full,
        )

        # bars → prices DataFrame 변환 시도
        prices_dict: dict = {}
        for ticker, bars in result.bars.items():
            if bars:
                prices_dict[ticker] = {b["ts"]: b["close"] for b in bars}

        import pandas as pd
        prices_df = pd.DataFrame(prices_dict)

        # engine.run 기대 포맷: index=date, cols=ticker
        assert isinstance(prices_df.index[0], date), "인덱스가 date 타입이어야 한다"
        assert set(prices_df.columns) >= {"000001", "000002"}

    def test_to_prices_dataframe_helper_if_provided(self):
        """LoadResult.to_prices_dataframe() 헬퍼가 있으면 포맷이 올바르다."""
        from trading.backtest.historical_loader import load_historical_ohlcv

        result = load_historical_ohlcv(
            tickers=["000001"],
            start=_START_2018,
            end=_END_2018,
            cutoff=_END_2018,
            ohlcv_provider=_ohlcv_provider_full,
        )

        # to_prices_dataframe() 헬퍼가 있을 경우만 검증
        if hasattr(result, "to_prices_dataframe"):
            import pandas as pd
            df = result.to_prices_dataframe()
            assert isinstance(df, pd.DataFrame)
            assert "000001" in df.columns


# ── TC-7: pykrx import 격리 ──────────────────────────────────────────────

class TestPykrxIsolation:
    """단위 테스트 환경에서 pykrx가 로드되지 않는다."""

    def test_module_importable_without_network(self):
        """historical_loader는 pykrx 없이 import 가능해야 한다."""
        import trading.backtest.historical_loader  # noqa: F401

    def test_provider_spy_called_exactly_once_per_ticker(self):
        """ohlcv_provider는 종목당 1회 호출된다."""
        from trading.backtest.historical_loader import load_historical_ohlcv

        call_count: dict[str, int] = {}

        def spy_provider(ticker: str, start: date, end: date) -> list[Bar]:
            call_count[ticker] = call_count.get(ticker, 0) + 1
            return _ohlcv_provider_full(ticker, start, end)

        load_historical_ohlcv(
            tickers=["000001", "000002"],
            start=_START_2018,
            end=_END_2018,
            cutoff=_END_2018,
            ohlcv_provider=spy_provider,
        )

        assert call_count.get("000001", 0) == 1, "000001 provider 1회 호출"
        assert call_count.get("000002", 0) == 1, "000002 provider 1회 호출"
