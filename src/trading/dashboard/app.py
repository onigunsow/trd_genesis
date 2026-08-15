"""SPEC-TRADING-047 M1+M2 / SPEC-TRADING-050 M1: FastAPI 읽기 전용 대시보드 API + 정적 페이지.

보안 규칙:
- 쓰기 엔드포인트 없음 (GET only). REQ-050-1.
- 민감 정보(자격증명, KIS 페이로드) 응답 제외. REQ-050-8.
- halt/resume 제어 없음 — CLI/텔레그램 전용.

SPEC-TRADING-050 M1 추가 엔드포인트:
  GET /api/news, /api/story-clusters, /api/trends,
  /api/postmortem, /api/confidence-analysis, /api/pipeline.

SPEC-TRADING-050 M1 확장 엔드포인트:
  GET /api/decisions (+risk_reviews LEFT JOIN),
  GET /api/status (+halt 사유/cool_down/late_cycle),
  GET /api/equity (+drawdown 시리즈).
"""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from trading.dashboard import gate_queries, queries

LOG = logging.getLogger(__name__)

app = FastAPI(
    title="Trading Dashboard API",
    description="SPEC-TRADING-047/050: 읽기 전용 모니터링 대시보드",
    version="2.0.0",
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json",
)

_STATIC_DIR = Path(__file__).parent / "static"


# ---------------------------------------------------------------------------
# Static UI (M2)
# ---------------------------------------------------------------------------

if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# SPEC-065 REQ-065-2a: 4개 집계 라우트가 공유하는 since 쿼리 파라미터.
_SINCE_Q = Query(default=None, description="SPEC-065: 이 날(ISO) 이후 진입한 왕복만")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """대시보드 정적 HTML 페이지."""
    html_path = _STATIC_DIR / "index.html"
    if html_path.exists():
        return FileResponse(str(html_path))
    raise HTTPException(status_code=404, detail="index.html not found")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """서비스 생존 확인."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# API endpoints (read-only)
# ---------------------------------------------------------------------------

@app.get("/api/status", tags=["status"])
def get_status() -> dict[str, Any]:
    """시스템 상태: halt_state, trading_mode, regime, risk_appetite.

    SPEC-050 REQ-050-4 확장: halt 사유(CIRCUIT_BREAKER_TRIP) + cool_down_active +
    late_cycle_defense_active / late_cycle_level 포함.
    """
    try:
        status = queries.fetch_system_status()
        # SPEC-065 REQ-065-2b: 게이트 설정(env)을 status 에 실어 프런트가 "수정 이후만"
        # 토글의 활성 여부·기준일 라벨을 폴링 1회로 안다. since=null 이면 토글 비활성.
        status["gate"] = queries.gate_config()
        return status
    except Exception as exc:
        LOG.error("fetch_system_status failed: %s", exc)
        raise HTTPException(status_code=503, detail="DB 조회 실패") from exc


@app.get("/api/decisions", tags=["decisions"])
def get_decisions(limit: int = 50) -> list[dict[str, Any]]:
    """페르소나 결정 피드 (persona_decisions + persona_runs + risk_reviews LEFT JOIN).

    SPEC-050 REQ-050-3 확장: risk_verdict / risk_rationale 필드 포함.
    """
    limit = min(limit, 200)
    try:
        return queries.fetch_recent_decisions(limit=limit)
    except Exception as exc:
        LOG.error("fetch_recent_decisions failed: %s", exc)
        raise HTTPException(status_code=503, detail="DB 조회 실패") from exc


@app.get("/api/decisions/{decision_id}/trace", tags=["decisions"])
def get_decision_trace(decision_id: int) -> dict[str, Any]:
    """SPEC-TRADING-064 그룹 C: 결정 하나의 추적 페이로드(REQ-064-C1).

    결정 본문 + 관여 audit_log 이벤트 → codemap ast 브릿지 역인덱스로 계산한
    노드별 4-상태 강조(REQ-064-C2/C3) + 연결 주문(정확 매칭만) + 매핑 안 된
    이벤트(침묵 금지). 읽기 전용, 비용 0(REQ-064-C11).
    """
    try:
        trace = queries.fetch_decision_trace(decision_id)
    except Exception as exc:
        LOG.error("fetch_decision_trace failed: %s", exc)
        raise HTTPException(status_code=503, detail="DB 조회 실패") from exc
    if trace is None:
        raise HTTPException(status_code=404, detail=f"decision {decision_id} 없음")
    return trace


@app.get("/api/orders", tags=["orders"])
def get_orders(limit: int = 50) -> list[dict[str, Any]]:
    """최근 주문 목록 (민감 컬럼 제외)."""
    limit = min(limit, 200)
    try:
        return queries.fetch_recent_orders(limit=limit)
    except Exception as exc:
        LOG.error("fetch_recent_orders failed: %s", exc)
        raise HTTPException(status_code=503, detail="DB 조회 실패") from exc


@app.get("/api/holdings", tags=["holdings"])
def get_holdings() -> list[dict[str, Any]]:
    """현재 순보유 포지션 (ticker별 qty_net > 0)."""
    try:
        return queries.fetch_holdings()
    except Exception as exc:
        LOG.error("fetch_holdings failed: %s", exc)
        raise HTTPException(status_code=503, detail="DB 조회 실패") from exc


@app.get("/api/equity", tags=["equity"])
def get_equity(days: int = 90) -> list[dict[str, Any]]:
    """일별 자산 스냅샷 (equity curve) + drawdown 시리즈.

    SPEC-050 REQ-050-5 확장: drawdown_pct 필드 추가.
    """
    days_arg: int | None = days if days > 0 else None
    try:
        return queries.fetch_equity_curve(days=days_arg)
    except Exception as exc:
        LOG.error("fetch_equity_curve failed: %s", exc)
        raise HTTPException(status_code=503, detail="DB 조회 실패") from exc


@app.get("/api/scorecard", tags=["scorecard"])
def get_scorecard(since: str | None = _SINCE_Q) -> dict[str, Any]:
    """엣지 검증 스코어카드 (verdict, grade, alpha, CAGR, MDD, Sharpe, sortino).

    REQ-054-A4: sortino 필드 추가 (edge.analytics 에서 이미 계산된 값 노출만).
    SPEC-065 REQ-065-2a: since 는 진입일 기준 필터. low_sample 로 표본 부족 신호.
    """
    try:
        return queries.fetch_scorecard_with_sortino(since=since)
    except Exception as exc:
        LOG.error("fetch_scorecard failed: %s", exc)
        raise HTTPException(status_code=503, detail="스코어카드 계산 실패") from exc


# ---------------------------------------------------------------------------
# SPEC-TRADING-050 M1 신규 엔드포인트 (REQ-050-2)
# ---------------------------------------------------------------------------

@app.get("/api/news", tags=["news"])
def get_news(days: int = 7, limit: int = 50) -> list[dict[str, Any]]:
    """뉴스 기사 + 분석 결과 (impact_score / sentiment / keywords / summary_2line).

    REQ-050-2: news_articles + news_analysis JOIN.
    """
    limit = min(limit, 200)
    try:
        return queries.fetch_recent_news(days=days, limit=limit)
    except Exception as exc:
        LOG.error("fetch_recent_news failed: %s", exc)
        raise HTTPException(status_code=503, detail="DB 조회 실패") from exc


@app.get("/api/story-clusters", tags=["news"])
def get_story_clusters(days: int = 7, limit: int = 50) -> list[dict[str, Any]]:
    """스토리 클러스터 (portfolio_relevant 우선, relevance_tickers 포함).

    REQ-050-2/AC-M1-1: representative_title / sector / sentiment_dominant /
    portfolio_relevant / relevance_tickers 포함.
    """
    limit = min(limit, 200)
    try:
        return queries.fetch_story_clusters(days=days, limit=limit)
    except Exception as exc:
        LOG.error("fetch_story_clusters failed: %s", exc)
        raise HTTPException(status_code=503, detail="DB 조회 실패") from exc


@app.get("/api/trends", tags=["news"])
def get_trends(trend_type: str = "daily", days: int = 14) -> list[dict[str, Any]]:
    """키워드 트렌드 (mention_count / 감성 분포).

    REQ-050-2/AC-M5-3: news_trends.
    """
    if trend_type not in ("daily", "weekly"):
        raise HTTPException(status_code=422, detail="trend_type 은 'daily' 또는 'weekly'")
    try:
        return queries.fetch_trends(trend_type=trend_type, days=days)
    except Exception as exc:
        LOG.error("fetch_trends failed: %s", exc)
        raise HTTPException(status_code=503, detail="DB 조회 실패") from exc


@app.get("/api/postmortem", tags=["analytics"])
def get_postmortem(
    days: int = 30, limit: int = 200,
    since: str | None = _SINCE_Q,
) -> dict[str, Any]:
    """결정 postmortem 분포 (4분류: TP/FP/REGIME_MISMATCH/MISSED + 페르소나 귀인).

    REQ-050-6/7: 어댑터 → edge.postmortem.classify_decision_outcome → 지연계산 + TTL 캐시.
    """
    limit = min(limit, 500)
    try:
        return queries.fetch_postmortem(days=days, limit=limit, since=since)
    except Exception as exc:
        LOG.error("fetch_postmortem failed: %s", exc)
        raise HTTPException(status_code=503, detail="postmortem 계산 실패") from exc


@app.get("/api/confidence-analysis", tags=["analytics"])
def get_confidence_analysis(
    days: int = 30, since: str | None = _SINCE_Q,
) -> dict[str, Any]:
    """Confidence 엣지 분석 (버킷별 성적 + Pearson/Spearman 상관).

    REQ-050-6a/7: 어댑터 → edge.roundtrips.build_roundtrips → edge.confidence.analyze.
    """
    try:
        return queries.fetch_confidence_analysis(days=days, since=since)
    except Exception as exc:
        LOG.error("fetch_confidence_analysis failed: %s", exc)
        raise HTTPException(status_code=503, detail="confidence 분석 실패") from exc


@app.get("/api/pipeline", tags=["pipeline"])
def get_pipeline() -> dict[str, Any]:
    """최신 사이클 파이프라인 재구성 (macro→micro→decision→risk→portfolio).

    REQ-050-2: persona_runs 를 최신 사이클 기준으로 재구성.
    """
    try:
        return queries.fetch_pipeline()
    except Exception as exc:
        LOG.error("fetch_pipeline failed: %s", exc)
        raise HTTPException(status_code=503, detail="pipeline 조회 실패") from exc


# ---------------------------------------------------------------------------
# SPEC-TRADING-054 M1: 신규 엔드포인트
# ---------------------------------------------------------------------------

# @MX:NOTE: [AUTO] 아래 엔드포인트는 edge 단일원천 읽기 전용이다.
# 손익/KPI 수식을 재구현하지 않고 edge 모듈 / position_eval_snapshot 만 읽는다.
# 모든 DB 접근은 ro_connection 경유 (REQ-054-A7).
# @MX:SPEC: SPEC-TRADING-054 M1


# ── SPEC-TRADING-065 그룹 3: 검증 게이트 뷰 ─────────────────────────────────
@app.get("/api/gate/holding-period", tags=["gate"])
def get_gate_holding_period(since: str | None = _SINCE_Q) -> dict[str, Any]:
    """REQ-065-3a 보유기간별 손익(FIFO 왕복, since=진입일)."""
    try:
        return gate_queries.fetch_holding_period_pnl(since=since)
    except Exception as exc:
        LOG.error("fetch_holding_period_pnl failed: %s", exc)
        raise HTTPException(status_code=503, detail="보유기간 집계 실패") from exc


@app.get("/api/gate/entry-quality", tags=["gate"])
def get_gate_entry_quality(since: str | None = _SINCE_Q) -> dict[str, Any]:
    """REQ-065-3b 진입 품질 매트릭스 — confidence x entry_freshness, 결정 전체 반사실."""
    try:
        return gate_queries.fetch_entry_quality_matrix(since=since)
    except Exception as exc:
        LOG.error("fetch_entry_quality_matrix failed: %s", exc)
        raise HTTPException(status_code=503, detail="진입 품질 집계 실패") from exc


@app.get("/api/gate/risk", tags=["gate"])
def get_gate_risk(since: str | None = _SINCE_Q) -> dict[str, Any]:
    """REQ-065-3c 리스크 판정 분포 + HOLD 사유 + HOLD 반사실 + code_rules_passed."""
    try:
        return gate_queries.fetch_risk_verdicts(since=since)
    except Exception as exc:
        LOG.error("fetch_risk_verdicts failed: %s", exc)
        raise HTTPException(status_code=503, detail="리스크 판정 집계 실패") from exc


@app.get("/api/gate/sizing", tags=["gate"])
def get_gate_sizing(
    since: str | None = _SINCE_Q,
    top_n: int = Query(default=gate_queries.TOP_N_DEFAULT, le=200),
) -> dict[str, Any]:
    """REQ-065-3d 매수 축소·차단 게이트별 건수·삭감률 + 최근 사유 표."""
    try:
        return gate_queries.fetch_sizing_gates(since=since, top_n=top_n)
    except Exception as exc:
        LOG.error("fetch_sizing_gates failed: %s", exc)
        raise HTTPException(status_code=503, detail="사이징 게이트 집계 실패") from exc


@app.get("/api/roundtrips", tags=["analytics"])
def get_roundtrips(
    days: int | None = Query(default=None, description="최근 N일 필터"),
    limit: int = Query(default=500, le=2000, description="최대 반환 행 수"),
    since: str | None = _SINCE_Q,
) -> list[dict[str, Any]]:
    """라운드트립 거래 원장.

    REQ-054-A1: edge.roundtrips.compute_roundtrips() 단일원천.

    응답 필드:
        ticker, entry_date, exit_date, qty, entry_price, exit_price,
        net_pnl, return_pct, entry_fee, exit_fee, fees, holding_days,
        confidence, verdict, persona, is_win
    """
    try:
        return queries.fetch_roundtrips(days=days, limit=limit, since=since)
    except Exception as exc:
        LOG.error("fetch_roundtrips failed: %s", exc)
        raise HTTPException(status_code=503, detail="라운드트립 조회 실패") from exc


@app.get("/api/portfolio", tags=["portfolio"])
def get_portfolio() -> dict[str, Any]:
    """포트폴리오 구성·집중도·섹터 분해.

    REQ-054-A2, REQ-054-G1: position_eval_snapshot(최신) + ticker_metadata 조인.
    대시보드 읽기전용 — ro_connection 경유 (REQ-054-A7).

    응답 필드:
        holdings[]: {ticker, qty, avg_cost, eval_price, eval_amount,
                     unrealized_pnl, pnl_pct, weight_pct, sector}
        nav, cash_amount, cash_ratio, herfindahl, top3_pct,
        sector_breakdown[]: {sector, weight_pct},
        snapshot_date
    """
    try:
        return queries.fetch_portfolio()
    except Exception as exc:
        LOG.error("fetch_portfolio failed: %s", exc)
        raise HTTPException(status_code=503, detail="포트폴리오 조회 실패") from exc


@app.get("/api/pnl-daily", tags=["analytics"])
def get_pnl_daily(
    days: int | None = Query(default=None, description="최근 N일 필터"),
    period: str = Query(default="daily", pattern="^(daily|weekly|monthly)$"),
    start_date: str | None = Query(default=None, description="시작일 (ISO date)"),
    end_date: str | None = Query(default=None, description="종료일 (ISO date)"),
) -> dict[str, Any]:
    """기간별 실현손익 + 누적 + KOSPI 알파.

    REQ-054-A3, REQ-054-A8: period ∈ {daily, weekly, monthly}.
    KOSPI 미가용 시 alpha_pct=null, benchmark_available=false (REQ-054-A8).

    응답 필드:
        period, benchmark_available,
        rows[]: {period_label, realized_pnl, cumulative_pnl, alpha_pct}
    """
    if period not in ("daily", "weekly", "monthly"):
        raise HTTPException(status_code=422, detail="period 는 daily|weekly|monthly")
    try:
        return queries.fetch_pnl_daily(
            days=days,
            period=period,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as exc:
        LOG.error("fetch_pnl_daily failed: %s", exc)
        raise HTTPException(status_code=503, detail="PnL 조회 실패") from exc


_CSV_DATASETS = frozenset({"roundtrips", "portfolio", "pnl-daily"})

# REQ-054-A5: dataset 경로 파라미터로 {dataset}.csv 형태 지원
# Content-Disposition: attachment 로 파일 다운로드 유도.
# 행 소스는 fetch_* 재호출 — 별도 계산 경로 없음(ADR-003).
@app.get("/api/export/{dataset}", tags=["export"])
def get_export(dataset: str) -> StreamingResponse:
    """CSV 내보내기.

    REQ-054-A5: dataset ∈ {roundtrips, portfolio, pnl-daily}.
    Content-Type: text/csv, Content-Disposition: attachment.
    행 값 = 동일 fetch_* 함수 재호출(단일원천, REQ-054-A6).

    응답: text/csv 스트림
    """
    # .csv 확장자 포함 허용: "roundtrips.csv" → "roundtrips"
    clean = dataset.removesuffix(".csv")
    if clean not in _CSV_DATASETS:
        raise HTTPException(
            status_code=404,
            detail=f"dataset '{dataset}' 미지원. {sorted(_CSV_DATASETS)} 중 선택.",
        )

    try:
        if clean == "roundtrips":
            data = queries.fetch_roundtrips()
            if not data:
                fieldnames = [
                    "ticker", "entry_date", "exit_date", "qty",
                    "entry_price", "exit_price", "net_pnl", "return_pct",
                    "entry_fee", "exit_fee", "fees", "holding_days",
                    "confidence", "verdict", "persona", "is_win",
                ]
            else:
                fieldnames = list(data[0].keys())
            rows_iter = data

        elif clean == "portfolio":
            portfolio = queries.fetch_portfolio()
            rows_iter = portfolio.get("holdings", [])
            if not rows_iter:
                fieldnames = [
                    "ticker", "qty", "avg_cost", "eval_price", "eval_amount",
                    "unrealized_pnl", "pnl_pct", "weight_pct", "sector",
                ]
            else:
                fieldnames = list(rows_iter[0].keys())

        else:  # pnl-daily
            pnl = queries.fetch_pnl_daily()
            rows_iter = pnl.get("rows", [])
            if not rows_iter:
                fieldnames = [
                    "period_label", "realized_pnl", "cumulative_pnl", "alpha_pct",
                ]
            else:
                fieldnames = list(rows_iter[0].keys())

    except Exception as exc:
        LOG.error("get_export(%s) 데이터 조회 실패: %s", dataset, exc)
        raise HTTPException(status_code=503, detail="CSV 내보내기 실패") from exc

    def _generate():
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows_iter:
            writer.writerow(row)
        yield buf.getvalue()

    filename = f"{clean}.csv"
    return StreamingResponse(
        _generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
