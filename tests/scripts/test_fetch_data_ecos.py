"""SPEC-TRADING-036 REQ-036-1 — fetch-data --ecos wires market funds.

Regression guard for the live-smoke wiring gap: ``fetch_market_funds`` (901Y056
S23E/S23A) was defined but never CALLED, so the macro_indicators cache for the
신용융자/예탁금 signals was never populated. The ``--ecos`` branch must now
invoke it after the DEFAULT_SERIES loop and add its count to the total.

@MX:SPEC: SPEC-TRADING-036
"""

from __future__ import annotations

from unittest.mock import patch

from trading.scripts import fetch_data


class TestFetchDataEcosWiresMarketFunds:
    def test_ecos_branch_calls_fetch_market_funds(self):
        with (
            patch.object(fetch_data.ecos_adapter, "fetch_series", return_value=3),
            patch.object(fetch_data.ecos_adapter, "fetch_market_funds", return_value=24) as fmf,
        ):
            rc = fetch_data.main(["--ecos"])
        assert rc == 0
        fmf.assert_called_once()

    def test_market_funds_count_added_to_total(self, capsys):
        with (
            patch.object(fetch_data.ecos_adapter, "fetch_series", return_value=0),
            patch.object(fetch_data.ecos_adapter, "fetch_market_funds", return_value=24),
        ):
            fetch_data.main(["--ecos"])
        out = capsys.readouterr().out
        # The market-funds rows are reported and rolled into the total.
        assert "market funds" in out.lower() or "MARKET_FUNDS" in out or "24" in out
        assert "ECOS total: 24" in out

    def test_ecos_branch_survives_market_funds_failure(self, capsys):
        def _boom(*_a, **_k):
            raise RuntimeError("ECOS funds down")

        with (
            patch.object(fetch_data.ecos_adapter, "fetch_series", return_value=2),
            patch.object(fetch_data.ecos_adapter, "fetch_market_funds", side_effect=_boom),
        ):
            rc = fetch_data.main(["--ecos"])
        # A funds failure must not abort the command (graceful, exit 0).
        assert rc == 0
        out = capsys.readouterr().out
        assert "ECOS total" in out
