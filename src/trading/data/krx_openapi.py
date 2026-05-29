"""KRX OpenAPI adapter — V-KOSPI (코스피200 변동성지수) via 파생상품지수 시세정보.

SPEC-TRADING-036 REQ-036-1(c): the only official source of the V-KOSPI is the
KRX OpenAPI ``idx/drvprod_dd_trd`` ("파생상품지수 시세정보") endpoint. The
endpoint lists *all* derivative indices for a given business day; we filter the
row whose index name is the 코스피200 변동성지수 (VKOSPI).

Current state (2026-05): the issued key returns HTTP 401 "Unauthorized API Call"
because the per-API service approval (~1 day) is still pending on the user's
side. The fetcher therefore returns ``None`` gracefully on 401 / any error /
missing key, and will auto-return real values once the service is approved —
**no code change required**.

All fetches are best-effort: this module NEVER raises (R-2 / C-9). A failure
yields ``None`` (or an ``(unavailable: ...)`` marker string), never an abort of
the macro context build or the late-cycle evaluation that reads it.

@MX:SPEC: SPEC-TRADING-036
"""

from __future__ import annotations

import logging
from datetime import date

import httpx

from trading.config import get_settings

LOG = logging.getLogger(__name__)

BASE = "https://data-dbg.krx.co.kr/svc/apis"
VKOSPI_ENDPOINT = "idx/drvprod_dd_trd"

# Candidate response keys (KRX OpenAPI wraps rows under an OutBlock_N list).
_ROW_KEYS = ("OutBlock_1", "output", "block1", "data")
# Candidate index-name / close-price field names across KRX schemas.
_NAME_KEYS = ("IDX_NM", "IDX_NAME", "idxNm", "index_name")
_CLOSE_KEYS = ("CLSPRC_IDX", "CLOSE_PRC", "clsprcIdx", "close")


def _normalise(text: str) -> str:
    """Strip whitespace so '코스피 200 변동성지수' == '코스피200 변동성지수'."""
    return "".join((text or "").split())


def _is_vkospi_row(name: str) -> bool:
    """True when ``name`` denotes the KOSPI200 변동성지수 (VKOSPI) row.

    Whitespace-insensitive (handles both '코스피 200 변동성지수' and the
    no-space '코스피200변동성지수' variants KRX has used).
    """
    n = _normalise(name)
    return "변동성지수" in n and "코스피200" in n


def _extract_rows(body: object) -> list[dict]:
    if not isinstance(body, dict):
        return []
    for key in _ROW_KEYS:
        rows = body.get(key)
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    return []


def _first(row: dict, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        if k in row and row[k] is not None:
            return str(row[k])
    return None


def fetch_vkospi(bas_dd: date | None = None) -> float | None:
    """Return the latest V-KOSPI value, or ``None`` on any failure (graceful).

    Args:
        bas_dd: Business day to query. Defaults to today (KRX returns the most
            recent trading day's snapshot for the supplied date).
    """
    settings = get_settings()
    secret = settings.data_apis.krx_openapi_key
    if secret is None:
        # No key wired yet -> graceful (unavailable). Mirrors ecos_adapter's
        # missing-key handling but never raises (build must not crash).
        LOG.info("krx_openapi: KRX_OPENAPI_KEY missing — V-KOSPI unavailable")
        return None

    day = (bas_dd or date.today()).strftime("%Y%m%d")
    url = f"{BASE}/{VKOSPI_ENDPOINT}"
    headers = {"AUTH_KEY": secret.get_secret_value()}

    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(url, headers=headers, params={"basDd": day})
        resp.raise_for_status()
        rows = _extract_rows(resp.json())
        for row in rows:
            name = _first(row, _NAME_KEYS)
            if name and _is_vkospi_row(name):
                raw = _first(row, _CLOSE_KEYS)
                return float(str(raw).replace(",", ""))
    except Exception:
        # 401 (approval pending), timeout, parse error — all swallowed (C-9).
        LOG.info("krx_openapi: V-KOSPI fetch failed (graceful unavailable)")
        return None
    return None


def vkospi_marker(bas_dd: date | None = None) -> str:
    """Return the V-KOSPI value as a display string, or an (unavailable) marker."""
    val = fetch_vkospi(bas_dd)
    if val is None:
        return "(unavailable: KRX OpenAPI 401 — 파생상품지수 서비스 승인 대기)"
    return f"{val:.2f}"
