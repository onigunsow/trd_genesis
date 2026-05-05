"""Content normalizer — unified Article dataclass with dedup (SPEC-TRADING-013 Module 4).

Produces a normalized Article from both RSS and web scraper raw outputs.
Deduplication by SHA-256 of normalized title within a single crawl cycle.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal


@dataclass
class Article:
    """Normalized article ready for database storage."""

    title: str
    url: str
    summary: str | None
    body_text: str | None
    source_name: str
    sector: str
    language: Literal["en", "ko"]
    published_at: datetime
    crawled_at: datetime
    content_hash: str
    date_inferred: bool = False


# Maximum summary length (truncated at word boundary)
MAX_SUMMARY_LENGTH = 500
# Maximum body text length
MAX_BODY_TEXT_LENGTH = 5000
# Summary auto-generated from body_text if no explicit summary
AUTO_SUMMARY_LENGTH = 300


def normalize_title(raw_title: str) -> str:
    """Normalize a title: strip, collapse whitespace, decode HTML entities."""
    # Decode HTML entities (e.g., &amp; -> &)
    title = html.unescape(raw_title)
    # Strip leading/trailing whitespace
    title = title.strip()
    # Collapse multiple whitespace
    title = re.sub(r"\s+", " ", title)
    # Remove leading/trailing punctuation artifacts from RSS encoding
    title = title.strip("·•–—-")
    title = title.strip()
    return title


def compute_content_hash(normalized_title: str) -> str:
    """Compute SHA-256 hash of normalized title for deduplication."""
    return hashlib.sha256(normalized_title.encode("utf-8")).hexdigest()


def strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace to produce plain text."""
    # Remove HTML tags
    clean = re.sub(r"<[^>]+>", " ", text)
    # Decode HTML entities
    clean = html.unescape(clean)
    # Collapse whitespace
    clean = re.sub(r"\s+", " ", clean)
    return clean.strip()


def truncate_body_text(body: str | None, max_length: int = MAX_BODY_TEXT_LENGTH) -> str | None:
    """Truncate body text to max_length characters."""
    if body is None:
        return None
    body = body.strip()
    if not body:
        return None
    if len(body) <= max_length:
        return body
    # Truncate at word boundary
    truncated = body[:max_length]
    last_space = truncated.rfind(" ")
    if last_space > max_length // 2:
        return truncated[:last_space] + "..."
    return truncated + "..."


def truncate_summary(summary: str | None, max_length: int = MAX_SUMMARY_LENGTH) -> str | None:
    """Truncate summary at word boundary to max_length characters."""
    if summary is None:
        return None
    summary = summary.strip()
    if not summary:
        return None
    if len(summary) <= max_length:
        return summary
    # Find last space before max_length
    truncated = summary[:max_length]
    last_space = truncated.rfind(" ")
    if last_space > max_length // 2:
        return truncated[:last_space] + "..."
    return truncated + "..."


def normalize_articles(
    raw_articles: list[dict[str, Any]],
    crawled_at: datetime | None = None,
) -> list[Article]:
    """Normalize and deduplicate a batch of raw articles.

    Args:
        raw_articles: List of dicts from RSS fetcher or web scraper.
        crawled_at: Timestamp for this crawl cycle (defaults to now UTC).

    Returns:
        Deduplicated list of Article instances.
    """
    if crawled_at is None:
        crawled_at = datetime.now(timezone.utc)

    seen_hashes: set[str] = set()
    articles: list[Article] = []

    for raw in raw_articles:
        title = normalize_title(raw.get("title", ""))
        if not title:
            continue

        content_hash = compute_content_hash(title)

        # Within-batch deduplication (REQ-NEWS-04-4)
        if content_hash in seen_hashes:
            continue
        seen_hashes.add(content_hash)

        # Date inference (REQ-NEWS-04-5)
        published_at = raw.get("published_at")
        date_inferred = False
        if published_at is None:
            published_at = crawled_at
            date_inferred = True
        elif not isinstance(published_at, datetime):
            published_at = crawled_at
            date_inferred = True

        # Ensure timezone awareness
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)

        # Process body_text: strip HTML, truncate to MAX_BODY_TEXT_LENGTH
        raw_body = raw.get("body_text")
        if raw_body and isinstance(raw_body, str):
            body_text = strip_html(raw_body)
            body_text = truncate_body_text(body_text)
        else:
            body_text = None

        # Strip HTML from summary and truncate (REQ-NEWS-04-6)
        raw_summary = raw.get("summary")
        if raw_summary and isinstance(raw_summary, str):
            summary = strip_html(raw_summary)
            # Google News descriptions become just "Source Name" after HTML
            # stripping — too short to be useful, discard them.
            if len(summary) < 30:
                summary = None
            else:
                summary = truncate_summary(summary)
        else:
            summary = None

        # Auto-generate summary from body_text if no explicit summary
        if summary is None and body_text:
            summary = truncate_summary(body_text, max_length=AUTO_SUMMARY_LENGTH)

        articles.append(Article(
            title=title,
            url=raw.get("url", "")[:500],
            summary=summary,
            body_text=body_text,
            source_name=raw.get("source_name", ""),
            sector=raw.get("sector", ""),
            language=raw.get("language", "ko"),
            published_at=published_at,
            crawled_at=crawled_at,
            content_hash=content_hash,
            date_inferred=date_inferred,
        ))

    return articles
