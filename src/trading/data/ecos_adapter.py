"""ECOS (Bank of Korea) adapter — Korean macro indicators.

API: https://ecos.bok.or.kr/api/StatisticSearch/{KEY}/json/kr/{startCount}/{endCount}/
     {STAT_CODE}/{CYCLE}/{StartTime}/{EndTime}/{ITEM_CODE1}/...
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from trading.config import get_settings
from trading.data.cache import upsert_macro

SOURCE = "ecos"
BASE = "https://ecos.bok.or.kr/api/StatisticSearch"

# (stat_code, cycle, item_code1, label) — common series for the Macro persona.
DEFAULT_SERIES = (
    ("722Y001", "M", "0101000",  "BOK_BASE_RATE"),    # 한국은행 기준금리 (월별)
    ("200Y001", "Q", "10101",    "GDP_REAL_GROWTH"),  # 실질 GDP 성장률 (분기)
    ("901Y009", "M", "0",        "CPI"),              # 소비자물가지수 (월별)
)


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
