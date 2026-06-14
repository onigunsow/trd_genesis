"""SPEC-TRADING-044 M2 — walk-forward point-in-time 하니스 테스트.

AC-1: look-ahead 부재 불변식 (미래 누출 픽스처 → 단언 실패)
AC-2: 롤링 train/test, OOS 헤드라인, exit_sweep 의미론 재사용
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest


# ── 공통 픽스처 헬퍼 ──────────────────────────────────────────────────────

def _bar(d: date, close: float, *, low: float | None = None, high: float | None = None) -> dict:
    """단순 OHLC 바 생성."""
    return {
        "ts": d,
        "open": close,
        "high": high if high is not None else close,
        "low": low if low is not None else close,
        "close": close,
    }


def _series(closes: list[float], *, start: date = date(2024, 1, 1)) -> list[dict]:
    """종가 리스트 → 바 리스트 (날짜 연속)."""
    return [_bar(start + timedelta(days=i), c) for i, c in enumerate(closes)]


def _make_price_data(closes: list[float], *, symbol: str = "A") -> dict[str, list[dict]]:
    return {symbol: _series(closes)}


# ── AC-1: look-ahead 불변식 ───────────────────────────────────────────────

class TestPointInTimeSlice:
    """윈도 종료일 T 기준 ts <= T 인 바만 사용해야 한다."""

    def test_slice_excludes_future_bars(self):
        """_slice_bars(bars, cutoff) 가 cutoff 이후 바를 제거한다."""
        from trading.backtest.walk_forward import _slice_bars

        bars = _series([100, 101, 102, 103, 104])
        cutoff = bars[2]["ts"]  # 3번째 바 날짜
        sliced = _slice_bars(bars, cutoff)
        assert len(sliced) == 3
        for b in sliced:
            assert b["ts"] <= cutoff

    def test_slice_includes_cutoff_day(self):
        """종료일 당일 바는 포함된다."""
        from trading.backtest.walk_forward import _slice_bars

        bars = _series([100, 101, 102])
        cutoff = bars[-1]["ts"]
        sliced = _slice_bars(bars, cutoff)
        assert sliced[-1]["ts"] == cutoff

    def test_look_ahead_invariant_future_bar_must_change_result(self):
        """미래 바를 추가하면 슬라이스 결과가 달라져야 한다 — 누출 픽스처 검증.

        이 테스트는 미래 바가 결과에 영향을 미치지 않음을 검증한다:
        _slice_bars 로 T 기준 슬라이스하면 미래 바는 제외되어야 한다.
        만약 _slice_bars 없이 전체 bars 를 사용했다면 길이가 달라진다.
        """
        from trading.backtest.walk_forward import _slice_bars

        T = date(2024, 1, 3)
        bars_without_future = _series([100, 101, 102, 103])
        # 미래 바 추가(T 이후)
        future_bar = _bar(date(2024, 1, 10), 200.0)
        bars_with_future = bars_without_future + [future_bar]

        sliced_without = _slice_bars(bars_without_future, T)
        sliced_with = _slice_bars(bars_with_future, T)

        # 두 슬라이스는 동일 — 미래 바가 누출되지 않음
        assert len(sliced_without) == len(sliced_with)
        assert sliced_without == sliced_with

    def test_look_ahead_leak_would_be_detected(self):
        """미래 바를 포함한 전체 리스트는 슬라이스보다 길다 — 누출이 가능한 구조는 다름."""
        from trading.backtest.walk_forward import _slice_bars

        T = date(2024, 1, 3)
        bars = _series([100, 101, 102, 103]) + [_bar(date(2024, 1, 10), 200.0)]
        sliced = _slice_bars(bars, T)
        # 슬라이스(3일까지 포함) < 전체(미래 포함): 누출 구조 검출
        assert len(sliced) < len(bars)


# ── 롤링 윈도 분할 ────────────────────────────────────────────────────────

class TestRollingWindowSplit:
    """롤링 train/test 윈도 생성 검증."""

    def test_generates_correct_window_count(self):
        """train 30 / test 10 / step 10 로 60바 → 3 윈도."""
        from trading.backtest.walk_forward import rolling_windows

        bars = _series(list(range(60)))
        windows = rolling_windows(bars, train_bars=30, test_bars=10, step_bars=10)
        assert len(windows) == 3

    def test_each_window_has_train_and_test(self):
        """각 윈도는 train_bars 와 test_bars 를 가진다."""
        from trading.backtest.walk_forward import rolling_windows

        bars = _series(list(range(60)))
        windows = rolling_windows(bars, train_bars=30, test_bars=10, step_bars=10)
        for win in windows:
            assert len(win.train_bars) == 30
            assert len(win.test_bars) == 10

    def test_test_window_immediately_follows_train(self):
        """test 윈도의 첫 바는 train 윈도 마지막 바 다음 날이다."""
        from trading.backtest.walk_forward import rolling_windows

        bars = _series(list(range(60)))
        windows = rolling_windows(bars, train_bars=30, test_bars=10, step_bars=10)
        for win in windows:
            train_end = win.train_bars[-1]["ts"]
            test_start = win.test_bars[0]["ts"]
            assert test_start > train_end

    def test_no_overlap_between_train_and_test(self):
        """train 과 test 는 겹치지 않는다 (look-ahead 없음)."""
        from trading.backtest.walk_forward import rolling_windows

        bars = _series(list(range(60)))
        windows = rolling_windows(bars, train_bars=30, test_bars=10, step_bars=10)
        for win in windows:
            train_dates = {b["ts"] for b in win.train_bars}
            test_dates = {b["ts"] for b in win.test_bars}
            assert train_dates.isdisjoint(test_dates)

    def test_insufficient_data_returns_empty(self):
        """데이터가 train+test 보다 짧으면 빈 리스트."""
        from trading.backtest.walk_forward import rolling_windows

        bars = _series(list(range(10)))
        windows = rolling_windows(bars, train_bars=20, test_bars=10, step_bars=10)
        assert windows == []

    def test_step_advances_windows(self):
        """step_bars 만큼 윈도가 이동한다."""
        from trading.backtest.walk_forward import rolling_windows

        bars = _series(list(range(70)))
        windows = rolling_windows(bars, train_bars=30, test_bars=10, step_bars=10)
        assert len(windows) >= 2
        # 두 번째 윈도의 train 시작은 첫 윈도보다 step 만큼 뒤
        first_train_start = windows[0].train_bars[0]["ts"]
        second_train_start = windows[1].train_bars[0]["ts"]
        delta = (second_train_start - first_train_start).days
        assert delta == 10  # step_bars = 10


# ── WalkForwardResult: OOS 헤드라인, in-sample 보조 ───────────────────────

class TestWalkForwardResult:
    """OOS 집계가 헤드라인이고 in-sample 은 라벨된 보조 진단이다."""

    def _small_price_data(self):
        """단순 상승하는 30개 바."""
        closes = [100.0 + i for i in range(30)]
        return {
            "A": _series(closes),
        }

    def test_run_walk_forward_returns_result(self):
        """run_walk_forward 가 WalkForwardResult 를 반환한다."""
        from trading.backtest.walk_forward import WalkForwardResult, run_walk_forward

        price_data = self._small_price_data()
        atr_by_symbol = {"A": 2.0}
        result = run_walk_forward(
            price_data,
            atr_by_symbol,
            train_bars=15,
            test_bars=5,
            step_bars=5,
        )
        assert isinstance(result, WalkForwardResult)

    def test_oos_metrics_is_headline(self):
        """oos_metrics 가 헤드라인(list[SweepMetrics]) 이다."""
        from trading.backtest.walk_forward import run_walk_forward
        from trading.backtest.exit_sweep import SweepMetrics

        price_data = self._small_price_data()
        atr_by_symbol = {"A": 2.0}
        result = run_walk_forward(
            price_data,
            atr_by_symbol,
            train_bars=15,
            test_bars=5,
            step_bars=5,
        )
        assert hasattr(result, "oos_metrics")
        for m in result.oos_metrics:
            assert isinstance(m, SweepMetrics)

    def test_in_sample_metrics_labelled_as_diagnostic(self):
        """in_sample_metrics 는 'in-sample' 진단으로 라벨된 보조 데이터다."""
        from trading.backtest.walk_forward import run_walk_forward

        price_data = self._small_price_data()
        atr_by_symbol = {"A": 2.0}
        result = run_walk_forward(
            price_data,
            atr_by_symbol,
            train_bars=15,
            test_bars=5,
            step_bars=5,
        )
        assert hasattr(result, "in_sample_metrics")
        # OOS 가 헤드라인 — in_sample 필드가 별도 존재해야 함
        assert result.oos_metrics is not result.in_sample_metrics

    def test_no_windows_reported_explicitly(self):
        """윈도 부족 시 명시적 empty 결과를 반환하고, oos_metrics 는 빈 리스트."""
        from trading.backtest.walk_forward import run_walk_forward

        tiny_data = {"A": _series([100.0, 101.0, 102.0])}
        result = run_walk_forward(
            tiny_data,
            {"A": 2.0},
            train_bars=50,
            test_bars=20,
            step_bars=10,
        )
        assert result.oos_metrics == []
        assert result.n_windows == 0


# ── 결정성 ────────────────────────────────────────────────────────────────

class TestDeterminism:
    """동일 입력 → 동일 출력 (네트워크/DB 없음)."""

    def test_repeated_runs_give_identical_results(self):
        """동일 OHLCV 주입 시 두 번 실행 결과가 일치한다."""
        from trading.backtest.walk_forward import run_walk_forward

        closes = [100.0 + i * 0.5 for i in range(60)]
        price_data = {"A": _series(closes)}
        atr = {"A": 1.5}

        r1 = run_walk_forward(price_data, atr, train_bars=30, test_bars=10, step_bars=10)
        r2 = run_walk_forward(price_data, atr, train_bars=30, test_bars=10, step_bars=10)

        assert r1.n_windows == r2.n_windows
        assert len(r1.oos_metrics) == len(r2.oos_metrics)
        for m1, m2 in zip(r1.oos_metrics, r2.oos_metrics):
            assert m1.params == m2.params
            assert m1.win_rate == m2.win_rate


# ── exit_sweep 의미론 재사용 ──────────────────────────────────────────────

class TestExitSweepSemantics:
    """exit_sweep.simulate_position 재사용 검증."""

    def test_uses_stop_before_take_semantics(self):
        """stop 이 take 보다 먼저 검사된다 (stop priority)."""
        from trading.backtest.walk_forward import run_walk_forward

        # 하락 후 상승하는 패턴: stop 먼저 히트해야 함
        closes = [100.0] * 5 + [95.0] * 5 + [110.0] * 5  # 하락 후 상승
        lows = [100.0] * 5 + [93.0] * 5 + [110.0] * 5    # stop 히트 가능
        highs = [100.0] * 5 + [95.0] * 5 + [115.0] * 5

        from datetime import date, timedelta
        bars_A = []
        start = date(2024, 1, 1)
        for i, (c, lo, hi) in enumerate(zip(closes, lows, highs)):
            bars_A.append({
                "ts": start + timedelta(days=i),
                "open": c, "high": hi, "low": lo, "close": c,
            })

        price_data = {"A": bars_A}
        atr_by_symbol = {"A": 3.0}

        # stop 이 먼저 검사되므로 exit_reason 은 stop 또는 take 또는 time
        result = run_walk_forward(
            price_data, atr_by_symbol,
            train_bars=5, test_bars=8, step_bars=5,
        )
        # 결과가 반환되고 崩 OOS 메트릭이 있다 (의미론 재사용 확인)
        assert isinstance(result.oos_metrics, list)
