"""SPEC-TRADING-036 REQ-036-1 — Korean market momentum snapshot.

Assembles the data behind the ``## 한국 시장 모멘텀`` macro-context section and
the 16:05 late-cycle defence evaluation. Three concerns are kept separate:

- :func:`gather_momentum` — best-effort I/O. Every sub-fetch is wrapped so a
  failure leaves that field ``None`` (robust signals from pykrx/yfinance,
  external signals from ECOS / KRX OpenAPI). It NEVER raises (R-2 / C-9): a
  dead provider must not abort the 06:00 macro build or the 16:05 evaluation.
- :func:`render_section` — pure markdown renderer (S-2 format). Missing
  external fields render as ``(unavailable: ...)``; robust fields render when
  present.

Robust signals (KOSPI/KOSDAQ daily%/5d%/52w, investor flows, VIX) are the floor
of the defence (REQ-036-3 c): even if every external signal is unavailable, the
KOSPI daily-% flash signal still works.

@MX:SPEC: SPEC-TRADING-036
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from trading.data.ecos_adapter import latest_market_funds
from trading.data.krx_openapi import fetch_vkospi, vkospi_marker
from trading.db.session import connection

LOG = logging.getLogger(__name__)

# KRX index codes for pykrx get_index_ohlcv.
KOSPI_CODE = "1001"
KOSDAQ_CODE = "2001"


@dataclass(frozen=True)
class MomentumSnapshot:
    """Point-in-time Korean market momentum. Any field may be ``None``."""

    kospi_close: float | None
    kospi_daily_pct: float | None
    kospi_5d_pct: float | None
    kospi_52w_ratio_pct: float | None
    kosdaq_close: float | None
    kosdaq_daily_pct: float | None
    kosdaq_5d_pct: float | None
    vix: float | None
    vkospi: float | None
    vkospi_marker: str
    margin_jo: float | None
    margin_stale: bool
    deposits_jo: float | None
    deposits_stale: bool
    foreign_5d: int | None
    institution_5d: int | None
    individual_5d: int | None


# ---------------------------------------------------------------------------
# Sub-gatherers (each best-effort; callers wrap in try/except)
# ---------------------------------------------------------------------------
def _index_metrics(code: str, today: date) -> dict[str, float | None]:
    """Daily %, 5d %, 52w-high ratio % + close for a KRX index via pykrx."""
    from pykrx import stock  # lazy import (heavy)

    start = (today - timedelta(days=400)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    df = stock.get_index_ohlcv(start, end, code)
    if df is None or df.empty:
        return {"close": None, "daily_pct": None, "pct_5d": None, "ratio_52w": None}

    closes = [float(c) for c in df["종가"].tolist() if c]
    if len(closes) < 2:
        return {"close": None, "daily_pct": None, "pct_5d": None, "ratio_52w": None}

    last = closes[-1]
    prev = closes[-2]
    daily = (last - prev) / prev * 100.0 if prev else None
    ref5 = closes[-6] if len(closes) >= 6 else closes[0]
    pct5 = (last - ref5) / ref5 * 100.0 if ref5 else None
    high_52w = max(closes)
    ratio = last / high_52w * 100.0 if high_52w else None
    return {"close": last, "daily_pct": daily, "pct_5d": pct5, "ratio_52w": ratio}


def _gather_index_block(today: date) -> dict[str, float | None]:
    """Both indices' metrics. pykrx failure -> empty (all None downstream)."""
    out: dict[str, float | None] = {}
    for prefix, code in (("kospi", KOSPI_CODE), ("kosdaq", KOSDAQ_CODE)):
        try:
            m = _index_metrics(code, today)
        except Exception:
            LOG.info("korea_momentum: index %s fetch failed (graceful)", code)
            m = {"close": None, "daily_pct": None, "pct_5d": None, "ratio_52w": None}
        out[f"{prefix}_close"] = m["close"]
        out[f"{prefix}_daily_pct"] = m["daily_pct"]
        out[f"{prefix}_5d_pct"] = m["pct_5d"]
        if prefix == "kospi":
            out["kospi_52w_ratio_pct"] = m["ratio_52w"]
    return out


def _gather_flows_block() -> dict[str, int | None]:
    """Market-wide foreign/institution/individual 5d net buying (억원).

    Aggregates the cached per-ticker ``flows`` table over the last ~7 calendar
    days. Values are summed and scaled to 억원 (/1e8) for display parity with
    the micro context.
    """
    sql = """
        SELECT COALESCE(SUM(foreign_net), 0)     AS f,
               COALESCE(SUM(institution_net), 0)  AS i,
               COALESCE(SUM(individual_net), 0)   AS p
          FROM flows
         WHERE ts >= CURRENT_DATE - 7
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    if not row:
        return {"foreign_5d": None, "institution_5d": None, "individual_5d": None}
    return {
        "foreign_5d": int(row["f"] // 1e8),
        "institution_5d": int(row["i"] // 1e8),
        "individual_5d": int(row["p"] // 1e8),
    }


def _gather_vix() -> float | None:
    """Latest cached VIX (^VIX, yfinance). None if no cache."""
    sql = """
        SELECT close FROM ohlcv
         WHERE source = 'yfinance' AND symbol = '^VIX'
         ORDER BY ts DESC LIMIT 1
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    return float(row["close"]) if row and row["close"] is not None else None


# ---------------------------------------------------------------------------
# gather_momentum — the single best-effort entry point
# ---------------------------------------------------------------------------
def gather_momentum(today: date | None = None) -> MomentumSnapshot:
    """Assemble a :class:`MomentumSnapshot`. Best-effort, never raises (C-9)."""
    today = today or date.today()

    def _safe(fn, default):
        try:
            return fn()
        except Exception:
            LOG.info("korea_momentum: sub-gather failed (graceful)")
            return default

    idx = _safe(lambda: _gather_index_block(today), {})
    flows = _safe(_gather_flows_block, {})
    funds = _safe(
        latest_market_funds,
        {"margin_jo": None, "margin_stale": False,
         "deposits_jo": None, "deposits_stale": False},
    )
    vkospi = _safe(fetch_vkospi, None)
    marker = _safe(vkospi_marker, "(unavailable)")
    vix = _safe(_gather_vix, None)

    return MomentumSnapshot(
        kospi_close=idx.get("kospi_close"),
        kospi_daily_pct=idx.get("kospi_daily_pct"),
        kospi_5d_pct=idx.get("kospi_5d_pct"),
        kospi_52w_ratio_pct=idx.get("kospi_52w_ratio_pct"),
        kosdaq_close=idx.get("kosdaq_close"),
        kosdaq_daily_pct=idx.get("kosdaq_daily_pct"),
        kosdaq_5d_pct=idx.get("kosdaq_5d_pct"),
        vix=vix,
        vkospi=vkospi,
        vkospi_marker=marker,
        margin_jo=funds.get("margin_jo"),
        margin_stale=bool(funds.get("margin_stale")),
        deposits_jo=funds.get("deposits_jo"),
        deposits_stale=bool(funds.get("deposits_stale")),
        foreign_5d=flows.get("foreign_5d"),
        institution_5d=flows.get("institution_5d"),
        individual_5d=flows.get("individual_5d"),
    )


# ---------------------------------------------------------------------------
# render_section — pure markdown (S-2 format)
# ---------------------------------------------------------------------------
def _pct(v: float | None) -> str:
    return f"{v:+.2f}%" if v is not None else "(unavailable)"


def _num(v: float | None, fmt: str = ",.2f") -> str:
    return format(v, fmt) if v is not None else "(unavailable)"


def _fund_line(label: str, item: str, value_jo: float | None, stale: bool) -> str:
    if value_jo is None:
        return f"- {label}: (unavailable: 캐시 없음) (ECOS 901Y056 {item})"
    if stale:
        return f"- {label}: (unavailable: stale, {value_jo:.1f}조원) (ECOS 901Y056 {item})"
    return f"- {label}: {value_jo:.1f}조원 (ECOS 901Y056 {item})"


def render_section(snap: MomentumSnapshot, today: date | None = None) -> str:
    """Render the ``## 한국 시장 모멘텀`` section (S-2 format). Pure, never raises."""
    today = today or date.today()
    lines = [f"## 한국 시장 모멘텀 (as of {today.isoformat()} KST)", ""]

    # 지수
    lines.append("### 지수")
    kospi_5d = _pct(snap.kospi_5d_pct)
    ratio = (
        f"{snap.kospi_52w_ratio_pct:.1f}%"
        if snap.kospi_52w_ratio_pct is not None
        else "(unavailable)"
    )
    lines.append(
        f"- KOSPI: {_num(snap.kospi_close)} ({_pct(snap.kospi_daily_pct)} / "
        f"{kospi_5d} over 5d) — 52주 고점 대비 {ratio}"
    )
    lines.append(
        f"- KOSDAQ: {_num(snap.kosdaq_close)} ({_pct(snap.kosdaq_daily_pct)} / "
        f"{_pct(snap.kosdaq_5d_pct)} over 5d)"
    )
    lines.append("")

    # 수급
    lines.append("### 수급 (최근 5거래일 누적, 억원)")
    def _flow(v: int | None) -> str:
        return f"{v:+,}" if v is not None else "(unavailable)"
    lines.append(f"- 외국인: {_flow(snap.foreign_5d)}")
    lines.append(f"- 기관: {_flow(snap.institution_5d)}")
    lines.append(f"- 개인: {_flow(snap.individual_5d)}")
    lines.append("")

    # 변동성 / 레버리지
    lines.append("### 변동성 / 레버리지")
    lines.append(f"- VIX: {_num(snap.vix)}")
    lines.append(f"- V-KOSPI: {snap.vkospi_marker}")
    lines.append(_fund_line("신용융자 잔고", "S23E", snap.margin_jo, snap.margin_stale))
    lines.append(_fund_line("투자자예탁금", "S23A", snap.deposits_jo, snap.deposits_stale))

    return "\n".join(lines)
