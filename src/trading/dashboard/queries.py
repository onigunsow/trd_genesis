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
from collections import defaultdict
from functools import lru_cache

import psycopg
from typing import Any

from trading.dashboard.db import ro_connection
from trading.db.session import DECISION_SCOPE_EXEMPT_EVENTS
from trading.kis.kis_ticker_info import lookup_names_from_db, resolve_ticker_name
from trading.scripts.codemap import scan_audit_calls

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
    def _sql(cool_expr: str) -> str:
        return f"""
            SELECT
                ss.halt_state,
                ss.trading_mode,
                ss.current_regime,
                ss.current_risk_appetite,
                ss.late_cycle_defense_active,
                ss.late_cycle_level,
                {cool_expr},
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

    with ro_connection() as conn, conn.cursor() as cur:
        try:
            cur.execute(_sql("ss.cool_down_active"))
            row = cur.fetchone()
        except psycopg.errors.UndefinedColumn:
            # mig 033 미적용 환경: cool_down_active 컬럼 부재 → 기본 false 로 graceful 폴백.
            # (선택 컬럼 하나로 status 패널 전체가 503 되지 않도록)
            conn.rollback()
            cur.execute(_sql("false AS cool_down_active"))
            row = cur.fetchone()

    if not row:
        raise RuntimeError("system_state 행 없음 — migration 001 미적용?")

    return dict(row)


# ---------------------------------------------------------------------------
# fetch_recent_decisions (SPEC-047 + SPEC-050 REQ-050-3 확장)
# ---------------------------------------------------------------------------


# @MX:ANCHOR: response key set is a contract with the frontend Decision type.
# @MX:REASON: SPEC-TRADING-064 결함 A — the TS type declared six fields the SELECT
#   never returned; tsc passed and the UI rendered blank. Adding or removing a
#   column here requires updating api/types.ts and the response-derived fixtures.
# @MX:SPEC: SPEC-TRADING-064
def fetch_recent_decisions(*, limit: int = 50) -> list[dict[str, Any]]:
    """persona_decisions + persona_runs + risk_reviews LEFT JOIN — 최신 N 건.

    REQ-050-3: risk_reviews(verdict/rationale)를 decision_id 로 LEFT JOIN.
    매칭 없는 결정은 risk_verdict/risk_rationale 가 null 이며 행 누락 없음.

    SPEC-TRADING-064 REQ-064-A1: regime_at_decision/trigger_context/response_json 은
    이미 JOIN 되어 있는 persona_runs 소유 컬럼이다 — 신규 JOIN·마이그레이션 없음.

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
            rr.rationale AS risk_rationale,
            pr.regime_at_decision,
            pr.trigger_context,
            pr.response_json
        FROM persona_decisions pd
        JOIN persona_runs pr ON pr.id = pd.persona_run_id
        LEFT JOIN risk_reviews rr ON rr.decision_id = pd.id
        ORDER BY pd.ts DESC
        LIMIT %s
    """
    with ro_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (limit,))
        rows = cur.fetchall()

    result = [dict(r) for r in rows]

    # ticker_name 보강: ticker_metadata 에서 일괄 조회 (KIS/pykrx 호출 없음)
    tickers = [r["ticker"] for r in result if r.get("ticker")]
    db_names = lookup_names_from_db(tickers)
    for row in result:
        row["ticker_name"] = resolve_ticker_name(
            row.get("ticker") or "", db_names=db_names
        )

    return result


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

    # ticker_name 보강: ticker_metadata 에서 일괄 조회 (KIS/pykrx 호출 없음)
    tickers = [r["ticker"] for r in result if r.get("ticker")]
    db_names = lookup_names_from_db(tickers)
    for row in result:
        row["ticker_name"] = resolve_ticker_name(
            row.get("ticker") or "", db_names=db_names
        )

    return result


# ---------------------------------------------------------------------------
# fetch_holdings (SPEC-047 기존)
# ---------------------------------------------------------------------------


def fetch_holdings() -> list[dict[str, Any]]:
    """현재 순매수 포지션 집계 (매수 - 매도, fill 완료 orders 기준).

    ticker 별 qty_net > 0 인 행만 반환.
    """
    # SPEC-042 D1 교정: 보유 진실원은 KIS-동기화된 `positions`(broker-truth 캐시)다.
    # 과거엔 orders 순매수 집계를 썼으나, 합성(synthetic) 매수가 orders 를 부풀려
    # 유령 보유를 노출시켰다(086790 13 vs 10 등). positions 는 reconcile_from_balance
    # 로 KIS 잔고와 정합되므로 이것을 단일 소스로 삼는다. 평가 데이터는 최신
    # position_eval_snapshot LEFT JOIN. avg_cost·qty 는 positions 가 권위.
    sql = """
        WITH latest AS (
            SELECT ticker, eval_price, eval_amount, unrealized_pnl, pnl_pct
            FROM position_eval_snapshot
            WHERE trading_day = (SELECT MAX(trading_day) FROM position_eval_snapshot)
        )
        SELECT
            p.ticker,
            p.qty                       AS qty_net,
            p.avg_cost                  AS avg_fill_price,
            (p.qty * p.avg_cost)        AS total_cost,
            l.eval_price,
            l.eval_amount,
            l.unrealized_pnl,
            l.pnl_pct
        FROM positions p
        LEFT JOIN latest l ON l.ticker = p.ticker
        WHERE p.qty > 0
        ORDER BY p.ticker
    """
    with ro_connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    result = [dict(r) for r in rows]

    # ticker_name 보강: ticker_metadata 에서 일괄 조회 (KIS/pykrx 호출 없음)
    tickers = [r["ticker"] for r in result if r.get("ticker")]
    db_names = lookup_names_from_db(tickers)
    for row in result:
        row["ticker_name"] = resolve_ticker_name(
            row.get("ticker") or "", db_names=db_names
        )

    return result


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

# ---------------------------------------------------------------------------
# SPEC-TRADING-050 follow-up: postmortem 헬퍼 — KOSPI 상대수익률 계산
# ---------------------------------------------------------------------------


def _kospi_relative(
    entry_date: "date",
    exit_date: "date",
    trade_return_pct: float,
    closes_dict: "dict[date, float]",
) -> "tuple[float, float]":
    """거래 기간 KOSPI 상대수익률 (relative_5d, relative_20d) 반환.

    동일 보유 기간의 KOSPI 수익률을 거래 수익률에서 빼서 초과수익률을 구한다.
    closes_dict 가 비어 있거나 시작·종료 종가를 찾을 수 없으면 (0.0, 0.0) 반환.

    Args:
        entry_date:        진입일.
        exit_date:         청산일.
        trade_return_pct:  거래 수익률 % (RoundTrip.return_pct).
        closes_dict:       {date: close} — benchmark.kospi_closes() 결과.

    Returns:
        (relative_5d, relative_20d) — 동일 기간 초과수익률로 양쪽 모두 동일 값.
    """
    if not closes_dict:
        return 0.0, 0.0

    sorted_dates = sorted(closes_dict.keys())

    # 시작 종가: entry_date 이후 최초 거래일
    start_close: float | None = None
    for d in sorted_dates:
        if d >= entry_date:
            start_close = closes_dict[d]
            break

    # 종료 종가: exit_date 이후 최초 거래일 (없으면 마지막 가용 종가)
    end_close: float | None = None
    for d in sorted_dates:
        if d >= exit_date:
            end_close = closes_dict[d]
            break
    if end_close is None and sorted_dates:
        end_close = closes_dict[sorted_dates[-1]]

    if start_close is None or end_close is None or start_close == 0.0:
        return 0.0, 0.0

    kospi_ret = (end_close / start_close - 1.0) * 100.0
    relative = trade_return_pct - kospi_ret
    return relative, relative


def _kospi_forward_relative(
    decision_date: "date",
    closes_dict: "dict[date, float]",
    *,
    trading_days: int = 20,
) -> float:
    """미진입 결정 기준 이후 N거래일 KOSPI 수익률 반환.

    결정일 종가 대비 N거래일 후 종가를 구한다.
    데이터 부재 시 0.0 반환 (graceful).

    Args:
        decision_date:  결정 날짜.
        closes_dict:    {date: close} — benchmark.kospi_closes() 결과.
        trading_days:   몇 거래일 후를 참조할지 (기본 20).

    Returns:
        KOSPI N거래일 수익률 %.
    """
    if not closes_dict:
        return 0.0

    sorted_dates = sorted(closes_dict.keys())

    # 결정일 이후 최초 거래일 종가를 시작으로 사용
    start_idx: int | None = None
    for i, d in enumerate(sorted_dates):
        if d >= decision_date:
            start_idx = i
            break
    if start_idx is None:
        return 0.0

    # N거래일 후 종가 인덱스
    end_idx = min(start_idx + trading_days, len(sorted_dates) - 1)
    if end_idx <= start_idx:
        return 0.0

    start_close = closes_dict[sorted_dates[start_idx]]
    end_close = closes_dict[sorted_dates[end_idx]]
    if not start_close:
        return 0.0

    return (end_close / start_close - 1.0) * 100.0


# @MX:ANCHOR: [AUTO] fetch_postmortem — postmortem 분류 단일 읽기 진입점.
# @MX:REASON: 대시보드·테스트 모두 이 함수를 소비(fan_in ≥ 3 예상). 구형 stub 대체.
def fetch_postmortem(*, days: int = 30, limit: int = 200) -> dict[str, Any]:
    """persona_decisions → 라운드트립 매칭 → classify_decision_outcome → 4분류 + 귀인.

    SPEC-050 follow-up 수정사항:
    - prob_* (강세/기준/약세 확률) 컬럼 제거 → mig 033 없이도 동작 (문제 1 해결).
    - 라운드트립 매칭 구현 → relative_5d/20d 실값 계산 (문제 2 해결).
    - KOSPI 상대수익률 계산 — 데이터 없으면 0.0 graceful fallback.

    어댑터 흐름:
    1. persona_decisions + persona_runs(regime) 조회 (prob_* 제외).
    2. orders(체결) 조회 → build_roundtrips → RoundTrip 목록.
    3. benchmark.kospi_closes 로 KOSPI 종가 로드 (캐시 우선, 없으면 0.0 폴백).
    4. BUY 결정 → 동일 종목 라운드트립 날짜 매칭 → relative_5d/20d 계산.
    5. HOLD/SELL 결정 또는 라운드트립 없는 BUY → 미진입 경로 (20일 KOSPI 선행).
    6. classify_decision_outcome 호출 → 4분류 집계 + 페르소나 귀인.

    Args:
        days: 최근 N일 이내 결정만 포함.
        limit: DB 조회 최대 행 수.

    Returns:
        dict: distribution(4분류 카운트), per_persona(페르소나별 귀인), total, days.
              반환 형태는 PostmortemBreakdown 프론트엔드와 하위호환.
    """
    from datetime import date as _date
    from datetime import timedelta

    from trading.edge.benchmark import kospi_closes
    from trading.edge.postmortem import (
        PersonaStats,
        attribute_to_persona,
        classify_decision_outcome,
    )
    from trading.edge.roundtrips import build_roundtrips

    cache_key = f"postmortem:{days}:{limit}"
    cached = _cache_get(_postmortem_cache, cache_key)
    if cached is not None:
        return cached

    # -------------------------------------------------------------------------
    # 쿼리 1: persona_decisions — prob_* 컬럼 제외 (mig 033 미적용 환경 호환)
    # -------------------------------------------------------------------------
    decision_sql = """
        SELECT
            pd.id,
            pd.ts,
            pr.persona_name,
            pd.cycle_kind,
            pd.ticker,
            pd.side,
            pd.confidence,
            pd.rationale,
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
        cur.execute(decision_sql, (str(int(days)), limit))
        decision_rows = cur.fetchall()

    # -------------------------------------------------------------------------
    # 쿼리 2: orders(체결) → build_roundtrips (fetch_confidence_analysis 와 동일 패턴)
    # -------------------------------------------------------------------------
    # C1[CRITICAL] SPEC-TRADING-042: correction 컬럼 필수 — 미포함 시 교정 SELL 이
    # 정상 매도로 오인돼 가짜 RoundTrip 생성(거짓그린 실패모드, 감사 반영).
    fill_sql = """
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
              ORDER BY rr.ts DESC LIMIT 1) AS verdict,
            COALESCE(o.correction, false) AS correction
        FROM orders o
        LEFT JOIN persona_decisions pd ON pd.id = o.persona_decision_id
        WHERE o.status IN ('filled', 'partial')
          AND o.fill_qty IS NOT NULL AND o.fill_qty > 0
          AND o.fill_price IS NOT NULL
          AND o.ts >= NOW() - (%s || ' days')::INTERVAL
        ORDER BY o.ticker, COALESCE(o.filled_at, o.ts), o.id
    """
    with ro_connection() as conn, conn.cursor() as cur:
        cur.execute(fill_sql, (str(int(days)),))
        fill_rows = cur.fetchall()

    rt_result = build_roundtrips([dict(r) for r in fill_rows])

    # -------------------------------------------------------------------------
    # KOSPI 종가 로드 — 전체 결정 기간 + 선행 20거래일(≈28일)
    # -------------------------------------------------------------------------
    # 결정 날짜 범위 수집
    decision_dates: list[_date] = []
    for row in decision_rows:
        d = dict(row)
        ts_val = d.get("ts")
        if hasattr(ts_val, "date"):
            decision_dates.append(ts_val.date())
        elif isinstance(ts_val, _date):
            decision_dates.append(ts_val)

    closes_dict: dict[_date, float] = {}
    if decision_dates:
        min_d = min(decision_dates)
        # 선행 20거래일 여유분 포함(≈28일)
        max_d = _date.today() + timedelta(days=1)
        try:
            closes_list = kospi_closes(min_d, max_d)
            closes_dict = {d: c for d, c in closes_list}
        except Exception:  # noqa: BLE001 — KOSPI 조회 실패 graceful 처리
            LOG.info("fetch_postmortem: KOSPI 종가 조회 실패 — relative_5d/20d=0.0 폴백")

    # -------------------------------------------------------------------------
    # 라운드트립 인덱스: ticker → list[RoundTrip] (날짜순)
    # -------------------------------------------------------------------------
    from collections import defaultdict

    rt_by_ticker: dict[str, list[Any]] = defaultdict(list)
    for rt in rt_result.roundtrips:
        rt_by_ticker[rt.ticker].append(rt)

    # 매칭에 사용된 라운드트립 추적 (중복 매칭 방지)
    used_rt_ids: set[int] = set()

    # -------------------------------------------------------------------------
    # 분류 집계
    # -------------------------------------------------------------------------
    distribution: dict[str, int] = {
        "TRUE_POSITIVE": 0,
        "FALSE_POSITIVE": 0,
        "REGIME_MISMATCH": 0,
        "MISSED": 0,
    }
    per_persona: dict[str, PersonaStats] = {}

    for row in decision_rows:
        d = dict(row)
        persona_name = str(d.get("persona_name") or "unknown")
        side = str(d.get("side") or "hold").lower()
        regime = str(d.get("regime_at_decision") or "neutral")

        ts_val = d.get("ts")
        if hasattr(ts_val, "date"):
            dec_date = ts_val.date()
        elif isinstance(ts_val, _date):
            dec_date = ts_val
        else:
            dec_date = _date.today()

        # 어댑터: decision dict 구성
        decision: dict[str, Any] = {
            "side": side,
            "confidence": d.get("confidence"),
            "signal_dir": side,
            "persona": persona_name,
        }

        roundtrip_dict: dict[str, Any] | None = None
        relative_5d = 0.0
        relative_20d = 0.0

        if side == "buy":
            # BUY 결정 → 같은 종목 라운드트립 날짜 매칭 (±2일 이내)
            ticker = str(d.get("ticker") or "")
            candidates = rt_by_ticker.get(ticker, [])
            best_rt = None
            best_delta = 999

            for rt in candidates:
                rt_id = id(rt)
                if rt_id in used_rt_ids:
                    continue
                delta = abs((rt.entry_date - dec_date).days)
                if delta <= 2 and delta < best_delta:
                    best_rt = rt
                    best_delta = delta

            if best_rt is not None:
                used_rt_ids.add(id(best_rt))
                # 라운드트립 dict: classify_decision_outcome 이 소비하는 형태
                roundtrip_dict = {
                    "net_pnl": best_rt.net_pnl,
                    "return_pct": best_rt.return_pct,
                    "ticker": best_rt.ticker,
                    "entry_date": best_rt.entry_date,
                    "exit_date": best_rt.exit_date,
                }
                # KOSPI 상대수익률 계산
                rel5, rel20 = _kospi_relative(
                    best_rt.entry_date,
                    best_rt.exit_date,
                    best_rt.return_pct,
                    closes_dict,
                )
                relative_5d, relative_20d = rel5, rel20
            else:
                # BUY 결정이지만 매칭되는 라운드트립 없음 (미체결/진행중)
                # 미진입 경로: 이후 20거래일 KOSPI 수익률
                relative_20d = _kospi_forward_relative(dec_date, closes_dict)
                relative_5d = relative_20d
        else:
            # HOLD/SELL 결정 — 미진입 경로
            relative_20d = _kospi_forward_relative(dec_date, closes_dict)
            relative_5d = relative_20d

        outcome = classify_decision_outcome(
            decision=decision,
            roundtrip_or_none=roundtrip_dict,
            relative_5d=relative_5d,
            relative_20d=relative_20d,
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

    # 어댑터: orders + persona_decisions(confidence) + risk_reviews(verdict) 조인.
    # C1[CRITICAL] SPEC-TRADING-042: correction 컬럼 필수 — 미포함 시 교정 SELL 이
    # 정상 매도로 오인돼 /api/confidence 오염(감사 C1 반영).
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
              ORDER BY rr.ts DESC LIMIT 1) AS verdict,
            COALESCE(o.correction, false) AS correction
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


# ---------------------------------------------------------------------------
# SPEC-TRADING-054 M1: 신규 백엔드 엔드포인트 쿼리 함수
# ---------------------------------------------------------------------------

# @MX:NOTE: [AUTO] 아래 fetch_* 함수들은 edge 단일원천 읽기 전용이다.
# 손익/라운드트립/KPI 수식을 재구현하지 않고 edge 모듈/position_eval_snapshot 에서만
# 읽는다 (REQ-054-A6, REQ-054-A7).
# @MX:SPEC: SPEC-TRADING-054 M1


def fetch_roundtrips(
    *,
    days: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """edge.roundtrips 에서 라운드트립 원장을 읽어 반환.

    REQ-054-A1: edge.roundtrips.compute_roundtrips() 단일원천.

    엔드포인트 응답 필드 (TypeScript 계약):
        ticker: string
        entry_date: string (ISO date)
        exit_date: string (ISO date)
        qty: number
        entry_price: number
        exit_price: number
        net_pnl: number
        return_pct: number
        entry_fee: number
        exit_fee: number
        fees: number
        holding_days: number
        confidence: number | null
        verdict: string | null
        persona: string | null
        is_win: boolean

    Args:
        days: 최근 N일 이내 라운드트립만. None 이면 전체.
        limit: 최대 반환 행 수. None 이면 제한 없음.
    """
    from trading.edge import roundtrips as _rt

    rt_result = _rt.compute_roundtrips(days)
    rts = rt_result.roundtrips

    # 최신 순 정렬 (exit_date 내림차순)
    rts = sorted(rts, key=lambda r: r.exit_date, reverse=True)

    if limit is not None:
        rts = rts[:limit]

    # ticker_name 보강: ticker_metadata 에서 일괄 조회 (KIS/pykrx 호출 없음)
    tickers = [rt.ticker for rt in rts]
    db_names = lookup_names_from_db(tickers)

    result = []
    for rt in rts:
        result.append({
            "ticker": rt.ticker,
            "ticker_name": resolve_ticker_name(rt.ticker, db_names=db_names),
            "entry_date": rt.entry_date.isoformat(),
            "exit_date": rt.exit_date.isoformat(),
            "qty": rt.qty,
            "entry_price": rt.entry_price,
            "exit_price": rt.exit_price,
            "net_pnl": rt.net_pnl,
            "return_pct": rt.return_pct,
            "entry_fee": rt.entry_fee,
            "exit_fee": rt.exit_fee,
            "fees": rt.fees,
            "holding_days": rt.holding_days,
            "confidence": rt.confidence,
            "verdict": rt.verdict,
            "persona": rt.persona,
            "is_win": rt.is_win,
        })
    return result


def _get_latest_equity_nav() -> float:
    """daily_equity_snapshot 최신 행에서 NAV(total_assets) 반환.

    데이터 없으면 0.0 반환.
    """
    sql = """
        SELECT total_assets
          FROM daily_equity_snapshot
         ORDER BY trading_day DESC
         LIMIT 1
    """
    try:
        with ro_connection() as conn, conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
        if row:
            return float(row.get("total_assets") or 0)
    except Exception as exc:
        LOG.warning("NAV 조회 실패 (0.0 폴백): %s", exc)
    return 0.0


def fetch_portfolio() -> dict[str, Any]:
    """position_eval_snapshot(최신) + ticker_metadata 조인으로 포트폴리오 구성 반환.

    REQ-054-A2, REQ-054-G1: 종목별 평가금액·비중·집중도·섹터 분해.
    대시보드는 이 함수로 읽기만 한다(REQ-054-A7).

    엔드포인트 응답 필드 (TypeScript 계약):
        holdings: Array<{
            ticker: string
            qty: number
            avg_cost: number
            eval_price: number
            eval_amount: number      -- market_value
            unrealized_pnl: number
            pnl_pct: number
            weight_pct: number       -- eval_amount / NAV * 100
            sector: string           -- 미분류 폴백
        }>
        nav: number                  -- 총자산(NAV)
        cash_amount: number
        cash_ratio: number           -- 현금/NAV * 100
        herfindahl: number           -- 집중도 지수 (0~1)
        top3_pct: number             -- 상위 3종목 비중 합 (%)
        sector_breakdown: Array<{sector: string, weight_pct: number}>
        snapshot_date: string | null -- 기준 거래일 (ISO date)
    """
    # 최신 trading_day 의 스냅샷 조회
    sql = """
        SELECT
            p.ticker,
            p.qty,
            p.avg_cost,
            p.eval_price,
            p.eval_amount,
            p.unrealized_pnl,
            p.pnl_pct,
            p.trading_day,
            COALESCE(m.sector, '미분류') AS sector
        FROM position_eval_snapshot p
        LEFT JOIN ticker_metadata m ON m.ticker = p.ticker
        WHERE p.trading_day = (
            SELECT MAX(trading_day) FROM position_eval_snapshot
        )
          AND p.qty > 0
        ORDER BY p.eval_amount DESC
    """
    try:
        with ro_connection() as conn, conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
    except Exception as exc:
        LOG.error("fetch_portfolio 스냅샷 조회 실패: %s", exc)
        rows = []

    nav = _get_latest_equity_nav()
    snapshot_date: str | None = None

    # ticker_name 보강: ticker_metadata 에서 일괄 조회 (KIS/pykrx 호출 없음)
    all_tickers = [dict(r)["ticker"] for r in rows if dict(r).get("ticker")]
    db_names = lookup_names_from_db(all_tickers)

    holdings = []
    total_stock_eval = 0.0
    for row in rows:
        eval_amount = float(row.get("eval_amount") or 0)
        total_stock_eval += eval_amount
        weight_pct = (eval_amount / nav * 100.0) if nav > 0 else 0.0
        if snapshot_date is None and row.get("trading_day"):
            td = row["trading_day"]
            snapshot_date = td.isoformat() if hasattr(td, "isoformat") else str(td)
        ticker = row["ticker"]
        holdings.append({
            "ticker": ticker,
            "ticker_name": resolve_ticker_name(ticker, db_names=db_names),
            "qty": int(row.get("qty") or 0),
            "avg_cost": float(row.get("avg_cost") or 0),
            "eval_price": float(row.get("eval_price") or 0),
            "eval_amount": eval_amount,
            "unrealized_pnl": float(row.get("unrealized_pnl") or 0),
            "pnl_pct": float(row.get("pnl_pct") or 0),
            "weight_pct": weight_pct,
            "sector": row.get("sector") or "미분류",
        })

    # 현금 비율: NAV - 주식평가총액
    cash_amount = max(0.0, nav - total_stock_eval)
    cash_ratio = (cash_amount / nav * 100.0) if nav > 0 else 0.0

    # Herfindahl 지수: Σ (weight_i)^2 (0~1 범위, weight_i = 비중/100)
    weights = [(h["eval_amount"] / nav) for h in holdings if nav > 0]
    herfindahl = sum(w * w for w in weights) if weights else 0.0

    # 상위 3종목 비중 합계 (%)
    sorted_weights_pct = sorted(
        [h["weight_pct"] for h in holdings], reverse=True
    )
    top3_pct = sum(sorted_weights_pct[:3])

    # 섹터별 비중 집계
    sector_totals: dict[str, float] = {}
    for h in holdings:
        sec = h["sector"] or "미분류"
        sector_totals[sec] = sector_totals.get(sec, 0.0) + h["eval_amount"]

    sector_breakdown = []
    for sec, amt in sorted(sector_totals.items(), key=lambda x: -x[1]):
        sector_breakdown.append({
            "sector": sec,
            "weight_pct": (amt / nav * 100.0) if nav > 0 else 0.0,
        })

    return {
        "holdings": holdings,
        "nav": nav,
        "cash_amount": cash_amount,
        "cash_ratio": cash_ratio,
        "herfindahl": herfindahl,
        "top3_pct": top3_pct,
        "sector_breakdown": sector_breakdown,
        "snapshot_date": snapshot_date,
    }


def fetch_pnl_daily(
    *,
    days: int | None = None,
    period: str = "daily",
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """edge 라운드트립 exit_date 기준 일별/주별/월별 실현손익 + 누적 + KOSPI 알파.

    REQ-054-A3, REQ-054-A8:
        - period ∈ {daily, weekly, monthly}
        - KOSPI 미가용 시 alpha 필드 null, benchmark_available=false (빈 패널/0 오기재 금지)

    엔드포인트 응답 필드 (TypeScript 계약):
        period: string
        benchmark_available: boolean
        rows: Array<{
            period_label: string     -- ISO date / "YYYY-Www" / "YYYY-MM"
            realized_pnl: number
            cumulative_pnl: number
            alpha_pct: number | null -- KOSPI 상대 (미가용 시 null)
        }>
    """
    from datetime import date as _date
    from trading.edge import roundtrips as _rt
    from trading.edge import benchmark as _bm

    rt_result = _rt.compute_roundtrips(days)
    rts = rt_result.roundtrips

    # 날짜 필터
    if start_date:
        sd = _date.fromisoformat(start_date)
        rts = [r for r in rts if r.exit_date >= sd]
    if end_date:
        ed = _date.fromisoformat(end_date)
        rts = [r for r in rts if r.exit_date <= ed]

    # KOSPI 종가 (가용 시)
    bm_closes: dict = {}
    try:
        if rts:
            all_dates = sorted(r.exit_date for r in rts)
            bm_closes = _bm.kospi_closes(all_dates[0], all_dates[-1])
    except Exception as exc:
        LOG.warning("KOSPI 종가 조회 실패 (알파 null 폴백): %s", exc)
        bm_closes = {}

    benchmark_available = bool(bm_closes)

    # 기간 레이블 생성 헬퍼
    def _label(d: "_date") -> str:
        if period == "weekly":
            # ISO week: YYYY-Www
            return f"{d.isocalendar().year}-W{d.isocalendar().week:02d}"
        if period == "monthly":
            return d.strftime("%Y-%m")
        return d.isoformat()

    # 기간별 집계
    period_map: dict[str, float] = {}
    for rt in rts:
        lbl = _label(rt.exit_date)
        period_map[lbl] = period_map.get(lbl, 0.0) + rt.net_pnl

    # KOSPI 기간별 알파: 간단 근사(기간 내 첫날~마지막날 KOSPI 수익률)
    def _period_kospi_return(label: str) -> float | None:
        if not bm_closes:
            return None
        # 해당 레이블에 속하는 날짜들 필터
        label_dates = [
            rt.exit_date for rt in rts if _label(rt.exit_date) == label
        ]
        if not label_dates:
            return None
        first_d = min(label_dates)
        last_d = max(label_dates)
        sorted_dates = sorted(bm_closes.keys())
        # first_d 이전 가장 가까운 날 종가
        prev_dates = [d for d in sorted_dates if d <= first_d]
        next_dates = [d for d in sorted_dates if d <= last_d]
        if not prev_dates or not next_dates:
            return None
        start_close = bm_closes[prev_dates[-1]]
        end_close = bm_closes[next_dates[-1]]
        if start_close <= 0:
            return None
        return (end_close / start_close - 1.0) * 100.0

    rows = []
    cumulative = 0.0
    for label in sorted(period_map.keys()):
        pnl = period_map[label]
        cumulative += pnl
        kospi_ret = _period_kospi_return(label)
        # 해당 기간 전략 수익률 (원화 → 비율 계산은 edge 미제공이므로 null 근사)
        alpha = None  # 원화 알파 직접 산출 불가(단위 불일치) — null 처리
        rows.append({
            "period_label": label,
            "realized_pnl": pnl,
            "cumulative_pnl": cumulative,
            "alpha_pct": alpha,
        })

    return {
        "period": period,
        "benchmark_available": benchmark_available,
        "rows": rows,
    }


# fetch_scorecard 에 sortino 를 추가하는 패치는 기존 함수를 교체로 처리.
# 아래 함수가 app.py /api/scorecard 에서 호출된다.

def fetch_scorecard_with_sortino() -> dict[str, Any]:
    """fetch_scorecard() + sortino 노출.

    REQ-054-A4: analytics.sortino 는 이미 계산되어 있으나 기존
    fetch_scorecard() 응답에 포함되지 않았다. 이 함수가 그 키를 추가한다.
    신규 계산 없음 — 노출만.

    엔드포인트 응답 필드 (TypeScript 계약):
        verdict: string
        grade: string
        reasons: string[]
        n_closed: number
        win_rate: number
        expectancy_adj: number
        profit_factor_adj: number
        alpha_pct: number | null
        benchmark_available: boolean
        cagr: number | null
        mdd: number | null
        sharpe: number | null
        sortino: number              -- 추가 (REQ-054-A4)
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
        "sortino": analytics.sortino,  # REQ-054-A4: 노출만, 재계산 없음
    }


# ---------------------------------------------------------------------------
# SPEC-TRADING-064 그룹 C: 결정 추적 흐름도 (REQ-064-C1/C3/C9/C10/C11)
# ---------------------------------------------------------------------------

# REQ-064-C1: 단일 결정 상세 — fetch_recent_decisions 와 동일한 15키(그룹 A 계약).
# 의도적 중복(코드 재사용 대신): fetch_recent_decisions 는 이미 배포된 계약이라
# 리팩터로 공유 상수화하면 그 회귀 표면을 건드리게 된다 — 여기서는 새 WHERE 절만
# 추가한 독립 SQL 을 둔다.
_DECISION_BY_ID_SQL = """
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
        rr.rationale AS risk_rationale,
        pr.regime_at_decision,
        pr.trigger_context,
        pr.response_json
    FROM persona_decisions pd
    JOIN persona_runs pr ON pr.id = pd.persona_run_id
    LEFT JOIN risk_reviews rr ON rr.decision_id = pd.id
    WHERE pd.id = %s
"""

# REQ-064-C1/그룹 B: 단일 결정은 details 최상위 decision_id(스칼라, TIER 1~4/7).
# 배치 이벤트는 스칼라 키를 안 쓴다(B3a/B5) — decision_ids 배열 또는
# adjusted/rejected/sells/dropped 각 항목의 decision_id 다. 넷을 OR 로 안 묶으면
# COUNT_HALT_BYPASS_SELL/PORTFOLIO_ADJUSTMENT/PORTFOLIO_GATE_DROP 이 조용히 추적에서 빠진다
# (이 SPEC 이 없애려는 침묵을 새로 만드는 셈).
_TRACE_EVENTS_SQL = """
    SELECT ts, event_type, actor, details
    FROM audit_log
    WHERE details->>'decision_id' = %s
       OR details->'context'->>'decision_id' = %s
       OR details->'decision_ids' @> to_jsonb(%s::int)
       OR details->'adjusted' @> jsonb_build_array(jsonb_build_object('decision_id', %s::int))
       OR details->'rejected' @> jsonb_build_array(jsonb_build_object('decision_id', %s::int))
       OR details->'sells' @> jsonb_build_array(jsonb_build_object('decision_id', %s::int))
       OR details->'dropped' @> jsonb_build_array(jsonb_build_object('decision_id', %s::int))
    ORDER BY ts ASC
"""

# REQ-064-C1: 정확 매칭만. 규칙 기반 주문(persona_decision_id IS NULL — 워치독
# 손절/익절, forced_deleverage, ghost-buy 교정행)은 이 스칼라 조회로 원천적으로
# 안 걸린다. 같은 종목·같은 사이클 창을 근사로 엮는 것은 하지 않는다 — 부정확한
# 연결은 누락보다 나쁘다(과제 지시 사항).
_TRACE_ORDERS_SQL = """
    SELECT
        id, ts, side, ticker, qty, status, rejected_reason,
        fill_price, fill_qty, synthetic, correction, persona_decision_id
    FROM orders
    WHERE persona_decision_id = %s
    ORDER BY ts ASC
"""


@lru_cache(maxsize=1)
def _audit_bridge() -> tuple[dict[tuple[str, str], list[str]], set[str]]:
    """REQ-064-C3: ast 브릿지((file,function) -> [event_type]) 를 프로세스 생애주기당
    1회만 스캔한다 — 요청마다 `src/trading/**/*.py` 전체를 재파싱하지 않는다.
    별도 이벤트→노드 하드코딩 맵을 두지 않고 `scan_audit_calls` 결과만 단일원천으로 쓴다.
    """
    return scan_audit_calls()


def _classify_node_state(
    event_types: list[str], matched_events: list[dict[str, Any]]
) -> str:
    """REQ-064-C2 [HARD] 4-상태 판정.

    matched_events: 이 노드(함수)의 event_types 중 하나이며 이 결정과 상관된
    audit_log 행. 상태:
      - "not_involved": 상관 행 0건 + 이 노드가 결정 비종속 이벤트만 내지는 않음.
      - "decision_agnostic": 상관 행 0건이지만 이 노드가 내는 이벤트가 전부
        DECISION_SCOPE_EXEMPT_EVENTS(TIER 5/6, 영구 면제) 뿐이거나, 상관 행은
        있지만 전부 decision_scope="batch"(또는 그 외 비-결정 스코프) 뿐임.
      - "recorded": 상관 행 중 decision_scope 가 비어 있는(=진짜 결정 단위) 행이
        하나라도 있음.
      - "rule_based": 상관 행 중 decision_scope="rule_based" 인 행이 있음(진짜
        결정 단위 행은 없음). 참고: `_TRACE_ORDERS_SQL`/`_TRACE_EVENTS_SQL` 이
        스칼라 decision_id 정확 매칭만 쓰므로(REQ-064-C1 docstring 참조),
        rule_based 행은 decision_id 가 애초에 null 이라 실 운영 데이터에서는 이
        분기가 안 걸린다 — 그래도 REQ-064-C2 가 요구하는 네 상태 도메인을 완전하게
        지키기 위해 분류 로직 자체는 남겨 둔다.
    """
    if not matched_events:
        if event_types and all(et in DECISION_SCOPE_EXEMPT_EVENTS for et in event_types):
            return "decision_agnostic"
        return "not_involved"
    scopes = {(e.get("details") or {}).get("decision_scope") for e in matched_events}
    if None in scopes:
        return "recorded"
    if "rule_based" in scopes:
        return "rule_based"
    return "decision_agnostic"


def _serialize_trace_event(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_type": row["event_type"],
        "ts": row["ts"],
        "actor": row["actor"],
        "details": row["details"],
    }


def _build_trace_nodes(
    event_rows: list[dict[str, Any]],
    audit_map: dict[tuple[str, str], list[str]],
) -> list[dict[str, Any]]:
    """REQ-064-C3: audit_map((file,function)->[event_type]) 전체를 순회해 노드
    하나당 항목 하나를 만든다 — 강조 대상만 거르지 않고 `not_involved` 노드도 포함해
    "관여 안 함"과 "기록 없음"을 구별한다(REQ-064-C2)."""
    events_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in event_rows:
        events_by_type[row["event_type"]].append(row)

    nodes = []
    for (file, func), event_types in sorted(audit_map.items()):
        matched = [e for et in event_types for e in events_by_type.get(et, [])]
        module = file.removeprefix("src/trading/").removesuffix(".py").split("/")[0]
        nodes.append(
            {
                "file": file,
                "function": func,
                "module": module,
                "state": _classify_node_state(event_types, matched),
                "events": [_serialize_trace_event(e) for e in matched],
            }
        )
    return nodes


def _serialize_trace_order(row: dict[str, Any]) -> dict[str, Any]:
    """REQ-064-C10/그룹 B6: persona_decision_id 유무로 origin 을 밝힌다.

    `_TRACE_ORDERS_SQL` 이 정확 매칭(`persona_decision_id = %s`)만 반환하므로
    실사용 결과의 origin 은 항상 "decision" 이다. 그래도 매핑을 범용으로 두는
    이유는 계약(origin 필드)이 규칙 기반 실행(NULL)까지 표현해야 하고, 향후 더
    정밀한 상관 경로가 생기면 이 함수 하나로 충분해서다.
    """
    origin = "decision" if row.get("persona_decision_id") is not None else "rule_based"
    return {
        "id": row["id"],
        "ts": row["ts"],
        "side": row["side"],
        "ticker": row["ticker"],
        "qty": row["qty"],
        "status": row["status"],
        "rejected_reason": row.get("rejected_reason"),
        "fill_price": row.get("fill_price"),
        "fill_qty": row.get("fill_qty"),
        "synthetic": row.get("synthetic"),
        "correction": row.get("correction"),
        "origin": origin,
    }


# @MX:ANCHOR: [AUTO] SPEC-TRADING-064 REQ-064-C1 — 결정 추적 응답 계약.
# @MX:REASON: 프런트가 이 응답 키를 1:1로 그대로 그린다(cytoscape 노드/엣지 강조).
#   키 이름이나 nodes[].state 도메인이 어긋나면 예외가 아니라 빈 화면으로 조용히
#   실패한다 — 계약을 바꿀 때 반드시 프런트 타입도 함께 갱신할 것.
# @MX:SPEC: SPEC-TRADING-064
def fetch_decision_trace(decision_id: int) -> dict[str, Any] | None:
    """결정 하나의 추적 페이로드: 본문 + 관여 audit_log 이벤트 + 연결 주문 + 노드 강조.

    REQ-064-C1: {"decision": 그룹 A 15키, "nodes": REQ-064-C3 역인덱스로 계산된
    (file,function) 단위 강조(REQ-064-C2 4상태), "orders": 정확 매칭만(위 SQL
    docstring 참조), "unmatched_events": ast 브릿지가 매핑 못한 이벤트(침묵 금지,
    REQ-064-C9 정신 — 값이 없으면 없다고 보여준다)}.

    REQ-064-C11: 읽기 전용. ro_connection(dashboard_ro 역할, SELECT 전용)만
    쓰고 매매 테이블에 쓰지 않으며, 페르소나 재실행이나 LLM 호출을 하지 않는다.

    낡은 행(870f5fe 이전, `details.context.decision_id` 중첩) 처리: **같이 읽는다.**

    초안은 최상위 키만 읽고 레거시 경로를 기각했으나, 배포 직후 실측으로 뒤집혔다:
    최근 30일 `LIMIT_BREACH` 166건 중 **최상위 `decision_id` 보유 0건, 중첩 166건**
    (배선이 870f5fe 로 오늘 배포됐으니 과거 데이터는 전부 중첩이다). 최상위만 읽으면
    지난 결정을 추적할 때 주문을 막은 이유인 `LIMIT_BREACH` 가 한 건도 안 보인다 —
    "왜 이 결정이 주문으로 안 이어졌나"가 이 기능의 핵심 용도인데 몇 주치 새 데이터가
    쌓일 때까지 빈 화면이 된다. 정본 규약이 최상위 단일 키라는 점은 변함없고, 이
    OR 절은 과거 데이터를 위한 읽기 전용 호환이다(쓰기 경로는 최상위만 쓴다).
    라이브 검증: 결정 2324 추적에서 `risk.record_breach` 가 `recorded` 로 잡힌다.

    Args:
        decision_id: persona_decisions.id.

    Returns:
        None: 해당 id 의 결정이 없음(엔드포인트가 404 로 변환).
        dict: {"decision", "nodes", "orders", "unmatched_events"}.
    """
    with ro_connection() as conn, conn.cursor() as cur:
        cur.execute(_DECISION_BY_ID_SQL, (decision_id,))
        row = cur.fetchone()
        if row is None:
            return None
        decision = dict(row)

        cur.execute(
            _TRACE_EVENTS_SQL,
            # 최상위 / 과거 중첩(context) / 배치 4종 — 순서는 SQL 술어 순서와 일치.
            # 2026-08-15: dropped[] 추가 — PORTFOLIO_GATE_DROP(섹터캡/현금바닥)이
            # 이 키를 쓴다. 빠뜨리면 게이트 드롭이 추적에서 조용히 사라진다.
            (
                str(decision_id),
                str(decision_id),
                decision_id,
                decision_id,
                decision_id,
                decision_id,
                decision_id,
            ),
        )
        event_rows = [dict(r) for r in cur.fetchall()]

        cur.execute(_TRACE_ORDERS_SQL, (decision_id,))
        order_rows = [dict(r) for r in cur.fetchall()]

    ticker = decision.get("ticker") or ""
    db_names = lookup_names_from_db([ticker]) if ticker else {}
    decision["ticker_name"] = resolve_ticker_name(ticker, db_names=db_names)

    audit_map, _all_funcs = _audit_bridge()
    known_event_types = {et for ets in audit_map.values() for et in ets}
    unmatched_events = [
        _serialize_trace_event(e)
        for e in event_rows
        if e["event_type"] not in known_event_types
    ]

    return {
        "decision": decision,
        "nodes": _build_trace_nodes(event_rows, audit_map),
        "orders": [_serialize_trace_order(r) for r in order_rows],
        "unmatched_events": unmatched_events,
    }
