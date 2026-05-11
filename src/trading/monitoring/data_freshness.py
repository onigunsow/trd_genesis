"""SPEC-TRADING-019 REQ-019-5: Stale data monitoring + Telegram alert.

09:00 KST mon-fri cron checks the four data tables (ohlcv / fundamentals /
flows / disclosures) and fires a Telegram alert when any table exceeds the
stale threshold (KRX holiday-adjusted).

Q-5 routing decision (2026-05-11): alerts should go to the dev bot
(@onitrddev_bot). The repo currently exposes a single ``TELEGRAM_BOT_TOKEN_TRADING``
in ``.env``, so we route through the existing ``trading.alerts.telegram.
system_briefing`` helper which uses that token. The shared helper sends to the
trading prod chat — see ``# TODO(SPEC-019)`` below for the eventual dev-bot
split.

Implementation notes:
- Clock and table-latest lookups are injected so tests can monkeypatch without
  hitting the DB or the wall clock (per plan.md "Testing Strategy" hint).
- Expected-ts is computed via ``trading.scheduler.calendar.is_trading_day``
  so a Friday → Monday "Friday data" snapshot does not raise a false alert.
"""

# @MX:ANCHOR: SPEC-019 REQ-019-5 operational visibility entrypoint
# @MX:REASON: data-pipeline stale alerts gate the whole trading system
# @MX:SPEC: SPEC-TRADING-019

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from trading.db.session import connection
from trading.scheduler.calendar import is_trading_day

LOG = logging.getLogger(__name__)

# REQ-019-5 (e) — base stale threshold (KRX holidays / weekends adjusted below).
STALE_THRESHOLD_HOURS = 36
FUNDAMENTALS_STALE_DAYS = 8  # REQ-019-5 (d): weekly + 1d grace
DEFAULT_TABLES = ("ohlcv", "fundamentals", "flows", "disclosures")


def _latest_ts_from_db(table: str) -> date | None:
    """SELECT MAX(ts) for ohlcv / flows / fundamentals.

    `disclosures` uses ``rcept_dt`` instead of ``ts``.
    """
    if table == "disclosures":
        sql = "SELECT MAX(rcept_dt) AS hi FROM disclosures"
    elif table in ("ohlcv", "fundamentals", "flows"):
        sql = f"SELECT MAX(ts) AS hi FROM {table}"  # noqa: S608 (whitelisted)
    else:
        raise ValueError(f"unsupported table: {table}")

    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    if not row:
        return None
    hi = row.get("hi") if isinstance(row, dict) else row[0]
    return hi  # date | None


def _previous_trading_day(d: date) -> date:
    """Return the most recent trading day strictly before ``d``."""
    cur = d - timedelta(days=1)
    for _ in range(14):  # bounded scan — holidays don't span > 2 weeks
        if is_trading_day(cur):
            return cur
        cur = cur - timedelta(days=1)
    return cur


def _expected_ts(table: str, now: datetime) -> date:
    """REQ-019-5 (d): KRX-aware expected latest timestamp per table.

    For ohlcv/flows/disclosures: previous KRX trading day. Disclosures run
    365/yr in production but the staleness *check* runs 09:00 mon-fri, and
    after a weekend the most-recent expected disclosure publication is also
    bounded by the previous trading day (REQ-019-5 (h) false-positive
    avoidance — see Scenario 8 in acceptance.md).
    """
    today = now.date()
    if table == "fundamentals":
        # Weekly — expected the most recent Sunday + 1d grace.
        # `weekday()`: Monday = 0 .. Sunday = 6.
        days_since_sun = (today.weekday() + 1) % 7
        last_sunday = today - timedelta(days=days_since_sun)
        return last_sunday
    # ohlcv / flows / disclosures — previous KRX trading day.
    return _previous_trading_day(today)


def _hours_between(now: datetime, latest: date) -> float:
    """Return hours between `latest` (end-of-day) and `now`."""
    latest_dt = datetime.combine(latest, datetime.min.time()).replace(tzinfo=now.tzinfo)
    delta = now - latest_dt
    return delta.total_seconds() / 3600.0


def _threshold_hours_for(table: str) -> float:
    if table == "fundamentals":
        return FUNDAMENTALS_STALE_DAYS * 24
    return STALE_THRESHOLD_HOURS


def _format_alert(entries: list[dict[str, Any]]) -> str:
    """REQ-019-5 (f): structured message with table / latest / expected / stale."""
    lines = ["[SPEC-019] STALE DATA DETECTED", ""]
    for e in entries:
        if not e["stale"]:
            continue
        days_stale = (
            max(0, (e["expected"] - e["latest"]).days) if e["latest"] else "n/a"
        )
        latest_str = e["latest"].isoformat() if e["latest"] else "(empty)"
        lines.append(
            f"table: {e['table']}\n"
            f"latest: {latest_str}\n"
            f"expected: {e['expected'].isoformat()}\n"
            f"stale: {days_stale} days"
        )
        lines.append("")
    lines.append("=> check container logs / rerun refresh_market_data.py")
    return "\n".join(lines)


def _default_alert_sender(category: str, message: str) -> None:
    """Default alert sender — delegates to existing Telegram briefing helper.

    TODO(SPEC-019): Split TELEGRAM_BOT_TOKEN into dev/prod once the dev bot
    @onitrddev_bot has a dedicated token in .env. Per user decision Q-5
    (2026-05-11), SPEC-019 alerts should route to the dev bot; currently the
    repo only exposes ``TELEGRAM_BOT_TOKEN_TRADING`` so we share that token
    with prod trade briefings.
    """
    from trading.alerts.telegram import system_briefing

    system_briefing(category, message)


def check_and_alert(
    clock: Callable[[], datetime] = datetime.now,
    latest_ts_fn: Callable[[str], date | None] = _latest_ts_from_db,
    alert_sender: Callable[[str, str], None] = _default_alert_sender,
    tables: tuple[str, ...] = DEFAULT_TABLES,
) -> dict[str, Any]:
    """REQ-019-5: Check 4 data tables and alert on stale state.

    Args:
        clock: Returns current datetime (KST aware in production; naive in tests).
        latest_ts_fn: ``table_name -> latest ts (date | None)``.
        alert_sender: ``(category, message) -> None`` Telegram bridge.
        tables: List of tables to check.

    Returns:
        Summary dict ``{"entries": [...], "alert_sent": bool}``.
    """
    now = clock()
    entries: list[dict[str, Any]] = []
    stale_entries: list[dict[str, Any]] = []

    for table in tables:
        try:
            latest = latest_ts_fn(table)
        except Exception as exc:
            LOG.warning("data_freshness: %s latest_ts lookup failed: %s", table, exc)
            latest = None

        expected = _expected_ts(table, now)
        threshold = _threshold_hours_for(table)

        if latest is None:
            stale = True
            hours_stale = float("inf")
        else:
            hours_stale = _hours_between(now, latest)
            # REQ-019-5 (d): KRX-aware — if latest >= expected we're fresh.
            stale = (latest < expected) and (hours_stale > threshold)

        entry = {
            "table": table,
            "latest": latest,
            "expected": expected,
            "hours_stale": hours_stale,
            "stale": stale,
        }
        entries.append(entry)

        latest_str = latest.isoformat() if latest else "(empty)"
        LOG.info(
            "data_freshness: table=%s latest=%s expected=%s stale=%s",
            table,
            latest_str,
            expected.isoformat(),
            "yes" if stale else "ok",
        )

        if stale:
            stale_entries.append(entry)

    alert_sent = False
    if stale_entries:
        category = "SPEC-019 STALE DATA"
        message = _format_alert(entries)
        try:
            alert_sender(category, message)
            alert_sent = True
        except Exception as exc:
            LOG.exception("data_freshness alert delivery failed: %s", exc)

    return {"entries": entries, "alert_sent": alert_sent}
