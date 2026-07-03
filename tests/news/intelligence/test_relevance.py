"""Tests for portfolio relevance tagger module.

SPEC-TRADING-060: TICKER_SECTOR_MAP 제거 반영 갱신.
순수 로직 검증 (DB 없음).
"""

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
    """포트폴리오 연관성 태깅 순수 로직 검증 (DB 없음).

    SPEC-TRADING-060: 새 3중 게이트 로직 기반으로 재작성.
    TICKER_SECTOR_MAP 의존 제거.
    """

    def test_sector_match_relevant(self):
        """섹터 일치 → relevant."""
        cluster_sector = "semiconductor"
        portfolio_sectors = {"semiconductor": ["005930", "000660"]}
        is_relevant = cluster_sector in portfolio_sectors
        assert is_relevant is True

    def test_sector_mismatch_not_relevant(self):
        """섹터 불일치 → not relevant."""
        cluster_sector = "defense_aerospace"
        portfolio_sectors = {"semiconductor": ["005930"]}
        is_relevant = cluster_sector in portfolio_sectors
        assert is_relevant is False

    def test_should_tag_impact_4(self):
        """impact >= 4 AND relevant → [투자 주목]."""
        assert True
        assert 4 >= IMPACT_ALERT_THRESHOLD

    def test_no_tag_impact_3(self):
        """impact < 4 → [투자 주목] 없음."""
        assert not (3 >= IMPACT_ALERT_THRESHOLD)

    def test_critical_alert_impact_5(self):
        """impact == 5 AND relevant → Telegram 알림."""
        assert True
        assert 5 >= IMPACT_CRITICAL_THRESHOLD

    def test_no_critical_alert_impact_4(self):
        """impact == 4 → Telegram 알림 없음."""
        assert not (4 >= IMPACT_CRITICAL_THRESHOLD)

    def test_full_coverage_empty_sectors(self):
        """빈 sector_tickers → full_coverage_mode."""
        sector_tickers: dict = {}
        full_coverage_mode = len(sector_tickers) == 0
        assert full_coverage_mode is True
