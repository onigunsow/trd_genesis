"""SPEC-TRADING-065 그룹 2 — since 파라미터·게이트 설정·표본 부족 신호."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from trading.dashboard import queries


class TestGateConfig:
    def test_env_missing_disables_gate(self, monkeypatch):
        monkeypatch.delenv("DASHBOARD_GATE_SINCE", raising=False)
        monkeypatch.delenv("DASHBOARD_GATE_MIN_N", raising=False)
        cfg = queries.gate_config()
        assert cfg["since"] is None
        assert cfg["min_n"] == queries._GATE_MIN_N_DEFAULT

    def test_env_iso_date_and_min_n(self, monkeypatch):
        monkeypatch.setenv("DASHBOARD_GATE_SINCE", "2026-08-17")
        monkeypatch.setenv("DASHBOARD_GATE_MIN_N", "7")
        cfg = queries.gate_config()
        assert cfg == {"since": "2026-08-17", "min_n": 7}

    def test_bad_date_disables_not_crashes(self, monkeypatch):
        monkeypatch.setenv("DASHBOARD_GATE_SINCE", "next monday")
        assert queries.gate_config()["since"] is None

    def test_no_hardcoded_gate_date_in_source(self):
        """운영자 [HARD]: 게이트 기준일은 코드 리터럴 금지."""
        import inspect
        src = inspect.getsource(queries)
        assert "2026-08-17" not in src


class TestParseSince:
    def test_valid(self):
        assert queries._parse_since("2026-08-17") == date(2026, 8, 17)

    def test_none_and_empty(self):
        assert queries._parse_since(None) is None
        assert queries._parse_since("") is None

    def test_invalid_falls_back_to_all(self):
        assert queries._parse_since("08/17/2026") is None


class TestScorecardSince:
    def _fake_rt(self, entry_dates):
        from trading.edge.roundtrips import RoundTrip, RoundTripResult
        rts = [RoundTrip(ticker="A", entry_date=d, exit_date=d, qty=1,
                         entry_price=100, exit_price=101, entry_fee=0, exit_fee=0,
                         confidence=None, verdict=None) for d in entry_dates]
        return RoundTripResult(roundtrips=rts)

    def _run(self, since, entry_dates, min_n=10):
        from trading.edge import roundtrips as _rt
        with (
            patch.object(_rt, "compute_roundtrips", return_value=self._fake_rt(entry_dates)),
            patch("trading.edge.report.load_equity_snapshots", return_value=[]),
            patch.object(queries, "gate_config", return_value={"since": None, "min_n": min_n}),
        ):
            return queries.fetch_scorecard_with_sortino(since=since)

    def test_since_filters_by_entry_date_and_echoes(self):
        dates = [date(2026, 8, 10)] * 5 + [date(2026, 8, 18)] * 3
        out = self._run("2026-08-17", dates)
        assert out["n_closed"] == 3
        assert out["since"] == "2026-08-17"

    def test_low_sample_flag_when_below_min_n(self):
        dates = [date(2026, 8, 18)] * 3
        out = self._run("2026-08-17", dates, min_n=10)
        assert out["low_sample"] is True
        assert out["gate_min_n"] == 10

    def test_low_sample_false_when_enough(self):
        dates = [date(2026, 8, 18)] * 12
        assert self._run("2026-08-17", dates, min_n=10)["low_sample"] is False

    def test_no_since_never_low_sample(self):
        """전기간 조회는 표본 부족 개념이 없다 — 토글 꺼진 상태를 오염시키지 않는다."""
        out = self._run(None, [date(2026, 8, 18)] * 2, min_n=10)
        assert out["since"] is None
        assert out["low_sample"] is False
