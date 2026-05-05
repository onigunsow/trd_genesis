"""News RSS polling — 10-minute interval during market hours.

REQ-DELTA-01-4: News RSS polling for broad market + watchlist headlines.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable

from trading.db.session import audit
from trading.jit.events import insert_delta
from trading.jit.merge import invalidate_cache
from trading.jit.models import DeltaEvent

LOG = logging.getLogger(__name__)

# REQ-DELTA-01-4: Poll every 10 minutes during market hours
NEWS_POLL_INTERVAL_S: int = 600  # 10 minutes


class NewsPoller:
    """Polls news RSS feeds for market headlines at regular intervals."""

    def __init__(
        self,
        tickers: list[str] | None = None,
        on_news: Callable[[DeltaEvent], None] | None = None,
    ) -> None:
        self._tickers = tickers or []
        self._on_news = on_news
        self._thread: threading.Thread | None = None
        self._running = False
        self._seen_urls: set[str] = set()

    @property
    def running(self) -> bool:
        return self._running

    def start(self, tickers: list[str] | None = None) -> None:
        """Start news polling in background thread."""
        if self._running:
            return
        if tickers:
            self._tickers = tickers
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="news-poller",
            daemon=True,
        )
        self._thread.start()
        LOG.info("News poller started")

    def stop(self) -> None:
        """Stop news polling."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        LOG.info("News poller stopped")

    def _poll_loop(self) -> None:
        """Main polling loop — fetch news every 10 minutes."""
        while self._running:
            try:
                self._poll_once()
            except Exception:
                LOG.exception("News poll cycle failed")

            for _ in range(NEWS_POLL_INTERVAL_S):
                if not self._running:
                    return
                time.sleep(1)

    def _poll_once(self) -> None:
        """Execute a single news poll cycle."""
        articles = self._fetch_news()
        new_count = 0

        for article in articles:
            url = article.get("url", "")
            if url in self._seen_urls:
                continue
            self._seen_urls.add(url)

            ticker = article.get("ticker")

            event = DeltaEvent(
                event_type="news",
                source="news_rss",
                ticker=ticker,
                payload={
                    "ticker": ticker,
                    "headline": article.get("headline", ""),
                    "source_name": article.get("source_name", ""),
                    "url": url,
                    "published_at": article.get("published_at", ""),
                    "sentiment": article.get("sentiment"),
                },
                event_ts=datetime.now(),
            )

            insert_delta(event)
            new_count += 1
            invalidate_cache("micro")

            if self._on_news:
                self._on_news(event)

        if new_count > 0:
            audit(
                "NEWS_POLL_COMPLETED",
                actor="jit_news",
                details={"new_articles": new_count},
            )

    def _fetch_news(self) -> list[dict[str, Any]]:
        """Fetch news from RSS feeds using existing news_adapter."""
        try:
            from trading.data.news_adapter import fetch_news_rss
            return fetch_news_rss(self._tickers)
        except ImportError:
            LOG.debug("news_adapter.fetch_news_rss not available")
            return []
        except Exception:
            LOG.exception("News RSS fetch failed")
            return []
