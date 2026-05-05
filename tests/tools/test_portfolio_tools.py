"""Tests for tools/portfolio_tools.py — portfolio and watchlist tools."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestGetPortfolioStatus:
    """Verify portfolio status fetching wraps KIS balance correctly."""

    def test_returns_portfolio_summary(self):
        mock_balance = {
            "total_assets": 10_000_000,
            "cash_d2": 8_000_000,
            "stock_eval": 2_000_000,
            "holdings": [
                {"ticker": "005930", "name": "삼성전자", "qty": 10,
                 "avg_cost": 70000, "current_price": 72000,
                 "eval_amount": 720000, "pnl_amount": 20000},
            ],
        }
        with (
            patch("trading.tools.portfolio_tools.get_settings") as mock_settings,
            patch("trading.tools.portfolio_tools.KisClient") as mock_client_cls,
            patch("trading.tools.portfolio_tools.balance", return_value=mock_balance),
        ):
            mock_settings.return_value = MagicMock(trading_mode="paper")
            from trading.tools.portfolio_tools import get_portfolio_status
            result = get_portfolio_status()

        assert result["total_assets"] == 10_000_000
        assert result["cash"] == 8_000_000
        assert result["cash_pct"] == 80.0
        assert result["equity_pct"] == 20.0
        assert result["holdings_count"] == 1
        assert result["holdings"][0]["ticker"] == "005930"
        assert result["holdings"][0]["pnl_pct"] == pytest.approx(2.86, abs=0.01)

    def test_empty_portfolio(self):
        mock_balance = {
            "total_assets": 10_000_000,
            "cash_d2": 10_000_000,
            "stock_eval": 0,
            "holdings": [],
        }
        with (
            patch("trading.tools.portfolio_tools.get_settings") as mock_settings,
            patch("trading.tools.portfolio_tools.KisClient"),
            patch("trading.tools.portfolio_tools.balance", return_value=mock_balance),
        ):
            mock_settings.return_value = MagicMock(trading_mode="paper")
            from trading.tools.portfolio_tools import get_portfolio_status
            result = get_portfolio_status()

        assert result["cash_pct"] == 100.0
        assert result["holdings_count"] == 0
        assert result["holdings"] == []


class TestGetWatchlist:
    """Verify watchlist returns default tickers with names."""

    def test_returns_default_watchlist(self):
        from trading.tools.portfolio_tools import get_watchlist
        result = get_watchlist()

        assert result["count"] == 5
        tickers = result["tickers"]
        codes = [t["code"] for t in tickers]
        assert "005930" in codes
        assert "000660" in codes

    def test_each_ticker_has_name(self):
        from trading.tools.portfolio_tools import get_watchlist
        result = get_watchlist()

        for t in result["tickers"]:
            assert "code" in t
            assert "name" in t
            assert t["name"] != ""  # All default tickers have names
