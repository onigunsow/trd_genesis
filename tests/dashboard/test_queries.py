"""SPEC-TRADING-047 M1: dashboard query function tests.

RED phase — tests written before implementation.
DB is mocked via the FakeConnection/FakeCursor pattern from conftest.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_patch(rows: list[dict[str, Any]]):
    """Patch trading.dashboard.queries.ro_connection returning preset rows."""

    @contextmanager
    def _conn(autocommit: bool = False):
        from tests.conftest import FakeConnection, FakeCursor
        cursor = FakeCursor(rows)
        yield FakeConnection(cursor)

    return patch("trading.dashboard.queries.ro_connection", side_effect=_conn)


# ---------------------------------------------------------------------------
# fetch_system_status
# ---------------------------------------------------------------------------

class TestFetchSystemStatus:
    """fetch_system_status returns system_state singleton."""

    def test_returns_halt_and_regime(self) -> None:
        from trading.dashboard import queries

        state_row = {
            "halt_state": True,
            "trading_mode": "paper",
            "current_regime": "bull",
            "current_risk_appetite": "risk-on",
            "late_cycle_defense_active": False,
            "updated_at": datetime(2026, 6, 14, 9, 0, tzinfo=UTC),
        }
        with _make_patch([state_row]):
            result = queries.fetch_system_status()

        assert result["halt_state"] is True
        assert result["trading_mode"] == "paper"
        assert result["current_regime"] == "bull"

    def test_missing_row_raises(self) -> None:
        from trading.dashboard import queries

        with _make_patch([]):
            with pytest.raises(RuntimeError, match="system_state"):
                queries.fetch_system_status()


# ---------------------------------------------------------------------------
# fetch_recent_decisions
# ---------------------------------------------------------------------------

class TestFetchRecentDecisions:
    """fetch_recent_decisions returns joined persona_runs + decisions rows."""

    def test_returns_list_of_dicts(self) -> None:
        from trading.dashboard import queries

        rows = [
            {
                "id": 1,
                "ts": datetime(2026, 6, 14, 9, 30, tzinfo=UTC),
                "persona_name": "decision",
                "cycle_kind": "intraday",
                "ticker": "005930",
                "side": "buy",
                "qty": 10,
                "confidence": 0.82,
                "rationale": "모멘텀 확인",
            }
        ]
        with _make_patch(rows):
            result = queries.fetch_recent_decisions(limit=20)

        assert len(result) == 1
        assert result[0]["ticker"] == "005930"
        assert result[0]["side"] == "buy"

    def test_empty_table_returns_empty_list(self) -> None:
        from trading.dashboard import queries

        with _make_patch([]):
            result = queries.fetch_recent_decisions(limit=20)

        assert result == []


# ---------------------------------------------------------------------------
# fetch_recent_orders
# ---------------------------------------------------------------------------

class TestFetchRecentOrders:
    """fetch_recent_orders returns orders with fill_price if available."""

    def test_returns_order_fields(self) -> None:
        from trading.dashboard import queries

        rows = [
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
        with _make_patch(rows):
            result = queries.fetch_recent_orders(limit=50)

        assert len(result) == 1
        assert result[0]["status"] == "filled"
        assert result[0]["fill_price"] == 75000

    def test_no_secrets_in_response_fields(self) -> None:
        """Response rows must not contain request/response JSONB (may hold credentials)."""
        from trading.dashboard import queries

        rows = [
            {
                "id": 1,
                "ts": datetime(2026, 6, 14, tzinfo=UTC),
                "side": "buy",
                "ticker": "000660",
                "qty": 5,
                "order_type": "market",
                "status": "submitted",
                "fill_price": None,
                "mode": "paper",
                "request": {"api_key": "SECRET"},   # must NOT appear in output
                "response": {"token": "SECRET"},
            }
        ]
        with _make_patch(rows):
            result = queries.fetch_recent_orders(limit=50)

        for row in result:
            assert "request" not in row
            assert "response" not in row


# ---------------------------------------------------------------------------
# fetch_holdings
# ---------------------------------------------------------------------------

class TestFetchHoldings:
    """fetch_holdings returns current open position summary from orders/fills."""

    def test_returns_holdings_list(self) -> None:
        from trading.dashboard import queries

        rows = [
            {
                "ticker": "005930",
                "qty_net": 30,
                "avg_fill_price": 74000,
                "total_cost": 2220000,
            }
        ]
        with _make_patch(rows):
            result = queries.fetch_holdings()

        assert len(result) == 1
        assert result[0]["ticker"] == "005930"
        assert result[0]["qty_net"] == 30

    def test_empty_positions_returns_empty_list(self) -> None:
        from trading.dashboard import queries

        with _make_patch([]):
            result = queries.fetch_holdings()

        assert result == []


# ---------------------------------------------------------------------------
# fetch_equity_curve
# ---------------------------------------------------------------------------

class TestFetchEquityCurve:
    """fetch_equity_curve returns daily_equity_snapshot rows for charting."""

    def test_returns_date_and_total_assets(self) -> None:
        from datetime import date

        from trading.dashboard import queries
        rows = [
            {"trading_day": date(2026, 6, 10), "total_assets": 10_000_000},
            {"trading_day": date(2026, 6, 11), "total_assets": 10_050_000},
        ]
        with _make_patch(rows):
            result = queries.fetch_equity_curve(days=30)

        assert len(result) == 2
        assert result[0]["trading_day"].isoformat() == "2026-06-10"
        assert result[1]["total_assets"] == 10_050_000
