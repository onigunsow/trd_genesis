"""Tests for daily report extensions (SPEC-009 Phase D TASK-013).

Tests cover:
- REQ-PTOOL-02-9: Tool usage summary line in daily report
- REQ-REFL-03-7: Reflection summary line in daily report
- REQ-NFR-09-4: Observability metrics (tool_calls_total, tool_failures, etc.)
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


class TestDailyReportFallbackText:
    """Test _fallback_text includes tool and reflection stats."""

    def _make_data(self, tool_stats=None, reflection_stats=None):
        """Create minimal report data dict."""
        return {
            "today": "2026-05-05",
            "orders": [
                {"id": 1, "side": "buy", "ticker": "005930", "qty": 5,
                 "status": "filled", "fill_price": 80000, "fill_qty": 5, "fee": 60, "mode": "paper"},
            ],
            "runs": [
                {"persona_name": "micro", "n": 1, "cost": 15.5,
                 "in_tok": 2000, "cache_read": 500, "cache_create": 100},
                {"persona_name": "decision", "n": 1, "cost": 12.3,
                 "in_tok": 1500, "cache_read": 300, "cache_create": 50},
            ],
            "risk": [{"verdict": "APPROVE", "n": 1}],
            "cost": {"executed_count": 1, "exec_fee_total": 60, "attempted_fee_total": 60},
            "cumulative": {"week_orders": 5, "month_orders": 20, "week_fee": 300, "month_fee": 1200},
            "tool_stats": tool_stats or {"total_calls": 0, "failures": 0, "persona_invocations": 0},
            "reflection_stats": reflection_stats or {
                "total_rounds": 0, "approved": 0, "rejected": 0, "withdrawn": 0
            },
        }

    def test_tool_usage_line_present(self):
        """REQ-PTOOL-02-9: Report includes tool usage summary."""
        from trading.reports.daily_report import _fallback_text

        data = self._make_data(
            tool_stats={"total_calls": 12, "failures": 2, "persona_invocations": 4}
        )
        text = _fallback_text(data)

        assert "Tool" in text
        assert "12" in text
        assert "3.0" in text  # 12/4 = 3.0 avg
        assert "2" in text  # failures

    def test_tool_usage_line_format(self):
        """Exact format: 'Tool 호출: 총 X회, 평균 Y회/페르소나, 실패 Z건'."""
        from trading.reports.daily_report import _fallback_text

        data = self._make_data(
            tool_stats={"total_calls": 20, "failures": 1, "persona_invocations": 5}
        )
        text = _fallback_text(data)

        assert "Tool 호출: 총 20회, 평균 4.0회/페르소나, 실패 1건" in text

    def test_reflection_line_present(self):
        """REQ-REFL-03-7: Report includes reflection summary."""
        from trading.reports.daily_report import _fallback_text

        data = self._make_data(
            reflection_stats={"total_rounds": 3, "approved": 2, "rejected": 1, "withdrawn": 0}
        )
        text = _fallback_text(data)

        assert "Reflection" in text
        assert "시도 3건" in text
        assert "성공(APPROVE) 2건" in text
        assert "최종 REJECT 1건" in text
        assert "철회 0건" in text

    def test_observability_metrics(self):
        """REQ-NFR-09-4: Observability metrics included."""
        from trading.reports.daily_report import _fallback_text

        data = self._make_data(
            tool_stats={"total_calls": 10, "failures": 3, "persona_invocations": 4},
            reflection_stats={"total_rounds": 2, "approved": 1, "rejected": 1, "withdrawn": 0},
        )
        text = _fallback_text(data)

        assert "tool_calls_total=10" in text
        assert "tool_failures=3" in text
        assert "reflection_rounds=2" in text
        assert "reflection_success_rate=50.0%" in text

    def test_zero_state_graceful(self):
        """Zero values should display cleanly (no divisions by zero)."""
        from trading.reports.daily_report import _fallback_text

        data = self._make_data()  # All zeros
        text = _fallback_text(data)

        assert "Tool 호출: 총 0회, 평균 0.0회/페르소나, 실패 0건" in text
        assert "Reflection: 시도 0건, 성공(APPROVE) 0건, 최종 REJECT 0건, 철회 0건" in text
        assert "reflection_success_rate=0.0%" in text

    def test_missing_tool_stats_graceful(self):
        """If tool_stats is None (table not yet created), show zeros."""
        from trading.reports.daily_report import _fallback_text

        data = self._make_data()
        data["tool_stats"] = None
        data["reflection_stats"] = None
        text = _fallback_text(data)

        assert "Tool 호출: 총 0회" in text
        assert "Reflection: 시도 0건" in text
