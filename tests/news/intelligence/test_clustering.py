"""Tests for story clustering module."""

from datetime import datetime, timedelta, timezone

from trading.news.intelligence.clustering import (
    _compute_dominant_sentiment,
    _find_clusters,
    _keyword_overlap,
    _normalize_title_for_comparison,
    _should_cluster,
    _title_similarity,
)


class TestTitleNormalization:
    def test_lowercase(self):
        assert _normalize_title_for_comparison("HELLO World") == "hello world"

    def test_strips_punctuation(self):
        result = _normalize_title_for_comparison("U.S.-Korea trade deal!")
        assert "." not in result
        assert "-" not in result
        assert "!" not in result

    def test_collapses_whitespace(self):
        assert _normalize_title_for_comparison("a   b    c") == "a b c"


class TestTitleSimilarity:
    def test_identical_titles(self):
        assert _title_similarity("Hello World", "Hello World") == 1.0

    def test_similar_korean_titles(self):
        a = "Samsung Electronics posts record quarterly profits"
        b = "Samsung Electronics quarterly profits hit record high"
        sim = _title_similarity(a, b)
        assert sim > 0.6  # Should cluster

    def test_unrelated_titles(self):
        a = "Samsung reports record profits in Q1 earnings"
        b = "Weather forecast for next week shows rain"
        sim = _title_similarity(a, b)
        assert sim < 0.5  # Well below clustering threshold of 0.6


class TestKeywordOverlap:
    def test_full_overlap(self):
        assert _keyword_overlap(["a", "b", "c"], ["a", "b", "c"]) == 3

    def test_partial_overlap(self):
        assert _keyword_overlap(["semiconductor", "export", "samsung"], ["samsung", "semiconductor", "earnings"]) == 2

    def test_no_overlap(self):
        assert _keyword_overlap(["a", "b"], ["c", "d"]) == 0

    def test_empty_lists(self):
        assert _keyword_overlap([], []) == 0


class TestShouldCluster:
    def _make_article(self, title, keywords=None, hours_ago=0):
        pub_time = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        return {
            "title": title,
            "keywords": keywords or [],
            "published_at": pub_time,
        }

    def test_similar_titles_same_day(self):
        a = self._make_article("Samsung semiconductor revenue hits all-time high", hours_ago=1)
        b = self._make_article("Samsung semiconductor revenue reaches record level", hours_ago=2)
        assert _should_cluster(a, b) is True

    def test_keyword_overlap_clusters(self):
        a = self._make_article("Article A", keywords=["semiconductor", "export", "samsung"], hours_ago=1)
        b = self._make_article("Article B", keywords=["samsung", "semiconductor", "earnings"], hours_ago=2)
        assert _should_cluster(a, b) is True

    def test_outside_time_window_no_cluster(self):
        a = self._make_article("Same Title Exactly", hours_ago=0)
        b = self._make_article("Same Title Exactly", hours_ago=25)  # > 24h
        assert _should_cluster(a, b) is False

    def test_different_titles_no_keywords_no_cluster(self):
        a = self._make_article("Completely different topic about baseball", hours_ago=1)
        b = self._make_article("Another unrelated article on cooking recipes", hours_ago=2)
        assert _should_cluster(a, b) is False


class TestFindClusters:
    def _make_articles(self, titles_and_keywords):
        now = datetime.now(timezone.utc)
        articles = []
        for i, (title, keywords) in enumerate(titles_and_keywords):
            articles.append({
                "title": title,
                "keywords": keywords,
                "published_at": now - timedelta(hours=i),
            })
        return articles

    def test_two_similar_articles_cluster(self):
        articles = self._make_articles([
            ("Samsung semiconductor revenue record high", ["semiconductor", "samsung", "revenue"]),
            ("Samsung semiconductor revenue hits record", ["semiconductor", "samsung", "earnings"]),
        ])
        clusters = _find_clusters(articles)
        assert len(clusters) == 1
        assert len(clusters[0]) == 2

    def test_unrelated_articles_separate(self):
        articles = self._make_articles([
            ("Samsung profits soar this quarter", ["samsung", "profit"]),
            ("Weather report for the upcoming week", ["weather", "forecast"]),
        ])
        clusters = _find_clusters(articles)
        assert len(clusters) == 2

    def test_transitive_clustering(self):
        # A~B (kw overlap >= 2) and B~C (kw overlap >= 2) -> cluster [A,B,C]
        articles = self._make_articles([
            ("Semiconductor export boom continues", ["semiconductor", "export", "boom"]),
            ("Semiconductor export hits record", ["semiconductor", "export", "record"]),
            ("Semiconductor record growth expected", ["semiconductor", "record", "growth"]),
        ])
        clusters = _find_clusters(articles)
        # All three should be in same cluster due to transitive keyword overlap
        assert len(clusters) == 1
        assert len(clusters[0]) == 3


class TestDominantSentiment:
    def test_majority_positive(self):
        assert _compute_dominant_sentiment(["positive", "positive", "neutral"]) == "positive"

    def test_majority_negative(self):
        assert _compute_dominant_sentiment(["negative", "negative", "positive"]) == "negative"

    def test_empty_returns_neutral(self):
        assert _compute_dominant_sentiment([]) == "neutral"

    def test_tie_returns_first_most_common(self):
        # Counter.most_common returns first item for ties
        result = _compute_dominant_sentiment(["positive", "negative"])
        assert result in ("positive", "negative")
