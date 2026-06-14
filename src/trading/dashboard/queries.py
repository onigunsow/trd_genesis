"""SPEC-TRADING-047 M1: 대시보드 읽기 전용 쿼리 함수.

모든 함수는 ro_connection 을 통해 dashboard_ro 역할로만 접근한다.
쓰기 작업 없음.
"""

from __future__ import annotations

import logging
from typing import Any

from trading.dashboard.db import ro_connection

LOG = logging.getLogger(__name__)

# 응답에서 제외할 민감 컬럼 (KIS 요청/응답 페이로드)
_SENSITIVE_FIELDS = frozenset({"request", "response", "kis_order_no"})


def fetch_system_status() -> dict[str, Any]:
    """system_state 싱글톤 행 반환 (halt_state, regime 등).

    Returns:
        dict with halt_state, trading_mode, current_regime, current_risk_appetite,
        late_cycle_defense_active, updated_at.

    Raises:
        RuntimeError: system_state 행이 없을 때.
    """
    # @MX:ANCHOR: [AUTO] SPEC-047 M1 — 시스템 상태 읽기 진입점.
    # @MX:REASON: halt_state/regime 를 대시보드가 직접 읽는 단일 경로.
    with ro_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT halt_state, trading_mode, current_regime, current_risk_appetite,
                   late_cycle_defense_active, updated_at
            FROM system_state
            WHERE id = 1
            """
        )
        row = cur.fetchone()

    if not row:
        raise RuntimeError("system_state 행 없음 — migration 001 미적용?")

    return dict(row)


def fetch_recent_decisions(*, limit: int = 50) -> list[dict[str, Any]]:
    """persona_decisions + persona_runs 조인 — 최신 N 건.

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
            pd.rationale
        FROM persona_decisions pd
        JOIN persona_runs pr ON pr.id = pd.persona_run_id
        ORDER BY pd.ts DESC
        LIMIT %s
    """
    with ro_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (limit,))
        rows = cur.fetchall()

    return [dict(r) for r in rows]


def fetch_recent_orders(*, limit: int = 50) -> list[dict[str, Any]]:
    """orders 테이블 최신 N 건 — 민감 컬럼 제외.

    request / response JSONB 는 KIS API 자격증명이 담길 수 있어 제외한다.
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


def fetch_equity_curve(*, days: int | None = 90) -> list[dict[str, Any]]:
    """daily_equity_snapshot — 날짜 오름차순.

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

    return [dict(r) for r in rows]


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
# SPEC-TRADING-048 M3 REQ-048-M3-6: postmortem/calibration 읽기전용 쿼리
# ---------------------------------------------------------------------------


def fetch_postmortem_distribution(*, limit: int = 200) -> list[dict[str, Any]]:
    """결정 분류 분포 읽기전용 쿼리 (dashboard_ro 역할).

    persona_decisions + 분류 라벨(임시 컬럼 또는 뷰 — 마이그레이션 033 적용 후)
    을 읽어 TRUE_POSITIVE/FALSE_POSITIVE/REGIME_MISMATCH/MISSED 분포를 반환한다.

    쓰기 작업 없음(AC-M3-5).
    """
    sql = """
        SELECT
            pd.id,
            pd.ts,
            pr.persona_name,
            pd.cycle_kind,
            pd.confidence,
            pd.prob_bull,
            pd.prob_base,
            pd.prob_bear
        FROM persona_decisions pd
        LEFT JOIN persona_runs pr ON pr.id = pd.run_id
        ORDER BY pd.ts DESC
        LIMIT %s
    """
    with ro_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (limit,))
        rows = cur.fetchall()

    return [dict(r) for r in rows]


def fetch_calibration_scores(*, limit: int = 200) -> list[dict[str, Any]]:
    """calibration 점수 원재료 읽기전용 쿼리 (dashboard_ro 역할).

    prob_bull/base/bear 가 채워진 결정 행만 반환 (NULL 행 제외).
    Brier 점수 계산은 호출자가 처리한다.

    쓰기 작업 없음(AC-M3-5).
    """
    sql = """
        SELECT
            pd.id,
            pd.ts,
            pr.persona_name,
            pd.confidence,
            pd.prob_bull,
            pd.prob_base,
            pd.prob_bear
        FROM persona_decisions pd
        LEFT JOIN persona_runs pr ON pr.id = pd.run_id
        WHERE pd.prob_bull IS NOT NULL
          AND pd.prob_base IS NOT NULL
          AND pd.prob_bear IS NOT NULL
        ORDER BY pd.ts DESC
        LIMIT %s
    """
    with ro_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (limit,))
        rows = cur.fetchall()

    return [dict(r) for r in rows]
