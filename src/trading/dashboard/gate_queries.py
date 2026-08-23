"""SPEC-TRADING-065 그룹 3 — 검증 게이트 뷰 쿼리 4종.

2026-08-15 하루에 배포한 13개 변경(출구·진입·리스크·사이징)의 8/17~8/29 검증
게이트를 화면에서 보기 위한 읽기 전용 집계다. 같은 날 세션에서 결정 근거로 쓴
SQL 을 그대로 옮겼다 — 화면이 그 실측을 **재현**해야 한다(AC-3·AC-4).

원칙:
- 결정 전체(체결 무관) 기준. 체결 65건만 보면 표본 편향으로 정반대 결론이 났다
  (confidence 사례). 반사실은 ohlcv 종가로 20/40 거래일 뒤를 본다.
- 하드코딩 금지: 보유기간 구간·반사실 창·상위 N 은 상수로 두되 시장 종속 값 아님.
- 모두 ro_connection + TTL 캐시(queries._cache_get/_cache_put 재사용). DB 쓰기 없음.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from trading.dashboard.db import ro_connection
from trading.dashboard.queries import _cache_get, _cache_put, _parse_since

LOG = logging.getLogger(__name__)

_gate_cache: dict[str, tuple[float, Any]] = {}

# 보유기간 구간(거래일 아님, 달력일 — RoundTrip.holding_days 와 동일 정의).
# 2026-08-15 실측: 2~5일 승률 17%·6~15일 20% 가 -35만, 16~30일 83% 가 +5만.
HOLDING_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("0-1일", 0, 1),
    ("2-5일", 2, 5),
    ("6-15일", 6, 15),
    ("16-30일", 16, 30),
    ("31일+", 31, 10**6),
)

# 반사실 관측 창(달력일). 20일 = decision.jinja 의 confidence 정의와 일치.
COUNTERFACTUAL_HORIZONS: tuple[int, ...] = (20, 40)

# HOLD 사유 키워드 → 라벨. risk.jinja 의 재량 사유와 1:1. 순서 = 우선순위(첫 매치).
HOLD_REASON_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("단기과열", ("단기과열", "단일가")),
    ("늦은 진입", ("늦은 진입", "late", "후행 확인")),
    ("20일 근거 부재", ("20일을 버틸", "20거래일 보유", "단기 촉매")),
    ("손실 재진입", ("재진입", "손실 청산")),
    ("한도", ("한도", "80% 이상")),
    ("수치 불일치", ("불일치", "실측")),
    ("순매도", ("순매도",)),
)

TOP_N_DEFAULT = 20


# ---------------------------------------------------------------------------
# 1) 보유기간별 손익 (REQ-065-3a) — compute_roundtrips 재사용
# ---------------------------------------------------------------------------


def fetch_holding_period_pnl(*, since: str | None = None) -> dict[str, Any]:
    """구간별 n·승률·평균수익률·합계원. 소스 = edge.roundtrips(FIFO), since 는 진입일."""
    from trading.edge.roundtrips import compute_roundtrips, filter_since

    since_d = _parse_since(since)
    key = f"holding:{since_d}"
    cached = _cache_get(_gate_cache, key)
    if cached is not None:
        return cached

    rts = filter_since(compute_roundtrips(None), since_d).roundtrips
    buckets: dict[str, list] = defaultdict(list)
    for rt in rts:
        d = rt.holding_days
        for label, lo, hi in HOLDING_BUCKETS:
            if lo <= d <= hi:
                buckets[label].append(rt)
                break

    rows = []
    for label, _lo, _hi in HOLDING_BUCKETS:
        L = buckets.get(label, [])
        n = len(L)
        wins = sum(1 for r in L if r.net_pnl > 0)
        rows.append({
            "bucket": label,
            "n": n,
            "win_rate": (wins / n) if n else None,
            "avg_return_pct": (sum(r.return_pct for r in L) / n) if n else None,
            "sum_net_pnl": int(sum(r.net_pnl for r in L)),
        })
    out = {"since": since_d.isoformat() if since_d else None, "n_total": len(rts), "buckets": rows}
    _cache_put(_gate_cache, key, out)
    return out


# ---------------------------------------------------------------------------
# 2) 진입 품질 매트릭스 (REQ-065-3b) — 결정 전체 x 반사실
# ---------------------------------------------------------------------------

_ENTRY_QUALITY_SQL = """
    WITH d AS (
        SELECT pd.id, pd.ts::date AS d, pd.ticker,
               floor(pd.confidence * 10) / 10 AS conf_bucket,
               COALESCE(NULLIF(pd.raw->>'entry_freshness', ''), 'unlabeled') AS freshness
          FROM persona_decisions pd
         WHERE pd.side = 'buy' AND pd.confidence IS NOT NULL
           AND (%(since)s::date IS NULL OR pd.ts::date >= %(since)s::date)
    ), px AS (
        SELECT d.*,
          (SELECT close FROM ohlcv o WHERE o.symbol = d.ticker AND o.ts >= d.d
            ORDER BY o.ts LIMIT 1) AS p0,
          (SELECT close FROM ohlcv o WHERE o.symbol = d.ticker AND o.ts >= d.d + %(h1)s
            ORDER BY o.ts LIMIT 1) AS p1,
          (SELECT close FROM ohlcv o WHERE o.symbol = d.ticker AND o.ts >= d.d + %(h2)s
            ORDER BY o.ts LIMIT 1) AS p2
          FROM d
    )
    SELECT conf_bucket, freshness, count(*) AS n,
           count(*) FILTER (WHERE p1 IS NOT NULL) AS n_h1,
           avg(100.0 * (p1 - p0) / NULLIF(p0, 0)) AS ret_h1,
           avg(100.0 * (p2 - p0) / NULLIF(p0, 0)) AS ret_h2,
           -- 2026-08-23: 종전엔 p1 이 NULL(아직 미래 봉이 없음)일 때 ELSE 0.0 으로
           -- 떨어져 '데이터 없음' 이 '패배' 로 집계됐다. 최근 결정이 많은 구간의
           -- 승률이 0 으로 표시되고 화면에선 '전부 실패' 로 읽혔다.
           -- NULL 은 avg 가 무시하므로 데이터 있는 행만 평균한다(없으면 NULL).
           -- (주석에 퍼센트 기호를 쓰지 말 것 — psycopg 가 플레이스홀더로 해석한다)
           avg(CASE WHEN p1 IS NULL THEN NULL WHEN p1 > p0 THEN 1.0 ELSE 0.0 END) AS win_h1
      FROM px WHERE p0 > 0
     GROUP BY 1, 2 ORDER BY 1, 2
"""


def fetch_entry_quality_matrix(*, since: str | None = None) -> dict[str, Any]:
    """행=confidence 버킷, 열=entry_freshness, 셀=n·20/40일 반사실. **결정 전체** 기준."""
    since_d = _parse_since(since)
    key = f"entryq:{since_d}"
    cached = _cache_get(_gate_cache, key)
    if cached is not None:
        return cached

    h1, h2 = COUNTERFACTUAL_HORIZONS
    with ro_connection() as conn, conn.cursor() as cur:
        cur.execute(_ENTRY_QUALITY_SQL, {"since": since_d, "h1": h1, "h2": h2})
        rows = [dict(r) for r in cur.fetchall()]

    cells = [{
        "conf_bucket": float(r["conf_bucket"]),
        "freshness": r["freshness"],
        "n": int(r["n"]),
        "n_with_horizon": int(r["n_h1"]),
        f"ret_{h1}d": (float(r["ret_h1"]) if r["ret_h1"] is not None else None),
        f"ret_{h2}d": (float(r["ret_h2"]) if r["ret_h2"] is not None else None),
        f"win_{h1}d": (float(r["win_h1"]) if r["win_h1"] is not None else None),
    } for r in rows]
    out = {
        "since": since_d.isoformat() if since_d else None,
        "horizons": list(COUNTERFACTUAL_HORIZONS),
        "basis": "all_buy_decisions",  # 체결 무관 — 화면 라벨용
        "cells": cells,
    }
    _cache_put(_gate_cache, key, out)
    return out


# ---------------------------------------------------------------------------
# 3) 리스크 판정 (REQ-065-3c)
# ---------------------------------------------------------------------------

_RISK_ROWS_SQL = """
    SELECT rr.verdict, rr.rationale, pd.ticker, pd.ts::date AS d, pd.side,
           EXISTS (SELECT 1 FROM orders o WHERE o.persona_decision_id = pd.id) AS reached_order
      FROM risk_reviews rr JOIN persona_decisions pd ON pd.id = rr.decision_id
     WHERE (%(since)s::date IS NULL OR pd.ts::date >= %(since)s::date)
"""

_HOLD_CF_SQL = """
    WITH h AS (
        SELECT pd.ticker, pd.ts::date AS d
          FROM risk_reviews rr JOIN persona_decisions pd ON pd.id = rr.decision_id
         WHERE rr.verdict IN ('HOLD','REJECT') AND pd.side = 'buy'
           AND (%(since)s::date IS NULL OR pd.ts::date >= %(since)s::date)
    ), px AS (
        SELECT h.*,
          (SELECT close FROM ohlcv o WHERE o.symbol=h.ticker AND o.ts>=h.d
            ORDER BY o.ts LIMIT 1) p0,
          (SELECT close FROM ohlcv o WHERE o.symbol=h.ticker AND o.ts>=h.d+%(h1)s
            ORDER BY o.ts LIMIT 1) p1,
          (SELECT close FROM ohlcv o WHERE o.symbol=h.ticker AND o.ts>=h.d+%(h2)s
            ORDER BY o.ts LIMIT 1) p2
          FROM h)
    SELECT count(*) AS n,
           avg(100.0*(p1-p0)/NULLIF(p0,0)) AS ret_h1,
           avg(100.0*(p2-p0)/NULLIF(p0,0)) AS ret_h2
      FROM px WHERE p0 > 0 AND p1 IS NOT NULL
"""


def _classify_hold_reason(rationale: str) -> str:
    text = rationale or ""
    for label, kws in HOLD_REASON_KEYWORDS:
        if any(k in text for k in kws):
            return label
    return "기타"


def fetch_risk_verdicts(*, since: str | None = None) -> dict[str, Any]:
    """verdict 분포 + HOLD 사유 집계 + HOLD 종목 반사실 + code_rules_passed 비율."""
    since_d = _parse_since(since)
    key = f"risk:{since_d}"
    cached = _cache_get(_gate_cache, key)
    if cached is not None:
        return cached

    h1, h2 = COUNTERFACTUAL_HORIZONS
    with ro_connection() as conn, conn.cursor() as cur:
        cur.execute(_RISK_ROWS_SQL, {"since": since_d})
        rows = [dict(r) for r in cur.fetchall()]
        cur.execute(_HOLD_CF_SQL, {"since": since_d, "h1": h1, "h2": h2})
        cf = dict(cur.fetchone() or {})

    verdict_counts: dict[str, int] = defaultdict(int)
    hold_reasons: dict[str, int] = defaultdict(int)
    # 2026-08-23: 종전엔 risk_reviews.code_rules_passed 를 그대로 셌는데 그 컬럼이
    # 신뢰 불가다 — 실측(8/8~)에서 false 인 83건 중 8건이 실제로 주문이 됐다.
    # 한도 검사 전에 걸러진 신호(risk HOLD·세션가드·섹터캡 등)와 "검사 후 실패" 를
    # 구분하지 못하고, 갱신 자체가 4/95 에서만 일어났다.
    # 대신 orders 원장으로 실행 도달률을 직접 센다 — 원장은 사후 조작이 없다.
    exec_reached = exec_total = 0
    for r in rows:
        verdict_counts[r["verdict"]] += 1
        if r["verdict"] == "APPROVE":
            exec_total += 1
            exec_reached += 1 if r.get("reached_order") else 0
        if r["verdict"] in ("HOLD", "REJECT"):
            hold_reasons[_classify_hold_reason(r.get("rationale") or "")] += 1

    n_hold = sum(hold_reasons.values())
    out = {
        "since": since_d.isoformat() if since_d else None,
        "n": len(rows),
        "verdicts": dict(verdict_counts),
        "hold_reasons": [
            {"reason": k, "n": v, "share": (v / n_hold) if n_hold else None}
            for k, v in sorted(hold_reasons.items(), key=lambda kv: -kv[1])
        ],
        "hold_counterfactual": {
            "n": int(cf.get("n") or 0),
            f"ret_{h1}d": (float(cf["ret_h1"]) if cf.get("ret_h1") is not None else None),
            f"ret_{h2}d": (float(cf["ret_h2"]) if cf.get("ret_h2") is not None else None),
        },
        # risk 가 승인한 결정 중 실제 주문까지 간 비율. 낮으면 페르소나와 코드 한도가
        # 서로 다른 세계를 보고 있다는 뜻(프롬프트-코드 한도 모순 등).
        "execution_reach_share": (exec_reached / exec_total) if exec_total else None,
        "execution_reach_n": exec_total,
        "horizons": list(COUNTERFACTUAL_HORIZONS),
    }
    _cache_put(_gate_cache, key, out)
    return out


# ---------------------------------------------------------------------------
# 4) 매수 축소·차단 게이트 (REQ-065-3d)
# ---------------------------------------------------------------------------

_SIZING_SQL = """
    SELECT ts, event_type, details
      FROM audit_log
     WHERE event_type IN ('PORTFOLIO_ADJUSTMENT','PORTFOLIO_GATE_DROP',
                          'LIMIT_BREACH','ORDER_BLOCKED_OUTSIDE_SESSION')
       AND (%(since)s::date IS NULL OR ts::date >= %(since)s::date)
     ORDER BY ts DESC
"""


def _gate_of_breach(breach: str) -> str:
    """'repeat_buy: 000270 ...' → 'repeat_buy'."""
    return (breach or "").split(":", 1)[0].strip() or "unknown"


def fetch_sizing_gates(*, since: str | None = None, top_n: int = TOP_N_DEFAULT) -> dict[str, Any]:
    """게이트별 건수·평균 삭감률 + 최근 N 건 rationale/사유 표."""
    since_d = _parse_since(since)
    key = f"sizing:{since_d}:{top_n}"
    cached = _cache_get(_gate_cache, key)
    if cached is not None:
        return cached

    with ro_connection() as conn, conn.cursor() as cur:
        cur.execute(_SIZING_SQL, {"since": since_d})
        rows = [dict(r) for r in cur.fetchall()]

    counts: dict[str, int] = defaultdict(int)
    cut_pcts: dict[str, list[float]] = defaultdict(list)
    recent: list[dict[str, Any]] = []

    for r in rows:
        et, d, ts = r["event_type"], r["details"] or {}, r["ts"]
        if et == "PORTFOLIO_ADJUSTMENT":
            for a in d.get("adjusted") or []:
                g = a.get("gate") or "portfolio_persona"
                counts[g] += 1
                qo, qa = a.get("qty_original"), a.get("qty_adjusted")
                if qo and qa is not None and qo > 0:
                    cut_pcts[g].append(100.0 * (1 - qa / qo))
                recent.append({"ts": ts, "gate": g, "ticker": a.get("ticker"),
                               "qty_original": qo, "qty_adjusted": qa,
                               "reason": a.get("rationale"), "decision_id": a.get("decision_id")})
            for rj in d.get("rejected") or []:
                g = rj.get("gate") or "portfolio_persona"
                counts[g] += 1
                recent.append({"ts": ts, "gate": g, "ticker": rj.get("ticker"),
                               "qty_original": None, "qty_adjusted": 0,
                               "reason": rj.get("reason"), "decision_id": rj.get("decision_id")})
        elif et == "PORTFOLIO_GATE_DROP":
            g = d.get("gate") or "gate_drop"
            for dr in d.get("dropped") or []:
                counts[g] += 1
                recent.append({"ts": ts, "gate": g, "ticker": dr.get("ticker"),
                               "qty_original": None, "qty_adjusted": 0,
                               "reason": dr.get("reason") or d.get("reason"),
                               "decision_id": dr.get("decision_id")})
        elif et == "LIMIT_BREACH":
            for b in d.get("breaches") or []:
                g = _gate_of_breach(b)
                counts[g] += 1
                sig = (d.get("context") or {}).get("signal") or {}
                recent.append({"ts": ts, "gate": g, "ticker": sig.get("ticker"),
                               "qty_original": sig.get("qty"), "qty_adjusted": 0,
                               "reason": b, "decision_id": d.get("decision_id")})
        elif et == "ORDER_BLOCKED_OUTSIDE_SESSION":
            counts["session"] += 1
            recent.append({"ts": ts, "gate": "session", "ticker": d.get("ticker"),
                           "qty_original": d.get("qty"), "qty_adjusted": 0,
                           "reason": d.get("reason"), "decision_id": d.get("decision_id")})

    gates = [{
        "gate": g, "n": n,
        "avg_cut_pct": (sum(cut_pcts[g]) / len(cut_pcts[g])) if cut_pcts.get(g) else None,
    } for g, n in sorted(counts.items(), key=lambda kv: -kv[1])]

    for r in recent:
        r["ts"] = r["ts"].isoformat() if hasattr(r["ts"], "isoformat") else str(r["ts"])
    out = {
        "since": since_d.isoformat() if since_d else None,
        "gates": gates,
        "recent": recent[:top_n],
        "n_events": len(rows),
    }
    _cache_put(_gate_cache, key, out)
    return out
