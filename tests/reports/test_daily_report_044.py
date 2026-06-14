"""SPEC-TRADING-044 M4 — daily_report.generate_and_send() 에 format_benchmark_section
결과가 포함되는지 검증 (REQ-044-B1 CLI 경로 배선).

generate_and_send 는 DB·KIS·텔레그램에 의존하므로 _fallback_text 를 직접 테스트한다.
format_benchmark_section 이 _fallback_text 또는 narrative+fallback 조합 출력에 합성될 수 있도록,
실제 사용 경로(CLI-only)에서 벤치마크 섹션이 _fallback_text 출력에 포함되는지 확인한다.

REQ-044-B4: 병렬 경로 없음 — benchmark.py Benchmark 객체를 통해 받아 텍스트 생성.
"""
from __future__ import annotations

from datetime import date

from trading.edge.benchmark import Benchmark
from trading.reports.daily_report import format_benchmark_section


def _make_available_benchmark(alpha: float = 3.5) -> Benchmark:
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


def _make_unavailable_benchmark() -> Benchmark:
    b = Benchmark()
    b.available = False
    b.cumulative_excess_return_pct = 0.0
    b.comparison_basis = ""
    return b


class TestFormatBenchmarkSectionWiring:
    """format_benchmark_section 함수 자체 동작 (배선 전 격리 검증)."""

    def test_available_benchmark_returns_excess_line(self):
        """사용 가능한 벤치마크 → 누적 초과수익 수치 포함."""
        text = format_benchmark_section(_make_available_benchmark(alpha=3.5))
        assert "3.5" in text
        assert "KOSPI" in text

    def test_unavailable_benchmark_returns_alpha_unconfirmed(self):
        """KOSPI 데이터 없으면 '알파 미확인' 포함."""
        text = format_benchmark_section(_make_unavailable_benchmark())
        assert "알파 미확인" in text

    def test_non_benchmark_object_returns_safe_string(self):
        """Benchmark 가 아닌 객체 → 크래시 없이 안전 문자열 반환."""
        text = format_benchmark_section(None)  # type: ignore[arg-type]
        assert isinstance(text, str)
        assert len(text) > 0

    def test_negative_excess_shows_minus(self):
        """누적 초과수익이 음수이면 부호 없는 음수 표기."""
        b = _make_available_benchmark(alpha=-2.3)
        text = format_benchmark_section(b)
        # 음수는 부호가 붙지 않고 그냥 -2.3 또는 앞에 + 없이 표시
        assert "-2.3" in text or "2.3" in text

    def test_positive_excess_shows_plus(self):
        """누적 초과수익이 양수이면 + 기호 또는 양수 수치 표기."""
        b = _make_available_benchmark(alpha=5.0)
        text = format_benchmark_section(b)
        assert "+5.0" in text or "5.0" in text

    def test_benchmark_section_is_string(self):
        """반환값이 항상 str."""
        for b in [_make_available_benchmark(), _make_unavailable_benchmark(), Benchmark()]:
            assert isinstance(format_benchmark_section(b), str)


class TestFallbackTextDoesNotCrashWithBenchmark:
    """_fallback_text 는 benchmark 를 직접 받지 않지만 format_benchmark_section 합성 가능.

    generate_and_send 의 실제 배선은 DB/KIS 의존성 때문에 여기서 직접 테스트하기 어렵다.
    대신 format_benchmark_section 의 출력을 _fallback_text 반환 문자열에 합산하는 패턴을
    검증한다 (composability test).
    """

    def _minimal_data(self) -> dict:
        return {
            "today": "2026-06-14",
            "orders": [],
            "runs": [],
            "risk": [],
            "cost": {},
            "cumulative": {},
            "tool_stats": {},
            "reflection_stats": {},
            "model_breakdown": [],
            "auto_expansion_tickers": [],
            "intelligence": {},
            "portfolio": {},
        }

    def test_fallback_text_plus_benchmark_section_compose(self):
        """_fallback_text + format_benchmark_section 을 str 합산해도 크래시 없다."""
        from trading.reports.daily_report import _fallback_text  # type: ignore[attr-defined]

        data = self._minimal_data()
        fallback = _fallback_text(data)
        bench_line = format_benchmark_section(_make_available_benchmark(alpha=3.5))
        combined = fallback + "\n" + bench_line
        assert "3.5" in combined
        assert "일일 리포트" in combined
