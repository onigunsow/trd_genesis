"""Trend Analyzer — keyword/sentiment aggregation (SPEC-TRADING-014 Module 3).

Pure SQL/Python aggregation, no LLM calls.
Daily and weekly trend computation from news_analysis data.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from trading.db.session import audit, connection
from trading.news.intelligence.models import TrendEntry

LOG = logging.getLogger(__name__)


def compute_daily_trends(
    *,
    trend_date: date | None = None,
    sector: str | None = None,
) -> list[TrendEntry]:
    """Compute daily keyword frequency and sentiment distribution.

    REQ-INTEL-03-4: Count keyword occurrences, compute per-sector sentiment.
    REQ-INTEL-03-6: Skip without error if no data exists.
    """
    if trend_date is None:
        trend_date = date.today()

    # Aggregate keywords and sentiments from today's analyzed articles
    sql = """
        SELECT unnest(na.keywords) AS keyword,
               a.sector,
               COUNT(*) AS mention_count,
               SUM(CASE WHEN na.sentiment = 'positive' THEN 1 ELSE 0 END) AS pos,
               SUM(CASE WHEN na.sentiment = 'neutral' THEN 1 ELSE 0 END) AS neu,
               SUM(CASE WHEN na.sentiment = 'negative' THEN 1 ELSE 0 END) AS neg
          FROM news_analysis na
          JOIN news_articles a ON na.article_id = a.id
         WHERE a.published_at >= %s::date
           AND a.published_at < (%s::date + INTERVAL '1 day')
    """
    params: list = [trend_date, trend_date]
    if sector:
        sql += " AND a.sector = %s"
        params.append(sector)
    sql += """
         GROUP BY keyword, a.sector
         ORDER BY mention_count DESC
         LIMIT 200
    """

    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = list(cur.fetchall())

    if not rows:
        LOG.info("No trend data for %s (cold start or no articles)", trend_date)
        return []

    # Build TrendEntry objects and compute sentiment_avg
    entries: list[TrendEntry] = []
    for row in rows:
        total = row["pos"] + row["neu"] + row["neg"]
        sentiment_avg = (row["pos"] - row["neg"]) / total if total > 0 else None

        entries.append(TrendEntry(
            trend_date=trend_date,
            trend_type="daily",
            sector=row["sector"],
            keyword=row["keyword"],
            mention_count=row["mention_count"],
            sentiment_positive=row["pos"],
            sentiment_neutral=row["neu"],
            sentiment_negative=row["neg"],
            sentiment_avg=sentiment_avg,
        ))

    # Upsert into news_trends
    _upsert_trends(entries)

    audit("NEWS_INTEL_TREND_DAILY_OK", actor="trends", details={
        "trend_date": str(trend_date),
        "entries_count": len(entries),
    })

    LOG.info("Daily trends computed: %d entries for %s", len(entries), trend_date)
    return entries


def compute_weekly_trends(
    *,
    end_date: date | None = None,
) -> dict[str, list[str]]:
    """Compute weekly rising/falling keywords.

    REQ-INTEL-03-5: Compare this week vs previous week.
    Rising: > 50% increase. Falling: > 50% decrease.
    """
    if end_date is None:
        end_date = date.today()

    this_week_start = end_date - timedelta(days=6)
    prev_week_start = this_week_start - timedelta(days=7)
    prev_week_end = this_week_start - timedelta(days=1)

    # This week keyword counts
    this_week = _get_keyword_counts(this_week_start, end_date)
    # Previous week keyword counts
    prev_week = _get_keyword_counts(prev_week_start, prev_week_end)

    rising: list[str] = []
    falling: list[str] = []

    # Detect rising keywords
    for keyword, count in this_week.items():
        prev_count = prev_week.get(keyword, 0)
        if prev_count > 0 and count / prev_count > 1.5:
            rising.append(keyword)
        elif prev_count == 0 and count >= 3:
            # New keyword with significant mentions
            rising.append(keyword)

    # Detect falling keywords
    for keyword, prev_count in prev_week.items():
        this_count = this_week.get(keyword, 0)
        if prev_count > 0 and this_count / prev_count < 0.5:
            falling.append(keyword)

    # Store weekly trend entries
    weekly_entries: list[TrendEntry] = []
    for keyword in set(list(this_week.keys()) + list(prev_week.keys())):
        count = this_week.get(keyword, 0)
        if count > 0:
            weekly_entries.append(TrendEntry(
                trend_date=end_date,
                trend_type="weekly",
                sector=None,
                keyword=keyword,
                mention_count=count,
            ))

    if weekly_entries:
        _upsert_trends(weekly_entries)

    result = {"rising": rising[:10], "falling": falling[:10]}

    audit("NEWS_INTEL_TREND_WEEKLY_OK", actor="trends", details={
        "end_date": str(end_date),
        "rising_count": len(rising),
        "falling_count": len(falling),
    })

    LOG.info("Weekly trends: %d rising, %d falling keywords", len(rising), len(falling))
    return result


def get_sector_sentiments(trend_date: date | None = None) -> dict[str, dict[str, float]]:
    """Get per-sector sentiment distribution for the given date.

    Returns: {sector: {"positive": pct, "neutral": pct, "negative": pct, "avg": float}}
    """
    if trend_date is None:
        trend_date = date.today()

    sql = """
        SELECT a.sector,
               COUNT(*) AS total,
               SUM(CASE WHEN na.sentiment = 'positive' THEN 1 ELSE 0 END) AS pos,
               SUM(CASE WHEN na.sentiment = 'neutral' THEN 1 ELSE 0 END) AS neu,
               SUM(CASE WHEN na.sentiment = 'negative' THEN 1 ELSE 0 END) AS neg
          FROM news_analysis na
          JOIN news_articles a ON na.article_id = a.id
         WHERE a.published_at >= %s::date
           AND a.published_at < (%s::date + INTERVAL '1 day')
         GROUP BY a.sector
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, [trend_date, trend_date])
        rows = list(cur.fetchall())

    result: dict[str, dict[str, float]] = {}
    for row in rows:
        total = row["total"]
        if total == 0:
            continue
        result[row["sector"]] = {
            "positive": round(row["pos"] / total * 100, 1),
            "neutral": round(row["neu"] / total * 100, 1),
            "negative": round(row["neg"] / total * 100, 1),
            "avg": round((row["pos"] - row["neg"]) / total, 2),
        }

    return result


def _get_keyword_counts(start_date: date, end_date: date) -> dict[str, int]:
    """Get keyword mention counts for a date range."""
    sql = """
        SELECT unnest(na.keywords) AS keyword, COUNT(*) AS cnt
          FROM news_analysis na
          JOIN news_articles a ON na.article_id = a.id
         WHERE a.published_at >= %s::date
           AND a.published_at < (%s::date + INTERVAL '1 day')
         GROUP BY keyword
         ORDER BY cnt DESC
         LIMIT 100
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, [start_date, end_date])
        return {row["keyword"]: row["cnt"] for row in cur.fetchall()}


def _upsert_trends(entries: list[TrendEntry]) -> None:
    """Upsert trend entries into news_trends table."""
    sql = """
        INSERT INTO news_trends
            (trend_date, trend_type, sector, keyword, mention_count,
             sentiment_positive, sentiment_neutral, sentiment_negative, sentiment_avg)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (trend_date, trend_type, sector, keyword)
        DO UPDATE SET
            mention_count = EXCLUDED.mention_count,
            sentiment_positive = EXCLUDED.sentiment_positive,
            sentiment_neutral = EXCLUDED.sentiment_neutral,
            sentiment_negative = EXCLUDED.sentiment_negative,
            sentiment_avg = EXCLUDED.sentiment_avg
    """
    with connection() as conn, conn.cursor() as cur:
        for entry in entries:
            cur.execute(sql, (
                entry.trend_date,
                entry.trend_type,
                entry.sector,
                entry.keyword,
                entry.mention_count,
                entry.sentiment_positive,
                entry.sentiment_neutral,
                entry.sentiment_negative,
                entry.sentiment_avg,
            ))
