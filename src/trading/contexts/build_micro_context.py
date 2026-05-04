"""Build micro_context.md from cached pykrx fundamentals + flows + ohlcv.

REQ-CTX-01-3: 매일 06:30 KST cron. No LLM. 워치리스트 종목별 표.
"""

from __future__ import annotations

from datetime import date

from trading.contexts.utils import contexts_dir, guarded_build, now_kst_str
from trading.db.session import connection
from trading.personas.context import DEFAULT_WATCHLIST, TICKER_NAMES


def _ticker_block(ticker: str) -> str:
    name = TICKER_NAMES.get(ticker, ticker)
    sql_ohlcv = """
        SELECT ts, close FROM ohlcv WHERE source='pykrx' AND symbol=%s
         ORDER BY ts DESC LIMIT 60
    """
    sql_fund = """
        SELECT ts, market_cap, per, pbr, eps, bps, div_yield
          FROM fundamentals WHERE ticker=%s ORDER BY ts DESC LIMIT 1
    """
    sql_flows = """
        SELECT ts, foreign_net, institution_net, individual_net
          FROM flows WHERE ticker=%s ORDER BY ts DESC LIMIT 5
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql_ohlcv, (ticker,))
        ohlcv = list(cur.fetchall())
        cur.execute(sql_fund, (ticker,))
        fund = cur.fetchone()
        cur.execute(sql_flows, (ticker,))
        flows = list(cur.fetchall())

    if not ohlcv:
        return f"### {name} ({ticker})\n_(데이터 없음)_\n"

    closes = [float(r["close"]) for r in reversed(ohlcv)]
    last = closes[-1]
    ma20 = sum(closes[-20:]) / min(20, len(closes))
    ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else None
    last_ts = ohlcv[0]["ts"]

    diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    up = sum(d for d in diffs[-14:] if d > 0)
    dn = sum(-d for d in diffs[-14:] if d < 0)
    rsi = 100 - 100 / (1 + (up / 14) / (dn / 14)) if dn > 0 else (100.0 if up > 0 else 50.0)

    parts = [f"### {name} ({ticker})", ""]
    parts.append(f"- 종가 {last:,.0f}원 ({last_ts}) | MA20 {ma20:,.0f}" +
                 (f" | MA60 {ma60:,.0f}" if ma60 else " | MA60 N/A") +
                 f" | RSI14 {rsi:.1f}")

    if fund:
        cap_T = (fund["market_cap"] / 1e12) if fund.get("market_cap") else None
        per = fund.get("per")
        pbr = fund.get("pbr")
        div = fund.get("div_yield")
        per_str = f"{float(per):.1f}" if per else "?"
        pbr_str = f"{float(pbr):.2f}" if pbr else "?"
        div_str = f"{float(div):.2f}" if div else "?"
        cap_str = f"시총 {cap_T:.1f}조" if cap_T else ""
        parts.append(f"- {cap_str} | PER {per_str} | PBR {pbr_str} | DIV {div_str}%")

    if flows:
        f5 = sum(int(r["foreign_net"]) for r in flows) / 1e8
        i5 = sum(int(r["institution_net"]) for r in flows) / 1e8
        parts.append(f"- 수급(5D, 억원): 외인 {f5:+.0f} / 기관 {i5:+.0f}")

    parts.append("")
    return "\n".join(parts)


def build() -> str:
    today = date.today()
    parts = [
        f"# Micro Context · {today.isoformat()}",
        f"_생성: {now_kst_str()} · 자동 갱신 (06:30 KST cron)_",
        "",
        "본 문서는 마이크로 페르소나 컨텍스트. 워치리스트 종목별 OHLCV + 펀더 + 수급 정리.",
        "",
        "## 워치리스트",
        "",
    ]
    for tk in DEFAULT_WATCHLIST:
        parts.append(_ticker_block(tk))
    parts.extend([
        "---",
        "_데이터 소스: pykrx (KRX 로그인), 캐시: Postgres_",
    ])
    return "\n".join(parts)


def main() -> int:
    target = contexts_dir() / "micro_context.md"
    return 0 if guarded_build("micro_context", build, target) else 1


if __name__ == "__main__":
    raise SystemExit(main())
