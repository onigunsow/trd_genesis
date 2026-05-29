"""SPEC-TRADING-035 REQ-035-4 — regime -> portfolio cash-target shift tests.

The portfolio gate reads the cached macro ``current_regime`` and shifts the cash
target conservatively: bull -> low end (but never below the 20% bull floor),
bear -> high end, neutral -> unchanged. The gate passes ``current_regime`` (and
the regime-shifted cash guide) into ``portfolio.run``'s input, and the
``portfolio.jinja`` template carries a regime-aware cash guide line.

Also verifies the hard cash-floor guard wired into the gate (REQ-035-2e / R-1)
and the SPEC-034 fail-safe is preserved on a regime-read failure.

@MX:SPEC: SPEC-TRADING-035
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from trading.personas import portfolio_gate as pg
from trading.personas import regime_branch as rb


def _buy(t, qty=10):
    return {"ticker": t, "side": "buy", "qty": qty}


def _sell(t, qty=10):
    return {"ticker": t, "side": "sell", "qty": qty}


def _pres(adjusted=None, rejected=None):
    return SimpleNamespace(
        response_json={"adjusted_signals": adjusted or [], "rejected": rejected or []}
    )


# ---------------------------------------------------------------------------
# REQ-035-4 (b): current_regime passed into portfolio.run input + cash shift
# ---------------------------------------------------------------------------
class TestPortfolioRegimeInput:
    def _run_gate(self, regime, *, cash_pct=50.0):
        captured = {}

        def _fake_run(input_data, cycle_kind="pre_market"):
            captured["input"] = input_data
            captured["cycle_kind"] = cycle_kind
            return _pres()

        holdings = [{"ticker": str(i)} for i in range(6)]  # >= 5 -> active
        with (
            patch.object(pg.portfolio, "run", side_effect=_fake_run),
            patch.object(pg, "get_effective_regime", return_value=(regime, "neutral")),
            patch.object(pg, "_emit_transparency"),
        ):
            pg._apply_portfolio_adjustment(
                [_buy("005930")], [1],
                holdings=holdings, holdings_count=6,
                total_assets=10_000_000, cash_pct=cash_pct,
                today="2026-05-29", cycle_kind="pre_market",
            )
        return captured

    def test_current_regime_passed_into_portfolio_run(self):
        cap = self._run_gate("bull")
        assert cap["input"]["current_regime"] == "bull"

    def test_bull_shifts_cash_target_low(self):
        cap = self._run_gate("bull")
        assert cap["input"]["regime_cash_target_shift"] == "low"

    def test_bear_shifts_cash_target_high(self):
        cap = self._run_gate("bear")
        assert cap["input"]["regime_cash_target_shift"] == "high"

    def test_neutral_keeps_cash_target(self):
        cap = self._run_gate("neutral")
        assert cap["input"]["regime_cash_target_shift"] == "keep"

    def test_bull_cash_floor_in_input_is_20_not_10(self):
        cap = self._run_gate("bull")
        # REQ-035-4 / REQ-035-2 consistency: floor never below 20% in bull.
        assert cap["input"]["regime_cash_floor_pct"] == 20.0
        assert cap["input"]["regime_cash_floor_pct"] >= 20.0


# ---------------------------------------------------------------------------
# REQ-035-2(e) / R-1: hard cash-floor guard wired into the gate
# ---------------------------------------------------------------------------
class TestPortfolioCashFloorGuard:
    def test_bull_below_20pct_drops_buys_keeps_sells(self):
        holdings = [{"ticker": str(i)} for i in range(6)]
        with (
            patch.object(pg, "get_effective_regime", return_value=("bull", "risk-on")),
            patch.object(pg.portfolio, "run") as prun,
            patch.object(pg, "_emit_transparency"),
        ):
            signals, _sig_ids = pg._apply_portfolio_adjustment(
                [_buy("005930"), _sell("000660")], [1, 2],
                holdings=holdings, holdings_count=6,
                total_assets=10_000_000, cash_pct=15.0,  # < 20% bull floor
                today="2026-05-29", cycle_kind="pre_market",
            )
        kept = {s["ticker"] for s in signals}
        assert "005930" not in kept  # buy blocked by hard floor
        assert "000660" in kept       # sell never touched
        # With cash below floor and no buys surviving, the persona is not called.
        prun.assert_not_called()

    def test_sell_only_passthrough_regardless_of_regime(self):
        """Negative test: sells pass through unadjusted at any regime/cash."""
        holdings = [{"ticker": str(i)} for i in range(6)]
        with (
            patch.object(pg, "get_effective_regime", return_value=("bull", "risk-on")),
            patch.object(pg.portfolio, "run") as prun,
        ):
            signals, sig_ids = pg._apply_portfolio_adjustment(
                [_sell("000660")], [9],
                holdings=holdings, holdings_count=6,
                total_assets=10_000_000, cash_pct=3.0,
                today="2026-05-29", cycle_kind="pre_market",
            )
        assert [s["ticker"] for s in signals] == ["000660"]
        assert sig_ids == [9]
        prun.assert_not_called()  # no buys -> no persona call


# ---------------------------------------------------------------------------
# REQ-035-4 (d): fail-safe on regime read failure (SPEC-034 preserved)
# ---------------------------------------------------------------------------
class TestPortfolioRegimeFailSafe:
    def test_regime_read_failure_falls_back_to_neutral_no_block(self):
        holdings = [{"ticker": str(i)} for i in range(6)]
        captured = {}

        def _fake_run(input_data, cycle_kind="pre_market"):
            captured["input"] = input_data
            return _pres()

        with (
            patch.object(pg, "get_effective_regime", side_effect=RuntimeError("db down")),
            patch.object(pg.portfolio, "run", side_effect=_fake_run),
            patch.object(pg, "_emit_transparency"),
        ):
            signals, _sig_ids = pg._apply_portfolio_adjustment(
                [_buy("005930")], [1],
                holdings=holdings, holdings_count=6,
                total_assets=10_000_000, cash_pct=50.0,
                today="2026-05-29", cycle_kind="pre_market",
            )
        # Fail-safe: regime treated as neutral, cycle NOT blocked, buy survives.
        assert captured["input"]["current_regime"] == "neutral"
        assert [s["ticker"] for s in signals] == ["005930"]


# ---------------------------------------------------------------------------
# REQ-035-4 (b): portfolio.jinja regime cash guide line (grep)
# ---------------------------------------------------------------------------
class TestPortfolioPromptInjection:
    def test_portfolio_jinja_has_regime_cash_guide(self):
        path = Path(rb.__file__).resolve().parent / "prompts" / "portfolio.jinja"
        text = path.read_text(encoding="utf-8")
        assert "current_regime" in text or "regime_cash_target_shift" in text
