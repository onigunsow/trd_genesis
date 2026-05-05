"""Web scraper for 8 sites using httpx + BeautifulSoup (SPEC-TRADING-013 Module 3).

Per-site CSS selector rules. No Playwright/Selenium — httpx + lxml only.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from trading.news.rate_limiter import DomainRateLimiter
from trading.news.sources import NewsSource

LOG = logging.getLogger(__name__)

USER_AGENT = "trading-bot/0.2 (personal use; non-commercial)"
HTTP_TIMEOUT = 15.0


@dataclass(frozen=True)
class ScrapeRule:
    """Per-site extraction rule for web scraping."""

    source_name: str
    headline_selector: str
    link_selector: str
    date_selector: str | None = None
    encoding: str = "utf-8"


# Registry of 8 scrape rules — one per web source
# Uses href-based selectors as primary strategy for Korean CMS sites
# (more robust than class-based selectors which change often)
SCRAPE_RULES: dict[str, ScrapeRule] = {
    "디일렉 (The Elec)": ScrapeRule(
        source_name="디일렉 (The Elec)",
        headline_selector="#section-list a[href*='articleView']",
        link_selector="#section-list a[href*='articleView']",
        date_selector="#section-list .list-dated",
    ),
    "바이오타임즈": ScrapeRule(
        source_name="바이오타임즈",
        headline_selector="#section-list a[href*='articleView']",
        link_selector="#section-list a[href*='articleView']",
        date_selector="#section-list .list-dated",
    ),
    "한국금융신문 웹": ScrapeRule(
        source_name="한국금융신문 웹",
        headline_selector="a[href*='view.php']",
        link_selector="a[href*='view.php']",
        date_selector=None,
    ),
    "에너지신문": ScrapeRule(
        source_name="에너지신문",
        headline_selector="#section-list a[href*='articleView']",
        link_selector="#section-list a[href*='articleView']",
        date_selector="#section-list .list-dated",
    ),
    "인공지능신문": ScrapeRule(
        source_name="인공지능신문",
        headline_selector="#section-list a[href*='articleView']",
        link_selector="#section-list a[href*='articleView']",
        date_selector="#section-list .list-dated",
    ),
    "철강금속신문": ScrapeRule(
        source_name="철강금속신문",
        headline_selector="#user-container a[href*='articleView']",
        link_selector="#user-container a[href*='articleView']",
        date_selector="#user-container .list-dated",
    ),
    "게임메카 웹": ScrapeRule(
        source_name="게임메카 웹",
        headline_selector=".news-list a[href*='view.php']",
        link_selector=".news-list a[href*='view.php']",
        date_selector=".news-list .date",
    ),
    "네이버증권 뉴스": ScrapeRule(
        source_name="네이버증권 뉴스",
        headline_selector=".mainNewsList li a, .news_list a",
        link_selector=".mainNewsList li a, .news_list a",
        date_selector=".mainNewsList li .wdate, .news_list .date",
        encoding="euc-kr",
    ),
    # NOTE: 한국은행 보도자료 removed (site under maintenance, JS-rendered)
    # NOTE: 전기차시대 converted to RSS source (WordPress /wp/feed/)
    # NOTE: 국방일보 removed (JS SPA, no static content)
}


class WebScraper:
    """Async web scraper using httpx + BeautifulSoup with per-site CSS rules."""

    def __init__(
        self,
        *,
        timeout: float = HTTP_TIMEOUT,
        rate_limiter: DomainRateLimiter | None = None,
    ) -> None:
        self._timeout = timeout
        self._rate_limiter = rate_limiter or DomainRateLimiter()

    async def scrape_all(
        self, sources: list[NewsSource],
    ) -> tuple[list[dict[str, Any]], dict[str, bool]]:
        """Scrape all web sources sequentially (rate limited).

        Returns:
            (articles, health_map) where health_map[source_name] = success/failure
        """
        articles: list[dict[str, Any]] = []
        health_map: dict[str, bool] = {}

        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            for source in sources:
                items, success = await self._scrape_one(client, source)
                articles.extend(items)
                health_map[source.name] = success

        return articles, health_map

    async def _scrape_one(
        self, client: httpx.AsyncClient, source: NewsSource,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Scrape a single web source."""
        rule = SCRAPE_RULES.get(source.name)
        if rule is None:
            LOG.warning("No scrape rule for: %s", source.name)
            return [], False

        await self._rate_limiter.acquire(source.url)

        try:
            response = await client.get(source.url)
            response.raise_for_status()
        except httpx.TimeoutException:
            LOG.warning("Web scrape timeout (>%ss): %s", self._timeout, source.name)
            return [], False
        except httpx.HTTPStatusError as e:
            LOG.warning("Web scrape HTTP %d: %s", e.response.status_code, source.name)
            return [], False
        except Exception as e:  # noqa: BLE001
            LOG.warning("Web scrape error: %s — %s", source.name, e)
            return [], False

        # Decode with per-site encoding
        try:
            if rule.encoding != "utf-8":
                content = response.content.decode(rule.encoding, errors="replace")
            else:
                content = response.text
        except Exception as e:  # noqa: BLE001
            LOG.warning("Encoding error %s: %s — %s", rule.encoding, source.name, e)
            content = response.text

        items = self._extract_articles(content, source, rule)

        if not items:
            LOG.warning(
                "structure_change_detected: %s — CSS selectors yielded zero results",
                source.name,
            )
            return [], False

        return items, True

    @staticmethod
    def _get_parser() -> str:
        """Return best available parser: lxml preferred, html.parser fallback."""
        try:
            import lxml  # noqa: F401
            return "lxml"
        except ImportError:
            return "html.parser"

    def _extract_articles(
        self, html: str, source: NewsSource, rule: ScrapeRule,
    ) -> list[dict[str, Any]]:
        """Extract articles from HTML using CSS selectors."""
        soup = BeautifulSoup(html, self._get_parser())
        items: list[dict[str, Any]] = []

        headlines = soup.select(rule.headline_selector)
        if not headlines:
            return []

        for el in headlines[:30]:  # Cap at 30 per source
            title = el.get_text(strip=True)
            if not title:
                continue

            # Extract URL (resolve relative links)
            href = el.get("href", "")
            url = urljoin(source.url, href) if href else ""

            items.append({
                "title": title,
                "url": url,
                "summary": None,
                "published_at": None,  # Deferred to normalizer
                "source_name": source.name,
                "sector": source.sector,
                "language": source.language,
            })

        return items
