"""Tests for Crawler Orchestrator (SPEC-TRADING-013)."""

from __future__ import annotations

from trading.news.crawler import CrawlResult, is_news_v2_enabled


def test_crawl_result_representation():
    """CrawlResult has a useful repr."""
    r = CrawlResult()
    r.total_fetched = 100
    r.new_inserted = 80
    r.duplicates_skipped = 20
    r.sources_failed = 3
    r.sources_succeeded = 39
    r.duration_seconds = 15.5
    s = repr(r)
    assert "fetched=100" in s
    assert "inserted=80" in s
    assert "failed=3" in s
