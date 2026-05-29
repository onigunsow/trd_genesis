"""Edge Validation — CLI 디스패치 + report.generate E2E 스모크."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

from trading.edge import report
from trading.edge.roundtrips import RoundTrip, RoundTripResult


def _rt(i, profit):
    d = date(2026, 1, 1) + timedelta(days=i)
    return RoundTrip(
        ticker="A", entry_date=d, exit_date=d + timedelta(days=1),
        qty=1, entry_price=10_000, exit_price=10_000 + profit,
        entry_fee=0, exit_fee=0, confidence=0.8, verdict="APPROVE",
    )


class TestCliDispatch:
    def test_edge_report_parses_flags_and_prints(self, capsys):
        from trading import cli

        with patch(
            "trading.edge.report.generate_and_send", return_value="REPORT-BODY"
        ) as gen:
            rc = cli.main(["edge-report", "--days", "90", "--telegram"])

        assert rc == 0
        gen.assert_called_once()
        args, kwargs = gen.call_args
        assert args[0] == 90
        assert kwargs["telegram"] is True
        assert kwargs["include_unrealized"] is False
        assert "REPORT-BODY" in capsys.readouterr().out

    def test_edge_report_invalid_days(self, capsys):
        from trading import cli

        rc = cli.main(["edge-report", "--days", "abc"])
        assert rc == 2

    def test_edge_snapshot_dispatch(self, capsys):
        from trading import cli

        with patch(
            "trading.edge.snapshot.record_snapshot",
            return_value={
                "trading_day": date(2026, 5, 29), "total_assets": 1_000_000,
                "stock_eval": 0, "cash": 1_000_000, "unrealized_pnl": 0,
            },
        ):
            rc = cli.main(["edge-snapshot"])
        assert rc == 0
        assert "equity_snapshot" in capsys.readouterr().out


class TestGenerateEndToEnd:
    def test_generate_renders_full_report(self):
        rts = [_rt(i, 2000.0) for i in range(40)]
        result = RoundTripResult(roundtrips=rts)
        closes = [(date(2026, 1, 1), 2500.0), (date(2026, 3, 1), 2550.0)]
        snaps = [(date(2026, 1, 1) + timedelta(days=i), 1_000_000 * (1.001 ** i)) for i in range(25)]

        with (
            patch("trading.edge.report._rt.compute_roundtrips", return_value=result),
            patch("trading.edge.report._bm.kospi_closes", return_value=closes),
            patch("trading.edge.report.load_equity_snapshots", return_value=snaps),
        ):
            text = report.generate(days=90)

        assert "엣지 검증 리포트" in text
        assert "판정:" in text
        assert "LLM 확신도 엣지" in text          # confidence 섹션
        assert "시간가중 지표 (캘린더" in text     # 25행 → 활성
        assert "한계" in text                      # 푸터 항상

    def test_generate_no_data(self):
        with (
            patch("trading.edge.report._rt.compute_roundtrips", return_value=RoundTripResult()),
            patch("trading.edge.report._bm.kospi_closes", return_value=[]),
            patch("trading.edge.report.load_equity_snapshots", return_value=[]),
        ):
            text = report.generate()
        assert "라운드트립이 없습니다" in text
        assert "한계" in text
