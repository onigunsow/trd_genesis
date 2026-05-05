"""Async RSS fetcher for 31 RSS sources (SPEC-TRADING-013 Module 2).

Uses feedparser as primary parser with xml.etree.ElementTree as fallback.
Parallel fetch via asyncio.gather with Semaphore(10) concurrency limit.
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

from trading.news.rate_limiter import DomainRateLimiter
from trading.news.sources import NewsSource

LOG = logging.getLogger(__name__)

USER_AGENT = "trading-bot/0.2 (personal use; non-commercial)"
HTTP_TIMEOUT = 15.0
MAX_CONCURRENCY = 10


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
        """Fetch and parse a single RSS feed."""
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

            return items, True

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

            items.append({
                "title": title,
                "url": entry.get("link", ""),
                "summary": summary or None,
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
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        # Try RSS 2.0 items
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            items.append({
                "title": title,
                "url": item.findtext("link") or "",
                "summary": (item.findtext("description") or "").strip() or None,
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
                items.append({
                    "title": title,
                    "url": url,
                    "summary": (
                        entry.findtext(f"{{{ns['atom']}}}summary") or ""
                    ).strip() or None,
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
