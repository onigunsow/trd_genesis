"""SPEC-TRADING-047 M1: FastAPI endpoint tests.

RED phase — tests written before implementation.
All DB calls are mocked; no live Postgres required.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """TestClient for the dashboard FastAPI app."""
    from fastapi.testclient import TestClient

    from trading.dashboard.app import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_returns_200_and_ok(self, client) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# GET /api/status
# ---------------------------------------------------------------------------

class TestStatusEndpoint:
    def test_returns_system_state_fields(self, client) -> None:
        state = {
            "halt_state": False,
            "trading_mode": "paper",
            "current_regime": "bull",
            "current_risk_appetite": "risk-on",
            "late_cycle_defense_active": False,
            "updated_at": datetime(2026, 6, 14, 9, 0, tzinfo=UTC),
        }
        with patch("trading.dashboard.queries.fetch_system_status", return_value=state):
            resp = client.get("/api/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["halt_state"] is False
        assert data["trading_mode"] == "paper"
        assert data["current_regime"] == "bull"

    def test_no_db_credentials_in_response(self, client) -> None:
        """Response must not expose any password/secret fields."""
        state = {
            "halt_state": False,
            "trading_mode": "paper",
            "current_regime": "neutral",
            "current_risk_appetite": "neutral",
            "late_cycle_defense_active": False,
            "updated_at": datetime(2026, 6, 14, tzinfo=UTC),
        }
        with patch("trading.dashboard.queries.fetch_system_status", return_value=state):
            resp = client.get("/api/status")

        body = resp.text.lower()
        for keyword in ("password", "secret", "api_key", "token", "private"):
            assert keyword not in body, f"secret keyword '{keyword}' found in /api/status response"

    def test_db_error_returns_503(self, client) -> None:
        with patch(
            "trading.dashboard.queries.fetch_system_status",
            side_effect=RuntimeError("DB down"),
        ):
            resp = client.get("/api/status")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/decisions
# ---------------------------------------------------------------------------

class TestDecisionsEndpoint:
    def test_returns_list(self, client) -> None:
        decisions = [
            {
                "id": 1,
                "ts": datetime(2026, 6, 14, 9, 30, tzinfo=UTC),
                "persona_name": "decision",
                "cycle_kind": "intraday",
                "ticker": "005930",
                "side": "buy",
                "qty": 10,
                "confidence": 0.82,
                "rationale": "강한 모멘텀",
            }
        ]
        with patch("trading.dashboard.queries.fetch_recent_decisions", return_value=decisions):
            resp = client.get("/api/decisions")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert data[0]["ticker"] == "005930"

    def test_limit_param_forwarded(self, client) -> None:
        with patch(
            "trading.dashboard.queries.fetch_recent_decisions", return_value=[]
        ) as mock_fn:
            client.get("/api/decisions?limit=5")
            mock_fn.assert_called_once_with(limit=5)

    def test_limit_capped_at_200(self, client) -> None:
        """limit cannot exceed 200 to avoid large payload."""
        with patch(
            "trading.dashboard.queries.fetch_recent_decisions", return_value=[]
        ) as mock_fn:
            client.get("/api/decisions?limit=9999")
            called_limit = mock_fn.call_args[1]["limit"]
            assert called_limit <= 200


# ---------------------------------------------------------------------------
# GET /api/orders
# ---------------------------------------------------------------------------

class TestOrdersEndpoint:
    def test_returns_orders_list(self, client) -> None:
        orders = [
            {
                "id": 99,
                "ts": datetime(2026, 6, 14, 9, 31, tzinfo=UTC),
                "side": "buy",
                "ticker": "005930",
                "qty": 10,
                "order_type": "market",
                "status": "filled",
                "fill_price": 75000,
                "mode": "paper",
            }
        ]
        with patch("trading.dashboard.queries.fetch_recent_orders", return_value=orders):
            resp = client.get("/api/orders")

        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["status"] == "filled"

    def test_no_raw_request_response_in_output(self, client) -> None:
        """Sensitive KIS request/response blobs must not appear."""
        orders = [{"id": 1, "ticker": "000660", "side": "buy", "qty": 5,
                   "status": "submitted", "order_type": "market",
                   "fill_price": None, "mode": "paper",
                   "ts": datetime(2026, 6, 14, tzinfo=UTC)}]
        with patch("trading.dashboard.queries.fetch_recent_orders", return_value=orders):
            resp = client.get("/api/orders")

        body = resp.json()
        for item in body:
            assert "request" not in item
            assert "response" not in item


# ---------------------------------------------------------------------------
# GET /api/holdings
# ---------------------------------------------------------------------------

class TestHoldingsEndpoint:
    def test_returns_holdings(self, client) -> None:
        holdings = [{"ticker": "005930", "qty_net": 30, "avg_fill_price": 74000,
                     "total_cost": 2220000}]
        with patch("trading.dashboard.queries.fetch_holdings", return_value=holdings):
            resp = client.get("/api/holdings")

        assert resp.status_code == 200
        assert resp.json()[0]["ticker"] == "005930"

    def test_empty_holdings_returns_empty_list(self, client) -> None:
        with patch("trading.dashboard.queries.fetch_holdings", return_value=[]):
            resp = client.get("/api/holdings")

        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /api/scorecard
# ---------------------------------------------------------------------------

class TestScorecardEndpoint:
    def test_returns_verdict_and_grade(self, client) -> None:
        scorecard_data = {
            "verdict": "WEAK-GO",
            "grade": "WEAK",
            "n_closed": 15,
            "alpha_pct": 2.3,
            "benchmark_available": True,
            "reasons": ["보정 후 기대값 +3000원/거래, 손익비 1.20"],
        }
        with patch(
            "trading.dashboard.queries.fetch_scorecard", return_value=scorecard_data
        ):
            resp = client.get("/api/scorecard")

        assert resp.status_code == 200
        data = resp.json()
        assert data["verdict"] == "WEAK-GO"
        assert data["grade"] == "WEAK"
        assert "alpha_pct" in data

    def test_scorecard_error_returns_503(self, client) -> None:
        with patch(
            "trading.dashboard.queries.fetch_scorecard",
            side_effect=Exception("edge module failed"),
        ):
            resp = client.get("/api/scorecard")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/equity
# ---------------------------------------------------------------------------

class TestEquityEndpoint:
    def test_returns_curve_data(self, client) -> None:
        curve = [
            {"trading_day": date(2026, 6, 10), "total_assets": 10_000_000},
            {"trading_day": date(2026, 6, 11), "total_assets": 10_050_000},
        ]
        with patch("trading.dashboard.queries.fetch_equity_curve", return_value=curve):
            resp = client.get("/api/equity")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["total_assets"] == 10_000_000

    def test_days_param_forwarded(self, client) -> None:
        with patch(
            "trading.dashboard.queries.fetch_equity_curve", return_value=[]
        ) as mock_fn:
            client.get("/api/equity?days=60")
            mock_fn.assert_called_once_with(days=60)


# ---------------------------------------------------------------------------
# Security: no write endpoints
# ---------------------------------------------------------------------------

class TestNoWriteEndpoints:
    """Dashboard is strictly read-only — no mutating HTTP methods."""

    def test_post_status_not_allowed(self, client) -> None:
        resp = client.post("/api/status", json={})
        assert resp.status_code in (404, 405)

    def test_post_orders_not_allowed(self, client) -> None:
        resp = client.post("/api/orders", json={})
        assert resp.status_code in (404, 405)

    def test_delete_not_allowed(self, client) -> None:
        resp = client.delete("/api/holdings")
        assert resp.status_code in (404, 405)
