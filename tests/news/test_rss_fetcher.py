"""Tests for Module 2: RSS Fetcher (SPEC-TRADING-013 AC-2-*)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from trading.news.rss_fetcher import RSSFetcher, USER_AGENT
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
