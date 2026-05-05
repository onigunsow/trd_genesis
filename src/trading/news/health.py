"""Health monitoring for news sources (SPEC-TRADING-013 Module 7).

Tracks per-source availability, auto-disables after 7 consecutive failures,
sends Telegram alerts at 3 failures.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from trading.db.session import connection

LOG = logging.getLogger(__name__)


def record_success(source_name: str) -> None:
    """Record a successful fetch for a source. Resets consecutive failures."""
    now = datetime.now(timezone.utc)
    sql = """
        INSERT INTO news_source_health (source_name, enabled, consecutive_failures,
                                         last_success, total_fetches)
        VALUES (%s, TRUE, 0, %s, 1)
        ON CONFLICT (source_name) DO UPDATE SET
            consecutive_failures = 0,
            last_success = EXCLUDED.last_success,
            total_fetches = news_source_health.total_fetches + 1,
            enabled = TRUE
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (source_name, now))


def record_failure(source_name: str, error: str) -> int:
    """Record a failed fetch for a source.

    Returns the new consecutive_failures count.
    """
    now = datetime.now(timezone.utc)
    sql = """
        INSERT INTO news_source_health (source_name, enabled, consecutive_failures,
                                         last_failure, last_error, total_fetches, total_failures)
        VALUES (%s, TRUE, 1, %s, %s, 1, 1)
        ON CONFLICT (source_name) DO UPDATE SET
            consecutive_failures = news_source_health.consecutive_failures + 1,
            last_failure = EXCLUDED.last_failure,
            last_error = EXCLUDED.last_error,
            total_fetches = news_source_health.total_fetches + 1,
            total_failures = news_source_health.total_failures + 1
        RETURNING consecutive_failures
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (source_name, now, error[:500]))
        row = cur.fetchone()
        return row["consecutive_failures"] if row else 1


def check_and_alert(source_name: str, sector: str, consecutive_failures: int) -> None:
    """Check failure count and send Telegram alerts or auto-disable.

    - 3 consecutive failures: warning alert
    - 7 consecutive failures: critical alert + auto-disable
    """
    if consecutive_failures == 3:
        _send_warning_alert(source_name, sector)
    elif consecutive_failures >= 7:
        _auto_disable_source(source_name, sector)


def _send_warning_alert(source_name: str, sector: str) -> None:
    """Send Telegram warning at 3 consecutive failures."""
    try:
        from trading.alerts.telegram import system_briefing
        error = _get_last_error(source_name)
        system_briefing(
            "NEWS HEALTH",
            f"{source_name} ({sector}) failed 3 consecutive times. "
            f"Last error: {error}. Consider review.",
        )
    except Exception as e:  # noqa: BLE001
        LOG.warning("Failed to send health warning alert: %s", e)


def _auto_disable_source(source_name: str, sector: str) -> None:
    """Auto-disable source after 7 consecutive failures + send critical alert."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE news_source_health SET enabled = FALSE WHERE source_name = %s",
            (source_name,),
        )
    LOG.warning("Auto-disabled source: %s (%s) — 7 consecutive failures", source_name, sector)

    try:
        from trading.alerts.telegram import system_briefing
        system_briefing(
            "NEWS HEALTH CRITICAL",
            f"{source_name} ({sector}) auto-DISABLED after 7 consecutive failures. "
            f"Manual re-enable: trading crawl-news --source \"{source_name}\" --force",
        )
    except Exception as e:  # noqa: BLE001
        LOG.warning("Failed to send critical health alert: %s", e)


def _get_last_error(source_name: str) -> str:
    """Get last error message for a source."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT last_error FROM news_source_health WHERE source_name = %s",
            (source_name,),
        )
        row = cur.fetchone()
        return (row["last_error"] or "unknown") if row else "unknown"


def is_source_enabled(source_name: str) -> bool:
    """Check if a source is enabled in health table."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT enabled FROM news_source_health WHERE source_name = %s",
            (source_name,),
        )
        row = cur.fetchone()
        # Default enabled if no record exists
        return row["enabled"] if row else True


def re_enable_source(source_name: str) -> None:
    """Manually re-enable a disabled source and reset failure counters."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE news_source_health SET enabled = TRUE, consecutive_failures = 0 "
            "WHERE source_name = %s",
            (source_name,),
        )
    LOG.info("Re-enabled source: %s", source_name)


def get_all_health_status() -> list[dict[str, Any]]:
    """Get health status for all tracked sources."""
    sql = """
        SELECT source_name, enabled, consecutive_failures,
               last_success, last_failure, last_error,
               total_fetches, total_failures,
               CASE WHEN total_fetches > 0
                    THEN ROUND(100.0 * (total_fetches - total_failures) / total_fetches, 1)
                    ELSE 100.0
               END AS success_rate_pct
          FROM news_source_health
         ORDER BY enabled ASC, consecutive_failures DESC, source_name
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return list(cur.fetchall())


def get_disabled_sources() -> list[str]:
    """Get list of disabled source names."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT source_name FROM news_source_health WHERE enabled = FALSE"
        )
        return [row["source_name"] for row in cur.fetchall()]


def send_weekly_summary() -> None:
    """Send weekly health summary via Telegram (optional, Sunday)."""
    statuses = get_all_health_status()
    if not statuses:
        return

    total = len(statuses)
    active = sum(1 for s in statuses if s["enabled"])
    disabled = total - active
    avg_rate = (
        sum(s["success_rate_pct"] for s in statuses) / total
        if total > 0 else 100.0
    )

    # Find sectors with degraded coverage (< 50% sources active)
    from trading.news.sources import SECTORS, get_sources_by_sector
    degraded_sectors: list[str] = []
    disabled_names = set(s["source_name"] for s in statuses if not s["enabled"])
    for sector in SECTORS:
        sector_sources = get_sources_by_sector(sector)
        if not sector_sources:
            continue
        active_count = sum(1 for s in sector_sources if s.name not in disabled_names)
        if active_count / len(sector_sources) < 0.5:
            degraded_sectors.append(sector)

    try:
        from trading.alerts.telegram import system_briefing
        msg = (
            f"Active: {active}/{total} sources\n"
            f"Disabled: {disabled}\n"
            f"Avg success rate: {avg_rate:.1f}%\n"
        )
        if degraded_sectors:
            msg += f"Degraded sectors: {', '.join(degraded_sectors)}"
        system_briefing("NEWS WEEKLY HEALTH", msg)
    except Exception as e:  # noqa: BLE001
        LOG.warning("Failed to send weekly health summary: %s", e)
