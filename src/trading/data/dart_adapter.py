"""DART (전자공시) adapter — direct httpx, no third-party SDK.

Gracefully degrades if DART_API_KEY is missing or too short (less than 32 chars).
That allows M3 to proceed even before the user finishes DART signup.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx

from trading.config import get_settings
from trading.data.cache import upsert_disclosure

LOG = logging.getLogger(__name__)
BASE = "https://opendart.fss.or.kr/api"
MIN_KEY_LEN = 32


def is_configured() -> bool:
    s = get_settings()
    if s.data_apis.dart_api_key is None:
        return False
    return len(s.data_apis.dart_api_key.get_secret_value()) >= MIN_KEY_LEN


def list_recent(start: date, end: date, page_count: int = 100) -> list[dict[str, Any]]:
    """List recent disclosures across all listed companies in [start, end].

    Returns: list of disclosure dicts. Empty list if DART unavailable.
    """
    if not is_configured():
        LOG.info("DART_API_KEY not configured (or too short) — skipping disclosure fetch")
        return []

    s = get_settings()
    key = s.data_apis.dart_api_key.get_secret_value()
    params = {
        "crtfc_key": key,
        "bgn_de": start.strftime("%Y%m%d"),
        "end_de": end.strftime("%Y%m%d"),
        "page_count": str(min(page_count, 100)),
        "page_no": "1",
    }
    out: list[dict[str, Any]] = []
    with httpx.Client(timeout=15.0) as c:
        while True:
            r = c.get(f"{BASE}/list.json", params=params)
            r.raise_for_status()
            body = r.json()
            status = body.get("status")
            if status == "013":  # no data
                break
            if status not in ("000", "013"):
                LOG.warning("DART list error: status=%s msg=%s", status, body.get("message"))
                break
            page = body.get("list", []) or []
            for row in page:
                rcept = row.get("rcept_no")
                if not rcept:
                    continue
                try:
                    rcept_dt = date(
                        int(row["rcept_dt"][:4]),
                        int(row["rcept_dt"][4:6]),
                        int(row["rcept_dt"][6:8]),
                    )
                except (KeyError, ValueError):
                    continue
                norm = {
                    "rcept_no": rcept,
                    "corp_code": row.get("corp_code", ""),
                    "corp_name": row.get("corp_name", ""),
                    "stock_code": row.get("stock_code") or None,
                    "report_nm": row.get("report_nm", ""),
                    "rcept_dt": rcept_dt,
                    "flr_nm": row.get("flr_nm", ""),
                    "rm": row.get("rm", ""),
                }
                out.append(norm)
                upsert_disclosure(norm)
            total_page = int(body.get("total_page", 1) or 1)
            cur_page = int(params["page_no"])
            if cur_page >= total_page:
                break
            params["page_no"] = str(cur_page + 1)
    return out
