"""SPEC-TRADING-023 REQ-023-2: dynamic_tickers registry CRUD.

Stores tickers auto-expanded into the data universe via micro persona
recommendations of universe-out symbols. SPEC-019's daily refresh cron picks
these up automatically through ``get_data_universe()``.

Public API:
    register(ticker, source) -> bool   # True if newly inserted; False if updated
    list_active()             -> list[str]   # sorted ticker codes (ascending)

Capacity is bounded by ``DEFAULT_CAP`` (100, configurable via env
``DYNAMIC_UNIVERSE_CAP``). When the cap is reached, the row with the oldest
``first_seen_at`` is evicted (FIFO) in the same transaction as the INSERT to
avoid races (REQ-023-2 (d)).
"""

# @MX:ANCHOR: SPEC-023 REQ-023-2 dynamic_tickers CRUD with FIFO eviction
# @MX:REASON: fan_in >= 3 (refresh_market_data, universe.py, daily_report.py)
# @MX:SPEC: SPEC-TRADING-023

from __future__ import annotations

import logging
import os

from trading.db.session import connection

LOG = logging.getLogger(__name__)

DEFAULT_CAP = 100


def _cap() -> int:
    """Resolve the active cap from env (override) or DEFAULT_CAP."""
    raw = os.environ.get("DYNAMIC_UNIVERSE_CAP")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            LOG.warning("invalid DYNAMIC_UNIVERSE_CAP=%r; using default", raw)
    return DEFAULT_CAP


def register(ticker: str, source: str) -> bool:
    """REQ-023-2 (b): insert or refresh a ticker's registry entry.

    Returns True on first insert, False when the row already existed (the
    ``last_used_at`` column is then bumped to NOW()).

    When ``DEFAULT_CAP`` is reached AND the ticker is new, the row with the
    oldest ``first_seen_at`` is evicted FIFO inside the same transaction
    (REQ-023-2 (d)). Existing-row updates never trigger eviction.
    """
    cap = _cap()
    with connection() as conn, conn.cursor() as cur:
        # ON CONFLICT branch: ticker already present -> bump last_used_at,
        # return False without engaging the eviction path.
        cur.execute("SELECT 1 FROM dynamic_tickers WHERE ticker = %s", (ticker,))
        existed = cur.fetchone() is not None
        if existed:
            cur.execute(
                "INSERT INTO dynamic_tickers (ticker, source) VALUES (%s, %s) "
                "ON CONFLICT (ticker) DO UPDATE SET last_used_at = NOW()",
                (ticker, source),
            )
            LOG.info(
                "dynamic_universe touched ticker=%s source=%s (already registered)",
                ticker,
                source,
            )
            return False

        # New row -> enforce cap before INSERT.
        cur.execute("SELECT COUNT(*) AS n FROM dynamic_tickers")
        row = cur.fetchone()
        count = int(row["n"] if isinstance(row, dict) else row[0])

        if count >= cap:
            cur.execute(
                "SELECT ticker, first_seen_at FROM dynamic_tickers "
                "ORDER BY first_seen_at ASC LIMIT 1"
            )
            oldest = cur.fetchone()
            if oldest:
                oldest_ticker = (
                    oldest["ticker"]
                    if isinstance(oldest, dict)
                    else oldest[0]
                )
                oldest_first_seen = (
                    oldest["first_seen_at"]
                    if isinstance(oldest, dict)
                    else oldest[1]
                )
                cur.execute(
                    "DELETE FROM dynamic_tickers WHERE ticker = %s",
                    (oldest_ticker,),
                )
                LOG.info(
                    "dynamic_universe evicted ticker=%s (FIFO, was first_seen=%s)",
                    oldest_ticker,
                    oldest_first_seen,
                )

        cur.execute(
            "INSERT INTO dynamic_tickers (ticker, source) VALUES (%s, %s) "
            "ON CONFLICT (ticker) DO NOTHING",
            (ticker, source),
        )
        LOG.info(
            "dynamic_universe registered ticker=%s source=%s", ticker, source
        )
        return True


def list_active() -> list[str]:
    """REQ-023-2 (c): return all registered tickers, sorted ascending."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker FROM dynamic_tickers ORDER BY ticker")
        out: list[str] = []
        for row in cur.fetchall():
            t = row["ticker"] if isinstance(row, dict) else row[0]
            if t:
                out.append(str(t))
    return out
