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
# SPEC-041 follow-on: asset summary block (stock eval / cash / total)          #
# --------------------------------------------------------------------------- #

class TestFormatHoldingsSummaryBlock:
    def _holdings(self):
        return [
            {"name": "현대로템", "ticker": "064350", "qty": 10,
             "avg_cost": 100_000, "current_price": 105_000,
             "pnl_amount": 49_340, "pnl_pct": 4.9},
        ]

    def test_summary_block_renders_three_lines(self):
        """Summary params append 주식 평가금 / 보유 현금(D+2) / 합산(총자산)."""
        from trading.risk.emergency import _format_holdings

        text = _format_holdings(
            self._holdings(),
            stock_eval=3_154_900, cash=6_747_716, total=9_902_616,
        )
        # per-position list + TOTAL still present
        assert "현대로템" in text
        assert "TOTAL 평가손익" in text
        # three new summary lines with thousands separators (D+2 basis)
        assert "주식 평가금: 3,154,900원" in text
        assert "보유 현금(D+2): 6,747,716원" in text
        assert "합산(총자산): 9,902,616원" in text

    def test_summary_total_equals_provided_total(self):
        """합산 line uses the provided total verbatim (= cash + stock).

        With the D+2 basis (cash = nxdy_excc_amt, total = tot_evlu_amt) the
        headline total now reconciles exactly: stock_eval + cash == total.
        """
        from trading.risk.emergency import _format_holdings

        stock_eval, cash, total = 3_154_900, 6_747_716, 9_902_616
        assert total == stock_eval + cash  # guard the fixture itself
        text = _format_holdings(
            self._holdings(), stock_eval=stock_eval, cash=cash, total=total,
        )
        assert f"합산(총자산): {total:,}원" in text

    def test_no_summary_params_backward_compatible(self):
        """Calling without summary params: per-position + TOTAL, no summary."""
        from trading.risk.emergency import _format_holdings

        text = _format_holdings(self._holdings())
        assert "현대로템" in text
        assert "TOTAL 평가손익" in text
        assert "주식 평가금" not in text
        assert "보유 현금(D+2)" not in text
        assert "합산(총자산)" not in text

    def test_empty_holdings_with_summary_still_safe(self):
        """Empty holdings must not crash even when summary params are given."""
        from trading.risk.emergency import _format_holdings

        text = _format_holdings(
            [], stock_eval=0, cash=6_747_716, total=6_747_716,
        )
        assert "보유 종목 없음" in text


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

    def test_holdings_dispatch_renders_summary_block(self):
        """_holdings_summary passes stock_eval/buyable/total_assets through.

        Switched to the D+2 settlement basis so the summary reconciles with
        the 자산 in trade alerts / daily report: cash = buyable (nxdy_excc_amt),
        total = total_assets (tot_evlu_amt), and stock_eval + cash == total.
        """
        from trading.risk import emergency

        stock_eval, cash, total = 3_154_900, 6_747_716, 9_902_616
        assert total == stock_eval + cash  # D+2 basis reconciles exactly
        bal = {
            "holdings": [
                {"name": "현대로템", "ticker": "064350", "qty": 10,
                 "avg_cost": 100_000, "current_price": 105_000,
                 "pnl_amount": 49_340, "pnl_pct": 4.9},
            ],
            "stock_eval": stock_eval,
            "buyable": cash,            # nxdy_excc_amt (D+2 settlement cash)
            "total_assets": total,      # tot_evlu_amt (KIS headline)
            # legacy keys still present in the real dict — must NOT be used:
            "cash_d2": 5_385_788,       # dnca_tot_amt (current deposit)
            "invest_basis": 8_540_688,  # cash_d2 + stock_eval (does not reconcile)
        }
        with (
            patch("trading.risk.emergency.KisClient"),
            patch("trading.risk.emergency.balance", return_value=bal),
            patch("trading.risk.emergency.get_settings",
                  return_value=MagicMock(trading_mode="paper")),
        ):
            reply = emergency.handle("/holdings", actor="telegram")
        assert "주식 평가금: 3,154,900원" in reply
        assert "보유 현금(D+2): 6,747,716원" in reply
        assert "합산(총자산): 9,902,616원" in reply
        # rendered total must equal stock_eval + cash and match total_assets
        assert f"합산(총자산): {stock_eval + cash:,}원" in reply
        # legacy current-deposit basis must NOT leak into the output
        assert "5,385,788" not in reply
        assert "8,540,688" not in reply

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


# --------------------------------------------------------------------------- #
# Follow-on: underscore aliases for menu-registrable commands                  #
#                                                                              #
# Telegram requires command names match [a-z0-9_], so the hyphenated forms     #
# cannot be registered in the command menu. We add underscore aliases that     #
# dispatch to the SAME handler while keeping the hyphen forms working.         #
# --------------------------------------------------------------------------- #

class TestUnderscoreCommandAliases:
    """Each of the 4 commands must accept BOTH hyphen and underscore forms."""

    def test_tool_calling_underscore_equals_hyphen(self):
        from trading.risk import emergency

        with (
            patch("trading.risk.emergency.update_system_state") as upd,
            patch("trading.risk.emergency.audit"),
        ):
            reply_hyphen = emergency.handle("/tool-calling on", actor="t")
            reply_underscore = emergency.handle("/tool_calling on", actor="t")
        # Same handler → identical reply + flag toggled both times.
        assert reply_hyphen == reply_underscore
        assert "tool_calling_enabled=True" in reply_underscore
        assert upd.call_count == 2
        for call in upd.call_args_list:
            assert call.kwargs.get("tool_calling_enabled") is True

    def test_car_filter_underscore_equals_hyphen(self):
        from trading.risk import emergency

        with (
            patch("trading.risk.emergency.update_system_state") as upd,
            patch("trading.risk.emergency.audit"),
        ):
            reply_hyphen = emergency.handle("/car-filter on", actor="t")
            reply_underscore = emergency.handle("/car_filter on", actor="t")
        assert reply_hyphen == reply_underscore
        assert "car_filter_enabled=True" in reply_underscore
        assert upd.call_count == 2
        for call in upd.call_args_list:
            assert call.kwargs.get("car_filter_enabled") is True

    def test_dyn_threshold_underscore_equals_hyphen(self):
        from trading.risk import emergency

        with (
            patch("trading.risk.emergency.update_system_state") as upd,
            patch("trading.risk.emergency.audit"),
        ):
            reply_hyphen = emergency.handle("/dyn-threshold on", actor="t")
            reply_underscore = emergency.handle("/dyn_threshold on", actor="t")
        assert reply_hyphen == reply_underscore
        assert "dynamic_thresholds_enabled=True" in reply_underscore
        assert upd.call_count == 2
        for call in upd.call_args_list:
            assert call.kwargs.get("dynamic_thresholds_enabled") is True

    def test_prototype_status_underscore_equals_hyphen(self):
        from trading.risk import emergency

        # prototype-status takes no args; disabled state gives a deterministic
        # reply without touching prototype internals.
        with patch("trading.risk.emergency.get_system_state",
                   return_value={"prototype_risk_enabled": False}):
            reply_hyphen = emergency.handle("/prototype-status", actor="t")
            reply_underscore = emergency.handle("/prototype_status", actor="t")
        assert reply_hyphen == reply_underscore
        assert "ProtoHedge" in reply_underscore

    def test_help_lists_underscore_forms(self):
        from trading.risk.emergency import _help

        help_text = _help()
        assert "/tool_calling" in help_text
        assert "/car_filter" in help_text
        assert "/dyn_threshold" in help_text
        assert "/prototype_status" in help_text

    def test_hyphen_forms_still_work_regression(self):
        """Regression: the original hyphen forms must NOT break."""
        from trading.risk import emergency

        with (
            patch("trading.risk.emergency.update_system_state") as upd,
            patch("trading.risk.emergency.audit"),
        ):
            r1 = emergency.handle("/tool-calling off", actor="t")
            r2 = emergency.handle("/car-filter off", actor="t")
            r3 = emergency.handle("/dyn-threshold off", actor="t")
        assert "tool_calling_enabled=False" in r1
        assert "car_filter_enabled=False" in r2
        assert "dynamic_thresholds_enabled=False" in r3
        assert upd.call_count == 3

        with patch("trading.risk.emergency.get_system_state",
                   return_value={"prototype_risk_enabled": False}):
            r4 = emergency.handle("/prototype-status", actor="t")
        assert "ProtoHedge" in r4

    def test_usage_hints_use_underscore_form(self):
        """Usage hints (invalid args) should display the menu-valid form."""
        from trading.risk import emergency

        # Invalid arg path returns the usage hint without touching state.
        assert "/tool_calling on|off" in emergency._handle_tool_calling("/tool_calling", "t")
        assert "/car_filter on|off" in emergency._handle_car_filter("/car_filter", "t")
        assert "/dyn_threshold on|off" in emergency._handle_dyn_threshold("/dyn_threshold", "t")
