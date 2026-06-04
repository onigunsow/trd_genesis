"""SPEC-TRADING-041 REQ-041-1 — stock-name enrichment in system alerts.

``_ticker_label(ticker)`` is a tiny pure display helper used at the 4
``tg.system_briefing()`` call sites (pre_market trim/breach, intraday
trim/breach). It returns ``"코드 이름"`` when ``ticker_name`` resolves a name,
and degrades to ``"코드"`` (code only) when the name is None — never crashes,
never renders the literal "None" (AC-1.1, AC-1.2, REQ-041-4a).
"""

from __future__ import annotations

from unittest.mock import patch


class TestTickerLabel:
    def test_resolves_name(self):
        """AC-1.1: a resolvable ticker → '코드 이름'."""
        from trading.personas import orchestrator

        with patch.object(orchestrator, "ticker_name", return_value="현대로템"):
            assert orchestrator._ticker_label("064350") == "064350 현대로템"

    def test_none_name_falls_back_to_code(self):
        """AC-1.2 / REQ-041-4a: ticker_name None → code only, no 'None'."""
        from trading.personas import orchestrator

        with patch.object(orchestrator, "ticker_name", return_value=None):
            label = orchestrator._ticker_label("064350")
            assert label == "064350"
            assert "None" not in label

    def test_empty_name_falls_back_to_code(self):
        """An empty string name also degrades to code only."""
        from trading.personas import orchestrator

        with patch.object(orchestrator, "ticker_name", return_value=""):
            assert orchestrator._ticker_label("064350") == "064350"

    def test_ticker_name_raises_falls_back_to_code(self):
        """Even if ticker_name raises, the label degrades gracefully."""
        from trading.personas import orchestrator

        with patch.object(orchestrator, "ticker_name", side_effect=RuntimeError("boom")):
            assert orchestrator._ticker_label("064350") == "064350"
