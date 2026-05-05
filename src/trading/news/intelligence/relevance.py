"""Portfolio Relevance Tagger (SPEC-TRADING-014 Module 4).

Cross-references story clusters with current watchlist/portfolio holdings.
Tags high-impact portfolio-relevant clusters with [투자 주목].
"""

from __future__ import annotations

import logging
from datetime import date

from trading.db.session import audit, connection
from trading.news.context_builder import TICKER_SECTOR_MAP
from trading.news.intelligence.models import StoryCluster

LOG = logging.getLogger(__name__)

# REQ-INTEL-04-3: Minimum impact score for [투자 주목] tag
IMPACT_ALERT_THRESHOLD = 4
# REQ-INTEL-04-4: Critical alert threshold
IMPACT_CRITICAL_THRESHOLD = 5


def get_watchlist_sectors() -> dict[str, list[str]]:
    """Get sector -> tickers mapping from current watchlist/portfolio.

    Falls back to TICKER_SECTOR_MAP if no dynamic watchlist is available.
    Returns: {sector: [ticker1, ticker2, ...]}
    """
    # Try to load watchlist from DB (portfolio positions or explicit watchlist)
    tickers = _load_watchlist_tickers()

    if not tickers:
        # REQ-INTEL-04-5: Empty watchlist -> full coverage mode
        return {}

    # Build sector -> tickers mapping
    sector_tickers: dict[str, list[str]] = {}
    for ticker in tickers:
        sector = TICKER_SECTOR_MAP.get(ticker, "stock_market")
        sector_tickers.setdefault(sector, []).append(ticker)

    return sector_tickers


def _load_watchlist_tickers() -> list[str]:
    """Load current watchlist/portfolio tickers from DB."""
    try:
        with connection() as conn, conn.cursor() as cur:
            # Try positions table first (actual holdings)
            cur.execute("""
                SELECT DISTINCT ticker FROM positions
                WHERE quantity > 0
            """)
            tickers = [row["ticker"] for row in cur.fetchall()]

            # Also check watchlist if it exists
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = 'watchlist'
                )
            """)
            if cur.fetchone()["exists"]:
                cur.execute("SELECT DISTINCT ticker FROM watchlist WHERE active = true")
                tickers.extend(row["ticker"] for row in cur.fetchall())

            return list(set(tickers))
    except Exception:  # noqa: BLE001
        LOG.debug("Could not load watchlist from DB, using TICKER_SECTOR_MAP")
        return list(TICKER_SECTOR_MAP.keys())


def tag_portfolio_relevance(
    *,
    cluster_date: date | None = None,
    sector: str | None = None,
) -> dict[str, int]:
    """Tag story clusters with portfolio relevance.

    REQ-INTEL-04-2: Cross-reference clusters with TICKER_SECTOR_MAP.
    REQ-INTEL-04-3: [투자 주목] when impact >= 4 AND sector matches portfolio.
    REQ-INTEL-04-5: Full coverage mode when watchlist is empty.

    Returns: {"tagged": N, "alerts_sent": N}
    """
    if cluster_date is None:
        cluster_date = date.today()

    sector_tickers = get_watchlist_sectors()
    full_coverage_mode = len(sector_tickers) == 0

    # Fetch today's clusters
    clusters = _get_clusters_for_date(cluster_date, sector)

    tagged_count = 0
    alerts_sent = 0

    for cluster in clusters:
        is_relevant = False
        relevant_tickers: list[str] = []

        if full_coverage_mode:
            # REQ-INTEL-04-5: Tag ALL clusters with impact >= threshold
            is_relevant = cluster["impact_max"] >= IMPACT_ALERT_THRESHOLD
        else:
            # Check if cluster sector matches any portfolio sector
            cluster_sector = cluster["sector"]
            if cluster_sector in sector_tickers:
                is_relevant = True
                relevant_tickers = sector_tickers[cluster_sector]

        # Update portfolio_relevant flag
        portfolio_relevant = is_relevant
        should_tag = is_relevant and cluster["impact_max"] >= IMPACT_ALERT_THRESHOLD

        _update_cluster_relevance(
            cluster["id"],
            portfolio_relevant=portfolio_relevant,
            relevance_tickers=relevant_tickers,
        )

        if should_tag:
            tagged_count += 1

        # REQ-INTEL-04-4: Telegram alert for impact == 5 AND portfolio-relevant
        if portfolio_relevant and cluster["impact_max"] >= IMPACT_CRITICAL_THRESHOLD:
            _send_critical_alert(cluster)
            alerts_sent += 1

    result = {"tagged": tagged_count, "alerts_sent": alerts_sent}

    audit("NEWS_INTEL_RELEVANCE_OK", actor="relevance", details={
        "cluster_date": str(cluster_date),
        "clusters_evaluated": len(clusters),
        "tagged_count": tagged_count,
        "alerts_sent": alerts_sent,
        "full_coverage_mode": full_coverage_mode,
    })

    LOG.info(
        "Relevance tagging: %d clusters, %d tagged [투자 주목], %d alerts",
        len(clusters), tagged_count, alerts_sent,
    )
    return result


def _get_clusters_for_date(cluster_date: date, sector: str | None = None) -> list[dict]:
    """Fetch story clusters for a given date."""
    sql = "SELECT * FROM story_clusters WHERE cluster_date = %s"
    params: list = [cluster_date]
    if sector:
        sql += " AND sector = %s"
        params.append(sector)
    sql += " ORDER BY impact_max DESC"

    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def _update_cluster_relevance(
    cluster_id: int,
    *,
    portfolio_relevant: bool,
    relevance_tickers: list[str],
) -> None:
    """Update portfolio relevance fields on a cluster."""
    sql = """
        UPDATE story_clusters
           SET portfolio_relevant = %s,
               relevance_tickers = %s
         WHERE id = %s
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (portfolio_relevant, relevance_tickers, cluster_id))


def _send_critical_alert(cluster: dict) -> None:
    """Send Telegram alert for critical portfolio-relevant news.

    REQ-INTEL-04-4: When impact == 5 AND portfolio-relevant.
    """
    try:
        from trading.alerts.telegram import system_briefing
        title = cluster["representative_title"]
        sector = cluster["sector"]
        msg = (
            f"[NEWS ALERT] {title} "
            f"(Impact 5/5, Sector: {sector}) "
            f"— 포트폴리오 관련 고위험 뉴스 감지"
        )
        system_briefing("News Intelligence", msg)
    except Exception as e:  # noqa: BLE001
        LOG.warning("Failed to send critical news alert: %s", e)
