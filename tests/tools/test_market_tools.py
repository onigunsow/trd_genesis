"""Tests for tools/market_tools.py — market data fetcher functions."""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestGetMacroIndicators:
    """Verify macro indicator fetching wraps context.py correctly."""

    def test_returns_found_indicators(self):
        with patch("trading.tools.market_tools._latest_macro") as mock:
            mock.side_effect = lambda source, sid: (
                {"value": 5.25, "ts": "2026-05-01", "units": "%"} if sid == "DFF" else None
            )
            from trading.tools.market_tools import get_macro_indicators
            result = get_macro_indicators(series_ids=["DFF", "UNKNOWN"])

        assert result["DFF"]["value"] == 5.25
        assert result["DFF"]["ts"] == "2026-05-01"
        assert result["UNKNOWN"]["error"] == "not_found"

    def test_tries_fred_then_ecos(self):
        """If FRED returns None, tries ECOS."""
        call_log = []

        def mock_macro(source, sid):
            call_log.append((source, sid))
            if source == "ecos" and sid == "BOK_BASE_RATE":
                return {"value": 3.5, "ts": "2026-05-01", "units": "%"}
            return None

        with patch("trading.tools.market_tools._latest_macro", side_effect=mock_macro):
            from trading.tools.market_tools import get_macro_indicators
            result = get_macro_indicators(series_ids=["BOK_BASE_RATE"])

        assert ("fred", "BOK_BASE_RATE") in call_log
        assert ("ecos", "BOK_BASE_RATE") in call_log
        assert result["BOK_BASE_RATE"]["value"] == 3.5


class TestGetGlobalAssets:
    """Verify global asset fetching."""

    def test_returns_price_data(self):
        with patch("trading.tools.market_tools._latest_close") as mock:
            mock.return_value = {"close": 5200.50, "pct_change_1d": 0.012, "ts": "2026-05-04"}
            from trading.tools.market_tools import get_global_assets
            result = get_global_assets(symbols=["^GSPC"], days=10)

        assert result["^GSPC"]["close"] == 5200.50
        assert result["^GSPC"]["pct_change_1d"] == 0.012

    def test_no_data_returns_error(self):
        with patch("trading.tools.market_tools._latest_close", return_value=None):
            from trading.tools.market_tools import get_global_assets
            result = get_global_assets(symbols=["^MISSING"])

        assert result["^MISSING"]["error"] == "no_data"


class TestGetTickerTechnicals:
    """Verify technical indicator wrapping."""

    def test_returns_technicals_on_success(self):
        mock_tech = {"close": 70000, "ma20": 68000, "ma60": 65000, "rsi14": 55.0, "vs_ma20_pct": 2.94}
        with patch("trading.tools.market_tools._technicals", return_value=mock_tech):
            from trading.tools.market_tools import get_ticker_technicals
            result = get_ticker_technicals(ticker="005930")

        assert result["close"] == 70000
        assert result["rsi14"] == 55.0

    def test_insufficient_data_returns_error(self):
        with patch("trading.tools.market_tools._technicals", return_value=None):
            from trading.tools.market_tools import get_ticker_technicals
            result = get_ticker_technicals(ticker="999999")

        assert result["error"] == "insufficient_data"


class TestGetTickerFundamentals:
    """Verify fundamentals wrapping."""

    def test_returns_fundamentals(self):
        mock_fund = {"ts": "2026-05-01", "market_cap": 400_000_000_000_000,
                     "per": 12.5, "pbr": 1.2, "eps": 5600, "bps": 58000, "div_yield": 2.1}
        with patch("trading.tools.market_tools._fundamentals", return_value=mock_fund):
            from trading.tools.market_tools import get_ticker_fundamentals
            result = get_ticker_fundamentals(ticker="005930")

        assert result["per"] == 12.5
        assert result["market_cap"] == 400_000_000_000_000

    def test_not_found_returns_error(self):
        with patch("trading.tools.market_tools._fundamentals", return_value=None):
            from trading.tools.market_tools import get_ticker_fundamentals
            result = get_ticker_fundamentals(ticker="INVALID")

        assert result["error"] == "not_found"


class TestGetTickerFlows:
    """Verify flow data wrapping."""

    def test_returns_flows(self):
        mock_flows = {"foreign_5d": 50_000_000, "institution_5d": -20_000_000, "individual_5d": -30_000_000}
        with patch("trading.tools.market_tools._flows_5d", return_value=mock_flows):
            from trading.tools.market_tools import get_ticker_flows
            result = get_ticker_flows(ticker="005930")

        assert result["foreign_5d"] == 50_000_000

    def test_no_data_returns_error(self):
        with patch("trading.tools.market_tools._flows_5d", return_value=None):
            from trading.tools.market_tools import get_ticker_flows
            result = get_ticker_flows(ticker="MISSING")

        assert result["error"] == "no_flow_data"


class TestGetRecentDisclosures:
    """Verify disclosure fetching."""

    def test_returns_disclosure_list(self):
        mock_disclosures = [
            {"rcept_dt": "2026-05-04", "corp_name": "삼성전자", "stock_code": "005930", "report_nm": "분기보고서"}
        ]
        with patch("trading.tools.market_tools._recent_disclosures", return_value=mock_disclosures):
            from trading.tools.market_tools import get_recent_disclosures
            result = get_recent_disclosures(tickers=["005930"], days=3)

        assert len(result) == 1
        assert result[0]["corp_name"] == "삼성전자"

    def test_empty_returns_empty_list(self):
        with patch("trading.tools.market_tools._recent_disclosures", return_value=[]):
            from trading.tools.market_tools import get_recent_disclosures
            result = get_recent_disclosures(tickers=["999999"])

        assert result == []
