"""Build micro_news.md from cached DART disclosures.

REQ-CTX-01-4: 영업일 06:45 KST cron. No LLM. 최근 7일 공시 정리 (워치리스트 + 섹터).
"""

from __future__ import annotations

from datetime import date, timedelta

from trading.contexts.utils import contexts_dir, guarded_build, now_kst_str
from trading.db.session import connection
from trading.personas.context import DEFAULT_WATCHLIST, TICKER_NAMES


def _watchlist_block() -> str:
    """워치리스트 종목 최근 7일 공시."""
    sql = """
        SELECT rcept_dt, corp_name, stock_code, report_nm
          FROM disclosures
         WHERE stock_code = ANY(%s) AND rcept_dt >= CURRENT_DATE - 7
         ORDER BY rcept_dt DESC, corp_name
         LIMIT 50
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (DEFAULT_WATCHLIST,))
        rows = list(cur.fetchall())
    if not rows:
        return "_(워치리스트 종목 최근 7일 공시 없음)_"
    out = ["| 일자 | 종목 | 공시명 |", "|---|---|---|"]
    for r in rows:
        name = TICKER_NAMES.get(r["stock_code"], r["corp_name"])
        out.append(f"| {r['rcept_dt']} | {name} ({r['stock_code']}) | {r['report_nm']} |")
    return "\n".join(out)


def _market_summary() -> str:
    """전 종목 최근 24h 공시 요약 (개수만)."""
    sql = """
        SELECT rcept_dt, COUNT(*) AS n
          FROM disclosures
         WHERE rcept_dt >= CURRENT_DATE - 7
         GROUP BY rcept_dt ORDER BY rcept_dt DESC
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = list(cur.fetchall())
    if not rows:
        return "_(공시 캐시 없음)_"
    out = ["| 일자 | 전체 공시 수 |", "|---|---|"]
    for r in rows:
        out.append(f"| {r['rcept_dt']} | {r['n']} |")
    return "\n".join(out)


def _key_disclosure_types() -> str:
    """주요 공시 유형 카운트 (자사주, 배당, 실적, 인적분할 등 키워드)."""
    keywords = [
        ("자기주식", "자사주"),
        ("배당", "배당"),
        ("실적", "실적"),
        ("분할", "분할"),
        ("증자", "증자"),
        ("거래정지", "거래정지"),
        ("관리종목", "관리종목"),
    ]
    out = ["| 키워드 | 최근 7일 |", "|---|---|"]
    with connection() as conn, conn.cursor() as cur:
        for kw, label in keywords:
            cur.execute(
                "SELECT COUNT(*) AS n FROM disclosures "
                "WHERE rcept_dt >= CURRENT_DATE - 7 AND report_nm ILIKE %s",
                (f"%{kw}%",),
            )
            row = cur.fetchone()
            out.append(f"| {label} | {row['n']} |")
    return "\n".join(out)


def build() -> str:
    today = date.today()
    week_ago = today - timedelta(days=7)
    parts = [
        f"# Micro News · {today.isoformat()}",
        f"_생성: {now_kst_str()} · 영업일 06:45 KST cron_",
        f"_범위: {week_ago.isoformat()} ~ {today.isoformat()} (7일)_",
        "",
        "## 워치리스트 종목 공시 (최근 7일)",
        "",
        _watchlist_block(),
        "",
        "## 시장 전체 공시 빈도",
        "",
        _market_summary(),
        "",
        "## 주요 키워드별 공시 수",
        "",
        _key_disclosure_types(),
        "",
        "---",
        "_데이터 소스: OpenDART (전자공시) · 캐시: Postgres disclosures_",
    ]
    return "\n".join(parts)


def main() -> int:
    target = contexts_dir() / "micro_news.md"
    return 0 if guarded_build("micro_news", build, target) else 1


if __name__ == "__main__":
    raise SystemExit(main())
