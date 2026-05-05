"""Tests for intelligence report generator."""

from datetime import date, datetime, timezone
from unittest.mock import patch

from trading.news.intelligence.reporter import (
    MIN_REPORT_IMPACT,
    _format_cluster_entry,
)


class TestFormatClusterEntry:
    def test_basic_format_with_arrows(self):
        cluster = {
            "representative_title": "US-Iran Hormuz Tensions Resume",
            "impact_max": 5,
            "source_count": 3,
            "portfolio_relevant": False,
            "first_published": datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc),
            "article_ids": [1, 2, 3],
            "keywords": ["유가", "중동", "안전자산"],
        }
        with patch(
            "trading.news.intelligence.reporter._get_cluster_summary",
            return_value="유가 3% 급등 + 안전자산 선호 강화.\n한국 수출주 하방 압력. 정유/가스 섹터 단기 수혜.",
        ):
            result = _format_cluster_entry(cluster)

        # Arrow-prefixed investment implications
        assert "\u2192 유가 3% 급등" in result
        assert "\u2192 한국 수출주 하방" in result
        # Impact in header
        assert "(Impact: 5/5)" in result
        # Keywords visible
        assert "Keywords: 유가, 중동, 안전자산" in result
        # Source count (not full names)
        assert "3 sources" in result

    def test_high_impact_gets_tag(self):
        cluster = {
            "representative_title": "Important News",
            "impact_max": 4,
            "source_count": 2,
            "portfolio_relevant": True,
            "first_published": datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc),
            "article_ids": [1, 2],
            "keywords": ["금리"],
        }
        with patch(
            "trading.news.intelligence.reporter._get_cluster_summary",
            return_value="Line 1\nLine 2",
        ):
            result = _format_cluster_entry(cluster)

        assert "[투자 주목]" in result

    def test_no_tag_for_impact_below_4(self):
        cluster = {
            "representative_title": "Low Impact",
            "impact_max": 3,
            "source_count": 1,
            "portfolio_relevant": True,
            "first_published": datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc),
            "article_ids": [1],
            "keywords": [],
        }
        with patch(
            "trading.news.intelligence.reporter._get_cluster_summary",
            return_value="Summary",
        ):
            result = _format_cluster_entry(cluster)

        assert "[투자 주목]" not in result

    def test_missing_summary_shows_pending(self):
        cluster = {
            "representative_title": "No Summary",
            "impact_max": 3,
            "source_count": 1,
            "portfolio_relevant": False,
            "first_published": datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc),
            "article_ids": [1],
            "keywords": ["반도체"],
        }
        with patch(
            "trading.news.intelligence.reporter._get_cluster_summary",
            return_value="",
        ):
            result = _format_cluster_entry(cluster)

        assert "(투자 시사점 분석 대기중)" in result

    def test_min_report_impact_is_3(self):
        assert MIN_REPORT_IMPACT == 3
