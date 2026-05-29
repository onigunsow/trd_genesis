"""ECOS (Bank of Korea) adapter — Korean macro indicators.

API: https://ecos.bok.or.kr/api/StatisticSearch/{KEY}/json/kr/{startCount}/{endCount}/
     {STAT_CODE}/{CYCLE}/{StartTime}/{EndTime}/{ITEM_CODE1}/...
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx

from trading.config import get_settings
from trading.data.cache import upsert_macro
from trading.db.session import connection

LOG = logging.getLogger(__name__)

SOURCE = "ecos"
BASE = "https://ecos.bok.or.kr/api/StatisticSearch"

# (stat_code, cycle, item_code1, label) — common series for the Macro persona.
DEFAULT_SERIES = (
    ("722Y001", "M", "0101000",  "BOK_BASE_RATE"),    # 한국은행 기준금리 (월별)
    ("200Y001", "Q", "10101",    "GDP_REAL_GROWTH"),  # 실질 GDP 성장률 (분기)
    ("901Y009", "M", "0",        "CPI"),              # 소비자물가지수 (월별)
)

# SPEC-TRADING-036 REQ-036-1(b): 증시주변자금동향 (901Y056) — slow structural
# late-cycle signals. item S23E = 신용융자 잔고 (margin/빚투), S23A = 투자자
# 예탁금 (deposits). cycle=M (monthly), unit=원. Live sanity (2026-04):
# margin ~35.7조, deposits ~124.8조. The series id stored in macro_indicators is
# the item code (S23E/S23A) so the latest read is keyed by it.
MARKET_FUNDS_SERIES = (
    ("901Y056", "M", "S23E", "S23E"),   # 신용융자 잔고
    ("901Y056", "M", "S23A", "S23A"),   # 투자자 예탁금
)

# A monthly series is considered stale once it is older than ~2 months (allows
# the normal ~1-month publication lag plus a small grace window).
_MARKET_FUNDS_STALE_DAYS = 70


def _fetch_raw(stat_code: str, cycle: str, item: str, start: date, end: date) -> list[dict[str, Any]]:
    s = get_settings()
    if s.data_apis.ecos_api_key is None:
        raise RuntimeError("ECOS_API_KEY missing")

    key = s.data_apis.ecos_api_key.get_secret_value()
    if cycle == "M":
        st = start.strftime("%Y%m")
        et = end.strftime("%Y%m")
    elif cycle == "Q":
        st = f"{start.year}Q{(start.month - 1) // 3 + 1}"
        et = f"{end.year}Q{(end.month - 1) // 3 + 1}"
    elif cycle == "D":
        st = start.strftime("%Y%m%d")
        et = end.strftime("%Y%m%d")
    else:  # yearly etc.
        st = start.strftime("%Y")
        et = end.strftime("%Y")

    url = f"{BASE}/{key}/json/kr/1/10000/{stat_code}/{cycle}/{st}/{et}/{item}"
    with httpx.Client(timeout=20.0) as c:
        r = c.get(url)
    r.raise_for_status()
    body = r.json()
    if "StatisticSearch" not in body:
        return []
    return body["StatisticSearch"].get("row", []) or []


def _parse_time(raw_time: str, cycle: str) -> date:
    if cycle == "M":
        return date(int(raw_time[:4]), int(raw_time[4:6]), 1)
    if cycle == "Q":
        # 2026Q1 → 2026-01-01 / Q2→04-01 / Q3→07-01 / Q4→10-01
        year = int(raw_time[:4])
        q = int(raw_time[-1])
        return date(year, (q - 1) * 3 + 1, 1)
    if cycle == "D":
        return date(int(raw_time[:4]), int(raw_time[4:6]), int(raw_time[6:8]))
    return date(int(raw_time[:4]), 12, 31)


def fetch_series(stat_code: str, cycle: str, item: str, label: str,
                 start: date, end: date) -> int:
    raws = _fetch_raw(stat_code, cycle, item, start, end)
    rows = []
    for r in raws:
        try:
            ts = _parse_time(r["TIME"], cycle)
            v = float(r["DATA_VALUE"])
        except (KeyError, ValueError, TypeError):
            continue
        rows.append({"ts": ts, "value": v})
    return upsert_macro(SOURCE, label, rows)


def fetch_market_funds(start: date, end: date) -> int:
    """SPEC-TRADING-036 REQ-036-1(b): cache 신용융자/예탁금 (901Y056 S23E/S23A).

    Fetches a wide window so the cache holds every monthly observation; the
    latest is read separately via :func:`latest_market_funds` (the ECOS API
    returns rows oldest-first, so caching the whole window and reading MAX(ts)
    is what surfaces the most recent value). Best-effort per series — a failed
    series is logged and skipped, never raised (C-9 graceful fetcher).

    Returns the total row count written across both series.
    """
    total = 0
    for stat_code, cycle, item, label in MARKET_FUNDS_SERIES:
        try:
            total += fetch_series(stat_code, cycle, item, label, start, end)
        except Exception:
            LOG.info("ecos: market-funds fetch failed for %s (graceful skip)", item)
    return total


def _latest_macro_row(series_id: str) -> dict[str, Any] | None:
    """Return the latest cached macro row ``{value, ts}`` for ``series_id``."""
    sql = """
        SELECT value, ts
          FROM macro_indicators
         WHERE source = %s AND series_id = %s
         ORDER BY ts DESC
         LIMIT 1
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (SOURCE, series_id))
        row = cur.fetchone()
    return dict(row) if row else None


def _one_fund(series_id: str, today: date) -> tuple[float | None, bool]:
    """Return ``(value_in_jo_won, is_stale)`` for one 901Y056 item.

    원 -> 조원 by dividing by 1e12. Missing cache -> ``(None, False)``. Any DB
    error is swallowed (graceful) and reported as missing.
    """
    try:
        row = _latest_macro_row(series_id)
    except Exception:
        LOG.info("ecos: latest read failed for %s (graceful)", series_id)
        return (None, False)
    if not row:
        return (None, False)
    value_jo = float(row["value"]) / 1e12
    ts = row["ts"]
    stale = (today - ts).days > _MARKET_FUNDS_STALE_DAYS if ts else False
    return (value_jo, stale)


def latest_market_funds(today: date | None = None) -> dict[str, Any]:
    """Return the latest 신용융자/예탁금 in 조원 plus staleness flags.

    Keys: ``margin_jo``, ``margin_stale``, ``deposits_jo``, ``deposits_stale``.
    Values are ``None`` when the cache is empty. Never raises (C-9).
    """
    today = today or date.today()
    margin_jo, margin_stale = _one_fund("S23E", today)
    deposits_jo, deposits_stale = _one_fund("S23A", today)
    return {
        "margin_jo": margin_jo,
        "margin_stale": margin_stale,
        "deposits_jo": deposits_jo,
        "deposits_stale": deposits_stale,
    }
