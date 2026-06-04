"""SPEC-TRADING-041 REQ-041-3 — /holdings command + /pnl net-of-fee + /help.

- AC-3.1: /holdings renders per-holding name/qty/avg_cost/current/eval-P&L/% + TOTAL.
- AC-3.2 / REQ-041-4c: empty holdings + KIS failure → safe degrade (no crash).
- AC-3.3: /holdings appears in /help.
- AC-4.1: /pnl subtracts fees → NET; label clarifies it is net-of-fee estimate.
- AC-4.2: zero trades / zero fee → safe 0원 report.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

# --------------------------------------------------------------------------- #
# Feature 3a: _format_holdings (pure)                                          #
# --------------------------------------------------------------------------- #

class TestFormatHoldings:
    def test_renders_each_holding_and_total(self):
        from trading.risk.emergency import _format_holdings

        holdings = [
            {"name": "현대로템", "ticker": "064350", "qty": 10,
             "avg_cost": 100_000, "current_price": 105_000,
             "pnl_amount": 49_340, "pnl_pct": 4.9},
            {"name": "KB금융", "ticker": "105560", "qty": 5,
             "avg_cost": 80_000, "current_price": 78_000,
             "pnl_amount": -10_000, "pnl_pct": -2.5},
        ]
        text = _format_holdings(holdings)
        assert "현대로템" in text
        assert "KB금융" in text
        assert "10" in text
        assert "5" in text
        assert "100,000" in text
        assert "+49,340" in text
        assert "-10,000" in text
        assert "+4.9%" in text
        assert "-2.5%" in text
        # TOTAL eval P&L = 49,340 + (-10,000) = +39,340
        assert "TOTAL" in text or "총" in text
        assert "+39,340" in text

    def test_empty_holdings_message(self):
        from trading.risk.emergency import _format_holdings

        text = _format_holdings([])
        assert "보유 종목 없음" in text

    def test_missing_name_falls_back_to_ticker(self):
        from trading.risk.emergency import _format_holdings

        holdings = [{"name": "", "ticker": "064350", "qty": 1,
                     "avg_cost": 100_000, "current_price": 100_000,
                     "pnl_amount": 0, "pnl_pct": 0.0}]
        text = _format_holdings(holdings)
        assert "064350" in text


# --------------------------------------------------------------------------- #
# Feature 3a: /holdings dispatch + KIS wiring                                  #
# --------------------------------------------------------------------------- #

class TestHoldingsCommand:
    def test_holdings_dispatch_renders_balance(self):
        from trading.risk import emergency

        bal = {"holdings": [
            {"name": "현대로템", "ticker": "064350", "qty": 10,
             "avg_cost": 100_000, "current_price": 105_000,
             "pnl_amount": 49_340, "pnl_pct": 4.9},
        ]}
        with (
            patch("trading.risk.emergency.KisClient"),
            patch("trading.risk.emergency.balance", return_value=bal),
            patch("trading.risk.emergency.get_settings",
                  return_value=MagicMock(trading_mode="paper")),
        ):
            reply = emergency.handle("/holdings", actor="telegram")
        assert "현대로템" in reply
        assert "+49,340" in reply

    def test_holdings_kis_failure_degrades_safely(self):
        """AC-3.2 / REQ-041-4c: KIS error → safe message, never raises."""
        from trading.risk import emergency

        with (
            patch("trading.risk.emergency.KisClient"),
            patch("trading.risk.emergency.balance", side_effect=RuntimeError("timeout")),
            patch("trading.risk.emergency.get_settings",
                  return_value=MagicMock(trading_mode="paper")),
        ):
            reply = emergency.handle("/holdings", actor="telegram")
        assert "실패" in reply
        assert "timeout" not in reply.lower() or "조회" in reply  # safe, no raw stack

    def test_holdings_empty_safe(self):
        from trading.risk import emergency

        with (
            patch("trading.risk.emergency.KisClient"),
            patch("trading.risk.emergency.balance", return_value={"holdings": []}),
            patch("trading.risk.emergency.get_settings",
                  return_value=MagicMock(trading_mode="paper")),
        ):
            reply = emergency.handle("/holdings", actor="telegram")
        assert "보유 종목 없음" in reply


class TestHelpListsHoldings:
    def test_help_includes_holdings(self):
        from trading.risk.emergency import _help

        assert "/holdings" in _help()


# --------------------------------------------------------------------------- #
# Feature 3b: /pnl net-of-fee                                                  #
# --------------------------------------------------------------------------- #

class TestPnlNetOfFee:
    def test_pnl_subtracts_fee(self):
        """AC-4.1: gross 100,000 minus fee 1,500 = NET 98,500원."""
        from tests.conftest import mock_connection_factory
        from trading.risk import emergency

        # gross sell - buy = 1,000,000 - 900,000 = 100,000 ; fee = 1,500
        row = {"buys": 3, "sells": 2, "gross": 100_000, "fee": 1_500}
        with patch.object(emergency, "connection",
                          lambda: mock_connection_factory([row])):
            reply = emergency._pnl_summary()
        assert "98,500원" in reply

    def test_pnl_label_clarifies_net_of_fee_estimate(self):
        from tests.conftest import mock_connection_factory
        from trading.risk import emergency

        row = {"buys": 1, "sells": 1, "gross": 50_000, "fee": 500}
        with patch.object(emergency, "connection",
                          lambda: mock_connection_factory([row])):
            reply = emergency._pnl_summary()
        assert "추정" in reply
        assert "수수료" in reply  # label must mention fees are netted

    def test_pnl_zero_trades_safe(self):
        """AC-4.2: no trades / null aggregates → 0원, no crash."""
        from tests.conftest import mock_connection_factory
        from trading.risk import emergency

        row = {"buys": 0, "sells": 0, "gross": None, "fee": None}
        with patch.object(emergency, "connection",
                          lambda: mock_connection_factory([row])):
            reply = emergency._pnl_summary()
        assert "0원" in reply
