"""Story Clustering — groups related articles (SPEC-TRADING-014 Module 2).

Uses title similarity (SequenceMatcher > 0.6) and keyword overlap (>= 2 shared)
within a 24-hour window. No LLM calls — purely computational.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher

from trading.db.session import audit, connection
from trading.news.intelligence.models import StoryCluster

LOG = logging.getLogger(__name__)

TITLE_SIMILARITY_THRESHOLD = 0.6
KEYWORD_OVERLAP_MIN = 2
CLUSTER_WINDOW_HOURS = 24


def _normalize_title_for_comparison(title: str) -> str:
    """Normalize title for similarity comparison."""
    # Lowercase, strip punctuation, collapse whitespace
    t = title.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _title_similarity(a: str, b: str) -> float:
    """Compute title similarity using SequenceMatcher."""
    na = _normalize_title_for_comparison(a)
    nb = _normalize_title_for_comparison(b)
    return SequenceMatcher(None, na, nb).ratio()


def _keyword_overlap(kw_a: list[str], kw_b: list[str]) -> int:
    """Count shared keywords between two sets."""
    return len(set(kw_a) & set(kw_b))


def _should_cluster(art_a: dict, art_b: dict) -> bool:
    """Determine if two articles should be in the same cluster.

    REQ-INTEL-02-2: Title similarity > 0.6 OR keyword overlap >= 2,
    both within 24-hour published_at window.
    """
    # Check time window first (fast rejection)
    pub_a = art_a["published_at"]
    pub_b = art_b["published_at"]
    if isinstance(pub_a, datetime) and isinstance(pub_b, datetime):
        if abs((pub_a - pub_b).total_seconds()) > CLUSTER_WINDOW_HOURS * 3600:
            return False

    # Title similarity check
    if _title_similarity(art_a["title"], art_b["title"]) > TITLE_SIMILARITY_THRESHOLD:
        return True

    # Keyword overlap check
    kw_a = art_a.get("keywords", [])
    kw_b = art_b.get("keywords", [])
    if kw_a and kw_b and _keyword_overlap(kw_a, kw_b) >= KEYWORD_OVERLAP_MIN:
        return True

    return False


def _find_clusters(articles: list[dict]) -> list[list[int]]:
    """Union-Find clustering algorithm.

    REQ-INTEL-02-3: No LLM, purely computational.
    Returns list of clusters (each cluster = list of article indices).
    """
    n = len(articles)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    # Compare all pairs within window (O(n^2) but n <= 500 per SPEC)
    for i in range(n):
        for j in range(i + 1, n):
            if _should_cluster(articles[i], articles[j]):
                union(i, j)

    # Group by root
    groups: dict[int, list[int]] = {}
    for i in range(n):
        root = find(i)
        groups.setdefault(root, []).append(i)

    return list(groups.values())


def _compute_dominant_sentiment(sentiments: list[str]) -> str:
    """Return most frequent sentiment value."""
    if not sentiments:
        return "neutral"
    counter = Counter(sentiments)
    return counter.most_common(1)[0][0]


def get_analyzed_articles_for_clustering(
    *,
    hours: int = CLUSTER_WINDOW_HOURS,
    sector: str | None = None,
) -> list[dict]:
    """Fetch analyzed articles within the clustering window.

    Excludes noise articles (classification='noise') to prevent
    PR/CSR/HR articles from forming clusters.
    """
    sql = """
        SELECT a.id, a.title, a.source_name, a.sector, a.published_at,
               na.impact_score, na.keywords, na.sentiment, na.summary_2line,
               na.classification
          FROM news_articles a
          JOIN news_analysis na ON na.article_id = a.id
         WHERE a.published_at >= NOW() - make_interval(hours => %s)
           AND na.classification != 'noise'
           AND na.impact_score >= 1
    """
    params: list = [hours]
    if sector:
        sql += " AND a.sector = %s"
        params.append(sector)
    sql += " ORDER BY a.published_at DESC"

    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def cluster_stories(
    *,
    sector: str | None = None,
    cluster_date: date | None = None,
) -> list[StoryCluster]:
    """Run story clustering on recent analyzed articles.

    REQ-INTEL-02-9: Re-clusters articles from last 24 hours.
    """
    if cluster_date is None:
        cluster_date = date.today()

    articles = get_analyzed_articles_for_clustering(sector=sector)
    if not articles:
        LOG.info("No analyzed articles available for clustering")
        return []

    # Find clusters using union-find
    index_groups = _find_clusters(articles)

    clusters: list[StoryCluster] = []
    for group_indices in index_groups:
        group_articles = [articles[i] for i in group_indices]

        # REQ-INTEL-02-7: impact_max = max impact score in cluster
        impact_max = max(a["impact_score"] for a in group_articles)

        # REQ-INTEL-02-5: representative_title = title of highest-impact article
        best_article = max(group_articles, key=lambda a: a["impact_score"])
        representative_title = best_article["title"]

        # REQ-INTEL-02-6: source_count = distinct source_name values
        source_names = list(set(a["source_name"] for a in group_articles))
        source_count = len(source_names)

        # REQ-INTEL-02-8: sentiment_dominant = most frequent sentiment
        sentiments = [a["sentiment"] for a in group_articles]
        sentiment_dominant = _compute_dominant_sentiment(sentiments)

        # Aggregate keywords (top keywords across cluster)
        all_keywords: list[str] = []
        for a in group_articles:
            all_keywords.extend(a.get("keywords") or [])
        keyword_counter = Counter(all_keywords)
        top_keywords = [kw for kw, _ in keyword_counter.most_common(5)]

        # Sector from the group (should be same for all; take first)
        sector_val = group_articles[0]["sector"]

        # First published timestamp
        first_published = min(a["published_at"] for a in group_articles)

        article_ids = [a["id"] for a in group_articles]

        clusters.append(StoryCluster(
            representative_title=representative_title,
            article_ids=article_ids,
            source_count=source_count,
            impact_max=impact_max,
            sector=sector_val,
            keywords=top_keywords,
            sentiment_dominant=sentiment_dominant,
            first_published=first_published,
            cluster_date=cluster_date,
        ))

    # Store clusters in DB (replace existing for same cluster_date)
    _store_clusters(clusters, cluster_date, sector)

    audit("NEWS_INTEL_CLUSTER_OK", actor="clustering", details={
        "clusters_formed": len(clusters),
        "articles_clustered": sum(len(c.article_ids) for c in clusters),
        "cluster_date": str(cluster_date),
    })

    LOG.info("Clustering complete: %d clusters from %d articles", len(clusters), len(articles))
    return clusters


def _store_clusters(
    clusters: list[StoryCluster],
    cluster_date: date,
    sector: str | None = None,
) -> None:
    """Store clusters in DB, replacing existing entries for the same date."""
    delete_sql = "DELETE FROM story_clusters WHERE cluster_date = %s"
    params: list = [cluster_date]
    if sector:
        delete_sql += " AND sector = %s"
        params.append(sector)

    insert_sql = """
        INSERT INTO story_clusters
            (representative_title, article_ids, source_count, impact_max,
             sector, keywords, sentiment_dominant, first_published,
             cluster_date, portfolio_relevant, relevance_tickers)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """

    with connection() as conn, conn.cursor() as cur:
        cur.execute(delete_sql, params)
        for cluster in clusters:
            cur.execute(insert_sql, (
                cluster.representative_title,
                cluster.article_ids,
                cluster.source_count,
                cluster.impact_max,
                cluster.sector,
                cluster.keywords,
                cluster.sentiment_dominant,
                cluster.first_published,
                cluster.cluster_date,
                cluster.portfolio_relevant,
                cluster.relevance_tickers,
            ))
            row = cur.fetchone()
            if row:
                cluster.id = row["id"]
