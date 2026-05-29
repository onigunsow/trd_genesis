"""SPEC-TRADING-035 REQ-035-2 — conservative regime branching tests.

Covers the pure ``regime_branch`` module: the per-regime adjustment table
(bull = gentle loosen, bear = tighten, neutral = unchanged) and the hard
Python cash-floor guard that blocks NEW buys when cash is below the regime
floor (R-1 mitigation — the LLM context injection alone is not trusted).

All logic here is pure (no DB, no network), so the acceptance criteria are
verified directly against the adjustment/guard functions plus a grep-style
check that the prompt templates carry the regime context line.

@MX:SPEC: SPEC-TRADING-035
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from trading.personas import decision as decision_persona
from trading.personas import regime_branch as rb
from trading.personas import risk as risk_persona


# ---------------------------------------------------------------------------
# REQ-035-2 (a/b/c): per-regime adjustment table
# ---------------------------------------------------------------------------
class TestRegimeAdjustment:
    def test_bull_is_gentle_loosen_not_aggressive(self):
        adj = rb.adjust_for_regime("bull")
        # AC: cash floor 30 -> 20 (NOT 10), confidence -0.05 (NOT -0.1).
        assert adj.cash_floor_pct == 20.0
        assert adj.confidence_delta == -0.05
        # Sector limit modestly loosened (direction only — Q-3).
        assert adj.sector_cap_pct > rb.BASE_SECTOR_CAP_PCT
        # Bull never blocks leverage; cash target shifts to the LOW end.
        assert adj.block_leverage is False
        assert adj.cash_target_shift == "low"

    def test_bull_cash_floor_never_reaches_phase3_ten_percent(self):
        # Negative guard: Phase-3 aggressive mode (cash 10%) is OUT OF SCOPE.
        assert rb.adjust_for_regime("bull").cash_floor_pct >= 20.0

    def test_bear_is_tighten(self):
        adj = rb.adjust_for_regime("bear")
        # AC: confidence +0.1, sector tighten, leverage/margin buys blocked.
        assert adj.confidence_delta == 0.1
        assert adj.sector_cap_pct < rb.BASE_SECTOR_CAP_PCT
        assert adj.block_leverage is True
        # Bear shifts cash target to the HIGH end (more cash).
        assert adj.cash_target_shift == "high"
        # Floor stays at least the conservative base (never loosened in bear).
        assert adj.cash_floor_pct >= rb.BASE_CASH_FLOOR_PCT

    def test_neutral_is_unchanged(self):
        adj = rb.adjust_for_regime("neutral")
        assert adj.cash_floor_pct == rb.BASE_CASH_FLOOR_PCT
        assert adj.confidence_delta == 0.0
        assert adj.sector_cap_pct == rb.BASE_SECTOR_CAP_PCT
        assert adj.block_leverage is False
        assert adj.cash_target_shift == "keep"

    def test_unknown_regime_defaults_to_neutral(self):
        adj = rb.adjust_for_regime("sideways")
        assert adj.regime == "neutral"
        assert adj.confidence_delta == 0.0

    def test_base_constants_are_conservative(self):
        assert rb.BASE_CASH_FLOOR_PCT == 30.0
        assert rb.BASE_SECTOR_CAP_PCT == 40.0


class TestRegimeBranchApplied:
    def test_returns_regime_for_valid(self):
        assert rb.regime_branch_applied("bull") == "bull"
        assert rb.regime_branch_applied("bear") == "bear"
        assert rb.regime_branch_applied("neutral") == "neutral"

    def test_returns_neutral_for_invalid(self):
        assert rb.regime_branch_applied("weird") == "neutral"


# ---------------------------------------------------------------------------
# REQ-035-2 (e) / R-1: hard cash-floor Python guard
# ---------------------------------------------------------------------------
def _buy(t, qty=10):
    return {"ticker": t, "side": "buy", "qty": qty}


def _sell(t, qty=10):
    return {"ticker": t, "side": "sell", "qty": qty}


class TestCashFloorGuard:
    def test_bull_below_20pct_blocks_new_buys(self):
        # AC-2: regime=bull, cash 18% (< 20% floor) -> buys blocked.
        signals = [_buy("005930"), _sell("000660")]
        kept, dropped = rb.enforce_cash_floor(signals, cash_pct=18.0, regime="bull")
        kept_tickers = {s["ticker"] for s in kept}
        assert "005930" not in kept_tickers  # buy blocked
        assert "000660" in kept_tickers      # sell NEVER touched
        assert 0 in dropped                   # index 0 dropped

    def test_bull_at_or_above_20pct_allows_buys(self):
        signals = [_buy("005930")]
        kept, dropped = rb.enforce_cash_floor(signals, cash_pct=20.0, regime="bull")
        assert {s["ticker"] for s in kept} == {"005930"}
        assert dropped == []

    def test_neutral_below_30pct_blocks_buys(self):
        # Neutral floor is the conservative base 30%.
        signals = [_buy("005930")]
        kept, dropped = rb.enforce_cash_floor(signals, cash_pct=25.0, regime="neutral")
        assert kept == []
        assert dropped == [0]

    def test_neutral_at_30pct_allows_buys(self):
        signals = [_buy("005930")]
        kept, _ = rb.enforce_cash_floor(signals, cash_pct=30.0, regime="neutral")
        assert {s["ticker"] for s in kept} == {"005930"}

    def test_sells_and_holds_never_blocked_regardless_of_cash(self):
        # AC (REQ-035-4 negative): sells pass through untouched at any cash level.
        signals = [_sell("000660"), {"ticker": "035720", "side": "hold", "qty": 0}]
        kept, dropped = rb.enforce_cash_floor(signals, cash_pct=5.0, regime="bull")
        assert len(kept) == 2
        assert dropped == []

    def test_order_and_alignment_preserved(self):
        signals = [_sell("A"), _buy("B"), _sell("C")]
        kept, dropped = rb.enforce_cash_floor(signals, cash_pct=50.0, regime="bull")
        # Plenty of cash -> nothing dropped, original order preserved.
        assert [s["ticker"] for s in kept] == ["A", "B", "C"]
        assert dropped == []


# ---------------------------------------------------------------------------
# REQ-035-2 (d): prompt context line present (grep verification)
# ---------------------------------------------------------------------------
class TestPromptContextInjection:
    def _prompt(self, name: str) -> str:
        path = (
            Path(rb.__file__).resolve().parent / "prompts" / name
        )
        return path.read_text(encoding="utf-8")

    def test_decision_jinja_has_regime_line(self):
        sql = self._prompt("decision.jinja")
        assert "current_regime" in sql or "시장 regime" in sql

    def test_risk_jinja_has_regime_line(self):
        sql = self._prompt("risk.jinja")
        assert "current_regime" in sql or "시장 regime" in sql


# ---------------------------------------------------------------------------
# REQ-035-2 (d/f): decision.run / risk.run regime wiring
# ---------------------------------------------------------------------------
def _persona_result(response_json):
    return SimpleNamespace(
        persona_run_id=123,
        response_json=response_json,
        input_tokens=1,
        output_tokens=1,
        cost_krw=0.0,
    )


class TestDecisionRegimeWiring:
    """REQ-035-2(d): regime injected into prompt context; (f) regime_branch_applied
    added to response JSON and regime_at_decision stamped on persona_runs."""

    def test_decision_stamps_branch_applied_and_persists(self):
        res = _persona_result({"signals": []})
        with (
            patch.object(decision_persona, "is_cli_mode_active", return_value=True),
            patch.object(decision_persona, "call_persona_via_cli", return_value=res),
            patch.object(decision_persona, "render_prompt", return_value="SYS") as render,
            patch.object(
                decision_persona, "get_effective_regime", return_value=("bull", "risk-on")
            ),
            patch.object(decision_persona, "_stamp_regime_at_decision") as stamp,
            patch.object(decision_persona, "connection"),
        ):
            out, _sig_ids = decision_persona.run({"today": "2026-05-29"})

        # regime_branch_applied added to the response JSON (REQ-035-2f).
        assert out.response_json["regime_branch_applied"] == "bull"
        # regime snapshot persisted to persona_runs (REQ-035-2f).
        stamp.assert_called_once_with(123, "bull")
        # Prompt rendered with regime context vars (REQ-035-2d).
        ctx = render.call_args.kwargs
        assert ctx["current_regime"] == "bull"
        assert ctx["regime_cash_floor_pct"] == 20.0
        assert ctx["regime_confidence_delta"] == -0.05

    def test_decision_uses_explicit_input_regime_over_db(self):
        res = _persona_result({"signals": []})
        with (
            patch.object(decision_persona, "is_cli_mode_active", return_value=True),
            patch.object(decision_persona, "call_persona_via_cli", return_value=res),
            patch.object(decision_persona, "render_prompt", return_value="SYS"),
            patch.object(decision_persona, "get_effective_regime") as gdb,
            patch.object(decision_persona, "_stamp_regime_at_decision"),
            patch.object(decision_persona, "connection"),
        ):
            out, _ = decision_persona.run(
                {
                    "today": "2026-05-29",
                    "current_regime": "bear",
                    "current_risk_appetite": "risk-off",
                }
            )

        gdb.assert_not_called()  # explicit input wins, no extra DB read
        assert out.response_json["regime_branch_applied"] == "bear"


class TestRiskRegimeWiring:
    def test_risk_stamps_branch_applied_and_persists(self):
        res = _persona_result({"verdict": "APPROVE", "rationale": "ok"})
        with (
            patch.object(risk_persona, "is_cli_mode_active", return_value=True),
            patch.object(risk_persona, "call_persona_via_cli", return_value=res),
            patch.object(risk_persona, "render_prompt", return_value="SYS") as render,
            patch.object(risk_persona, "get_effective_regime", return_value=("bear", "risk-off")),
            patch.object(risk_persona, "_stamp_regime_at_decision") as stamp,
            patch.object(risk_persona, "connection"),
            patch.object(risk_persona, "audit"),
        ):
            out, _review_id, _verdict = risk_persona.run(
                {"today": "2026-05-29", "decision_signals": []}, decision_id=5
            )

        assert out.response_json["regime_branch_applied"] == "bear"
        stamp.assert_called_once_with(123, "bear")
        ctx = render.call_args.kwargs
        assert ctx["current_regime"] == "bear"
        assert ctx["regime_block_leverage"] is True
