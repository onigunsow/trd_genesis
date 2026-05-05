"""Intelligence Pipeline Scheduler (SPEC-TRADING-014 Module 6).

Orchestrates the full intelligence pipeline:
1. Analyze unanalyzed articles (Module 1)
2. Cluster stories (Module 2)
3. Update daily trends (Module 3)
4. Tag portfolio relevance (Module 4)
5. Generate intelligence reports (Module 5)

Also provides CLI entry point and feature flag checking.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from trading.db.session import audit, connection

LOG = logging.getLogger(__name__)

# Feature flag name in system_state
FEATURE_FLAG = "news_intelligence_enabled"


@dataclass
class PipelineResult:
    """Result metrics from a full pipeline run."""

    articles_analyzed: int = 0
    clusters_formed: int = 0
    trends_updated: int = 0
    relevance_tagged: int = 0
    intelligence_files_generated: bool = False
    total_cost_krw: float = 0.0
    duration_seconds: float = 0.0
    success: bool = True
    error: str | None = None


def is_intelligence_enabled() -> bool:
    """Check if the news intelligence feature flag is enabled.

    REQ-INTEL-06-6: Controlled by NEWS_INTELLIGENCE_ENABLED in system_state.
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT news_intelligence_enabled FROM system_state LIMIT 1
            """)
            row = cur.fetchone()
            if row is None:
                return True  # Default enabled
            return bool(row.get("news_intelligence_enabled", True))
    except Exception:  # noqa: BLE001
        # Column might not exist yet — default to enabled
        return True


def run_intelligence_pipeline(
    *,
    sector: str | None = None,
    force: bool = False,
) -> PipelineResult:
    """Execute the full intelligence pipeline.

    REQ-INTEL-06-2: Full pipeline in order (analyze -> cluster -> trend -> relevance -> report).
    """
    start_time = time.time()
    result = PipelineResult()

    try:
        # Module 1: Article Analysis
        from trading.news.intelligence.analyzer import analyze_articles
        analysis_metrics = analyze_articles(sector=sector, force=force)
        result.articles_analyzed = analysis_metrics.articles_processed
        result.total_cost_krw = analysis_metrics.total_cost_krw

        # Module 2: Story Clustering
        from trading.news.intelligence.clustering import cluster_stories
        clusters = cluster_stories(sector=sector)
        result.clusters_formed = len(clusters)

        # Module 3: Trend Aggregation
        from trading.news.intelligence.trends import compute_daily_trends
        trends = compute_daily_trends(sector=sector)
        result.trends_updated = len(trends)

        # Module 4: Portfolio Relevance Tagging
        from trading.news.intelligence.relevance import tag_portfolio_relevance
        relevance_result = tag_portfolio_relevance(sector=sector)
        result.relevance_tagged = relevance_result["tagged"]

        # Module 5: Intelligence Report Generation
        from trading.news.intelligence.reporter import write_intelligence_reports
        macro_bytes, micro_bytes = write_intelligence_reports()
        result.intelligence_files_generated = macro_bytes > 0 and micro_bytes > 0

        result.success = True

    except Exception as e:  # noqa: BLE001
        result.success = False
        result.error = f"{type(e).__name__}: {e}"
        LOG.exception("Intelligence pipeline failed")

    result.duration_seconds = time.time() - start_time

    # Audit logging
    if result.success:
        audit("NEWS_INTELLIGENCE_RUN_OK", actor="intelligence_pipeline", details={
            "articles_analyzed": result.articles_analyzed,
            "clusters_formed": result.clusters_formed,
            "trends_updated": result.trends_updated,
            "relevance_tagged": result.relevance_tagged,
            "intelligence_files_generated": result.intelligence_files_generated,
            "total_cost_krw": round(result.total_cost_krw, 2),
            "duration_seconds": round(result.duration_seconds, 2),
        })
    else:
        audit("NEWS_INTELLIGENCE_RUN_FAIL", actor="intelligence_pipeline", details={
            "error": result.error,
            "duration_seconds": round(result.duration_seconds, 2),
        })
        # Check for consecutive failures
        _check_consecutive_failures()

    LOG.info(
        "Pipeline %s: %d analyzed, %d clusters, %.1f KRW, %.1fs",
        "OK" if result.success else "FAIL",
        result.articles_analyzed, result.clusters_formed,
        result.total_cost_krw, result.duration_seconds,
    )
    return result


def scheduled_run() -> None:
    """Entry point for cron-triggered runs (legacy: single-step pipeline).

    REQ-INTEL-06-6: Check feature flag before executing.

    NOTE: Prefer scheduled_export() + scheduled_import() for the host CLI path.
    This function is kept for backward compatibility and fallback scenarios.
    """
    if not is_intelligence_enabled():
        LOG.info("News intelligence pipeline skipped (feature disabled)")
        audit("NEWS_INTELLIGENCE_SKIP", actor="scheduler", details={
            "reason": "feature_disabled",
        })
        return

    run_intelligence_pipeline()


def scheduled_export() -> None:
    """Container cron step 1: Export unanalyzed articles for host CLI processing.

    Runs at :05 after each crawl. Pre-filters noise, writes prompt to shared volume.
    The host cron picks up the pending file 5 minutes later.
    """
    if not is_intelligence_enabled():
        LOG.info("News intelligence export skipped (feature disabled)")
        return

    from trading.news.intelligence.analyzer import export_pending_for_host
    try:
        count = export_pending_for_host()
        LOG.info("Scheduled export: %d articles pending for host analysis", count)
    except Exception:  # noqa: BLE001
        LOG.exception("Scheduled export failed")


def scheduled_import() -> None:
    """Container cron step 2: Import host CLI results and run remaining pipeline.

    Runs at :15 after each crawl cycle. If host results are available, imports
    them and runs clustering/trends/relevance/reporter. If no results are found
    (host CLI failed or hasn't run), falls back to Haiku API for analysis.
    """
    if not is_intelligence_enabled():
        LOG.info("News intelligence import skipped (feature disabled)")
        return

    from trading.news.intelligence.analyzer import (
        RESULTS_FILE,
        import_host_results,
    )

    imported = 0
    try:
        imported = import_host_results()
    except Exception:  # noqa: BLE001
        LOG.exception("Host result import failed")

    if imported == 0 and not RESULTS_FILE.exists():
        # Fallback: host CLI did not produce results, try Haiku API
        LOG.warning("No host CLI results found — falling back to Haiku API")
        audit("NEWS_INTEL_FALLBACK_HAIKU", actor="scheduler", details={
            "reason": "no_host_results",
        })
        try:
            from trading.news.intelligence.analyzer import analyze_articles
            metrics = analyze_articles()
            imported = metrics.articles_processed
        except Exception:  # noqa: BLE001
            LOG.exception("Haiku fallback also failed")

    if imported > 0:
        # Run the rest of the pipeline (clustering, trends, relevance, reporter)
        _run_post_analysis_pipeline()


def _run_post_analysis_pipeline() -> None:
    """Run pipeline modules 2-5 (after analysis results are in DB)."""
    try:
        from trading.news.intelligence.clustering import cluster_stories
        clusters = cluster_stories()
        LOG.info("Post-analysis: %d clusters formed", len(clusters))

        from trading.news.intelligence.trends import compute_daily_trends
        trends = compute_daily_trends()
        LOG.info("Post-analysis: %d trends updated", len(trends))

        from trading.news.intelligence.relevance import tag_portfolio_relevance
        relevance = tag_portfolio_relevance()
        LOG.info("Post-analysis: %d articles tagged for relevance", relevance["tagged"])

        from trading.news.intelligence.reporter import write_intelligence_reports
        macro_bytes, micro_bytes = write_intelligence_reports()
        LOG.info("Post-analysis: reports written (macro=%d, micro=%d bytes)", macro_bytes, micro_bytes)

    except Exception:  # noqa: BLE001
        LOG.exception("Post-analysis pipeline failed")


def cli_analyze_news(
    *,
    force: bool = False,
    sector: str | None = None,
) -> int:
    """CLI entry point for `trading analyze-news`.

    REQ-INTEL-06-3: Manual trigger with --force and --sector options.
    REQ-INTEL-06-7: --force overrides feature flag.
    """
    if not force and not is_intelligence_enabled():
        print("News intelligence is disabled. Use --force to override.")
        return 1

    if force and not is_intelligence_enabled():
        LOG.info("Manual override — feature flag disabled but --force used")
        audit("NEWS_INTELLIGENCE_MANUAL_OVERRIDE", actor="cli", details={
            "force": True,
            "sector": sector,
        })

    print(f"Running intelligence pipeline{' (sector: ' + sector + ')' if sector else ''}...")
    result = run_intelligence_pipeline(sector=sector, force=force)

    if result.success:
        print(
            f"Done: {result.articles_analyzed} analyzed, "
            f"{result.clusters_formed} clusters, "
            f"{result.relevance_tagged} [투자 주목], "
            f"{result.total_cost_krw:.1f} KRW, "
            f"{result.duration_seconds:.1f}s"
        )
        return 0
    else:
        print(f"Pipeline failed: {result.error}")
        return 1


def _check_consecutive_failures() -> None:
    """Check if 3+ consecutive failures occurred and send alert.

    REQ-INTEL-06-5: Telegram alert on 3+ consecutive failures.
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT event_type FROM audit_log
                 WHERE event_type IN ('NEWS_INTELLIGENCE_RUN_OK', 'NEWS_INTELLIGENCE_RUN_FAIL')
                 ORDER BY created_at DESC
                 LIMIT 3
            """)
            recent = [row["event_type"] for row in cur.fetchall()]

        if len(recent) >= 3 and all(e == "NEWS_INTELLIGENCE_RUN_FAIL" for e in recent):
            from trading.alerts.telegram import system_briefing
            system_briefing(
                "News Intelligence",
                "[NEWS INTEL] 3회 연속 분석 파이프라인 실패. 확인 필요.",
            )
            LOG.error("3 consecutive intelligence pipeline failures — alert sent")
    except Exception as e:  # noqa: BLE001
        LOG.warning("Failed to check consecutive failures: %s", e)
