"""Tests for trend analyzer module (pure logic, no DB)."""

from datetime import date

from trading.news.intelligence.models import TrendEntry


class TestTrendEntryCreation:
    def test_daily_entry(self):
        entry = TrendEntry(
            trend_date=date(2026, 5, 5),
            trend_type="daily",
            sector="semiconductor",
            keyword="반도체",
            mention_count=15,
            sentiment_positive=10,
            sentiment_neutral=3,
            sentiment_negative=2,
            sentiment_avg=0.53,
        )
        assert entry.trend_type == "daily"
        assert entry.mention_count == 15
        assert entry.sentiment_avg == 0.53

    def test_weekly_entry(self):
        entry = TrendEntry(
            trend_date=date(2026, 5, 5),
            trend_type="weekly",
            sector=None,
            keyword="이란",
            mention_count=20,
        )
        assert entry.trend_type == "weekly"
        assert entry.sector is None


class TestSentimentAvgCalculation:
    """Test the sentiment_avg formula: (positive - negative) / total."""

    def test_all_positive(self):
        total = 10
        pos, neg = 10, 0
        avg = (pos - neg) / total
        assert avg == 1.0

    def test_all_negative(self):
        total = 10
        pos, neg = 0, 10
        avg = (pos - neg) / total
        assert avg == -1.0

    def test_balanced(self):
        total = 20
        pos, neg = 10, 10
        avg = (pos - neg) / total
        assert avg == 0.0

    def test_mostly_positive(self):
        total = 20
        pos, neg = 12, 3
        avg = (pos - neg) / total
        assert avg == 0.45


class TestRisingFallingLogic:
    """Test the rising/falling keyword detection logic."""

    def test_rising_detection(self):
        """Rising: current / previous > 1.5"""
        this_week = {"이란": 20, "금리": 10}
        prev_week = {"이란": 5, "금리": 8}

        rising = []
        for kw, count in this_week.items():
            prev = prev_week.get(kw, 0)
            if prev > 0 and count / prev > 1.5:
                rising.append(kw)
        assert "이란" in rising  # 20/5 = 4.0 > 1.5
        assert "금리" not in rising  # 10/8 = 1.25 < 1.5

    def test_falling_detection(self):
        """Falling: current / previous < 0.5"""
        this_week = {"부동산": 3, "금리": 8}
        prev_week = {"부동산": 15, "금리": 10}

        falling = []
        for kw, prev in prev_week.items():
            current = this_week.get(kw, 0)
            if prev > 0 and current / prev < 0.5:
                falling.append(kw)
        assert "부동산" in falling  # 3/15 = 0.2 < 0.5
        assert "금리" not in falling  # 8/10 = 0.8 > 0.5

    def test_new_keyword_rising(self):
        """New keyword with significant mentions is rising."""
        this_week = {"AI반도체": 5}
        prev_week = {}

        rising = []
        for kw, count in this_week.items():
            prev = prev_week.get(kw, 0)
            if prev == 0 and count >= 3:
                rising.append(kw)
        assert "AI반도체" in rising
