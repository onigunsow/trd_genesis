"""Tests for news intelligence data models."""

from datetime import date, datetime, timezone

from trading.news.intelligence.models import AnalysisResult, StoryCluster, TrendEntry


class TestAnalysisResult:
    def test_create_basic(self):
        result = AnalysisResult(
            article_id=42,
            summary_2line="첫번째 줄\n두번째 줄",
            impact_score=4,
            keywords=["반도체", "수출"],
            sentiment="positive",
        )
        assert result.article_id == 42
        assert result.impact_score == 4
        assert result.sentiment == "positive"
        assert len(result.keywords) == 2

    def test_default_token_values(self):
        result = AnalysisResult(
            article_id=1,
            summary_2line="test",
            impact_score=3,
            keywords=[],
            sentiment="neutral",
        )
        assert result.token_input == 0
        assert result.token_output == 0
        assert result.cost_krw == 0.0


class TestStoryCluster:
    def test_create_with_defaults(self):
        cluster = StoryCluster()
        assert cluster.id is None
        assert cluster.article_ids == []
        assert cluster.portfolio_relevant is False
        assert cluster.relevance_tickers == []

    def test_create_full(self):
        now = datetime.now(timezone.utc)
        cluster = StoryCluster(
            id=1,
            representative_title="Test Title",
            article_ids=[1, 2, 3],
            source_count=3,
            impact_max=5,
            sector="semiconductor",
            keywords=["반도체", "수출"],
            sentiment_dominant="positive",
            first_published=now,
            cluster_date=date.today(),
            portfolio_relevant=True,
            relevance_tickers=["005930"],
        )
        assert cluster.source_count == 3
        assert cluster.impact_max == 5
        assert len(cluster.article_ids) == 3


class TestTrendEntry:
    def test_create(self):
        entry = TrendEntry(
            trend_date=date.today(),
            trend_type="daily",
            sector="semiconductor",
            keyword="반도체",
            mention_count=15,
            sentiment_positive=10,
            sentiment_neutral=3,
            sentiment_negative=2,
            sentiment_avg=0.53,
        )
        assert entry.mention_count == 15
        assert entry.trend_type == "daily"
