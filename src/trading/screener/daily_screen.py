"""Daily stock screener -- 2-phase LLM-driven screening.

Phase 1 (container, 06:30 KST): Mechanical filter produces ~50 candidates from OHLCV
universe, then exports candidates + intelligence context to data/pending_screen.json.

Phase 2 (host cron, 06:35 KST): Claude Code CLI reads pending_screen.json, applies
LLM judgment (news + macro + data synthesis), selects final ~20 tickers, writes
data/screened_tickers.json.

Mechanical screening criteria (Phase 1):
- Market cap > 1 trillion KRW (liquidity)
- Average daily volume value > 10 billion KRW (tradeable)
- RSI between 30-70 (not overheated, not crashed)
- OR: PER < 15 (value opportunity)
- OR: foreign 5-day net buy > 0 (smart money inflow)

Phase 1 outputs top ~50 candidates for LLM review.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from trading.config import project_root
from trading.db.session import connection
from trading.personas.context import DEFAULT_WATCHLIST

LOG = logging.getLogger(__name__)
SCREEN_FILE = project_root() / "data" / "screened_tickers.json"
PENDING_FILE = project_root() / "data" / "pending_screen.json"
MAX_CANDIDATES = 50
MAX_SCREENED = 20


def _get_universe_tickers() -> list[str]:
    """Get broad universe of tickers from OHLCV data (pykrx source).

    Selects tickers with recent trading activity (last 5 days).
    """
    sql = """
        SELECT DISTINCT symbol
          FROM ohlcv
         WHERE source = 'pykrx'
           AND ts >= CURRENT_DATE - 7
         ORDER BY symbol
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return [row["symbol"] for row in cur.fetchall()]


def _screen_ticker(ticker: str) -> dict[str, Any] | None:
    """Screen a single ticker against criteria. Returns score dict or None."""
    # Get fundamentals
    sql_fund = """
        SELECT market_cap, per, pbr, div_yield
          FROM fundamentals
         WHERE ticker = %s
         ORDER BY ts DESC LIMIT 1
    """
    # Get recent OHLCV for RSI + volume
    sql_ohlcv = """
        SELECT ts, close, volume
          FROM ohlcv
         WHERE source = 'pykrx' AND symbol = %s
         ORDER BY ts DESC LIMIT 60
    """
    # Get flows for foreign net
    sql_flows = """
        SELECT COALESCE(SUM(foreign_net), 0) AS f5
          FROM flows
         WHERE ticker = %s AND ts >= CURRENT_DATE - 7
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql_fund, (ticker,))
        fund = cur.fetchone()

        cur.execute(sql_ohlcv, (ticker,))
        ohlcv = list(cur.fetchall())

        cur.execute(sql_flows, (ticker,))
        flows = cur.fetchone()

    if not ohlcv or len(ohlcv) < 20:
        return None

    # Market cap filter: > 1 trillion KRW
    market_cap = float(fund["market_cap"]) if fund and fund.get("market_cap") else 0
    if market_cap < 1e12:
        return None

    # Volume filter: avg daily trading value > 10 billion KRW (last 20 days)
    closes = [float(r["close"]) for r in reversed(ohlcv[:20])]
    volumes = [int(r["volume"]) for r in reversed(ohlcv[:20])]
    avg_value = sum(c * v for c, v in zip(closes, volumes)) / len(closes)
    if avg_value < 10e9:
        return None

    # Calculate RSI(14)
    all_closes = [float(r["close"]) for r in reversed(ohlcv)]
    diffs = [all_closes[i] - all_closes[i - 1] for i in range(1, len(all_closes))]
    gains = [d for d in diffs[-14:] if d > 0]
    losses = [-d for d in diffs[-14:] if d < 0]
    avg_gain = sum(gains) / 14 if gains else 0.0
    avg_loss = sum(losses) / 14 if losses else 0.0
    rsi = 100.0 - (100.0 / (1 + (avg_gain / avg_loss))) if avg_loss > 0 else (
        100.0 if avg_gain > 0 else 50.0
    )

    # PER
    per = float(fund["per"]) if fund and fund.get("per") else None

    # Foreign 5-day net
    foreign_5d = int(flows["f5"]) if flows else 0

    # Scoring: multiple criteria (any one qualifies)
    score = 0.0
    reasons: list[str] = []

    # RSI 30-70 (healthy range)
    if 30 <= rsi <= 70:
        score += 2.0
        reasons.append(f"RSI={rsi:.1f} (healthy)")

    # PER < 15 (value)
    if per is not None and 0 < per < 15:
        score += 1.5
        reasons.append(f"PER={per:.1f} (value)")

    # Foreign net buy (smart money)
    if foreign_5d > 0:
        score += 1.0
        # Extra score for strong foreign buying
        if foreign_5d > 50e8:  # > 50 billion
            score += 0.5
        reasons.append(f"Foreign 5D net={foreign_5d / 1e8:+.0f} billion")

    # Market cap bonus (larger = more liquid)
    if market_cap > 10e12:
        score += 0.5

    # Must have at least one qualifying criterion
    if score < 1.0:
        return None

    return {
        "ticker": ticker,
        "score": score,
        "market_cap_t": market_cap / 1e12,
        "rsi": round(rsi, 1),
        "per": per,
        "foreign_5d_b": foreign_5d / 1e8,
        "avg_value_b": avg_value / 1e9,
        "reasons": reasons,
    }


def _read_intelligence_md(name: str) -> str:
    """Read intelligence .md file from data/contexts/."""
    p = project_root() / "data" / "contexts" / name
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""


def run() -> dict[str, Any]:
    """Run Phase 1: mechanical filter + export pending_screen.json for Claude CLI.

    Produces ~50 candidates via mechanical screening, then writes
    data/pending_screen.json with candidates, intelligence context, and LLM prompt.
    Also writes a fallback screened_tickers.json with mechanical top 20.
    """
    universe = _get_universe_tickers()
    LOG.info("daily_screen: scanning %d tickers from pykrx universe", len(universe))

    # Exclude base watchlist (they're always included)
    base_set = set(DEFAULT_WATCHLIST)
    candidates = [t for t in universe if t not in base_set]

    results: list[dict[str, Any]] = []
    for ticker in candidates:
        try:
            result = _screen_ticker(ticker)
            if result:
                results.append(result)
        except Exception as e:  # noqa: BLE001
            LOG.debug("daily_screen: error screening %s: %s", ticker, e)
            continue

    # Sort by score descending, take top N for LLM review
    results.sort(key=lambda x: x["score"], reverse=True)
    top_candidates = results[:MAX_CANDIDATES]

    # Phase 1 output: write mechanical fallback (top 20) to screened_tickers.json
    # This serves as fallback if Claude CLI fails or hasn't run yet.
    mechanical_top = results[:MAX_SCREENED]
    fallback_output = {
        "date": date.today().isoformat(),
        "total_scanned": len(candidates),
        "qualified": len(results),
        "selected": len(mechanical_top),
        "tickers": [r["ticker"] for r in mechanical_top],
        "details": mechanical_top,
        "source": "mechanical",
    }
    SCREEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCREEN_FILE.write_text(json.dumps(fallback_output, ensure_ascii=False, indent=2))

    # Load intelligence context for LLM prompt
    intel_macro = _read_intelligence_md("intelligence_macro.md")
    intel_micro = _read_intelligence_md("intelligence_micro.md")

    # Build pending_screen.json for Claude CLI (Phase 2)
    pending_candidates = [
        {
            "ticker": r["ticker"],
            "name": "",
            "rsi": r.get("rsi"),
            "per": r.get("per"),
            "volume_b": round(r.get("avg_value_b", 0), 1),
            "foreign_5d_b": round(r.get("foreign_5d_b", 0), 1),
            "market_cap_t": round(r.get("market_cap_t", 0), 1),
            "score": r.get("score", 0),
            "reasons": r.get("reasons", []),
        }
        for r in top_candidates
    ]

    prompt = (
        "당신은 한국 주식시장 전문 애널리스트입니다. "
        "아래 시장 분석과 종목 데이터를 보고, "
        "오늘 매매 관심을 가져야 할 종목 20개를 선정하세요. "
        "각 종목에 대해 선정 이유를 1줄로 작성하세요.\n\n"
        "## 시장 분석 (매크로 인텔리전스)\n"
        f"{intel_macro}\n\n"
        "## 섹터/종목 분석 (마이크로 인텔리전스)\n"
        f"{intel_micro}\n\n"
        "## 기계적 스크리닝 후보 (데이터)\n"
        f"{json.dumps(pending_candidates, ensure_ascii=False, indent=2)}\n\n"
        "위 데이터를 종합 판단하여 매매 관심 종목 20개를 선정하세요.\n"
        "JSON 배열로만 응답하세요 (다른 텍스트 없이):\n"
        '[{"ticker": "005930", "name": "삼성전자", "reason": "선정 이유 1줄"}]'
    )

    pending_output = {
        "date": date.today().isoformat(),
        "total_scanned": len(candidates),
        "qualified": len(results),
        "candidate_count": len(pending_candidates),
        "candidates": pending_candidates,
        "prompt": prompt,
    }

    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_FILE.write_text(json.dumps(pending_output, ensure_ascii=False, indent=2))
    LOG.info(
        "daily_screen: %d qualified, %d candidates exported for LLM, "
        "%d mechanical fallback from %d scanned",
        len(results), len(pending_candidates), len(mechanical_top), len(candidates),
    )
    return fallback_output


def load_screened_tickers() -> list[str]:
    """Load screened tickers from cache file. Returns empty list if stale/missing.

    Handles multiple formats:
    - Mechanical/fallback: {"date": "...", "tickers": ["005930", ...], ...}
    - LLM output: [{"ticker": "005930", "name": "...", "reason": "..."}]
    - LLM output (text-wrapped): JSON array possibly wrapped in markdown fences
    """
    try:
        if not SCREEN_FILE.exists():
            return []

        raw = SCREEN_FILE.read_text().strip()
        if not raw:
            return []

        # Try parsing as JSON directly
        data = _parse_screened_json(raw)
        if data is None:
            return []

        # Format 1: Mechanical/fallback format with "tickers" key
        if isinstance(data, dict):
            # Check freshness (today or yesterday)
            if data.get("date") in (
                date.today().isoformat(),
                (date.today().replace(day=date.today().day)).isoformat(),
            ):
                return data.get("tickers", [])
            return []

        # Format 2: LLM array format [{"ticker": "...", ...}]
        if isinstance(data, list):
            return [
                item["ticker"]
                for item in data
                if isinstance(item, dict) and item.get("ticker")
            ]

    except Exception:  # noqa: BLE001
        pass
    return []


def _parse_screened_json(raw: str) -> dict | list | None:
    """Parse JSON from screened_tickers.json, handling markdown code fences."""
    # Try direct JSON parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # LLM may wrap JSON in markdown code fences: ```json\n...\n```
    import re

    match = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding first [ or { and last ] or }
    for start_char, end_char in [("[", "]"), ("{", "}")]:
        start = raw.find(start_char)
        end = raw.rfind(end_char)
        if start != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                continue

    return None
