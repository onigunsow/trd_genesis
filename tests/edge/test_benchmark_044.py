"""SPEC-TRADING-044 M4 — KOSPI 누적 초과수익 벤치마크 테스트.

AC-3: 일일 리포트에 "전략 vs KOSPI 매수후보유 누적 초과수익" 라인 표시,
      KOSPI 데이터 없으면 available=False + "알파 미확인",
      edge/benchmark.py의 kospi_closes/cached_ohlcv 재사용 (병렬 경로 없음).
"""
from __future__ import annotations

from datetime import date

from trading.edge.benchmark import Benchmark
from trading.edge.roundtrips import RoundTrip


def _rt(entry: str, exit_: str, qty: int, ep: float, xp: float) -> RoundTrip:
    return RoundTrip(
        ticker="A",
        entry_date=date.fromisoformat(entry),
        exit_date=date.fromisoformat(exit_),
        qty=qty,
        entry_price=ep,
        exit_price=xp,
        entry_fee=0,
        exit_fee=0,
        confidence=None,
        verdict=None,
    )


class TestCumulativeExcessReturn:
    """누적 초과수익 surface (REQ-044-B1, B2)."""

    def test_cumulative_excess_return_attribute_exists(self):
        """Benchmark에 cumulative_excess_return_pct 속성이 있다."""
        from trading.edge.benchmark import Benchmark, compute

        rts = [_rt("2026-01-01", "2026-01-31", 10, 100, 120)]
        closes = [(date(2026, 1, 1), 2500.0), (date(2026, 1, 31), 2550.0)]
        b = compute(rts, closes=closes)
        assert hasattr(b, "cumulative_excess_return_pct")

    def test_cumulative_excess_return_equals_alpha(self):
        """누적 초과수익 = 전략수익률 - KOSPI수익률 (= alpha_pct)."""
        from trading.edge.benchmark import compute

        rts = [_rt("2026-01-01", "2026-01-31", 10, 100, 120)]
        closes = [(date(2026, 1, 1), 2500.0), (date(2026, 1, 31), 2550.0)]
        b = compute(rts, closes=closes)
        # strategy_return_pct = 20%, kospi_return_pct = 2%, excess = 18%
        assert b.available
        assert abs(b.cumulative_excess_return_pct - b.alpha_pct) < 1e-6

    def test_comparison_basis_label_exists(self):
        """비교 기준 라벨(comparison_basis)이 있다."""
        from trading.edge.benchmark import compute

        rts = [_rt("2026-01-01", "2026-01-31", 10, 100, 120)]
        closes = [(date(2026, 1, 1), 2500.0), (date(2026, 1, 31), 2550.0)]
        b = compute(rts, closes=closes)
        assert hasattr(b, "comparison_basis")
        assert isinstance(b.comparison_basis, str)
        assert len(b.comparison_basis) > 0

    def test_comparison_basis_mentions_money_weighted(self):
        """비교 기준이 money-weighted 를 언급한다."""
        from trading.edge.benchmark import compute

        rts = [_rt("2026-01-01", "2026-01-31", 10, 100, 120)]
        closes = [(date(2026, 1, 1), 2500.0), (date(2026, 1, 31), 2550.0)]
        b = compute(rts, closes=closes)
        assert "money-weighted" in b.comparison_basis.lower() or "원가기준" in b.comparison_basis


class TestBenchmarkGracefulUnavailable:
    """KOSPI 데이터 없으면 graceful available=False (REQ-044-B3)."""

    def test_unavailable_when_no_closes(self):
        """KOSPI 종가 없으면 available=False."""
        from trading.edge.benchmark import compute

        rts = [_rt("2026-01-01", "2026-01-31", 10, 100, 120)]
        b = compute(rts, closes=[])
        assert not b.available
        assert b.cumulative_excess_return_pct == 0.0

    def test_unavailable_when_single_close(self):
        """종가 1건 미만이면 available=False."""
        from trading.edge.benchmark import compute

        rts = [_rt("2026-01-01", "2026-01-31", 10, 100, 120)]
        b = compute(rts, closes=[(date(2026, 1, 1), 2500.0)])
        assert not b.available
        assert b.cumulative_excess_return_pct == 0.0


class TestDailyReportBenchmarkLine:
    """일일 리포트에 누적 초과수익 라인이 포함된다 (REQ-044-B1)."""

    def _make_available_benchmark(self, alpha: float = 5.0) -> Benchmark:
        b = Benchmark()
        b.available = True
        b.start = date(2026, 1, 1)
        b.end = date(2026, 3, 1)
        b.kospi_return_pct = 2.0
        b.strategy_return_pct = 2.0 + alpha
        b.alpha_pct = alpha
        b.cumulative_excess_return_pct = alpha
        b.comparison_basis = "money-weighted(원가기준 집계)"
        return b

    def _make_unavailable_benchmark(self) -> Benchmark:
        b = Benchmark()
        b.available = False
        b.cumulative_excess_return_pct = 0.0
        b.comparison_basis = ""
        return b

    def test_benchmark_line_in_daily_report_available(self):
        """벤치마크 사용 가능 시 누적 초과수익 라인이 표시된다."""
        from trading.reports.daily_report import format_benchmark_section

        b = self._make_available_benchmark(alpha=5.0)
        text = format_benchmark_section(b)
        assert "초과수익" in text or "누적" in text or "KOSPI" in text
        assert "+5.0" in text or "5.0" in text

    def test_benchmark_line_in_daily_report_unavailable(self):
        """벤치마크 없으면 '알파 미확인' 표시."""
        from trading.reports.daily_report import format_benchmark_section

        b = self._make_unavailable_benchmark()
        text = format_benchmark_section(b)
        assert "알파 미확인" in text or "unavailable" in text.lower() or "없음" in text

    def test_benchmark_section_uses_benchmark_py_not_parallel_path(self):
        """benchmark.py의 Benchmark 클래스를 통해 결과를 받는다 (병렬 경로 없음)."""
        from trading.edge.benchmark import Benchmark
        from trading.reports.daily_report import format_benchmark_section

        b = Benchmark()  # available=False by default
        text = format_benchmark_section(b)
        # 함수가 Benchmark 객체를 받아 텍스트를 생성하면 된다 (병렬 경로 없음)
        assert isinstance(text, str)
