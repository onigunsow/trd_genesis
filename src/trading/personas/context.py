"""Context assemblers — pull cached data from Postgres and shape it for personas.

Used by orchestrator to inject real macro/micro inputs into prompt templates.
SPEC-007: 추가로 static .md (data/contexts/) 로드 + dynamic memory 주입.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from trading.config import project_root
from trading.db.session import connection

LOG = logging.getLogger(__name__)

# Default watchlist (M5 — to be refined with the user)
DEFAULT_WATCHLIST = ["005930", "000660", "035420", "035720", "373220"]
TICKER_NAMES = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "035420": "NAVER",
    "035720": "카카오",
    "373220": "LG에너지솔루션",
}


def _latest_macro(source: str, series_id: str) -> dict[str, Any] | None:
    sql = """
        SELECT ts, value, units FROM macro_indicators
         WHERE source=%s AND series_id=%s
         ORDER BY ts DESC LIMIT 1
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (source, series_id))
        row = cur.fetchone()
    return dict(row) if row else None


def _latest_close(source: str, symbol: str, days: int = 30) -> dict[str, Any] | None:
    sql = """
        SELECT ts, close,
               (close - LAG(close) OVER (ORDER BY ts)) / NULLIF(LAG(close) OVER (ORDER BY ts), 0) AS pct_change
          FROM ohlcv
         WHERE source=%s AND symbol=%s AND ts >= CURRENT_DATE - %s::int
         ORDER BY ts DESC LIMIT 5
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (source, symbol, days))
        rows = list(cur.fetchall())
    if not rows:
        return None
    latest = dict(rows[0])
    return {
        "ts": str(latest["ts"]),
        "close": float(latest["close"]),
        "pct_change_1d": float(latest["pct_change"] or 0),
    }


def _technicals(symbol: str, lookback_days: int = 150) -> dict[str, Any] | None:
    """Compute simple technicals (last close, MA20, MA60, RSI14) from cached pykrx."""
    sql = """
        SELECT ts, close FROM ohlcv
         WHERE source='pykrx' AND symbol=%s
           AND ts >= CURRENT_DATE - %s::int
         ORDER BY ts
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (symbol, lookback_days))
        rows = list(cur.fetchall())
    if not rows or len(rows) < 20:
        return None
    closes = [float(r["close"]) for r in rows]
    last = closes[-1]
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else None
    # RSI(14)
    diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d for d in diffs[-14:] if d > 0]
    losses = [-d for d in diffs[-14:] if d < 0]
    avg_gain = sum(gains) / 14 if gains else 0.0
    avg_loss = sum(losses) / 14 if losses else 0.0
    rsi = 100.0 - (100.0 / (1 + (avg_gain / avg_loss))) if avg_loss > 0 else (100.0 if avg_gain > 0 else 50.0)
    return {
        "close": last,
        "ma20": round(ma20, 2),
        "ma60": round(ma60, 2) if ma60 else None,
        "rsi14": round(rsi, 1),
        "vs_ma20_pct": round((last - ma20) / ma20 * 100, 2),
    }


def _recent_disclosures(stock_codes: list[str], days: int = 3) -> list[dict[str, Any]]:
    sql = """
        SELECT rcept_no, corp_name, stock_code, report_nm, rcept_dt
          FROM disclosures
         WHERE stock_code = ANY(%s) AND rcept_dt >= CURRENT_DATE - %s::int
         ORDER BY rcept_dt DESC
         LIMIT 30
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (stock_codes, days))
        rows = list(cur.fetchall())
    return [
        {
            "rcept_dt": str(r["rcept_dt"]),
            "corp_name": r["corp_name"],
            "stock_code": r["stock_code"],
            "report_nm": r["report_nm"],
        }
        for r in rows
    ]


def _read_md(name: str) -> str:
    """SPEC-007 — Load static context .md if exists."""
    p = project_root() / "data" / "contexts" / name
    if not p.exists():
        return f"_({name} 미생성 — cron 미실행 또는 첫 운영)_"
    try:
        return p.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return f"_({name} 읽기 실패: {e})_"


def _load_memory(table: str, limit: int = 20, scope_filter: list[str] | None = None) -> list[dict[str, Any]]:
    """REQ-MEM-04-1/2: Load active memory rows + update last_accessed_at (LRU)."""
    if scope_filter:
        sql = f"""
            SELECT id, scope, scope_id, kind, summary, importance, valid_until, updated_at
              FROM {table}
             WHERE status='active' AND importance >= 3
               AND (valid_until IS NULL OR valid_until >= CURRENT_DATE)
               AND scope_id = ANY(%s)
             ORDER BY importance DESC, updated_at DESC
             LIMIT %s
        """
        params = (scope_filter, limit)
    else:
        sql = f"""
            SELECT id, scope, scope_id, kind, summary, importance, valid_until, updated_at
              FROM {table}
             WHERE status='active' AND importance >= 3
               AND (valid_until IS NULL OR valid_until >= CURRENT_DATE)
             ORDER BY importance DESC, updated_at DESC
             LIMIT %s
        """
        params = (limit,)
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
    if rows:
        ids = [r["id"] for r in rows]
        with connection() as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {table} SET last_accessed_at=NOW() WHERE id = ANY(%s)", (ids,))
    return rows


def _format_memory(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_(활성 메모리 없음)_"
    out = []
    for r in rows:
        scope = f"{r.get('scope', '')}"
        if r.get("scope_id"):
            scope += f"/{r['scope_id']}"
        valid = f" (까지 {r['valid_until']})" if r.get("valid_until") else ""
        out.append(f"- [#{r['id']} {scope} {r['kind']} imp={r['importance']}{valid}] {r['summary']}")
    return "\n".join(out)


def assemble_macro_input(today: date | None = None) -> dict[str, Any]:
    """Build macro persona input from cached FRED + ECOS + yfinance + pykrx."""
    today = today or date.today()
    fred = {}
    for sid, label in [("DFF", "Fed funds (정책금리)"),
                       ("DGS10", "10Y UST"),
                       ("DGS2", "2Y UST"),
                       ("T10Y2Y", "10Y-2Y 스프레드"),
                       ("CPIAUCSL", "US CPI"),
                       ("UNRATE", "US 실업률"),
                       ("DEXKOUS", "USD/KRW (FRED)"),
                       # M5 정밀화 — 유동성·신용·달러·유가
                       ("RRPONTSYD", "역레포 잔고 (유동성)"),
                       ("BAMLH0A0HYM2", "HY 스프레드 (신용시장)"),
                       ("DCOILWTICO", "WTI 원유"),
                       ("STLFSI4", "St.Louis 금융스트레스 지수"),
                       ("DTWEXBGS", "달러 인덱스 (DXY proxy)")]:
        m = _latest_macro("fred", sid)
        if m:
            fred[label] = f"{m['value']} ({m['ts']})"

    ecos = {}
    for label in ["BOK_BASE_RATE", "CPI"]:
        m = _latest_macro("ecos", label)
        if m:
            ecos[label] = f"{m['value']} ({m['ts']})"

    global_assets = {}
    for sym in ("^GSPC", "^IXIC", "^VIX", "KRW=X", "GLD", "TLT"):
        info = _latest_close("yfinance", sym, days=10)
        if info:
            global_assets[sym] = (
                f"close {info['close']:.2f} ({info['pct_change_1d'] * 100:+.2f}% on {info['ts']})"
            )

    # Korea market summary — KOSPI bellwether + samsung change
    samsung_info = _latest_close("pykrx", "005930", days=10)
    korea_market = (
        f"삼성전자(005930) 종가 {samsung_info['close']:,.0f} ({samsung_info['pct_change_1d'] * 100:+.2f}%) on {samsung_info['ts']}"
        if samsung_info
        else "(데이터 부족)"
    )

    # Upcoming events placeholder — production would consult an FX/CB calendar.
    upcoming_events = [
        "주중 공시·실적 발표 (DART 캐시 참조)",
        "다음 FOMC 일정은 FRED 발표 일정 따로 확인 필요",
    ]

    # SPEC-007 — static context .md + dynamic memory injection
    static_macro = _read_md("macro_context.md")
    macro_news = _read_md("macro_news.md")
    memory_rows = _load_memory("macro_memory", limit=20)

    return {
        "today": today.isoformat(),
        "fred": fred,
        "ecos": ecos,
        "global_assets": global_assets,
        "korea_market": korea_market,
        "upcoming_events": upcoming_events,
        "static_context": static_macro,
        "static_news": macro_news,
        "memory": _format_memory(memory_rows),
    }


def _fundamentals(ticker: str) -> dict[str, Any] | None:
    sql = """
        SELECT ts, market_cap, per, pbr, eps, bps, div_yield
          FROM fundamentals WHERE ticker=%s ORDER BY ts DESC LIMIT 1
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (ticker,))
        row = cur.fetchone()
    return dict(row) if row else None


def _flows_5d(ticker: str) -> dict[str, int] | None:
    """5-day cumulative net foreign / institution / individual buying."""
    sql = """
        SELECT
            COALESCE(SUM(foreign_net), 0)     AS f5,
            COALESCE(SUM(institution_net), 0) AS i5,
            COALESCE(SUM(individual_net), 0)  AS p5,
            COUNT(*) AS n
          FROM flows WHERE ticker=%s AND ts >= CURRENT_DATE - 7
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (ticker,))
        row = cur.fetchone()
    if not row or row["n"] == 0:
        return None
    return {"foreign_5d": int(row["f5"]), "institution_5d": int(row["i5"]),
            "individual_5d": int(row["p5"])}


def assemble_micro_input(
    macro_summary: str | None,
    today: date | None = None,
    watchlist: list[str] | None = None,
) -> dict[str, Any]:
    """Build micro persona input — watchlist tickers' technicals + recent disclosures."""
    today = today or date.today()
    universe = watchlist or DEFAULT_WATCHLIST

    snapshot_lines = []
    for tk in universe:
        name = TICKER_NAMES.get(tk, tk)
        tech = _technicals(tk)
        if not tech:
            snapshot_lines.append(f"- {tk} {name}: 데이터 없음")
            continue
        ma60_str = f"MA60 {tech['ma60']:,.0f}" if tech["ma60"] else "MA60 N/A"
        line = (
            f"- {tk} {name}: 종가 {tech['close']:,.0f} | MA20 {tech['ma20']:,.0f} | "
            f"{ma60_str} | RSI14 {tech['rsi14']} | vs MA20 {tech['vs_ma20_pct']:+.2f}%"
        )
        # Fundamentals (pykrx 1.2+ with KRX login)
        f = _fundamentals(tk)
        if f:
            cap_T = (f["market_cap"] / 1e12) if f.get("market_cap") else None
            cap_str = f"시총 {cap_T:.1f}조" if cap_T else ""
            per = f.get("per")
            pbr = f.get("pbr")
            div = f.get("div_yield")
            per_str = f"{per:.1f}" if per else "?"
            pbr_str = f"{pbr:.2f}" if pbr else "?"
            div_str = f"{div:.2f}" if div else "?"
            line += f"\n    {cap_str} PER {per_str} PBR {pbr_str} DIV {div_str}%"
        # Flows (foreign/institution 5-day cumulative)
        fl = _flows_5d(tk)
        if fl:
            f_e = fl["foreign_5d"] / 1e8
            i_e = fl["institution_5d"] / 1e8
            line += f"\n    수급(5D, 억원) 외인 {f_e:+.0f} / 기관 {i_e:+.0f}"
        snapshot_lines.append(line)
    universe_snapshot = "\n".join(snapshot_lines)

    disclosures = _recent_disclosures(universe, days=3)

    # SPEC-007 — static .md + dynamic memory
    static_micro = _read_md("micro_context.md")
    micro_news_md = _read_md("micro_news.md")
    # Filter memory by ticker watchlist (scope='ticker') OR sector mapping (scope='sector') if needed.
    # Watchlist tickers as scope_id.
    memory_rows = _load_memory("micro_memory", limit=20, scope_filter=universe)

    return {
        "today": today.isoformat(),
        "macro_summary": macro_summary or "(없음)",
        "universe_snapshot": universe_snapshot,
        "recent_disclosures": disclosures,
        "user_watchlist": ", ".join(f"{tk}({TICKER_NAMES.get(tk, '')})" for tk in universe),
        "static_context": static_micro,
        "static_news": micro_news_md,
        "memory": _format_memory(memory_rows),
    }
