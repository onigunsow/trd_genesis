"""Tests for intelligence report generator."""

from datetime import date, datetime, timezone
from unittest.mock import patch

from trading.news.intelligence.reporter import _format_cluster_entry


class TestFormatClusterEntry:
    def test_basic_format(self):
        cluster = {
            "representative_title": "Test Article Title",
            "impact_max": 4,
            "source_count": 3,
            "portfolio_relevant": False,
            "first_published": datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc),
            "article_ids": [1, 2, 3],
        }
        with patch("trading.news.intelligence.reporter._get_cluster_summary", return_value="Summary line 1\nSummary line 2"):
            result = _format_cluster_entry(cluster)

        assert "### Test Article Title (Impact: 4/5)" in result
        assert "_Sources:" in result
        assert "- Summary line 1" in result
        assert "- Summary line 2" in result

    def test_portfolio_relevant_tag(self):
        cluster = {
            "representative_title": "Important News",
            "impact_max": 5,
            "source_count": 2,
            "portfolio_relevant": True,
            "first_published": datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc),
            "article_ids": [1, 2],
        }
        with patch("trading.news.intelligence.reporter._get_cluster_summary", return_value="Line 1\nLine 2"):
            result = _format_cluster_entry(cluster)

        assert "### [투자 주목] Important News (Impact: 5/5)" in result

    def test_no_tag_for_low_impact(self):
        cluster = {
            "representative_title": "Low Impact",
            "impact_max": 3,
            "source_count": 1,
            "portfolio_relevant": True,
            "first_published": datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc),
            "article_ids": [1],
        }
        with patch("trading.news.intelligence.reporter._get_cluster_summary", return_value="Summary"):
            result = _format_cluster_entry(cluster)

        assert "[투자 주목]" not in result

    def test_missing_summary_fallback(self):
        cluster = {
            "representative_title": "No Summary",
            "impact_max": 3,
            "source_count": 1,
            "portfolio_relevant": False,
            "first_published": datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc),
            "article_ids": [1],
        }
        with patch("trading.news.intelligence.reporter._get_cluster_summary", return_value=""):
            result = _format_cluster_entry(cluster)

        assert "(분석 데이터 없음)" in result
