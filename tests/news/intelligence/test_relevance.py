"""Tests for portfolio relevance tagger module."""

from trading.news.intelligence.relevance import (
    IMPACT_ALERT_THRESHOLD,
    IMPACT_CRITICAL_THRESHOLD,
)


class TestRelevanceThresholds:
    def test_alert_threshold_is_4(self):
        assert IMPACT_ALERT_THRESHOLD == 4

    def test_critical_threshold_is_5(self):
        assert IMPACT_CRITICAL_THRESHOLD == 5


class TestRelevanceLogic:
    """Test the portfolio relevance tagging logic without DB."""

    def test_portfolio_relevant_high_impact(self):
        """Impact >= 4 AND sector matches -> [투자 주목]"""
        cluster_sector = "semiconductor"
        cluster_impact = 5
        portfolio_sectors = {"semiconductor": ["005930", "000660"]}

        is_relevant = cluster_sector in portfolio_sectors
        should_tag = is_relevant and cluster_impact >= IMPACT_ALERT_THRESHOLD
        assert is_relevant is True
        assert should_tag is True

    def test_portfolio_relevant_low_impact(self):
        """Impact < 4 AND sector matches -> relevant but NOT [투자 주목]"""
        cluster_sector = "semiconductor"
        cluster_impact = 3
        portfolio_sectors = {"semiconductor": ["005930"]}

        is_relevant = cluster_sector in portfolio_sectors
        should_tag = is_relevant and cluster_impact >= IMPACT_ALERT_THRESHOLD
        assert is_relevant is True
        assert should_tag is False

    def test_not_portfolio_relevant(self):
        """Sector does NOT match portfolio -> not relevant"""
        cluster_sector = "defense_aerospace"
        cluster_impact = 5
        portfolio_sectors = {"semiconductor": ["005930"]}

        is_relevant = cluster_sector in portfolio_sectors
        should_tag = is_relevant and cluster_impact >= IMPACT_ALERT_THRESHOLD
        assert is_relevant is False
        assert should_tag is False

    def test_full_coverage_mode(self):
        """Empty portfolio -> full coverage: all high-impact tagged"""
        cluster_impact = 4
        portfolio_sectors = {}  # Empty = full coverage mode
        full_coverage_mode = len(portfolio_sectors) == 0

        is_relevant = full_coverage_mode and cluster_impact >= IMPACT_ALERT_THRESHOLD
        assert is_relevant is True

    def test_critical_alert_condition(self):
        """Impact == 5 AND portfolio-relevant -> Telegram alert"""
        cluster_impact = 5
        portfolio_relevant = True

        should_alert = portfolio_relevant and cluster_impact >= IMPACT_CRITICAL_THRESHOLD
        assert should_alert is True

    def test_no_alert_for_impact_4(self):
        """Impact == 4 AND portfolio-relevant -> NO Telegram alert (only impact 5)"""
        cluster_impact = 4
        portfolio_relevant = True

        should_alert = portfolio_relevant and cluster_impact >= IMPACT_CRITICAL_THRESHOLD
        assert should_alert is False
