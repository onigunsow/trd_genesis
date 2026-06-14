"""SPEC-TRADING-047/050 M1: 대시보드 읽기 전용 쿼리 함수.

모든 함수는 ro_connection 을 통해 dashboard_ro 역할로만 접근한다.
쓰기 작업 없음 (REQ-050-1).

SPEC-TRADING-050 M1 변경사항:
- fetch_postmortem_distribution / fetch_calibration_scores 구형 stub 제거 (REQ-050-6).
  두 함수는 pd.run_id 오류 FK 를 사용했고 엔드포인트에도 미연결이었음.
- 대체: fetch_postmortem / fetch_confidence_analysis (올바른 FK + 어댑터 + 캐시).
- 신규: fetch_recent_news / fetch_story_clusters / fetch_trends / fetch_pipeline.
- 확장: fetch_recent_decisions (+risk_reviews LEFT JOIN), fetch_system_status (+확장 필드),
  fetch_equity_curve (+drawdown 시리즈).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from trading.dashboard.db import ro_connection

LOG = logging.getLogger(__name__)

# 응답에서 제외할 민감 컬럼 (KIS 요청/응답 페이로드, 주문번호)
# REQ-050-8: 자격증명·KIS request/response·kis_order_no 제외.
_SENSITIVE_FIELDS = frozenset({"request", "response", "kis_order_no"})

# ---------------------------------------------------------------------------
# TTL 캐시 (postmortem / confidence 지연계산 폴링 부하 억제, REQ-050-7)
# ---------------------------------------------------------------------------

_CACHE_TTL_SECS = 120  # 기본 2분 TTL

# 캐시 구조: {cache_key: (ts_inserted, payload)}
# 테스트에서 .clear() 로 초기화 가능.
# @MX:NOTE: [AUTO] 서버측 TTL 메모리 캐시 — 프로세스 재시작 시 소멸.
# @MX:SPEC: SPEC-TRADING-050 REQ-050-7
_postmortem_cache: dict[str, tuple[float, Any]] = {}
_confidence_cache: dict[str, tuple[float, Any]] = {}


def _cache_get(store: dict[str, tuple[float, Any]], key: str) -> Any | None:
    """TTL 캐시 조회. 만료되었거나 없으면 None 반환."""
    entry = store.get(key)
    if entry is None:
        return None
    ts, payload = entry
    if time.monotonic() - ts > _CACHE_TTL_SECS:
        del store[key]
        return None
    return payload


def _cache_put(store: dict[str, tuple[float, Any]], key: str, payload: Any) -> None:
    """TTL 캐시 저장."""
    store[key] = (time.monotonic(), payload)


# ---------------------------------------------------------------------------
# fetch_system_status (SPEC-047 + SPEC-050 REQ-050-4 확장)
# ---------------------------------------------------------------------------


# @MX:ANCHOR: [AUTO] SPEC-047/050 M1 — 시스템 상태 읽기 진입점.
# @MX:REASON: halt_state/regime/cool_down/late_cycle 를 대시보드가 직접 읽는 단일 경로.
def fetch_system_status() -> dict[str, Any]:
    """system_state 싱글톤 행 반환 + halt 사유(audit_log) + cool_down/late_cycle.

    REQ-050-4: halt 사유(CIRCUIT_BREAKER_TRIP 최근 항목) + cool_down_active +
    late_cycle_defense_active / late_cycle_level 함께 반환.

    Returns:
        dict: halt_state, trading_mode, current_regime, current_risk_appetite,
              late_cycle_defense_active, late_cycle_level, cool_down_active,
              halt_reason (None 또는 문자열), updated_at.

    Raises:
        RuntimeError: system_state 행이 없을 때.
    """
    with ro_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                ss.halt_state,
                ss.trading_mode,
                ss.current_regime,
                ss.current_risk_appetite,
                ss.late_cycle_defense_active,
                ss.late_cycle_level,
                ss.cool_down_active,
                ss.updated_at,
                al.details->>'reason' AS halt_reason
            FROM system_state ss
            LEFT JOIN LATERAL (
                SELECT details FROM audit_log
                WHERE event_type = 'CIRCUIT_BREAKER_TRIP'
                ORDER BY ts DESC LIMIT 1
            ) al ON true
            WHERE ss.id = 1
            """
        )
        row = cur.fetchone()

    if not row:
        raise RuntimeError("system_state 행 없음 — migration 001 미적용?")

    return dict(row)


# ---------------------------------------------------------------------------
# fetch_recent_decisions (SPEC-047 + SPEC-050 REQ-050-3 확장)
# ---------------------------------------------------------------------------


def fetch_recent_decisions(*, limit: int = 50) -> list[dict[str, Any]]:
    """persona_decisions + persona_runs + risk_reviews LEFT JOIN — 최신 N 건.

    REQ-050-3: risk_reviews(verdict/rationale)를 decision_id 로 LEFT JOIN.
    매칭 없는 결정은 risk_verdict/risk_rationale 가 null 이며 행 누락 없음.

    Args:
        limit: 최대 반환 행 수.
    """
    sql = """
        SELECT
            pd.id,
            pd.ts,
            pr.persona_name,
            pd.cycle_kind,
            pd.ticker,
            pd.side,
            pd.qty,
            pd.confidence,
            pd.rationale,
            rr.verdict   AS risk_verdict,
            rr.rationale AS risk_rationale
        FROM persona_decisions pd
        JOIN persona_runs pr ON pr.id = pd.persona_run_id
        LEFT JOIN risk_reviews rr ON rr.decision_id = pd.id
        ORDER BY pd.ts DESC
        LIMIT %s
    """
    with ro_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (limit,))
        rows = cur.fetchall()

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# fetch_recent_orders (SPEC-047 기존)
# ---------------------------------------------------------------------------


def fetch_recent_orders(*, limit: int = 50) -> list[dict[str, Any]]:
    """orders 테이블 최신 N 건 — 민감 컬럼 제외.

    REQ-050-8: request / response JSONB(KIS API 자격증명 포함 가능) 및 kis_order_no 제외.
    """
    sql = """
        SELECT
            id, ts, side, ticker, qty, order_type,
            status, fill_price, mode
        FROM orders
        ORDER BY ts DESC
        LIMIT %s
    """
    with ro_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (limit,))
        rows = cur.fetchall()

    result = []
    for r in rows:
        row = dict(r)
        for f in _SENSITIVE_FIELDS:
            row.pop(f, None)
        result.append(row)
    return result


# ---------------------------------------------------------------------------
# fetch_holdings (SPEC-047 기존)
# ---------------------------------------------------------------------------


def fetch_holdings() -> list[dict[str, Any]]:
    """현재 순매수 포지션 집계 (매수 - 매도, fill 완료 orders 기준).

    ticker 별 qty_net > 0 인 행만 반환.
    """
    sql = """
        SELECT
            ticker,
            SUM(CASE WHEN side = 'buy' THEN qty ELSE -qty END) AS qty_net,
            ROUND(
                SUM(CASE WHEN side = 'buy' THEN fill_price::BIGINT * qty ELSE 0 END)::NUMERIC
                / NULLIF(SUM(CASE WHEN side = 'buy' THEN qty ELSE 0 END), 0)
            ) AS avg_fill_price,
            SUM(CASE WHEN side = 'buy' THEN fill_price::BIGINT * qty ELSE 0 END) AS total_cost
        FROM orders
        WHERE status = 'filled'
          AND fill_price IS NOT NULL
        GROUP BY ticker
        HAVING SUM(CASE WHEN side = 'buy' THEN qty ELSE -qty END) > 0
        ORDER BY ticker
    """
    with ro_connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# fetch_equity_curve (SPEC-047 + SPEC-050 REQ-050-5 확장)
# ---------------------------------------------------------------------------


def fetch_equity_curve(*, days: int | None = 90) -> list[dict[str, Any]]:
    """daily_equity_snapshot — 날짜 오름차순 + drawdown 시리즈.

    REQ-050-5: 일별 스냅샷에 더해 drawdown(러닝 맥스 대비 낙폭) 곡선 함께 반환.

    Args:
        days: None 이면 전체 기간.
    """
    if days is not None:
        sql = """
            SELECT trading_day, total_assets, stock_eval, cash, unrealized_pnl
            FROM daily_equity_snapshot
            WHERE trading_day >= (CURRENT_DATE - (%s || ' days')::INTERVAL)::DATE
            ORDER BY trading_day
        """
        params: list[Any] = [str(int(days))]
    else:
        sql = """
            SELECT trading_day, total_assets, stock_eval, cash, unrealized_pnl
            FROM daily_equity_snapshot
            ORDER BY trading_day
        """
        params = []

    with ro_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    if not rows:
        return []

    # drawdown 시리즈 계산 (러닝 최고점 대비 낙폭, REQ-050-5)
    result: list[dict[str, Any]] = [dict(r) for r in rows]
    running_max = 0.0
    for row in result:
        assets = float(row.get("total_assets") or 0)
        running_max = max(running_max, assets)
        if running_max > 0:
            row["drawdown_pct"] = (assets - running_max) / running_max
        else:
            row["drawdown_pct"] = 0.0

    return result


# ---------------------------------------------------------------------------
# fetch_scorecard (SPEC-047 기존)
# ---------------------------------------------------------------------------


def fetch_scorecard() -> dict[str, Any]:
    """edge.scorecard 계산 결과 반환 (DB 읽기 + 순수 연산).

    edge 모듈을 임포트해 실시간 계산한다. DB 쓰기 없음.
    """
    from trading.edge import analytics as _an
    from trading.edge import benchmark as _bm
    from trading.edge import roundtrips as _rt
    from trading.edge import scorecard as _sc
    from trading.edge.report import load_equity_snapshots

    rt_result = _rt.compute_roundtrips(None)
    analytics = _an.from_result(rt_result, balance=None)
    bm = _bm.compute(rt_result.roundtrips)
    card = _sc.decide(analytics, bm)

    snapshots = load_equity_snapshots(days=90)
    tw = _an.time_weighted_metrics(snapshots)

    return {
        "verdict": card.verdict,
        "grade": card.grade,
        "reasons": card.reasons,
        "n_closed": analytics.n_closed,
        "win_rate": analytics.win_rate,
        "expectancy_adj": analytics.expectancy_adj,
        "profit_factor_adj": (
            float("inf") if analytics.profit_factor_adj == float("inf")
            else analytics.profit_factor_adj
        ),
        "alpha_pct": bm.alpha_pct if bm.available else None,
        "benchmark_available": bm.available,
        "cagr": tw.cagr if tw.available else None,
        "mdd": tw.mdd if tw.available else None,
        "sharpe": tw.sharpe if tw.available else None,
    }


# ---------------------------------------------------------------------------
# SPEC-TRADING-050 M1: 신규 뉴스 인텔리전스 쿼리
# ---------------------------------------------------------------------------


def fetch_recent_news(*, days: int = 7, limit: int = 50) -> list[dict[str, Any]]:
    """news_articles + news_analysis JOIN — 최신 N 건.

    REQ-050-2: impact_score / sentiment / keywords / summary_2line / sector / published_at 포함.

    Args:
        days: 최근 N일 이내 기사만 반환.
        limit: 최대 반환 행 수.
    """
    limit = min(limit, 200)
    sql = """
        SELECT
            na.id,
            na.title,
            na.url,
            na.summary,
            na.source_name,
            na.sector,
            na.published_at,
            an.impact_score,
            an.sentiment,
            an.keywords,
            an.summary_2line
        FROM news_articles na
        LEFT JOIN news_analysis an ON an.article_id = na.id
        WHERE na.published_at >= NOW() - (%s || ' days')::INTERVAL
        ORDER BY na.published_at DESC
        LIMIT %s
    """
    with ro_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (str(int(days)), limit))
        rows = cur.fetchall()

    return [dict(r) for r in rows]


def fetch_story_clusters(*, days: int = 7, limit: int = 50) -> list[dict[str, Any]]:
    """story_clusters — portfolio_relevant 우선 정렬, relevance_tickers 포함.

    REQ-050-2/AC-M1-1: representative_title / sector / sentiment_dominant /
    portfolio_relevant / relevance_tickers 포함.

    Args:
        days: 최근 N일 이내 클러스터만 반환.
        limit: 최대 반환 행 수.
    """
    limit = min(limit, 200)
    sql = """
        SELECT
            id,
            representative_title,
            sector,
            sentiment_dominant,
            portfolio_relevant,
            relevance_tickers,
            source_count,
            impact_max,
            keywords,
            cluster_date,
            last_updated
        FROM story_clusters
        WHERE cluster_date >= (CURRENT_DATE - (%s || ' days')::INTERVAL)::DATE
        ORDER BY portfolio_relevant DESC, impact_max DESC, last_updated DESC
        LIMIT %s
    """
    with ro_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (str(int(days)), limit))
        rows = cur.fetchall()

    return [dict(r) for r in rows]


def fetch_trends(*, trend_type: str = "daily", days: int = 14) -> list[dict[str, Any]]:
    """news_trends — 키워드 mention_count / 감성 분포 반환.

    REQ-050-2/AC-M5-3: keyword / mention_count / sentiment_* 포함.

    Args:
        trend_type: 'daily' 또는 'weekly'.
        days: 최근 N일 이내 트렌드만 반환.
    """
    sql = """
        SELECT
            id,
            keyword,
            mention_count,
            sentiment_positive,
            sentiment_neutral,
            sentiment_negative,
            sentiment_avg,
            trend_type,
            trend_date,
            sector
        FROM news_trends
        WHERE trend_type = %s
          AND trend_date >= (CURRENT_DATE - (%s || ' days')::INTERVAL)::DATE
        ORDER BY trend_date DESC, mention_count DESC
        LIMIT 200
    """
    with ro_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (trend_type, str(int(days))))
        rows = cur.fetchall()

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# SPEC-TRADING-050 M1: postmortem 지연계산 (REQ-050-6/7, AC-M1-3/4)
# ---------------------------------------------------------------------------

# @MX:ANCHOR: [AUTO] fetch_postmortem — postmortem 분류 단일 읽기 진입점.
# @MX:REASON: 대시보드·테스트 모두 이 함수를 소비(fan_in ≥ 3 예상). 구형 stub 대체.
def fetch_postmortem(*, days: int = 30, limit: int = 200) -> dict[str, Any]:
    """persona_decisions → 어댑터 → classify_decision_outcome → 4분류 + 페르소나 귀인.

    REQ-050-6: 올바른 FK(pd.persona_run_id) 사용. pd.run_id 버그 제거.
    REQ-050-6a: edge.postmortem 재사용, 트레이딩 로직 복제 없음.
    REQ-050-7: 최근 N일 제한 + TTL 캐시.

    어댑터: raw DB 행 → edge.postmortem.classify_decision_outcome 이 소비하는
    decision dict 로 변환. relative_5d/20d 는 OHLCV 미조회(대시보드 지연계산 범위 외)
    → 0.0 으로 graceful fallback (보수적 분류; 알파 계산은 scorecard 가 담당).

    Args:
        days: 최근 N일 이내 결정만 포함.
        limit: DB 조회 최대 행 수.

    Returns:
        dict: distribution(4분류 카운트), per_persona(페르소나별 귀인), total, computed_at.
    """
    from trading.edge.postmortem import (
        PersonaStats,
        attribute_to_persona,
        classify_decision_outcome,
    )

    cache_key = f"postmortem:{days}:{limit}"
    cached = _cache_get(_postmortem_cache, cache_key)
    if cached is not None:
        return cached

    # DB 조회 — 올바른 FK: pd.persona_run_id (REQ-050-6)
    sql = """
        SELECT
            pd.id,
            pd.ts,
            pr.persona_name,
            pd.cycle_kind,
            pd.ticker,
            pd.side,
            pd.confidence,
            pd.rationale,
            pd.prob_bull,
            pd.prob_base,
            pd.prob_bear,
            pr.regime_at_decision,
            pd.persona_run_id,
            rr.verdict AS risk_verdict
        FROM persona_decisions pd
        JOIN persona_runs pr ON pr.id = pd.persona_run_id
        LEFT JOIN risk_reviews rr ON rr.decision_id = pd.id
        WHERE pd.ts >= NOW() - (%s || ' days')::INTERVAL
        ORDER BY pd.ts DESC
        LIMIT %s
    """
    with ro_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (str(int(days)), limit))
        rows = cur.fetchall()

    # 어댑터: raw rows → 분류 결과 집계
    distribution: dict[str, int] = {
        "TRUE_POSITIVE": 0,
        "FALSE_POSITIVE": 0,
        "REGIME_MISMATCH": 0,
        "MISSED": 0,
    }
    per_persona: dict[str, PersonaStats] = {}

    for row in rows:
        d = dict(row)
        persona_name = str(d.get("persona_name") or "unknown")

        # 어댑터: decision dict 구성 (edge 순수 함수가 소비하는 형태)
        decision: dict[str, Any] = {
            "side": d.get("side"),
            "confidence": d.get("confidence"),
            "signal_dir": d.get("side"),  # side 를 신호 방향으로 사용
            "persona": persona_name,
        }

        # 라운드트립은 대시보드에서 orders 조인 없이 None 으로 처리
        # (진입 경로 없음 → MISSED 분류 경로 적용)
        roundtrip_or_none = None

        # relative_5d/20d: OHLCV 미조회 → 0.0 graceful fallback
        regime = str(d.get("regime_at_decision") or "neutral")

        outcome = classify_decision_outcome(
            decision=decision,
            roundtrip_or_none=roundtrip_or_none,
            relative_5d=0.0,
            relative_20d=0.0,
            regime=regime,
        )

        label = outcome.label
        if label in distribution:
            distribution[label] += 1

        # 페르소나 귀인
        attributed = attribute_to_persona(outcome, d)
        if attributed not in per_persona:
            per_persona[attributed] = PersonaStats(persona=attributed)
        stats = per_persona[attributed]
        stats.n_total += 1
        if label == "TRUE_POSITIVE":
            stats.n_true_positive += 1
        elif label == "FALSE_POSITIVE":
            stats.n_false_positive += 1
        elif label == "REGIME_MISMATCH":
            stats.n_regime_mismatch += 1
        elif label == "MISSED":
            stats.n_missed += 1

    payload: dict[str, Any] = {
        "distribution": distribution,
        "per_persona": {
            k: {
                "persona": v.persona,
                "n_total": v.n_total,
                "n_true_positive": v.n_true_positive,
                "n_false_positive": v.n_false_positive,
                "n_regime_mismatch": v.n_regime_mismatch,
                "n_missed": v.n_missed,
            }
            for k, v in per_persona.items()
        },
        "total": sum(distribution.values()),
        "days": days,
    }

    _cache_put(_postmortem_cache, cache_key, payload)
    return payload


# ---------------------------------------------------------------------------
# SPEC-TRADING-050 M1: confidence 분석 지연계산 (REQ-050-6/7, AC-M1-3)
# ---------------------------------------------------------------------------


# @MX:ANCHOR: [AUTO] fetch_confidence_analysis — confidence 엣지 분석 읽기 진입점.
# @MX:REASON: 대시보드·차트·테스트가 소비(fan_in ≥ 3 예상). 구형 stub 대체.
def fetch_confidence_analysis(*, days: int = 30) -> dict[str, Any]:
    """체결 행 → build_roundtrips → confidence.analyze → 버킷/상관 반환.

    REQ-050-6a: edge.roundtrips.build_roundtrips / edge.confidence.analyze 재사용.
    REQ-050-7: 최근 N일 제한 + TTL 캐시.

    어댑터: ro_connection 으로 orders + persona_decisions + risk_reviews 를 조인하여
    build_roundtrips 가 소비하는 행 형식으로 변환.

    Args:
        days: 최근 N일 이내 체결만 포함.

    Returns:
        dict: buckets(list), n_with_conf, pearson, spearman, none_count, approve, overridden.
    """
    from trading.edge.confidence import analyze
    from trading.edge.roundtrips import build_roundtrips

    cache_key = f"confidence:{days}"
    cached = _cache_get(_confidence_cache, cache_key)
    if cached is not None:
        return cached

    # 어댑터: orders + persona_decisions(confidence) + risk_reviews(verdict) 조인
    sql = """
        SELECT
            o.id,
            o.ts,
            o.filled_at,
            o.side,
            o.ticker,
            o.fill_qty,
            o.fill_price,
            COALESCE(o.fee, 0) AS fee,
            pd.confidence,
            (SELECT rr.verdict FROM risk_reviews rr
              WHERE rr.decision_id = pd.id
              ORDER BY rr.ts DESC LIMIT 1) AS verdict
        FROM orders o
        LEFT JOIN persona_decisions pd ON pd.id = o.persona_decision_id
        WHERE o.status IN ('filled', 'partial')
          AND o.fill_qty IS NOT NULL AND o.fill_qty > 0
          AND o.fill_price IS NOT NULL
          AND o.ts >= NOW() - (%s || ' days')::INTERVAL
        ORDER BY o.ticker, COALESCE(o.filled_at, o.ts), o.id
    """
    with ro_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (str(int(days)),))
        rows = cur.fetchall()

    fill_rows = [dict(r) for r in rows]
    rt_result = build_roundtrips(fill_rows)
    report = analyze(rt_result.roundtrips)

    def _bucket_dict(b: Any) -> dict[str, Any]:
        return {
            "label": b.label,
            "n": b.n,
            "win_rate": b.win_rate,
            "avg_return_pct": b.avg_return_pct,
            "expectancy": b.expectancy,
        }

    def _group_dict(g: Any) -> dict[str, Any] | None:
        if g is None:
            return None
        return {"label": g.label, "n": g.n, "win_rate": g.win_rate, "expectancy": g.expectancy}

    payload: dict[str, Any] = {
        "buckets": [_bucket_dict(b) for b in report.buckets],
        "n_with_conf": report.n_with_conf,
        "none_count": report.none_count,
        "pearson": report.pearson,
        "spearman": report.spearman,
        "approve": _group_dict(report.approve),
        "overridden": _group_dict(report.overridden),
        "none_verdict_count": report.none_verdict_count,
        "days": days,
    }

    _cache_put(_confidence_cache, cache_key, payload)
    return payload


# ---------------------------------------------------------------------------
# SPEC-TRADING-050 M1: 파이프라인 재구성 (REQ-050-2)
# ---------------------------------------------------------------------------


def fetch_pipeline() -> dict[str, Any]:
    """최신 사이클의 persona_runs 재구성.

    REQ-050-2: GET /api/pipeline — 최신 사이클 persona_runs 를
    macro→micro→decision→risk→portfolio 흐름으로 재구성.

    최신 실행 기준 2시간 내 실행된 모든 persona_runs 를 "현재 사이클"로 간주한다.
    persona_runs 가 없으면 steps=[] 반환(E1 — 500 금지).

    Returns:
        dict: steps(list of step dict), cycle_ts(최신 실행 ts 또는 None).
    """
    sql = """
        SELECT
            pr.id,
            pr.ts,
            pr.persona_name,
            pr.cycle_kind,
            pr.input_tokens,
            pr.output_tokens,
            pr.latency_ms,
            pr.error,
            pr.regime_at_decision
        FROM persona_runs pr
        WHERE pr.ts >= (
            SELECT ts FROM persona_runs ORDER BY ts DESC LIMIT 1
        ) - INTERVAL '2 hours'
        ORDER BY pr.ts ASC
    """
    with ro_connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    if not rows:
        return {"steps": [], "cycle_ts": None}

    steps = []
    for r in rows:
        row = dict(r)
        steps.append({
            "id": row.get("id"),
            "ts": row.get("ts"),
            "persona_name": row.get("persona_name"),
            "cycle_kind": row.get("cycle_kind"),
            "input_tokens": row.get("input_tokens"),
            "output_tokens": row.get("output_tokens"),
            "latency_ms": row.get("latency_ms"),
            "status": "error" if row.get("error") else "completed",
            "regime_at_decision": row.get("regime_at_decision"),
        })

    # cycle_ts: 가장 최근 실행 ts
    cycle_ts = max((s["ts"] for s in steps if s["ts"]), default=None)
    return {"steps": steps, "cycle_ts": cycle_ts}
