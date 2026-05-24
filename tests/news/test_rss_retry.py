"""SPEC-TRADING-026 (B2) — RSS fetch retry on transient connection errors.

The crawler fetched each feed once; an intermittent ConnectError ("All
connection attempts failed") therefore failed the whole cycle for that source,
and 7 consecutive cycle-failures auto-disabled 연합뉴스 경제 even though the feed
was healthy between cycles. A small bounded retry with backoff rides over the
transient connect failures. HTTP status errors (4xx/5xx) are NOT retried.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from trading.news.rss_fetcher import MAX_FETCH_RETRIES, RSSFetcher
from trading.news.sources import NewsSource

_MIN_RSS = b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title></channel></rss>'


def _good_resp() -> MagicMock:
    r = MagicMock()
    r.content = _MIN_RSS
    r.raise_for_status = MagicMock()
    return r


def _src(name: str = "연합뉴스 경제") -> NewsSource:
    return NewsSource(name, "https://www.yna.co.kr/rss/economy.xml", "rss", "stock_market", "ko")


class TestFetchConnectRetry:
    async def test_retries_connect_error_then_succeeds(self):
        fetcher = RSSFetcher()
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[httpx.ConnectError("fail"), httpx.ConnectError("fail"), _good_resp()]
        )
        with patch("trading.news.rss_fetcher.asyncio.sleep", new=AsyncMock()), \
             patch.object(fetcher, "_parse_feedparser", return_value=[]):
            _items, ok = await fetcher._fetch_one(client, _src())
        assert ok is True
        assert client.get.call_count == 3  # 2 failures + 1 success

    async def test_gives_up_after_max_retries(self):
        fetcher = RSSFetcher()
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("All connection attempts failed"))
        with patch("trading.news.rss_fetcher.asyncio.sleep", new=AsyncMock()):
            _items, ok = await fetcher._fetch_one(client, _src())
        assert ok is False
        assert client.get.call_count == MAX_FETCH_RETRIES

    async def test_timeout_is_retried(self):
        fetcher = RSSFetcher()
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[httpx.ConnectTimeout("t"), _good_resp()]
        )
        with patch("trading.news.rss_fetcher.asyncio.sleep", new=AsyncMock()), \
             patch.object(fetcher, "_parse_feedparser", return_value=[]):
            _items, ok = await fetcher._fetch_one(client, _src())
        assert ok is True
        assert client.get.call_count == 2

    async def test_http_status_error_not_retried(self):
        fetcher = RSSFetcher()
        resp = MagicMock()
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "404", request=MagicMock(), response=MagicMock(status_code=404)
            )
        )
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)
        with patch("trading.news.rss_fetcher.asyncio.sleep", new=AsyncMock()):
            _items, ok = await fetcher._fetch_one(client, _src("X"))
        assert ok is False
        assert client.get.call_count == 1  # status errors are terminal
