"""Build macro_context.md from cached FRED + ECOS + yfinance data.

REQ-CTX-01-2: 매일 06:00 KST cron. No LLM. Uses cached macro_indicators + ohlcv.
"""

from __future__ import annotations

from datetime import date

from trading.contexts.utils import contexts_dir, guarded_build, now_kst_str
from trading.db.session import connection


def _latest_macro_table() -> str:
    """FRED + ECOS 최신 시리즈 표."""
    sql = """
        SELECT DISTINCT ON (source, series_id)
               source, series_id, ts, value
          FROM macro_indicators
         ORDER BY source, series_id, ts DESC
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = list(cur.fetchall())
    if not rows:
        return "_(거시지표 캐시 없음 — fetch-data --fred / --ecos 실행 필요)_"

    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(r["source"], []).append(dict(r))

    out = []
    labels = {
        "DFF": "Fed funds (정책금리, %)",
        "DGS10": "10Y UST 수익률 (%)",
        "DGS2": "2Y UST 수익률 (%)",
        "T10Y2Y": "10Y-2Y 스프레드 (%p)",
        "CPIAUCSL": "US CPI (지수)",
        "UNRATE": "US 실업률 (%)",
        "DEXKOUS": "USD/KRW (FRED)",
        "RRPONTSYD": "역레포 잔고 (조달러)",
        "BAMLH0A0HYM2": "HY 스프레드 (%p)",
        "DCOILWTICO": "WTI 원유 ($/배럴)",
        "STLFSI4": "St.Louis 금융스트레스 지수",
        "DTWEXBGS": "달러 인덱스 (DXY proxy)",
        "BOK_BASE_RATE": "한국은행 기준금리 (%)",
        "CPI": "한국 CPI (지수)",
    }

    for source in sorted(grouped):
        out.append(f"### {source.upper()}\n")
        out.append("| 지표 | 값 | 기준일 |")
        out.append("|---|---|---|")
        for r in grouped[source]:
            label = labels.get(r["series_id"], r["series_id"])
            out.append(f"| {label} | {float(r['value']):.4f} | {r['ts']} |")
        out.append("")
    return "\n".join(out)


def _global_assets_table() -> str:
    """yfinance 글로벌 자산 최근 종가 + 5일 변동."""
    sql = """
        WITH latest AS (
            SELECT DISTINCT ON (symbol) symbol, ts AS last_ts, close AS last_close
              FROM ohlcv WHERE source='yfinance' ORDER BY symbol, ts DESC
        ),
        prev5 AS (
            SELECT o.symbol, o.close AS prev_close
              FROM ohlcv o
              INNER JOIN latest l ON o.symbol = l.symbol
             WHERE o.source='yfinance' AND o.ts <= l.last_ts - INTERVAL '5 days'
             ORDER BY o.symbol, o.ts DESC
        )
        SELECT l.symbol, l.last_ts, l.last_close,
               (SELECT prev_close FROM prev5 p WHERE p.symbol=l.symbol LIMIT 1) AS p5
          FROM latest l ORDER BY l.symbol
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = list(cur.fetchall())
    if not rows:
        return "_(글로벌 자산 캐시 없음)_"
    out = ["| 심볼 | 종가 | 5D 변동 | 기준일 |", "|---|---|---|---|"]
    name = {
        "^GSPC": "S&P500", "^IXIC": "Nasdaq", "^VIX": "VIX",
        "KRW=X": "USD/KRW", "GLD": "Gold ETF", "TLT": "20Y UST ETF",
    }
    for r in rows:
        sym = r["symbol"]
        last = float(r["last_close"])
        p5 = float(r["p5"]) if r["p5"] is not None else None
        chg = f"{(last - p5) / p5 * 100:+.2f}%" if p5 else "—"
        out.append(f"| {name.get(sym, sym)} ({sym}) | {last:,.2f} | {chg} | {r['last_ts']} |")
    return "\n".join(out)


def _korea_market_table() -> str:
    """KOSPI 대표 종목 5일 흐름."""
    sql = """
        WITH latest AS (
            SELECT DISTINCT ON (symbol) symbol, ts AS last_ts, close AS last_close
              FROM ohlcv WHERE source='pykrx' ORDER BY symbol, ts DESC
        )
        SELECT l.symbol, l.last_ts, l.last_close
          FROM latest l ORDER BY l.symbol
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = list(cur.fetchall())
    if not rows:
        return "_(한국 시장 캐시 없음)_"
    name = {
        "005930": "삼성전자", "000660": "SK하이닉스", "035420": "NAVER",
        "035720": "카카오", "373220": "LG에너지솔루션",
    }
    out = ["| 종목 | 종가 | 기준일 |", "|---|---|---|"]
    for r in rows:
        out.append(f"| {name.get(r['symbol'], r['symbol'])} ({r['symbol']}) | {float(r['last_close']):,.0f} | {r['last_ts']} |")
    return "\n".join(out)


def build() -> str:
    today = date.today()
    parts = [
        f"# Macro Context · {today.isoformat()}",
        f"_생성: {now_kst_str()} · 자동 갱신 (06:00 KST cron)_",
        "",
        "본 문서는 페르소나 컨텍스트 주입용. raw 데이터의 정리본 — 분석·예측 X.",
        "",
        "## 거시 지표 (FRED + ECOS, 최신값)",
        _latest_macro_table(),
        "",
        "## 글로벌 자산 흐름 (yfinance)",
        _global_assets_table(),
        "",
        "## 한국 대형주 흐름 (워치리스트)",
        _korea_market_table(),
        "",
        "---",
        "_데이터 소스: FRED, ECOS (한국은행), yfinance, pykrx · 캐시: Postgres_",
    ]
    return "\n".join(parts)


def main() -> int:
    target = contexts_dir() / "macro_context.md"
    return 0 if guarded_build("macro_context", build, target) else 1


if __name__ == "__main__":
    raise SystemExit(main())
