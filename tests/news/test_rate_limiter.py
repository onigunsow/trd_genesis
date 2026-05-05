"""Tests for rate limiter utility (SPEC-TRADING-013)."""

from __future__ import annotations

import asyncio
import time

import pytest

from trading.news.rate_limiter import DomainRateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_enforces_delay():
    """AC-2-3/AC-3-4: 1-second delay between same domain requests."""
    limiter = DomainRateLimiter(delay_seconds=0.1)  # Shorter for test speed

    start = time.monotonic()
    await limiter.acquire("https://www.example.com/page1")
    await limiter.acquire("https://www.example.com/page2")
    elapsed = time.monotonic() - start

    # Should take at least 0.1s for the second request
    assert elapsed >= 0.09


@pytest.mark.asyncio
async def test_rate_limiter_different_domains_no_delay():
    """Different domains are not rate-limited against each other."""
    limiter = DomainRateLimiter(delay_seconds=1.0)

    start = time.monotonic()
    await limiter.acquire("https://www.example.com/page1")
    await limiter.acquire("https://www.other.com/page1")
    elapsed = time.monotonic() - start

    # Different domains should be near-instant
    assert elapsed < 0.5


@pytest.mark.asyncio
async def test_rate_limiter_domain_extraction():
    """Domain correctly extracted from various URL formats."""
    limiter = DomainRateLimiter(delay_seconds=0.05)

    # These should all hit the same domain
    await limiter.acquire("https://www.fnnews.com/rss/r20/stock.xml")
    start = time.monotonic()
    await limiter.acquire("https://www.fnnews.com/rss/r20/finance.xml")
    elapsed = time.monotonic() - start

    assert elapsed >= 0.04
