"""Postgres OHLCV / macro / disclosure cache (REQ-DATA-03-2, REQ-DATA-03-3).

Idempotent upsert by (source, symbol, ts) for OHLCV.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Iterable

from trading.db.session import connection

LOG = logging.getLogger(__name__)


def upsert_ohlcv(
    source: str,
    symbol: str,
    rows: Iterable[dict[str, Any]],
) -> int:
    """Upsert OHLCV rows. Each row dict requires keys: ts, open, high, low, close, volume.

    Optional: adj_close.
    Returns count of rows written.
    """
    rows = list(rows)
    if not rows:
        return 0
    sql = """
        INSERT INTO ohlcv (source, symbol, ts, open, high, low, close, volume, adj_close)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source, symbol, ts) DO UPDATE SET
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            volume = EXCLUDED.volume,
            adj_close = EXCLUDED.adj_close,
            fetched_at = NOW()
    """
    params = [
        (
            source,
            symbol,
            r["ts"],
            float(r["open"]),
            float(r["high"]),
            float(r["low"]),
            float(r["close"]),
            int(r.get("volume", 0)),
            float(r["adj_close"]) if r.get("adj_close") is not None else None,
        )
        for r in rows
    ]
    with connection() as conn, conn.cursor() as cur:
        cur.executemany(sql, params)
    return len(params)


def cached_ohlcv(
    source: str,
    symbol: str,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    """Return cached OHLCV rows in [start, end] inclusive."""
    sql = """
        SELECT ts, open, high, low, close, volume, adj_close
          FROM ohlcv
         WHERE source = %s AND symbol = %s AND ts BETWEEN %s AND %s
         ORDER BY ts
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (source, symbol, start, end))
        return [dict(r) for r in cur.fetchall()]


def cached_range(source: str, symbol: str) -> tuple[date, date] | None:
    """Return (min_ts, max_ts) of cached rows or None if none."""
    sql = "SELECT MIN(ts) AS lo, MAX(ts) AS hi FROM ohlcv WHERE source=%s AND symbol=%s"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (source, symbol))
        row = cur.fetchone()
        if not row or row["lo"] is None:
            return None
        return (row["lo"], row["hi"])


def upsert_macro(
    source: str,
    series_id: str,
    rows: Iterable[dict[str, Any]],
    units: str | None = None,
) -> int:
    rows = list(rows)
    if not rows:
        return 0
    sql = """
        INSERT INTO macro_indicators (source, series_id, ts, value, units)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (source, series_id, ts) DO UPDATE SET
            value = EXCLUDED.value,
            units = COALESCE(EXCLUDED.units, macro_indicators.units),
            fetched_at = NOW()
    """
    params = [
        (source, series_id, r["ts"], float(r["value"]), units)
        for r in rows
    ]
    with connection() as conn, conn.cursor() as cur:
        cur.executemany(sql, params)
    return len(params)


def upsert_fundamentals(ticker: str, rows: Iterable[dict[str, Any]]) -> int:
    """Upsert daily fundamentals for a ticker."""
    rows = list(rows)
    if not rows:
        return 0
    sql = """
        INSERT INTO fundamentals (ticker, ts, market_cap, per, pbr, eps, bps, div_yield, dps)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (ticker, ts) DO UPDATE SET
            market_cap = EXCLUDED.market_cap,
            per = EXCLUDED.per,
            pbr = EXCLUDED.pbr,
            eps = EXCLUDED.eps,
            bps = EXCLUDED.bps,
            div_yield = EXCLUDED.div_yield,
            dps = EXCLUDED.dps,
            fetched_at = NOW()
    """
    params = [
        (
            ticker,
            r["ts"],
            int(r["market_cap"]) if r.get("market_cap") is not None else None,
            float(r["per"]) if r.get("per") is not None else None,
            float(r["pbr"]) if r.get("pbr") is not None else None,
            float(r["eps"]) if r.get("eps") is not None else None,
            float(r["bps"]) if r.get("bps") is not None else None,
            float(r["div_yield"]) if r.get("div_yield") is not None else None,
            float(r["dps"]) if r.get("dps") is not None else None,
        )
        for r in rows
    ]
    with connection() as conn, conn.cursor() as cur:
        cur.executemany(sql, params)
    return len(params)


def upsert_flows(ticker: str, rows: Iterable[dict[str, Any]]) -> int:
    """Upsert daily flows (foreign/institution/individual net buying)."""
    rows = list(rows)
    if not rows:
        return 0
    sql = """
        INSERT INTO flows (ticker, ts, foreign_net, institution_net, individual_net)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (ticker, ts) DO UPDATE SET
            foreign_net = EXCLUDED.foreign_net,
            institution_net = EXCLUDED.institution_net,
            individual_net = EXCLUDED.individual_net,
            fetched_at = NOW()
    """
    params = [
        (
            ticker,
            r["ts"],
            int(r.get("foreign_net", 0) or 0),
            int(r.get("institution_net", 0) or 0),
            int(r.get("individual_net", 0) or 0),
        )
        for r in rows
    ]
    with connection() as conn, conn.cursor() as cur:
        cur.executemany(sql, params)
    return len(params)


def upsert_disclosure(d: dict[str, Any]) -> bool:
    """Upsert a single DART disclosure row by rcept_no. Returns True if new."""
    sql = """
        INSERT INTO disclosures
            (rcept_no, corp_code, corp_name, stock_code, report_nm, rcept_dt, flr_nm, rm, raw)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        ON CONFLICT (rcept_no) DO NOTHING
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (
            d["rcept_no"],
            d.get("corp_code", ""),
            d.get("corp_name", ""),
            d.get("stock_code"),
            d.get("report_nm", ""),
            d["rcept_dt"],
            d.get("flr_nm", ""),
            d.get("rm", ""),
            json.dumps(d, default=str),
        ))
        return cur.rowcount > 0
