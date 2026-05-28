"""SPEC-TRADING-029 v0.2.0 — balance() invest_basis field (REQ-029-10).

``account.balance()`` must expose ``invest_basis = cash_d2 + stock_eval`` so the
two trade-briefing percentages (현금 % / 주식 %) share a single denominator and
sum to 100%. The KIS ``tot_evlu_amt`` (total_assets) is NOT a valid denominator
because it does not equal ``dnca_tot_amt + scts_evlu_amt`` (verified live).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from trading.kis.client import KisResponse


def _balance_response(*, cash: int, stock: int, total: int) -> KisResponse:
    """Build an inquire-balance KisResponse with the summary fields we read."""
    raw: dict[str, Any] = {
        "rt_cd": "0",
        "msg_cd": "",
        "msg1": "",
        "output1": [],
        "output2": [
            {
                "dnca_tot_amt": str(cash),
                "scts_evlu_amt": str(stock),
                "tot_evlu_amt": str(total),
                "nxdy_excc_amt": str(cash),
                "nrcvb_buy_amt": "0",
                "evlu_pfls_smtl_amt": "0",
            }
        ],
    }
    return KisResponse(
        status_code=200, rt_cd="0", msg_cd="", msg="OK", output=[], raw=raw
    )


def _client(resp: KisResponse) -> MagicMock:
    client = MagicMock()
    client.account_prefix = "50185724"
    client.account_suffix = "01"
    client.tr_id = MagicMock(side_effect=lambda paper_id, live_id: paper_id)
    client.get.return_value = resp
    return client


class TestInvestBasis:
    def test_invest_basis_equals_cash_plus_stock(self):
        """REQ-029-10: invest_basis = cash_d2 + stock_eval (live values)."""
        from trading.kis.account import balance

        # Live-observed values from the SPEC.
        resp = _balance_response(cash=8_787_740, stock=3_128_400, total=9_919_870)
        bal = balance(_client(resp))

        assert bal["invest_basis"] == 8_787_740 + 3_128_400 == 11_916_140
        # total_assets is the KIS headline figure, distinct from invest_basis.
        assert bal["total_assets"] == 9_919_870

    def test_percentages_sum_to_100_using_invest_basis(self):
        """AC-029-14: cash_pct + equity_pct == 100 when computed on invest_basis."""
        from trading.kis.account import balance

        resp = _balance_response(cash=8_787_740, stock=3_128_400, total=9_919_870)
        bal = balance(_client(resp))

        basis = bal["invest_basis"]
        cash_pct = bal["cash_d2"] / basis * 100
        equity_pct = bal["stock_eval"] / basis * 100
        assert round(cash_pct + equity_pct, 1) == 100.0

    def test_invest_basis_zero_when_empty_account(self):
        """AC-029-15: new account (cash=0, stock=0) → invest_basis=0 (no crash)."""
        from trading.kis.account import balance

        resp = _balance_response(cash=0, stock=0, total=0)
        bal = balance(_client(resp))
        assert bal["invest_basis"] == 0
