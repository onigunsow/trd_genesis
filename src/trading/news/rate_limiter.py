"""Per-domain rate limiter for polite crawling (SPEC-TRADING-013).

Shared between RSS fetcher and web scraper. Enforces minimum 1-second delay
between consecutive requests to the same domain.
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse


class DomainRateLimiter:
    """Async per-domain rate limiter using asyncio.Lock + timestamp tracking."""

    def __init__(self, delay_seconds: float = 1.0) -> None:
        self._delay = delay_seconds
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}

    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL for rate limiting grouping."""
        parsed = urlparse(url)
        return parsed.netloc or parsed.hostname or url

    def _get_lock(self, domain: str) -> asyncio.Lock:
        """Get or create lock for a domain."""
        if domain not in self._locks:
            self._locks[domain] = asyncio.Lock()
        return self._locks[domain]

    async def acquire(self, url: str) -> None:
        """Wait until rate limit allows a request to the given URL's domain."""
        domain = self._extract_domain(url)
        lock = self._get_lock(domain)

        async with lock:
            now = time.monotonic()
            last = self._last_request.get(domain, 0.0)
            elapsed = now - last
            if elapsed < self._delay:
                await asyncio.sleep(self._delay - elapsed)
            self._last_request[domain] = time.monotonic()
