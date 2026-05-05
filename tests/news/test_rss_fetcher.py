"""Tests for Module 2: RSS Fetcher (SPEC-TRADING-013 AC-2-*)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from trading.news.rss_fetcher import (
    BODY_SELECTORS,
    DESCRIPTION_AS_BODY_THRESHOLD,
    MAX_BODY_FETCH_PER_SOURCE,
    RSSFetcher,
    USER_AGENT,
)
from trading.news.sources import NewsSource


@pytest.fixture
def sample_rss_source() -> NewsSource:
    return NewsSource(
        name="Test Feed",
        url="https://example.com/feed.xml",
        source_type="rss",
        sector="it_ai",
        language="en",
    )


@pytest.fixture
def valid_rss_xml() -> bytes:
    return b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
        <channel>
            <title>Test Feed</title>
            <item>
                <title>Article One</title>
                <link>https://example.com/1</link>
                <pubDate>Mon, 05 May 2026 10:00:00 +0000</pubDate>
                <description>Summary of article one</description>
            </item>
            <item>
                <title>Article Two</title>
                <link>https://example.com/2</link>
                <pubDate>Mon, 05 May 2026 09:00:00 +0000</pubDate>
            </item>
        </channel>
    </rss>"""


def test_user_agent_header():
    """AC-2-7: User-Agent is set correctly."""
    assert USER_AGENT == "trading-bot/0.2 (personal use; non-commercial)"


def test_parse_feedparser_valid(valid_rss_xml, sample_rss_source):
    """AC-2-1: Valid RSS returns extracted articles."""
    fetcher = RSSFetcher()
    items = fetcher._parse_feedparser(valid_rss_xml, sample_rss_source)
    assert items is not None
    assert len(items) == 2
    assert items[0]["title"] == "Article One"
    assert items[0]["url"] == "https://example.com/1"
    assert items[0]["source_name"] == "Test Feed"
    assert items[0]["sector"] == "it_ai"
    assert items[0]["language"] == "en"


def test_parse_feedparser_empty():
    """Unparseable content returns None for fallback."""
    fetcher = RSSFetcher()
    source = NewsSource("X", "http://x.com", "rss", "it_ai", "en")
    result = fetcher._parse_feedparser(b"not xml at all", source)
    assert result is None


def test_parse_etree_fallback(valid_rss_xml, sample_rss_source):
    """AC-2-6: ElementTree fallback parses valid RSS."""
    fetcher = RSSFetcher()
    items = fetcher._parse_etree_fallback(valid_rss_xml, sample_rss_source)
    assert items is not None
    assert len(items) == 2
    assert items[0]["title"] == "Article One"


def test_parse_etree_fallback_invalid():
    """ElementTree returns None for completely invalid content."""
    fetcher = RSSFetcher()
    source = NewsSource("X", "http://x.com", "rss", "it_ai", "en")
    result = fetcher._parse_etree_fallback(b"not xml", source)
    assert result is None


def test_parse_date_formats():
    """Date parser handles multiple common formats."""
    fetcher = RSSFetcher()

    # RFC 2822
    dt = fetcher._parse_date_str("Mon, 05 May 2026 10:00:00 +0000")
    assert dt is not None
    assert dt.year == 2026

    # ISO 8601
    dt = fetcher._parse_date_str("2026-05-05T10:00:00Z")
    assert dt is not None

    # Simple date
    dt = fetcher._parse_date_str("2026-05-05")
    assert dt is not None

    # Empty string
    assert fetcher._parse_date_str("") is None


def test_missing_published_date(sample_rss_source):
    """AC-2-8: Entry without pubDate still extracted with published_at=None."""
    rss_xml = b"""<?xml version="1.0"?>
    <rss version="2.0">
        <channel>
            <item>
                <title>No Date Article</title>
                <link>https://example.com/nodate</link>
            </item>
        </channel>
    </rss>"""
    fetcher = RSSFetcher()
    items = fetcher._parse_feedparser(rss_xml, sample_rss_source)
    assert items is not None
    assert len(items) == 1
    assert items[0]["published_at"] is None


# --- Article Body Extraction Tests ---


class TestExtractBodyFromPage:
    """Characterization tests for _extract_body_from_page static method."""

    def test_extracts_from_article_body_class(self):
        """Extracts body text from article .article-body selector."""
        html = """
        <html><body>
        <article>
            <div class="article-body">
                <p>First paragraph of the article with enough content.</p>
                <p>Second paragraph continues the story with more details.</p>
            </div>
        </article>
        </body></html>
        """
        result = RSSFetcher._extract_body_from_page(html)
        assert result is not None
        assert "First paragraph" in result
        assert "Second paragraph" in result

    def test_extracts_from_itemprop_articleBody(self):
        """Extracts body from [itemprop='articleBody'] selector."""
        html = """
        <html><body>
        <div itemprop="articleBody">
            <p>Article body content with itemprop attribute for semantic markup.</p>
            <p>Additional paragraph with more meaningful content here.</p>
        </div>
        </body></html>
        """
        result = RSSFetcher._extract_body_from_page(html)
        assert result is not None
        assert "itemprop attribute" in result

    def test_extracts_from_entry_content(self):
        """Extracts body from .entry-content selector (WordPress-style)."""
        html = """
        <html><body>
        <div class="entry-content">
            <p>WordPress-style entry content that contains the full article text.</p>
            <p>This is the continuation of the article with multiple paragraphs.</p>
        </div>
        </body></html>
        """
        result = RSSFetcher._extract_body_from_page(html)
        assert result is not None
        assert "WordPress-style" in result

    def test_extracts_from_article_p_fallback(self):
        """Extracts from 'article p' selector as fallback."""
        html = """
        <html><body>
        <article>
            <p>First paragraph directly inside article element with enough text.</p>
            <p>Second paragraph also directly in article for body extraction.</p>
        </article>
        </body></html>
        """
        result = RSSFetcher._extract_body_from_page(html)
        assert result is not None
        assert "First paragraph" in result

    def test_returns_none_for_short_content(self):
        """Returns None when extracted text is under 50 chars."""
        html = """
        <html><body>
        <article><p>Short.</p></article>
        </body></html>
        """
        result = RSSFetcher._extract_body_from_page(html)
        assert result is None

    def test_returns_none_for_no_matching_selectors(self):
        """Returns None when no selectors match."""
        html = """
        <html><body>
        <div class="sidebar">Not article content here.</div>
        </body></html>
        """
        result = RSSFetcher._extract_body_from_page(html)
        assert result is None

    def test_strips_html_tags(self):
        """HTML tags are stripped from output, returning plain text."""
        html = """
        <html><body>
        <article>
            <div class="article-body">
                <p>Text with <strong>bold</strong> and <a href="#">links</a> inside paragraph tags for testing.</p>
                <p>Another paragraph with <em>emphasis</em> and more content to pass threshold check.</p>
            </div>
        </article>
        </body></html>
        """
        result = RSSFetcher._extract_body_from_page(html)
        assert result is not None
        assert "<strong>" not in result
        assert "<a " not in result
        assert "bold" in result
        assert "links" in result


class TestFetchArticleBodies:
    """Characterization tests for _fetch_article_bodies async method."""

    @pytest.fixture
    def fetcher(self):
        return RSSFetcher()

    @pytest.fixture
    def sample_articles(self):
        return [
            {
                "title": "Article 1",
                "url": "https://example.com/article-1",
                "summary": "Short summary",
                "body_text": None,
                "source_name": "Test",
            },
            {
                "title": "Article 2",
                "url": "https://example.com/article-2",
                "summary": "Another short one",
                "body_text": None,
                "source_name": "Test",
            },
            {
                "title": "Article 3",
                "url": "https://example.com/article-3",
                "summary": None,
                "body_text": None,
                "source_name": "Test",
            },
            {
                "title": "Article 4",
                "url": "https://example.com/article-4",
                "summary": "Fourth article summary",
                "body_text": None,
                "source_name": "Test",
            },
        ]

    async def test_fetches_body_for_articles_without_body(
        self, fetcher, sample_articles,
    ):
        """Fetches body text for articles that lack body_text."""
        article_html = """
        <html><body><article>
            <div class="article-body">
                <p>Full article body text extracted from the page successfully.</p>
                <p>With multiple paragraphs to ensure minimum length threshold.</p>
            </div>
        </article></body></html>
        """
        mock_response = MagicMock()
        mock_response.text = article_html
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        await fetcher._fetch_article_bodies(mock_client, sample_articles)

        # Should fetch for max MAX_BODY_FETCH_PER_SOURCE articles
        assert mock_client.get.call_count == MAX_BODY_FETCH_PER_SOURCE
        # First 3 articles should have body_text set
        for i in range(MAX_BODY_FETCH_PER_SOURCE):
            assert sample_articles[i]["body_text"] is not None

    async def test_skips_articles_with_existing_body(self, fetcher):
        """Articles with existing body_text are not re-fetched."""
        articles = [
            {
                "title": "Has Body",
                "url": "https://example.com/1",
                "summary": "Short",
                "body_text": "Already has body content from content:encoded.",
                "source_name": "Test",
            },
        ]
        mock_client = AsyncMock()
        mock_client.get = AsyncMock()

        await fetcher._fetch_article_bodies(mock_client, articles)

        mock_client.get.assert_not_called()
        assert articles[0]["body_text"] == "Already has body content from content:encoded."

    async def test_uses_long_description_as_body(self, fetcher):
        """Articles with description > 200 chars use description as body."""
        long_desc = "A" * (DESCRIPTION_AS_BODY_THRESHOLD + 1)
        articles = [
            {
                "title": "Long Desc",
                "url": "https://example.com/1",
                "summary": long_desc,
                "body_text": None,
                "source_name": "Test",
            },
        ]
        mock_client = AsyncMock()
        mock_client.get = AsyncMock()

        await fetcher._fetch_article_bodies(mock_client, articles)

        mock_client.get.assert_not_called()
        assert articles[0]["body_text"] == long_desc

    async def test_graceful_degradation_on_fetch_failure(self, fetcher):
        """Failed article fetches leave body_text as None."""
        articles = [
            {
                "title": "Will Fail",
                "url": "https://example.com/fail",
                "summary": "Short",
                "body_text": None,
                "source_name": "Test",
            },
        ]
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.TimeoutException("timeout"),
        )

        await fetcher._fetch_article_bodies(mock_client, articles)

        assert articles[0]["body_text"] is None

    async def test_skips_articles_without_url(self, fetcher):
        """Articles without URL are skipped for body fetch."""
        articles = [
            {
                "title": "No URL",
                "url": "",
                "summary": "Short",
                "body_text": None,
                "source_name": "Test",
            },
        ]
        mock_client = AsyncMock()
        mock_client.get = AsyncMock()

        await fetcher._fetch_article_bodies(mock_client, articles)

        mock_client.get.assert_not_called()

    async def test_respects_max_per_source_limit(self, fetcher):
        """Only fetches up to max_per_source articles."""
        articles = [
            {
                "title": f"Article {i}",
                "url": f"https://example.com/{i}",
                "summary": "Short",
                "body_text": None,
                "source_name": "Test",
            }
            for i in range(10)
        ]
        mock_response = MagicMock()
        mock_response.text = "<html><body><article><div class='article-body'><p>Body content that is long enough to pass the threshold check.</p></div></article></body></html>"
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        await fetcher._fetch_article_bodies(mock_client, articles)

        assert mock_client.get.call_count == MAX_BODY_FETCH_PER_SOURCE
