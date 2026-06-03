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

SPEC-TRADING-025: Blocked-aware filtering. ``data/blocked_tickers.json`` is
loaded at the start of ``run()`` and applied as a pure set-difference against
the candidate pool BEFORE LLM scoring. This prevents the screener from
re-emitting tickers that the exchange has just designated as short-term
overheating (단기과열), which since 2026-05-13 had produced a 100% overlap
between screened picks and the blocked set.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from trading.config import project_root
from trading.db.session import connection
from trading.kis.market import OVERHEAT_STAT_CLS
from trading.personas.context import DEFAULT_WATCHLIST
from trading.strategy.volatility.rsi import rsi_from_closes

LOG = logging.getLogger(__name__)
SCREEN_FILE = project_root() / "data" / "screened_tickers.json"
PENDING_FILE = project_root() / "data" / "pending_screen.json"

# @MX:NOTE: SPEC-TRADING-025/026 — blocked-list source. Refreshed at 06:20 KST
# (SPEC-026 c-cron), BEFORE the 06:30 screener run, so the filter sees same-day
# data. _load_blocked_map() also accepts yesterday's file as a safety net if the
# refresh is late/failed.
# @MX:SPEC: SPEC-TRADING-026
BLOCKED_FILE = project_root() / "data" / "blocked_tickers.json"

MAX_CANDIDATES = 50
MAX_SCREENED = 20

# @MX:NOTE: SPEC-TRADING-025 REQ-025-5 — low-yield warning threshold.
# Configuration-only knob; recovery via universe expansion is deferred to
# SPEC-TRADING-026.
# @MX:SPEC: SPEC-TRADING-025
MIN_CANDIDATES_WARN = 5

# @MX:NOTE: SPEC-TRADING-026 — 단기과열(55) score penalty knobs. 55 is no longer
# excluded (that was the SPEC-025 self-collision bug on surge days); it is kept
# and de-weighted. The penalty is regime-aware (threshold guard): a handful of
# overheated names is a stock-specific signal (strong penalty), while a
# market-wide overheating day is a regime artifact (light penalty) so strong
# names still surface.
# @MX:SPEC: SPEC-TRADING-026
OVERHEAT_PENALTY_NORMAL = 2.5       # stock-specific overheating: strong de-rank
OVERHEAT_PENALTY_MARKETWIDE = 0.5  # market-wide surge: light tiebreaker only
OVERHEAT_MARKETWIDE_COUNT = 15     # >= N overheated tickers => market-wide
OVERHEAT_MARKETWIDE_RATIO = 0.30   # OR overheated/pool >= 30% => market-wide

# KST is the canonical timezone for the blocked-file freshness check
# (REQ-025-3, A-7). Consistent with the rest of the trading codebase.
_KST = ZoneInfo("Asia/Seoul")


def _today_kst_iso() -> str:
    """Return today's date in KST as an ISO string (YYYY-MM-DD).

    SPEC-TRADING-025 A-7: KST is the canonical timezone for blocked-file
    freshness checks. Exposed as a module-level helper so tests can pin the
    expected date without depending on local-tz semantics.
    """
    return datetime.now(_KST).date().isoformat()


# @MX:ANCHOR: SPEC-TRADING-025 REQ-025-1/-3 + SPEC-TRADING-026 — single
# load/validate entry point for the blocked list. Returns a ``ticker ->
# stat_cls`` map so callers can distinguish hard blocks (51~54 / unknown) from
# 단기과열(55). On missing, stale, or malformed input returns ``{}`` and emits a
# WARNING. Never raises — the daily cycle must not halt on a missing file.
# @MX:REASON: fan_in >= 2 (run() + _load_blocked_set wrapper) and it
# encapsulates the graceful-degrade + freshness contract.
# @MX:SPEC: SPEC-TRADING-026
def _load_blocked_map() -> dict[str, str]:
    """Load ``data/blocked_tickers.json`` → ``{ticker: stat_cls}``.

    ``stat_cls`` is ``""`` when the entry omits it (e.g. intraday
    safety-recorded blocks); such entries are treated as hard blocks by the
    caller (conservative default).

    Freshness (SPEC-026): the blocked refresh cron runs at 07:25 KST, *after*
    the screener at 06:30 KST, so at screen time the freshest file on disk is
    typically yesterday's. We therefore accept a ``date`` of today OR yesterday
    (KST); anything older is stale. On any failure (missing file, stale date,
    malformed JSON, unexpected schema) returns ``{}`` and emits a WARNING.
    """
    today = _today_kst_iso()
    yesterday = (datetime.now(_KST).date() - timedelta(days=1)).isoformat()

    if not BLOCKED_FILE.exists():
        LOG.warning(
            "daily_screen: blocked file missing at %s — proceeding with empty blocked set",
            BLOCKED_FILE,
        )
        return {}

    try:
        payload = json.loads(BLOCKED_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        LOG.warning(
            "daily_screen: blocked file unreadable (%s) — proceeding with empty blocked set",
            exc,
        )
        return {}

    if not isinstance(payload, dict):
        LOG.warning(
            "daily_screen: blocked file has unexpected schema (not a dict) — "
            "proceeding with empty blocked set"
        )
        return {}

    file_date = payload.get("date", "")
    if file_date not in (today, yesterday):
        LOG.warning(
            "daily_screen: blocked file stale: file_date=%s today=%s — "
            "proceeding with empty blocked set",
            file_date or "<missing>",
            today,
        )
        return {}

    blocked = payload.get("blocked", {})
    if not isinstance(blocked, dict):
        LOG.warning(
            "daily_screen: blocked field has unexpected schema (not a dict) — "
            "proceeding with empty blocked set"
        )
        return {}

    out: dict[str, str] = {}
    for ticker, info in blocked.items():
        if isinstance(info, dict):
            out[ticker] = str(info.get("stat_cls", "") or "")
        else:
            out[ticker] = ""
    return out


def _load_blocked_set() -> set[str]:
    """Backward-compatible wrapper: all blocked ticker codes (any stat_cls).

    Retained for SPEC-025 callers/tests; SPEC-026 logic uses
    :func:`_load_blocked_map` to split hard blocks from 단기과열(55).
    """
    return set(_load_blocked_map().keys())


def _overheat_penalty(overheat_count: int, pool_size: int) -> tuple[float, bool]:
    """SPEC-026 REQ-026-4 threshold guard → ``(penalty, market_wide)``.

    A market-wide overheating regime (many 단기과열 names) is a market artifact,
    not a per-stock red flag, so it uses a light penalty; a stock-specific
    handful uses a strong penalty.
    """
    market_wide = overheat_count >= OVERHEAT_MARKETWIDE_COUNT or (
        pool_size > 0 and (overheat_count / pool_size) >= OVERHEAT_MARKETWIDE_RATIO
    )
    penalty = OVERHEAT_PENALTY_MARKETWIDE if market_wide else OVERHEAT_PENALTY_NORMAL
    return penalty, market_wide


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

    # Calculate RSI(14) — SPEC-TRADING-040: shared with position_watchdog via the
    # extracted rsi_from_closes (single implementation, no duplicate formula).
    all_closes = [float(r["close"]) for r in reversed(ohlcv)]
    rsi = rsi_from_closes(all_closes)
    if rsi is None:  # < 15 closes (guarded above by len(ohlcv) >= 20, defensive)
        rsi = 50.0

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
    except Exception:
        return ""


def run() -> dict[str, Any]:
    """Run Phase 1: mechanical filter + export pending_screen.json for Claude CLI.

    Produces ~50 candidates via mechanical screening, then writes
    data/pending_screen.json with candidates, intelligence context, and LLM prompt.
    Also writes a fallback screened_tickers.json with mechanical top 20.
    """
    universe = _get_universe_tickers()
    LOG.info("daily_screen: scanning %d tickers from pykrx universe", len(universe))

    # SPEC-020 Q-1 decision (2026-05-12): KEEP exclusion of DEFAULT_WATCHLIST
    # from the screening candidate pool. Rationale: DEFAULT 5종 are already
    # known/well-covered large-caps; excluding them from screening reserves the
    # ~20 output slots for genuinely new discovery candidates. The downstream
    # universe (get_data_universe) still includes DEFAULT on cold-start, so no
    # liquidity guarantee is lost. If a follow-up SPEC moves DEFAULT into a
    # yaml-based watchlist, revisit this exclusion accordingly.
    base_set = set(DEFAULT_WATCHLIST)
    candidates = [t for t in universe if t not in base_set]

    # SPEC-TRADING-025/026: load the blocked map and split it by stat_cls.
    #  - Hard blocks (관리 51 / 투자위험 52 / 투자경고 53 / 거래정지 54, plus any
    #    entry without an explicit 55) are excluded BEFORE scoring (efficiency:
    #    no LLM tokens / scoring SQL spent on untradeable names).
    #  - 단기과열(55) is KEPT and score-penalized after scoring (SPEC-026), fixing
    #    the SPEC-025 self-collision that zeroed out signals on surge days.
    blocked_map = _load_blocked_map()
    hard_blocked = {t for t, sc in blocked_map.items() if sc != OVERHEAT_STAT_CLS}
    overheated = {t for t, sc in blocked_map.items() if sc == OVERHEAT_STAT_CLS}
    blocked_set = hard_blocked  # retained name for the REQ-025-4 leak check below

    before_count = len(candidates)
    if hard_blocked:
        candidates = [t for t in candidates if t not in hard_blocked]
        filtered_n = before_count - len(candidates)
        LOG.info("daily_screen: filtered %d tickers from blocked list", filtered_n)

    results: list[dict[str, Any]] = []
    for ticker in candidates:
        try:
            result = _screen_ticker(ticker)
            if result:
                results.append(result)
        except Exception as e:
            LOG.debug("daily_screen: error screening %s: %s", ticker, e)
            continue

    # SPEC-TRADING-026 REQ-026-2/-4: de-weight 단기과열(55) instead of excluding.
    # The penalty is regime-aware via the threshold guard — light on market-wide
    # overheating days so strong names still surface, strong for a stock-specific
    # handful. Penalty is applied before the score sort so it affects ranking.
    overheat_in_pool = overheated & {r["ticker"] for r in results}
    penalty, market_wide = _overheat_penalty(len(overheat_in_pool), len(results))
    n_penalized = 0
    for r in results:
        r["overheated"] = r["ticker"] in overheated
        if r["overheated"]:
            r["score"] = max(0.0, float(r["score"]) - penalty)
            r.setdefault("reasons", []).append(
                f"단기과열 감점(-{penalty:g}, {'장세' if market_wide else '개별'})"
            )
            n_penalized += 1
    if n_penalized:
        LOG.info(
            "daily_screen: de-weighted %d overheated(55) tickers "
            "(penalty=%.1f, market_wide=%s)",
            n_penalized, penalty, market_wide,
        )

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

    # SPEC-TRADING-025 REQ-025-4: defensive post-write verification of the
    # output invariant — screened set must contain zero blocked tickers.
    # Should be unreachable given the upstream set-difference filter, but
    # logged as ERROR if violated to make the invariant observable.
    if blocked_set:
        leaked = set(fallback_output["tickers"]) & blocked_set
        if leaked:
            LOG.error(
                "daily_screen: REQ-025-4 invariant violation — blocked tickers "
                "leaked into screened output: %s",
                sorted(leaked),
            )

    # SPEC-TRADING-025 REQ-025-5: low-yield warning when the post-filter
    # candidate pool falls below MIN_CANDIDATES_WARN. Recovery via universe
    # expansion is deferred to SPEC-TRADING-026.
    if len(candidates) < MIN_CANDIDATES_WARN:
        LOG.warning(
            "daily_screen: low yield — only %d candidates after blocked filter "
            "(threshold=%d). Recovery deferred to SPEC-TRADING-026.",
            len(candidates),
            MIN_CANDIDATES_WARN,
        )

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

    except Exception:
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
