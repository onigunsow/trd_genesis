"""Crawler orchestrator — coordinates RSS fetcher, web scraper, normalizer, and storage.

SPEC-TRADING-013: Full crawl cycle entry point. Called by scheduler (7x/day) and CLI.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from trading.db.session import audit, get_system_state
from trading.news.health import (
    check_and_alert,
    get_disabled_sources,
    is_source_enabled,
    re_enable_source,
    record_failure,
    record_success,
)
from trading.news.normalizer import normalize_articles
from trading.news.rate_limiter import DomainRateLimiter
from trading.news.rss_fetcher import RSSFetcher
from trading.news.sources import (
    NewsSource,
    all_sources,
    get_sources_by_sector,
    get_sources_by_type,
)
from trading.news.storage import cleanup_old_articles, insert_articles
from trading.news.web_scraper import WebScraper

LOG = logging.getLogger(__name__)


def is_news_v2_enabled() -> bool:
    """Check feature flag: news_crawling_v2_enabled in system_state."""
    try:
        state = get_system_state()
        return state.get("news_crawling_v2_enabled", True)
    except Exception:  # noqa: BLE001
        # Default enabled if state cannot be read
        return True


class CrawlResult:
    """Summary of a crawl cycle."""

    def __init__(self) -> None:
        self.total_fetched: int = 0
        self.new_inserted: int = 0
        self.duplicates_skipped: int = 0
        self.sources_failed: int = 0
        self.sources_succeeded: int = 0
        self.duration_seconds: float = 0.0

    def __repr__(self) -> str:
        return (
            f"CrawlResult(fetched={self.total_fetched}, "
            f"inserted={self.new_inserted}, "
            f"dupes={self.duplicates_skipped}, "
            f"failed={self.sources_failed}, "
            f"ok={self.sources_succeeded}, "
            f"duration={self.duration_seconds:.1f}s)"
        )


async def _run_crawl(
    sources: list[NewsSource],
    *,
    force: bool = False,
) -> CrawlResult:
    """Execute the async crawl pipeline.

    Args:
        sources: List of sources to crawl.
        force: If True, crawl even disabled sources.
    """
    result = CrawlResult()
    start = time.monotonic()
    crawled_at = datetime.now(timezone.utc)

    # Filter out disabled sources (unless forced)
    disabled = set(get_disabled_sources()) if not force else set()
    active_sources = [s for s in sources if s.name not in disabled]

    if len(active_sources) < len(sources):
        LOG.info(
            "Skipping %d disabled sources (use --force to override)",
            len(sources) - len(active_sources),
        )

    # Shared rate limiter across RSS and web modules
    rate_limiter = DomainRateLimiter(delay_seconds=1.0)

    # Split by type
    rss_sources = [s for s in active_sources if s.source_type == "rss"]
    web_sources = [s for s in active_sources if s.source_type == "web"]

    # Fetch RSS (parallel)
    rss_fetcher = RSSFetcher(rate_limiter=rate_limiter)
    rss_articles, rss_health = await rss_fetcher.fetch_all(rss_sources)

    # Scrape web (sequential with rate limiting)
    web_scraper = WebScraper(rate_limiter=rate_limiter)
    web_articles, web_health = await web_scraper.scrape_all(web_sources)

    # Combine raw articles
    all_raw = rss_articles + web_articles
    result.total_fetched = len(all_raw)

    # Normalize and deduplicate
    articles = normalize_articles(all_raw, crawled_at=crawled_at)

    # Store to database
    if articles:
        inserted, skipped = insert_articles(articles)
        result.new_inserted = inserted
        result.duplicates_skipped = skipped
    else:
        result.new_inserted = 0
        result.duplicates_skipped = 0

    # Update health for all sources
    all_health = {**rss_health, **web_health}
    for source in active_sources:
        success = all_health.get(source.name, False)
        if success:
            record_success(source.name)
            result.sources_succeeded += 1
        else:
            failures = record_failure(source.name, f"crawl cycle failure")
            check_and_alert(source.name, source.sector, failures)
            result.sources_failed += 1

    # Re-enable forced sources that succeeded
    if force:
        for source in sources:
            if source.name in disabled and all_health.get(source.name, False):
                re_enable_source(source.name)
                LOG.info("Re-enabled previously disabled source: %s", source.name)

    result.duration_seconds = time.monotonic() - start
    return result


def crawl_all(*, force: bool = False) -> CrawlResult:
    """Crawl all 42 sources (synchronous entry point for scheduler/CLI).

    Checks feature flag before executing.
    Runs DB retention cleanup (7 days) after each crawl cycle.
    """
    if not is_news_v2_enabled():
        LOG.info("News crawling v2 disabled by feature flag — skipping")
        return CrawlResult()

    sources = all_sources()
    result = asyncio.run(_run_crawl(sources, force=force))

    # Audit log entry (REQ-NEWS-05-3)
    audit("NEWS_CRAWL_COMPLETE", actor="cron.news_crawl", details={
        "total_fetched": result.total_fetched,
        "new_inserted": result.new_inserted,
        "duplicates_skipped": result.duplicates_skipped,
        "sources_failed": result.sources_failed,
        "sources_succeeded": result.sources_succeeded,
        "duration_seconds": round(result.duration_seconds, 1),
    })

    # Retention cleanup: keep only last 7 days (older articles are stale
    # since context .md files are rebuilt from last 24h every crawl cycle)
    try:
        deleted = cleanup_old_articles(retention_days=7)
        if deleted:
            LOG.info("Retention cleanup: removed %d articles older than 7 days", deleted)
    except Exception:  # noqa: BLE001
        LOG.exception("Retention cleanup failed (non-fatal)")

    LOG.info("Crawl complete: %s", result)
    return result


def crawl_sector(sector: str, *, force: bool = False) -> CrawlResult:
    """Crawl sources for a specific sector only."""
    if not is_news_v2_enabled():
        LOG.info("News crawling v2 disabled by feature flag — skipping")
        return CrawlResult()

    sources = get_sources_by_sector(sector)
    if not sources:
        LOG.warning("No sources found for sector: %s", sector)
        return CrawlResult()

    result = asyncio.run(_run_crawl(sources, force=force))

    audit("NEWS_CRAWL_SECTOR", actor="cron.news_crawl", details={
        "sector": sector,
        "total_fetched": result.total_fetched,
        "new_inserted": result.new_inserted,
        "sources_failed": result.sources_failed,
        "duration_seconds": round(result.duration_seconds, 1),
    })

    LOG.info("Sector crawl [%s]: %s", sector, result)
    return result


def crawl_source(source_name: str, *, force: bool = False) -> CrawlResult:
    """Crawl a single source by name (for manual retry)."""
    sources = [s for s in all_sources() if s.name == source_name]
    if not sources:
        LOG.warning("Source not found: %s", source_name)
        return CrawlResult()

    result = asyncio.run(_run_crawl(sources, force=force))
    LOG.info("Single source crawl [%s]: %s", source_name, result)
    return result
