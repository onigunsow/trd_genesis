"""Intelligence Report Generator (SPEC-TRADING-014 Module 5).

Generates intelligence_macro.md and intelligence_micro.md from story clusters.
Each run OVERWRITES the files (snapshot, not accumulation).

Key filtering rules:
- Macro report: ONLY classification == "macro_market_moving", impact >= 3
- Micro report: ONLY classification IN ("sector_specific", "company_specific"), impact >= 3
- Noise (classification == "noise") and low-impact (<=2) articles are EXCLUDED from both.
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
MAX_MACRO_STORIES = 15
MAX_MICRO_STORIES_PER_SECTOR = 10

# Minimum impact score for inclusion in reports
MIN_REPORT_IMPACT = 3


def _format_cluster_entry(cluster: dict) -> str:
    """Format a single story cluster for the intelligence report.

    New format with action-oriented arrows and keywords visible.
    """
    title = cluster["representative_title"]
    impact = cluster["impact_max"]
    source_count = cluster["source_count"]
    first_published = cluster.get("first_published")
    keywords = cluster.get("keywords", [])

    # Date formatting
    if isinstance(first_published, datetime):
        date_str = first_published.astimezone(KST).strftime("%Y-%m-%d")
    else:
        date_str = str(date.today())

    # Keywords display (top 3)
    keywords_str = ", ".join(keywords[:3]) if keywords else ""

    # Header with [투자 주목] for high impact
    tag = "[투자 주목] " if impact >= 4 else ""
    header = f"### {tag}{title} (Impact: {impact}/5)"

    # Metadata line with source count, date, keywords
    meta_parts = [f"{source_count} sources", date_str]
    if keywords_str:
        meta_parts.append(f"Keywords: {keywords_str}")
    meta_line = f"_{' | '.join(meta_parts)}_"

    # Investment implications with arrow prefix
    summary = _get_cluster_summary(cluster)
    impl_lines = []
    if summary:
        for line in summary.split("\n"):
            line = line.strip()
            if line:
                impl_lines.append(f"\u2192 {line}")
    if not impl_lines:
        impl_lines = ["\u2192 (투자 시사점 분석 대기중)"]

    parts = [header, meta_line]
    parts.extend(impl_lines)
    parts.append("")
    return "\n".join(parts)


def _get_cluster_summary(cluster: dict) -> str:
    """Get the investment implication for the representative article in a cluster."""
    article_ids = cluster.get("article_ids", [])
    if not article_ids:
        return ""

    # Get the summary from the highest-impact article (excluding noise)
    sql = """
        SELECT na.summary_2line
          FROM news_analysis na
         WHERE na.article_id = ANY(%s)
           AND na.classification != 'noise'
           AND na.impact_score >= %s
         ORDER BY na.impact_score DESC
         LIMIT 1
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (article_ids, MIN_REPORT_IMPACT))
            row = cur.fetchone()
            return row["summary_2line"] if row else ""
    except Exception:  # noqa: BLE001
        return ""


def _get_cluster_classification(cluster: dict) -> str:
    """Determine the dominant classification for a cluster.

    Uses the classification of the highest-impact article in the cluster.
    """
    article_ids = cluster.get("article_ids", [])
    if not article_ids:
        return "company_specific"

    sql = """
        SELECT na.classification
          FROM news_analysis na
         WHERE na.article_id = ANY(%s)
           AND na.classification != 'noise'
         ORDER BY na.impact_score DESC
         LIMIT 1
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (article_ids,))
            row = cur.fetchone()
            return row["classification"] if row else "company_specific"
    except Exception:  # noqa: BLE001
        return "company_specific"


def _is_valid_implication(cluster: dict) -> bool:
    """Check if the cluster has a valid (non-empty, non-title-restating) implication."""
    summary = _get_cluster_summary(cluster)
    if not summary or summary == "(투자 관련성 없음 - 자동 필터링)":
        return False
    return True


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
                label = "Neutral"
            sentiment_parts.append(f"{display_name}: {label}")
        lines.append(f"섹터 센티멘트: {', '.join(sentiment_parts[:6])}")
    else:
        lines.append("섹터 센티멘트: (데이터 부족)")

    return "\n".join(lines)


def _get_clusters_for_report(
    cluster_date: date,
    classification_filter: list[str],
    min_impact: int = MIN_REPORT_IMPACT,
    max_stories: int = 50,
    sectors: list[str] | None = None,
) -> list[dict]:
    """Fetch story clusters filtered by article classification and minimum impact.

    This is the key filtering function that ensures:
    - Only articles with the correct classification appear in each report
    - Impact >= min_impact (default 3) filters out low-relevance noise
    - Clusters without valid investment implications are excluded
    """
    # Base query: join clusters with their articles' analysis to filter by classification
    sql = """
        SELECT sc.*,
               (
                   SELECT na.classification
                     FROM news_analysis na
                    WHERE na.article_id = ANY(sc.article_ids)
                      AND na.classification = ANY(%s)
                    ORDER BY na.impact_score DESC
                    LIMIT 1
               ) AS dominant_classification
          FROM story_clusters sc
         WHERE sc.cluster_date = %s
           AND sc.impact_max >= %s
           AND EXISTS (
               SELECT 1 FROM news_analysis na
                WHERE na.article_id = ANY(sc.article_ids)
                  AND na.classification = ANY(%s)
                  AND na.impact_score >= %s
           )
    """
    params: list = [classification_filter, cluster_date, min_impact, classification_filter, min_impact]

    if sectors:
        sql += " AND sc.sector = ANY(%s)"
        params.append(sectors)

    sql += " ORDER BY sc.impact_max DESC, sc.source_count DESC LIMIT %s"
    params.append(max_stories)

    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = list(cur.fetchall())

    # Additional filter: exclude clusters with empty/invalid implications
    valid_clusters = []
    for row in rows:
        if _is_valid_implication(row):
            valid_clusters.append(row)

    return valid_clusters


def build_intelligence_macro(cluster_date: date | None = None) -> str:
    """Generate intelligence_macro.md content.

    ONLY includes articles classified as "macro_market_moving" with impact >= 3.
    Maximum 15 stories. Excludes PR/CSR/company-specific news entirely.
    """
    if cluster_date is None:
        cluster_date = date.today()

    parts = [
        f"# Intelligence Report (Macro) \u2014 {cluster_date.isoformat()}",
        f"_Generated: {now_kst_str()} | Source: News Intelligence Pipeline_",
        "",
    ]

    # Fetch ONLY macro_market_moving clusters with impact >= 3
    clusters = _get_clusters_for_report(
        cluster_date=cluster_date,
        classification_filter=["macro_market_moving"],
        min_impact=MIN_REPORT_IMPACT,
        max_stories=MAX_MACRO_STORIES,
    )

    if len(clusters) < 3:
        # Not enough market-moving events to report
        parts.append("## 시장 변동 이벤트 없음")
        parts.append("")
        parts.append("_금일 주요 시장 변동 이벤트가 감지되지 않았습니다._")
        parts.append("_Impact 3 이상의 macro_market_moving 뉴스가 3건 미만입니다._")
        parts.append("")
    else:
        parts.append(f"## Market-Moving Events ({len(clusters)} stories)")
        parts.append("")
        for cluster in clusters:
            parts.append(_format_cluster_entry(cluster))

    # Trend snapshot at bottom
    parts.append("---")
    parts.append("")
    parts.append(_format_trend_snapshot(cluster_date))
    parts.append("")
    parts.append("---")
    parts.append(
        f"_Total: {len(clusters)} stories | "
        f"Filter: macro_market_moving, impact>={MIN_REPORT_IMPACT} | "
        f"Pipeline: SPEC-014 | Model: Haiku 4.5_"
    )

    return "\n".join(parts)


def build_intelligence_micro(
    cluster_date: date | None = None,
    watchlist: list[str] | None = None,
) -> str:
    """Generate intelligence_micro.md content.

    ONLY includes articles classified as "sector_specific" or "company_specific"
    with impact >= 3. Grouped by sector, max 10 per sector.
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
        f"# Intelligence Report (Micro) \u2014 {cluster_date.isoformat()}",
        f"_Generated: {now_kst_str()} | Source: News Intelligence Pipeline_",
        "",
    ]

    # Fetch sector_specific and company_specific clusters
    all_clusters = _get_clusters_for_report(
        cluster_date=cluster_date,
        classification_filter=["sector_specific", "company_specific"],
        min_impact=MIN_REPORT_IMPACT,
        max_stories=MAX_MICRO_STORIES_PER_SECTOR * len(target_sectors),
        sectors=target_sectors,
    )

    # Group by sector
    sector_groups: dict[str, list[dict]] = {s: [] for s in target_sectors}
    for cluster in all_clusters:
        sector = cluster.get("sector", "stock_market")
        if sector in sector_groups and len(sector_groups[sector]) < MAX_MICRO_STORIES_PER_SECTOR:
            sector_groups[sector].append(cluster)

    total_stories = 0
    for sector in target_sectors:
        display_name = SECTOR_DISPLAY_NAMES.get(sector, sector)
        clusters = sector_groups.get(sector, [])

        if not clusters:
            continue  # Skip empty sectors (no [DATA UNAVAILABLE] noise)

        parts.append(f"## {display_name} ({len(clusters)} stories)")
        parts.append("")
        for cluster in clusters:
            parts.append(_format_cluster_entry(cluster))
            total_stories += 1

    if total_stories == 0:
        parts.append("## 섹터별 주요 뉴스 없음")
        parts.append("")
        parts.append("_금일 Impact 3 이상의 섹터/종목 뉴스가 감지되지 않았습니다._")
        parts.append("")

    # Trend snapshot
    parts.append("---")
    parts.append("")
    parts.append(_format_trend_snapshot(cluster_date))
    parts.append("")
    parts.append("---")
    parts.append(
        f"_Total: {total_stories} stories | "
        f"Filter: sector_specific+company_specific, impact>={MIN_REPORT_IMPACT} | "
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
