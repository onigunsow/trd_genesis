"""Async RSS fetcher for 31 RSS sources (SPEC-TRADING-013 Module 2).

Uses feedparser as primary parser with xml.etree.ElementTree as fallback.
Parallel fetch via asyncio.gather with Semaphore(10) concurrency limit.
Article body text fetched from individual URLs via BeautifulSoup extraction.
"""

from __future__ import annotations

import asyncio
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import feedparser
import httpx
from bs4 import BeautifulSoup

from trading.news.rate_limiter import DomainRateLimiter
from trading.news.sources import NewsSource

LOG = logging.getLogger(__name__)

USER_AGENT = "trading-bot/0.2 (personal use; non-commercial)"
HTTP_TIMEOUT = 15.0
MAX_CONCURRENCY = 10

# Article body fetching configuration
ARTICLE_BODY_TIMEOUT = 10.0
MAX_BODY_FETCH_PER_SOURCE = 3
BODY_FETCH_CONCURRENCY = 5
# If summary/description exceeds this length, use it as body instead of fetching
DESCRIPTION_AS_BODY_THRESHOLD = 200

# CSS selectors for extracting article body text (priority order)
BODY_SELECTORS: tuple[str, ...] = (
    "article .article-body",
    "[itemprop='articleBody']",
    ".article-content",
    ".post-content",
    ".entry-content",
    "#article-body",
    "article p",
    "main p",
)


class RSSFetcher:
    """Async RSS feed fetcher with rate limiting and fallback parsing."""

    def __init__(
        self,
        *,
        timeout: float = HTTP_TIMEOUT,
        max_concurrency: int = MAX_CONCURRENCY,
        rate_limiter: DomainRateLimiter | None = None,
    ) -> None:
        self._timeout = timeout
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._rate_limiter = rate_limiter or DomainRateLimiter()

    async def fetch_all(
        self, sources: list[NewsSource],
    ) -> tuple[list[dict[str, Any]], dict[str, bool]]:
        """Fetch all RSS sources in parallel.

        Returns:
            (articles, health_map) where health_map[source_name] = success/failure
        """
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            tasks = [self._fetch_one(client, source) for source in sources]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        articles: list[dict[str, Any]] = []
        health_map: dict[str, bool] = {}

        for source, result in zip(sources, results):
            if isinstance(result, Exception):
                LOG.warning("RSS fetch exception: %s — %s", source.name, result)
                health_map[source.name] = False
            else:
                items, success = result
                articles.extend(items)
                health_map[source.name] = success

        return articles, health_map

    async def _fetch_one(
        self, client: httpx.AsyncClient, source: NewsSource,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Fetch and parse a single RSS feed, then fetch article bodies."""
        async with self._semaphore:
            await self._rate_limiter.acquire(source.url)
            try:
                response = await client.get(source.url)
                response.raise_for_status()
            except httpx.TimeoutException:
                LOG.warning("RSS timeout (>%ss): %s", self._timeout, source.name)
                return [], False
            except httpx.HTTPStatusError as e:
                LOG.warning("RSS HTTP %d: %s", e.response.status_code, source.name)
                return [], False
            except Exception as e:  # noqa: BLE001
                LOG.warning("RSS fetch error: %s — %s", source.name, e)
                return [], False

            items = self._parse_feedparser(response.content, source)
            if items is None:
                items = self._parse_etree_fallback(response.content, source)
            if items is None:
                LOG.warning("RSS unparseable: %s", source.name)
                return [], False

            # Fetch article body text from individual article URLs
            await self._fetch_article_bodies(client, items)

            return items, True

    async def _fetch_article_bodies(
        self,
        client: httpx.AsyncClient,
        articles: list[dict[str, Any]],
        max_per_source: int = MAX_BODY_FETCH_PER_SOURCE,
    ) -> None:
        """Fetch body text for top articles by following their URLs.

        Uses asyncio.Semaphore for parallel fetches (max BODY_FETCH_CONCURRENCY).
        Articles with long descriptions (>200 chars) use the description as body
        instead of fetching, to reduce network calls.
        """
        sem = asyncio.Semaphore(BODY_FETCH_CONCURRENCY)
        tasks: list[asyncio.Task[None]] = []

        # Select articles needing body fetch (most recent first, up to max)
        fetch_candidates: list[dict[str, Any]] = []
        for article in articles:
            if article.get("body_text"):
                # Already has body from content:encoded
                continue
            summary = article.get("summary") or ""
            if len(summary) > DESCRIPTION_AS_BODY_THRESHOLD:
                # Long description is good enough as body
                article["body_text"] = summary
                continue
            if article.get("url"):
                fetch_candidates.append(article)
            if len(fetch_candidates) >= max_per_source:
                break

        if not fetch_candidates:
            return

        async def _fetch_one_body(article: dict[str, Any]) -> None:
            async with sem:
                url = article["url"]
                try:
                    response = await client.get(
                        url, timeout=ARTICLE_BODY_TIMEOUT,
                    )
                    response.raise_for_status()
                    body_text = self._extract_body_from_page(response.text)
                    if body_text:
                        article["body_text"] = body_text
                except Exception as e:  # noqa: BLE001
                    LOG.debug(
                        "RSS article body fetch failed: %s — %s",
                        url[:80], e,
                    )

        for article in fetch_candidates:
            tasks.append(asyncio.create_task(_fetch_one_body(article)))

        await asyncio.gather(*tasks)

    @staticmethod
    def _extract_body_from_page(html: str) -> str | None:
        """Extract main article body text from an HTML page.

        Tries CSS selectors in priority order. Returns joined paragraph text.
        """
        try:
            import lxml  # noqa: F401
            parser = "lxml"
        except ImportError:
            parser = "html.parser"

        soup = BeautifulSoup(html, parser)

        for selector in BODY_SELECTORS:
            elements = soup.select(selector)
            if not elements:
                continue

            # For selectors ending with " p" (e.g., "article p", "main p"),
            # the elements are individual <p> tags — join their text.
            # For container selectors, get all <p> within the container.
            if selector.endswith(" p"):
                paragraphs = [el.get_text(strip=True) for el in elements]
            else:
                container = elements[0]
                paragraphs = [
                    p.get_text(strip=True)
                    for p in container.find_all("p")
                ]

            text = "\n\n".join(p for p in paragraphs if p)
            if text and len(text) > 50:
                return text

        return None

    def _parse_feedparser(
        self, content: bytes, source: NewsSource,
    ) -> list[dict[str, Any]] | None:
        """Parse RSS with feedparser (primary parser)."""
        try:
            parsed = feedparser.parse(content)
            if not parsed.entries:
                return None
        except Exception:  # noqa: BLE001
            return None

        items: list[dict[str, Any]] = []
        for entry in parsed.entries:
            title = (entry.get("title") or "").strip()
            if not title:
                continue

            published_at = self._parse_date_feedparser(entry)
            summary = (entry.get("summary") or entry.get("description") or "").strip()

            # Extract full body text from content:encoded (feedparser stores
            # it in entry.content list, each with 'value' key)
            body_text: str | None = None
            if hasattr(entry, "content") and entry.content:
                # content is a list of dicts with 'type' and 'value'
                for content_item in entry.content:
                    value = content_item.get("value", "")
                    if value and len(value) > len(body_text or ""):
                        body_text = value

            items.append({
                "title": title,
                "url": entry.get("link", ""),
                "summary": summary or None,
                "body_text": body_text,
                "published_at": published_at,
                "source_name": source.name,
                "sector": source.sector,
                "language": source.language,
            })
        return items

    def _parse_etree_fallback(
        self, content: bytes, source: NewsSource,
    ) -> list[dict[str, Any]] | None:
        """Parse RSS with xml.etree.ElementTree (fallback)."""
        try:
            root = ET.fromstring(content)  # noqa: S314
        except ET.ParseError:
            return None

        items: list[dict[str, Any]] = []
        # Handle both RSS 2.0 and Atom formats
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "content": "http://purl.org/rss/1.0/modules/content/",
        }

        # Try RSS 2.0 items
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            # Extract content:encoded for full body text
            body_text = (
                item.findtext(f"{{{ns['content']}}}encoded")
                or item.findtext("content:encoded")
                or None
            )
            items.append({
                "title": title,
                "url": item.findtext("link") or "",
                "summary": (item.findtext("description") or "").strip() or None,
                "body_text": (body_text or "").strip() or None,
                "published_at": self._parse_date_str(
                    item.findtext("pubDate") or item.findtext("dc:date") or ""
                ),
                "source_name": source.name,
                "sector": source.sector,
                "language": source.language,
            })

        # Try Atom entries if no RSS items found
        if not items:
            for entry in root.iter(f"{{{ns['atom']}}}entry"):
                title = (entry.findtext(f"{{{ns['atom']}}}title") or "").strip()
                if not title:
                    continue
                link_el = entry.find(f"{{{ns['atom']}}}link")
                url = link_el.get("href", "") if link_el is not None else ""
                # Atom content element as body text
                atom_content = (
                    entry.findtext(f"{{{ns['atom']}}}content") or ""
                ).strip() or None
                items.append({
                    "title": title,
                    "url": url,
                    "summary": (
                        entry.findtext(f"{{{ns['atom']}}}summary") or ""
                    ).strip() or None,
                    "body_text": atom_content,
                    "published_at": self._parse_date_str(
                        entry.findtext(f"{{{ns['atom']}}}published")
                        or entry.findtext(f"{{{ns['atom']}}}updated")
                        or ""
                    ),
                    "source_name": source.name,
                    "sector": source.sector,
                    "language": source.language,
                })

        return items if items else None

    @staticmethod
    def _parse_date_feedparser(entry: Any) -> datetime | None:
        """Extract published datetime from feedparser entry."""
        if getattr(entry, "published_parsed", None):
            try:
                ts = time.mktime(entry.published_parsed)
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            except Exception:  # noqa: BLE001
                pass
        if getattr(entry, "updated_parsed", None):
            try:
                ts = time.mktime(entry.updated_parsed)
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            except Exception:  # noqa: BLE001
                pass
        return None

    @staticmethod
    def _parse_date_str(date_str: str) -> datetime | None:
        """Try common date formats."""
        if not date_str:
            return None
        formats = [
            "%a, %d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S %Z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        return None
