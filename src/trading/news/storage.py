"""Database storage for news articles (SPEC-TRADING-013 Module 5).

Batch upsert with ON CONFLICT (content_hash) DO NOTHING for idempotent inserts.
Single transaction per crawl cycle.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from trading.db.session import connection
from trading.news.normalizer import Article

LOG = logging.getLogger(__name__)

RETENTION_DAYS = 90


def insert_articles(articles: list[Article]) -> tuple[int, int]:
    """Batch insert articles into news_articles table.

    Uses ON CONFLICT (content_hash) DO NOTHING for cross-cycle deduplication.
    All inserts within a single transaction (REQ-NEWS-05-3).

    Returns:
        (inserted_count, skipped_count)
    """
    if not articles:
        return 0, 0

    sql = """
        INSERT INTO news_articles
            (title, url, summary, body_text, source_name, sector, language,
             published_at, crawled_at, content_hash, date_inferred)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (content_hash) DO NOTHING
    """

    inserted = 0
    with connection() as conn, conn.cursor() as cur:
        for article in articles:
            cur.execute(sql, (
                article.title,
                article.url,
                article.summary,
                article.body_text,
                article.source_name,
                article.sector,
                article.language,
                article.published_at,
                article.crawled_at,
                article.content_hash,
                article.date_inferred,
            ))
            if cur.rowcount > 0:
                inserted += 1
        # Transaction committed by connection() context manager

    skipped = len(articles) - inserted
    LOG.info("Inserted %d articles, skipped %d duplicates", inserted, skipped)
    return inserted, skipped


def cleanup_old_articles(retention_days: int = RETENTION_DAYS) -> int:
    """Delete articles older than retention period.

    Returns number of rows deleted.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM news_articles WHERE crawled_at < %s",
            (cutoff,),
        )
        deleted = cur.rowcount
    if deleted:
        LOG.info("Cleaned up %d articles older than %d days", deleted, retention_days)
    return deleted


def get_articles_by_sector(
    sector: str,
    *,
    days: int = 7,
    language: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Query articles for a sector within a time window.

    Used by context_builder to generate macro/micro news markdown.
    """
    params: list = [sector, days]
    sql = """
        SELECT title, url, source_name, sector, language, published_at,
               summary, body_text
          FROM news_articles
         WHERE sector = %s
           AND published_at >= NOW() - make_interval(days => %s)
    """
    if language:
        sql += " AND language = %s"
        params.append(language)
    sql += " ORDER BY published_at DESC LIMIT %s"
    params.append(limit)

    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def get_articles_multi_sector(
    sectors: list[str],
    *,
    days: int = 3,
    language_priority: str = "ko",
    limit_per_sector: int = 30,
) -> dict[str, list[dict]]:
    """Query articles for multiple sectors, prioritizing by language.

    Returns dict[sector] -> list of article dicts.
    """
    result: dict[str, list[dict]] = {}
    for sector in sectors:
        # Priority language first
        articles = get_articles_by_sector(
            sector, days=days, language=language_priority, limit=limit_per_sector,
        )
        # Fill remaining with other language
        if len(articles) < limit_per_sector:
            other_lang = "en" if language_priority == "ko" else "ko"
            remaining = limit_per_sector - len(articles)
            more = get_articles_by_sector(
                sector, days=days, language=other_lang, limit=remaining,
            )
            articles.extend(more)
        result[sector] = articles
    return result
