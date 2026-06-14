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

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from trading.dashboard import queries

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
        return queries.fetch_system_status()
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
def get_scorecard() -> dict[str, Any]:
    """엣지 검증 스코어카드 (verdict, grade, alpha, CAGR, MDD, Sharpe)."""
    try:
        return queries.fetch_scorecard()
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
def get_postmortem(days: int = 30, limit: int = 200) -> dict[str, Any]:
    """결정 postmortem 분포 (4분류: TP/FP/REGIME_MISMATCH/MISSED + 페르소나 귀인).

    REQ-050-6/7: 어댑터 → edge.postmortem.classify_decision_outcome → 지연계산 + TTL 캐시.
    """
    limit = min(limit, 500)
    try:
        return queries.fetch_postmortem(days=days, limit=limit)
    except Exception as exc:
        LOG.error("fetch_postmortem failed: %s", exc)
        raise HTTPException(status_code=503, detail="postmortem 계산 실패") from exc


@app.get("/api/confidence-analysis", tags=["analytics"])
def get_confidence_analysis(days: int = 30) -> dict[str, Any]:
    """Confidence 엣지 분석 (버킷별 성적 + Pearson/Spearman 상관).

    REQ-050-6a/7: 어댑터 → edge.roundtrips.build_roundtrips → edge.confidence.analyze.
    """
    try:
        return queries.fetch_confidence_analysis(days=days)
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
