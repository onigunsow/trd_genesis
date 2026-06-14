"""SPEC-TRADING-047 M1+M2: FastAPI 읽기 전용 대시보드 API + 정적 페이지.

보안 규칙:
- 쓰기 엔드포인트 없음 (GET only).
- 민감 정보(자격증명, KIS 페이로드) 응답 제외.
- halt/resume 제어 없음 — CLI/텔레그램 전용.
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
    description="SPEC-TRADING-047: 읽기 전용 모니터링 대시보드",
    version="1.0.0",
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
    """시스템 상태: halt_state, trading_mode, regime, risk_appetite."""
    try:
        return queries.fetch_system_status()
    except Exception as exc:
        LOG.error("fetch_system_status failed: %s", exc)
        raise HTTPException(status_code=503, detail="DB 조회 실패") from exc


@app.get("/api/decisions", tags=["decisions"])
def get_decisions(limit: int = 50) -> list[dict[str, Any]]:
    """페르소나 결정 피드 (persona_decisions + persona_runs 조인)."""
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
    """일별 자산 스냅샷 (equity curve)."""
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
