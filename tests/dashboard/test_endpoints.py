"""SPEC-TRADING-047 M1: FastAPI endpoint tests.

RED phase — tests written before implementation.
All DB calls are mocked; no live Postgres required.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import ClassVar
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
# GET /api/decisions/{decision_id}/trace (SPEC-TRADING-064 그룹 C)
# ---------------------------------------------------------------------------

_TRACE_STATE_DOMAIN = {"recorded", "decision_agnostic", "not_involved", "rule_based"}


def _sample_trace() -> dict:
    return {
        "decision": {
            "id": 2814,
            "ts": datetime(2026, 8, 7, 15, 10, tzinfo=UTC),
            "persona_name": "decision",
            "cycle_kind": "intraday",
            "ticker": "005930",
            "side": "buy",
            "qty": 10,
            "confidence": 0.82,
            "rationale": "모멘텀 확인",
            "risk_verdict": "APPROVE",
            "risk_rationale": None,
            "regime_at_decision": "bull",
            "trigger_context": "RSI 과매도",
            "response_json": '{"signals": []}',
            "ticker_name": "삼성전자",
        },
        "nodes": [
            {
                "file": "src/trading/risk/limits.py",
                "function": "record_breach",
                "module": "risk",
                "state": "recorded",
                "events": [
                    {
                        "event_type": "LIMIT_BREACH",
                        "ts": datetime(2026, 8, 7, 15, 10, tzinfo=UTC),
                        "actor": "risk",
                        "details": {"decision_id": 2814, "context": {"limit": "daily_loss"}},
                    }
                ],
            },
            {
                "file": "src/trading/kis/broker_truth.py",
                "function": "intraday_reconcile",
                "module": "kis",
                "state": "decision_agnostic",
                "events": [],
            },
            {
                "file": "src/trading/scripts/paper_buy_one.py",
                "function": "main",
                "module": "scripts",
                "state": "not_involved",
                "events": [],
            },
            {
                "file": "src/trading/watchers/position_watchdog.py",
                "function": "_execute_trim",
                "module": "watchers",
                "state": "rule_based",
                "events": [],
            },
        ],
        "orders": [
            {
                "id": 1,
                "ts": datetime(2026, 8, 7, 15, 11, tzinfo=UTC),
                "side": "buy",
                "ticker": "005930",
                "qty": 10,
                "status": "filled",
                "rejected_reason": None,
                "fill_price": 70000,
                "fill_qty": 10,
                "synthetic": False,
                "correction": False,
                "origin": "decision",
            }
        ],
        "unmatched_events": [],
    }


class TestDecisionTraceEndpoint:
    """REQ-064-C1/C2/C9/C11: 응답 키 집합 + nodes[].state 도메인 검증."""

    def test_returns_200_and_expected_key_set(self, client) -> None:
        with patch(
            "trading.dashboard.queries.fetch_decision_trace", return_value=_sample_trace()
        ):
            resp = client.get("/api/decisions/2814/trace")

        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"decision", "nodes", "orders", "unmatched_events"}
        assert data["decision"]["id"] == 2814

    def test_node_state_is_exactly_four_literals(self, client) -> None:
        """REQ-064-C2 [HARD]: state 는 정확히 이 네 값 중 하나여야 한다."""
        with patch(
            "trading.dashboard.queries.fetch_decision_trace", return_value=_sample_trace()
        ):
            resp = client.get("/api/decisions/2814/trace")

        nodes = resp.json()["nodes"]
        assert len(nodes) >= 4
        seen_states = {n["state"] for n in nodes}
        assert seen_states <= _TRACE_STATE_DOMAIN
        # 샘플이 네 상태를 전부 커버해 도메인이 정확히 이 네 값임을 함께 증명한다.
        assert seen_states == _TRACE_STATE_DOMAIN

    def test_unknown_decision_returns_404(self, client) -> None:
        """REQ-064-C1: 없는 id → 404, 빈 200 이 아니다(빈 200 은 '아무 관여 없음'과
        구별 불가하다)."""
        with patch("trading.dashboard.queries.fetch_decision_trace", return_value=None):
            resp = client.get("/api/decisions/999999/trace")

        assert resp.status_code == 404

    def test_db_error_returns_503(self, client) -> None:
        with patch(
            "trading.dashboard.queries.fetch_decision_trace",
            side_effect=RuntimeError("DB down"),
        ):
            resp = client.get("/api/decisions/2814/trace")

        assert resp.status_code == 503

    def test_details_is_object_not_string(self, client) -> None:
        """계약 회귀 방지: Group A 는 details 를 문자열로 내보내 React #31 을
        일으켰다. nodes[].events[].details 는 항상 객체여야 한다."""
        with patch(
            "trading.dashboard.queries.fetch_decision_trace", return_value=_sample_trace()
        ):
            resp = client.get("/api/decisions/2814/trace")

        recorded = next(n for n in resp.json()["nodes"] if n["state"] == "recorded")
        assert isinstance(recorded["events"][0]["details"], dict)


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
            "trading.dashboard.queries.fetch_scorecard_with_sortino", return_value=scorecard_data
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
# GET /api/pipeline (REQ-064-A6)
# ---------------------------------------------------------------------------

class TestPipelineEndpoint:
    """REQ-064-A6: /api/pipeline 응답 키 집합과 status 값 도메인 검증.

    개정 전에는 엔드포인트 레벨 테스트가 전무했다(SPEC-TRADING-064 결함 A).
    """

    _EXPECTED_STEP_KEYS: ClassVar[set[str]] = {
        "cycle_kind",
        "id",
        "input_tokens",
        "latency_ms",
        "output_tokens",
        "persona_name",
        "regime_at_decision",
        "status",
        "ts",
    }

    def test_returns_expected_key_set(self, client) -> None:
        payload = {
            "cycle_ts": datetime(2026, 6, 14, 9, 30, tzinfo=UTC),
            "steps": [
                {
                    "id": 1,
                    "ts": datetime(2026, 6, 14, 9, 30, tzinfo=UTC),
                    "persona_name": "macro",
                    "cycle_kind": "intraday",
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "latency_ms": 1200,
                    "status": "completed",
                    "regime_at_decision": "bull",
                }
            ],
        }
        with patch("trading.dashboard.queries.fetch_pipeline", return_value=payload):
            resp = client.get("/api/pipeline")

        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"cycle_ts", "steps"}
        assert set(data["steps"][0].keys()) == self._EXPECTED_STEP_KEYS

    def test_status_domain_is_error_or_completed(self, client) -> None:
        payload = {
            "cycle_ts": None,
            "steps": [
                {
                    "id": 1, "ts": None, "persona_name": "macro",
                    "cycle_kind": "intraday", "input_tokens": None,
                    "output_tokens": None, "latency_ms": None,
                    "status": "error", "regime_at_decision": None,
                },
                {
                    "id": 2, "ts": None, "persona_name": "micro",
                    "cycle_kind": "intraday", "input_tokens": None,
                    "output_tokens": None, "latency_ms": None,
                    "status": "completed", "regime_at_decision": None,
                },
            ],
        }
        with patch("trading.dashboard.queries.fetch_pipeline", return_value=payload):
            resp = client.get("/api/pipeline")

        data = resp.json()
        assert {s["status"] for s in data["steps"]} <= {"error", "completed"}

    def test_empty_db_returns_empty_steps_not_500(self, client) -> None:
        """빈 DB에서도 500이 아니라 {steps: [], cycle_ts: null}."""
        with patch(
            "trading.dashboard.queries.fetch_pipeline",
            return_value={"steps": [], "cycle_ts": None},
        ):
            resp = client.get("/api/pipeline")

        assert resp.status_code == 200
        assert resp.json() == {"steps": [], "cycle_ts": None}


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
