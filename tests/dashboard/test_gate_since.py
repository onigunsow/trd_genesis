"""SPEC-TRADING-065 그룹 2 — since 파라미터·게이트 설정·표본 부족 신호."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from trading.dashboard import queries


class TestGateConfig:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch):
        """env 미설정 + audit ACCOUNT_SWITCH 없음 이 기본. 캐시는 매 테스트 비운다."""
        queries._gate_cache.clear()
        monkeypatch.delenv("DASHBOARD_GATE_SINCE", raising=False)
        monkeypatch.delenv("DASHBOARD_GATE_MIN_N", raising=False)
        with patch.object(queries, "_account_switch_since", return_value=None):
            yield
        queries._gate_cache.clear()

    def test_env_missing_and_no_switch_disables_gate(self):
        cfg = queries.gate_config()
        assert cfg["since"] is None
        assert cfg["source"] is None
        assert cfg["min_n"] == queries._GATE_MIN_N_DEFAULT

    def test_env_iso_date_and_min_n(self, monkeypatch):
        monkeypatch.setenv("DASHBOARD_GATE_SINCE", "2026-08-17")
        monkeypatch.setenv("DASHBOARD_GATE_MIN_N", "7")
        cfg = queries.gate_config()
        assert cfg == {"since": "2026-08-17", "min_n": 7, "source": "env"}

    def test_bad_date_disables_not_crashes(self, monkeypatch):
        monkeypatch.setenv("DASHBOARD_GATE_SINCE", "next monday")
        assert queries.gate_config()["since"] is None

    def test_account_switch_fallback_when_env_missing(self):
        """모의계좌 리셋(ACCOUNT_SWITCH) 이 있으면 env 없이도 그 경계가 since."""
        with patch.object(queries, "_account_switch_since", return_value="2026-08-08"):
            cfg = queries.gate_config()
        assert cfg["since"] == "2026-08-08"
        assert cfg["source"] == "account_switch"

    def test_env_wins_over_account_switch(self, monkeypatch):
        monkeypatch.setenv("DASHBOARD_GATE_SINCE", "2026-08-17")
        with patch.object(queries, "_account_switch_since", return_value="2026-08-08"):
            assert queries.gate_config()["since"] == "2026-08-17"


class TestAccountSwitchSince:
    def _row(self, ts, details):
        return {"ts": ts, "details": details}

    def test_closeout_date_plus_one(self):
        from datetime import UTC, datetime
        row = self._row(datetime(2026, 8, 8, 16, tzinfo=UTC), {"closeout_date": "2026-08-07"})
        with patch.object(queries, "ro_connection") as ro:
            cur = ro.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cur.fetchone.return_value = row
            assert queries._account_switch_since() == "2026-08-08"

    def test_no_closeout_falls_back_to_event_date(self):
        from datetime import UTC, datetime
        row = self._row(datetime(2026, 8, 9, 1, tzinfo=UTC), {})
        with patch.object(queries, "ro_connection") as ro:
            cur = ro.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cur.fetchone.return_value = row
            assert queries._account_switch_since() == "2026-08-09"

    def test_no_event_returns_none(self):
        with patch.object(queries, "ro_connection") as ro:
            cur = ro.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cur.fetchone.return_value = None
            assert queries._account_switch_since() is None

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

    def test_low_sample_fires_without_since_too(self):
        """2026-08-23 정정: 표본 부족은 필터 여부와 무관한 사실이다.

        종전엔 `bool(since_d) and ...` 이라 게이트 토글이 꺼져 있으면 왕복 2건짜리
        PF 도 표본 부족 표시 없이 나갔다.
        """
        out = self._run(None, [date(2026, 8, 18)] * 2, min_n=10)
        assert out["since"] is None
        assert out["low_sample"] is True
