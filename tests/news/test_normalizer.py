"""Tests for Module 4: Content Normalizer (SPEC-TRADING-013 AC-4-*)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from trading.news.normalizer import (
    Article,
    compute_content_hash,
    normalize_articles,
    normalize_title,
    truncate_summary,
)


def test_title_normalization_whitespace():
    """AC-4-3: Whitespace stripped and collapsed."""
    assert normalize_title("  Hello   World  ") == "Hello World"
    assert normalize_title("\n\tTitle\t\n") == "Title"


def test_title_normalization_html_entities():
    """AC-4-3: HTML entities decoded."""
    assert normalize_title("Samsung &amp; TSMC") == "Samsung & TSMC"
    assert normalize_title("Price &lt; $100") == "Price < $100"


def test_title_normalization_punctuation_artifacts():
    """AC-4-3: Leading/trailing punctuation artifacts removed."""
    assert normalize_title("·Title·") == "Title"
    assert normalize_title("—Breaking News—") == "Breaking News"


def test_content_hash_sha256():
    """AC-4-5: Hash equals SHA-256 of normalized title."""
    title = "Samsung Q1 Earnings Beat Expectations"
    expected = hashlib.sha256(title.encode("utf-8")).hexdigest()
    assert compute_content_hash(title) == expected


def test_deduplication_within_batch():
    """AC-4-4: Duplicate titles in same batch keep only first occurrence."""
    raw = [
        {"title": "Breaking News", "url": "http://a.com/1", "source_name": "A", "sector": "it_ai", "language": "en", "published_at": None},
        {"title": "Breaking News", "url": "http://b.com/2", "source_name": "B", "sector": "it_ai", "language": "en", "published_at": None},
    ]
    articles = normalize_articles(raw)
    assert len(articles) == 1
    assert articles[0].source_name == "A"


def test_date_inference():
    """AC-4-6: Missing published_at uses crawled_at with date_inferred=True."""
    crawled = datetime(2026, 5, 5, 10, 0, 0, tzinfo=timezone.utc)
    raw = [
        {"title": "No Date Article", "url": "http://x.com", "source_name": "X", "sector": "it_ai", "language": "en", "published_at": None},
    ]
    articles = normalize_articles(raw, crawled_at=crawled)
    assert len(articles) == 1
    assert articles[0].published_at == crawled
    assert articles[0].date_inferred is True


def test_date_preserved_when_available():
    """Existing published_at is preserved with date_inferred=False."""
    pub = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)
    crawled = datetime(2026, 5, 5, 10, 0, 0, tzinfo=timezone.utc)
    raw = [
        {"title": "Dated Article", "url": "http://x.com", "source_name": "X", "sector": "it_ai", "language": "en", "published_at": pub},
    ]
    articles = normalize_articles(raw, crawled_at=crawled)
    assert articles[0].published_at == pub
    assert articles[0].date_inferred is False


def test_summary_truncation_at_word_boundary():
    """AC-4-7: Summary > 500 chars truncated at word boundary."""
    long_summary = "word " * 200  # 1000 chars
    result = truncate_summary(long_summary, max_length=500)
    assert result is not None
    # Should end with "..." and be at or near 500 chars
    assert result.endswith("...")
    assert len(result) <= 504  # 500 + "..."


def test_summary_within_limit_preserved():
    """AC-4-8: Summary <= 500 chars preserved as-is."""
    short = "This is a short summary."
    assert truncate_summary(short) == short


def test_summary_none_preserved():
    """None summary stays None."""
    assert truncate_summary(None) is None


def test_summary_empty_becomes_none():
    """Empty or whitespace-only summary becomes None."""
    assert truncate_summary("") is None
    assert truncate_summary("   ") is None


def test_normalize_articles_empty_titles_skipped():
    """Articles with empty titles after normalization are skipped."""
    raw = [
        {"title": "", "url": "http://x.com", "source_name": "X", "sector": "it_ai", "language": "en", "published_at": None},
        {"title": "   ", "url": "http://x.com", "source_name": "X", "sector": "it_ai", "language": "en", "published_at": None},
    ]
    articles = normalize_articles(raw)
    assert len(articles) == 0


def test_normalize_articles_url_truncated():
    """Long URLs are truncated to 500 chars."""
    long_url = "http://x.com/" + "a" * 600
    raw = [
        {"title": "Test", "url": long_url, "source_name": "X", "sector": "it_ai", "language": "en", "published_at": None},
    ]
    articles = normalize_articles(raw)
    assert len(articles[0].url) <= 500
