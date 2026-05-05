"""Intelligence Report Generator (SPEC-TRADING-014 Module 5).

Generates intelligence_macro.md and intelligence_micro.md from story clusters.
Each run OVERWRITES the files (snapshot, not accumulation).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from trading.contexts.utils import atomic_write, contexts_dir, now_kst_str
from trading.db.session import audit, connection
from trading.news.context_builder import (
    MACRO_SECTORS,
    SECTOR_DISPLAY_NAMES,
    TICKER_SECTOR_MAP,
)
from trading.news.intelligence.trends import compute_weekly_trends, get_sector_sentiments

LOG = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")

# REQ-INTEL-05-7: Maximum clusters per file
MAX_MACRO_CLUSTERS = 50
MAX_MICRO_CLUSTERS_PER_SECTOR = 30


def _format_cluster_entry(cluster: dict) -> str:
    """Format a single story cluster for the intelligence report.

    REQ-INTEL-05-4: Standard format with [투자 주목] prefix for relevant stories.
    """
    title = cluster["representative_title"]
    impact = cluster["impact_max"]
    source_count = cluster["source_count"]
    portfolio_relevant = cluster.get("portfolio_relevant", False)
    first_published = cluster.get("first_published")

    # Date formatting
    if isinstance(first_published, datetime):
        date_str = first_published.astimezone(KST).strftime("%Y-%m-%d")
    else:
        date_str = str(date.today())

    # Source names from article_ids (query or use available data)
    sources_str = f"{source_count}건"

    # Header with optional [투자 주목] tag
    tag = "[투자 주목] " if (portfolio_relevant and impact >= 4) else ""
    header = f"### {tag}{title} (Impact: {impact}/5)"

    # Source line
    source_line = f"_Sources: {sources_str} | {date_str}_"

    # Summary lines (from representative article's analysis)
    summary = _get_cluster_summary(cluster)
    summary_lines = []
    if summary:
        for line in summary.split("\n"):
            line = line.strip()
            if line:
                summary_lines.append(f"- {line}")
    if not summary_lines:
        summary_lines = ["- (분석 데이터 없음)"]

    parts = [header, source_line]
    parts.extend(summary_lines)
    parts.append("")
    return "\n".join(parts)


def _get_cluster_summary(cluster: dict) -> str:
    """Get the summary for the representative article in a cluster."""
    article_ids = cluster.get("article_ids", [])
    if not article_ids:
        return ""

    # Get the summary from the highest-impact article
    sql = """
        SELECT na.summary_2line
          FROM news_analysis na
         WHERE na.article_id = ANY(%s)
         ORDER BY na.impact_score DESC
         LIMIT 1
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (article_ids,))
            row = cur.fetchone()
            return row["summary_2line"] if row else ""
    except Exception:  # noqa: BLE001
        return ""


def _format_trend_snapshot(trend_date: date | None = None) -> str:
    """Generate the trend snapshot section for intelligence reports.

    REQ-INTEL-05-6: Rising/falling keywords + sector sentiments.
    """
    if trend_date is None:
        trend_date = date.today()

    # Get weekly trends
    weekly = compute_weekly_trends(end_date=trend_date)
    rising = weekly.get("rising", [])
    falling = weekly.get("falling", [])

    # Get sector sentiments
    sentiments = get_sector_sentiments(trend_date)

    # Date range for display
    week_start = trend_date - timedelta(days=6)
    date_range = f"{week_start.strftime('%m/%d')}~{trend_date.strftime('%m/%d')}"

    lines = [
        f"## 주간 트렌드 ({date_range})",
        f"상승 키워드: {', '.join(rising) if rising else '(데이터 부족)'}",
        f"하락 키워드: {', '.join(falling) if falling else '(데이터 부족)'}",
    ]

    # Sector sentiments
    if sentiments:
        sentiment_parts = []
        for sector_key, data in sentiments.items():
            display_name = SECTOR_DISPLAY_NAMES.get(sector_key, sector_key)
            # Determine dominant sentiment label
            pos = data.get("positive", 0)
            neg = data.get("negative", 0)
            if pos > neg:
                label = f"Positive({pos:.0f}%)"
            elif neg > pos:
                label = f"Negative({neg:.0f}%)"
            else:
                label = f"Neutral"
            sentiment_parts.append(f"{display_name}: {label}")
        lines.append(f"섹터 센티멘트: {', '.join(sentiment_parts[:6])}")
    else:
        lines.append("섹터 센티멘트: (데이터 부족)")

    return "\n".join(lines)


def _get_clusters_by_sector(
    cluster_date: date,
    sectors: list[str],
    max_per_sector: int,
) -> dict[str, list[dict]]:
    """Fetch story clusters grouped by sector.

    REQ-INTEL-05-5: Sorted by impact_max DESC, then source_count DESC.
    """
    sql = """
        SELECT *
          FROM story_clusters
         WHERE cluster_date = %s
           AND sector = ANY(%s)
         ORDER BY impact_max DESC, source_count DESC
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (cluster_date, sectors))
        rows = list(cur.fetchall())

    # Group by sector with per-sector limit
    result: dict[str, list[dict]] = {s: [] for s in sectors}
    for row in rows:
        sector = row["sector"]
        if sector in result and len(result[sector]) < max_per_sector:
            result[sector].append(row)

    return result


def build_intelligence_macro(cluster_date: date | None = None) -> str:
    """Generate intelligence_macro.md content.

    REQ-INTEL-05-2: Global macro intelligence (macro, finance, energy sectors).
    REQ-INTEL-05-3: OVERWRITES each run.
    """
    if cluster_date is None:
        cluster_date = date.today()

    parts = [
        f"# Intelligence Report (Macro) — {cluster_date.isoformat()}",
        f"_Generated: {now_kst_str()} | Source: News Intelligence Pipeline_",
        "",
    ]

    sector_clusters = _get_clusters_by_sector(
        cluster_date, MACRO_SECTORS, MAX_MACRO_CLUSTERS,
    )

    total_clusters = 0
    for sector in MACRO_SECTORS:
        display_name = SECTOR_DISPLAY_NAMES.get(sector, sector)
        clusters = sector_clusters.get(sector, [])

        if not clusters:
            # REQ-INTEL-05-8: Show DATA UNAVAILABLE for empty sectors
            parts.append(f"## {display_name} [DATA UNAVAILABLE — awaiting analysis]")
            parts.append("")
            continue

        parts.append(f"## {display_name} ({len(clusters)} stories)")
        parts.append("")
        for cluster in clusters:
            parts.append(_format_cluster_entry(cluster))
            total_clusters += 1
            if total_clusters >= MAX_MACRO_CLUSTERS:
                break
        if total_clusters >= MAX_MACRO_CLUSTERS:
            break

    # Trend snapshot at bottom
    parts.append("---")
    parts.append("")
    parts.append(_format_trend_snapshot(cluster_date))
    parts.append("")
    parts.append("---")
    parts.append(f"_Total: {total_clusters} stories | Pipeline: SPEC-014 | Model: Haiku 4.5_")

    return "\n".join(parts)


def build_intelligence_micro(
    cluster_date: date | None = None,
    watchlist: list[str] | None = None,
) -> str:
    """Generate intelligence_micro.md content.

    REQ-INTEL-05-2: Sector-specific intelligence matching watchlist.
    """
    if cluster_date is None:
        cluster_date = date.today()

    # Determine target sectors from watchlist
    if watchlist:
        target_sectors = list(set(
            TICKER_SECTOR_MAP.get(t, "stock_market") for t in watchlist
        ))
    else:
        # Full coverage mode
        target_sectors = list(SECTOR_DISPLAY_NAMES.keys())

    parts = [
        f"# Intelligence Report (Micro) — {cluster_date.isoformat()}",
        f"_Generated: {now_kst_str()} | Source: News Intelligence Pipeline_",
        "",
    ]

    sector_clusters = _get_clusters_by_sector(
        cluster_date, target_sectors, MAX_MICRO_CLUSTERS_PER_SECTOR,
    )

    total_clusters = 0
    for sector in target_sectors:
        display_name = SECTOR_DISPLAY_NAMES.get(sector, sector)
        clusters = sector_clusters.get(sector, [])

        if not clusters:
            parts.append(f"## {display_name} [DATA UNAVAILABLE — awaiting analysis]")
            parts.append("")
            continue

        parts.append(f"## {display_name} ({len(clusters)} stories)")
        parts.append("")
        for cluster in clusters:
            parts.append(_format_cluster_entry(cluster))
            total_clusters += 1

    # Trend snapshot
    parts.append("---")
    parts.append("")
    parts.append(_format_trend_snapshot(cluster_date))
    parts.append("")
    parts.append("---")
    parts.append(
        f"_Total: {total_clusters} stories | "
        f"Sectors: {len(target_sectors)} | "
        f"Watchlist: {len(watchlist) if watchlist else 'full coverage'}_"
    )

    return "\n".join(parts)


def write_intelligence_reports(
    *,
    cluster_date: date | None = None,
    watchlist: list[str] | None = None,
) -> tuple[int, int]:
    """Build and write both intelligence .md files.

    Returns: (macro_bytes, micro_bytes)
    """
    if cluster_date is None:
        cluster_date = date.today()

    out_dir = contexts_dir()
    macro_path = out_dir / "intelligence_macro.md"
    micro_path = out_dir / "intelligence_micro.md"

    # Build macro
    macro_content = build_intelligence_macro(cluster_date)
    atomic_write(macro_path, macro_content)

    # Build micro
    micro_content = build_intelligence_micro(cluster_date, watchlist)
    atomic_write(micro_path, micro_content)

    audit("NEWS_INTEL_REPORT_OK", actor="reporter", details={
        "cluster_date": str(cluster_date),
        "macro_bytes": len(macro_content),
        "micro_bytes": len(micro_content),
        "macro_path": str(macro_path),
        "micro_path": str(micro_path),
    })

    LOG.info(
        "Intelligence reports written: macro=%d bytes, micro=%d bytes",
        len(macro_content), len(micro_content),
    )
    return len(macro_content), len(micro_content)
