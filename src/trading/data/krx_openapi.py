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
from datetime import date, timedelta

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


# Maximum calendar-day lookback when no explicit date is given. Caps the walk
# so a dead service (every day empty/error) can never loop forever — a long
# weekend + holiday is at most ~4 days, so 7 comfortably covers it.
_MAX_LOOKBACK_DAYS = 7


def _today() -> date:
    """Current calendar date (test seam — patch to fix 'today')."""
    return date.today()


def _fetch_vkospi_for_day(secret_value: str, bas_dd: date) -> float | None:
    """Query exactly one business day. Returns the V-KOSPI value or ``None``.

    Never raises — a 401 / timeout / parse error / empty response all yield
    ``None`` (C-9). The caller decides whether to walk back to an earlier day.
    """
    url = f"{BASE}/{VKOSPI_ENDPOINT}"
    headers = {"AUTH_KEY": secret_value}
    day = bas_dd.strftime("%Y%m%d")
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
        LOG.info("krx_openapi: V-KOSPI fetch failed for %s (graceful)", day)
        return None
    return None


def fetch_vkospi(bas_dd: date | None = None) -> float | None:
    """Return the latest published V-KOSPI value, or ``None`` (graceful).

    Args:
        bas_dd: When given, queries exactly that business day (no fallback —
            callers and tests rely on the single-day behaviour). When ``None``,
            walks backwards from today over up to ``_MAX_LOOKBACK_DAYS`` calendar
            days and returns the first day that yields a value. This is needed
            because the derivatives-index EOD snapshot is not published intraday,
            so ``today`` is empty until the close — the walk lands on the most
            recent published trading day (skipping weekends/holidays/today).

    Never raises; returns ``None`` after the bounded lookback is exhausted.
    """
    secret = get_settings().data_apis.krx_openapi_key
    if secret is None:
        # No key wired -> graceful (unavailable). Never raises (build must not crash).
        LOG.info("krx_openapi: KRX_OPENAPI_KEY missing — V-KOSPI unavailable")
        return None
    secret_value = secret.get_secret_value()

    # Explicit date: query just that one day (current/expected behaviour).
    if bas_dd is not None:
        return _fetch_vkospi_for_day(secret_value, bas_dd)

    # No date: walk back from today to the most recent published trading day,
    # bounded by _MAX_LOOKBACK_DAYS so a dead service can't loop forever.
    today = _today()
    for offset in range(_MAX_LOOKBACK_DAYS + 1):
        value = _fetch_vkospi_for_day(secret_value, today - timedelta(days=offset))
        if value is not None:
            return value
    return None


def vkospi_marker(bas_dd: date | None = None) -> str:
    """Return the V-KOSPI value as a display string, or an (unavailable) marker.

    The marker is intentionally neutral: an unavailable V-KOSPI may be due to
    intraday-no-data, a timeout, or pending approval — not specifically 401.
    """
    val = fetch_vkospi(bas_dd)
    if val is None:
        return "(unavailable: V-KOSPI)"
    return f"{val:.2f}"
