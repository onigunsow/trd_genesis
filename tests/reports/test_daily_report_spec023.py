"""SPEC-TRADING-023 REQ-023-6: daily report auto-expansion integration.

The daily report (16:00 KST) must include a line listing today's auto-expansion
events. When no auto-expansion occurred, the line is suppressed.
"""

from __future__ import annotations


class TestDailyReportAutoExpansion:
    """REQ-023-6 (c, d): auto-expansion line appears in daily report."""

    def _make_data(self, auto_expansion_tickers: list[str] | None = None):
        """Minimal data dict mirroring _gather_today()."""
        return {
            "today": "2026-05-14",
            "orders": [],
            "runs": [],
            "risk": [],
            "cost": {
                "executed_count": 0,
                "exec_fee_total": 0,
                "attempted_fee_total": 0,
            },
            "cumulative": {
                "week_orders": 0,
                "month_orders": 0,
                "week_fee": 0,
                "month_fee": 0,
            },
            "tool_stats": {
                "total_calls": 0,
                "failures": 0,
                "persona_invocations": 0,
            },
            "reflection_stats": {
                "total_rounds": 0,
                "approved": 0,
                "rejected": 0,
                "withdrawn": 0,
            },
            "model_breakdown": [],
            "auto_expansion_tickers": auto_expansion_tickers or [],
        }

    def test_auto_expansion_line_present_when_events_exist(self):
        from trading.reports.daily_report import _fallback_text

        data = self._make_data(
            auto_expansion_tickers=["005935", "068270", "281820"]
        )
        text = _fallback_text(data)

        # REQ-023-6 (c): exact prefix + count + sorted tickers.
        assert "auto-expansion" in text
        assert "3건" in text
        # Sorted ascending by ticker code (acceptance scenario 6).
        assert "005935" in text
        assert "068270" in text
        assert "281820" in text
        # Sorted order: 005935 must come before 068270 in the text.
        assert text.index("005935") < text.index("068270") < text.index(
            "281820"
        )

    def test_auto_expansion_line_absent_when_no_events(self):
        """REQ-023-6 (c): zero events -> no line (preferred form)."""
        from trading.reports.daily_report import _fallback_text

        data = self._make_data(auto_expansion_tickers=[])
        text = _fallback_text(data)

        # Line is suppressed when zero events.
        assert "auto-expansion: 0" not in text
        assert "auto-expansion: 3건" not in text

    def test_auto_expansion_field_missing_does_not_crash(self):
        """Backward compat: existing data dicts (without the new field) must
        not raise."""
        from trading.reports.daily_report import _fallback_text

        data = self._make_data()
        data.pop("auto_expansion_tickers", None)

        # Should not raise.
        text = _fallback_text(data)
        assert isinstance(text, str)
