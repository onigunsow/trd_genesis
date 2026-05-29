"""SPEC-TRADING-036 REQ-036-2 — bull mode (aggressive profile) tests.

Bull mode is the AGGRESSIVE profile SPEC-035 deferred. It is gated by the
3-AND condition (S-4): ``regime=='bull' AND NOT late_cycle_defense_active AND
trading_mode=='paper'``. The paper-only and late-cycle gates are HARD Python
guards (R-M2 / S-3) — never trusted to the prompt.

@MX:SPEC: SPEC-TRADING-036
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from trading.personas import decision as decision_persona
from trading.personas import regime_branch as rb
from trading.personas import risk as risk_persona

_BULL_RO = ("bull", "risk-on")


# ---------------------------------------------------------------------------
# REQ-036-2 (g) / S-4: 3-AND gate, read-time derived
# ---------------------------------------------------------------------------
class TestBullModeGate:
    # bull_mode_active(regime, late_cycle_defense_active, trading_mode)
    def test_active_when_all_three_true(self):
        assert rb.bull_mode_active("bull", False, "paper") is True

    def test_inactive_when_not_bull(self):
        assert rb.bull_mode_active("neutral", False, "paper") is False
        assert rb.bull_mode_active("bear", False, "paper") is False

    def test_inactive_in_live_mode_paper_only_guard(self):
        # R-M2: aggressive params MUST NOT apply in live.
        assert rb.bull_mode_active("bull", False, "live") is False

    def test_inactive_when_late_cycle_defense_active(self):
        # S-3 mutual exclusion: defence forces bull OFF regardless of mode.
        assert rb.bull_mode_active("bull", True, "paper") is False
        assert rb.bull_mode_active("bull", True, "live") is False


# ---------------------------------------------------------------------------
# REQ-036-2 (a/b): aggressive params (SPEC-016 original)
# ---------------------------------------------------------------------------
class TestBullParams:
    def test_target_holdings_and_cash_target(self):
        p = rb.bull_params()
        assert p.target_holdings_min == 1
        assert p.target_holdings_max == 2
        assert p.cash_target_min == 10
        assert p.cash_target_max == 20

    def test_holding_days_and_car_threshold(self):
        p = rb.bull_params()
        assert p.holding_days_min == 4
        assert p.holding_days_max == 10
        # event-CAR threshold strengthened from |1.5%| to |1.0%|.
        assert p.event_car_threshold == 1.0

    def test_risk_limit_uplift(self):
        p = rb.bull_params()
        assert p.sector_cap_uplift_pct == 10.0
        assert p.single_stock_uplift_pct == 10.0

    def test_bull_cash_floor_is_ten(self):
        # Aggressive floor (10%) — distinct from SPEC-035 conservative bull (20%).
        assert rb.BULL_MODE_CASH_FLOOR_PCT == 10.0


# ---------------------------------------------------------------------------
# REQ-036-2 (b parametrize AC): event-CAR threshold by mode
# ---------------------------------------------------------------------------
class TestEventCarThreshold:
    def test_bull_threshold_is_one(self):
        assert rb.event_car_threshold(bull_active=True) == 1.0

    def test_non_bull_threshold_is_one_point_five(self):
        assert rb.event_car_threshold(bull_active=False) == 1.5


# ---------------------------------------------------------------------------
# REQ-036-2 (e) / AC: hard cash-floor guard with bull 10% override
# ---------------------------------------------------------------------------
def _buy(t, qty=10):
    return {"ticker": t, "side": "buy", "qty": qty}


def _sell(t, qty=10):
    return {"ticker": t, "side": "sell", "qty": qty}


class TestBullCashFloorGuard:
    def test_bull_floor_override_blocks_below_ten(self):
        signals = [_buy("005930"), _sell("000660")]
        kept, dropped = rb.enforce_cash_floor(
            signals, cash_pct=8.0, regime="bull", floor_override=10.0
        )
        kept_tickers = {s["ticker"] for s in kept}
        assert "005930" not in kept_tickers  # buy blocked below 10%
        assert "000660" in kept_tickers      # sell never touched
        assert 0 in dropped

    def test_bull_floor_override_allows_at_ten(self):
        kept, dropped = rb.enforce_cash_floor(
            [_buy("005930")], cash_pct=12.0, regime="bull", floor_override=10.0
        )
        assert {s["ticker"] for s in kept} == {"005930"}
        assert dropped == []

    def test_backward_compatible_without_override(self):
        # SPEC-035 path: no override -> conservative bull floor (20%).
        kept, dropped = rb.enforce_cash_floor([_buy("005930")], cash_pct=15.0, regime="bull")
        assert kept == []
        assert dropped == [0]


# ---------------------------------------------------------------------------
# REQ-036-2 (f): ON/OFF transition Telegram alert
# ---------------------------------------------------------------------------
class TestBullTransitionAlert:
    def setup_method(self):
        rb._reset_bull_state()

    def test_alerts_on_off_to_on(self):
        with patch.object(rb, "system_briefing") as tg:
            sent = rb.maybe_notify_bull_transition(True)
        assert sent is True
        tg.assert_called_once()
        assert "ON" in tg.call_args.args[1] or "ON" in tg.call_args.args[0]

    def test_no_alert_when_unchanged(self):
        rb.maybe_notify_bull_transition(False)  # baseline: already off
        with patch.object(rb, "system_briefing") as tg:
            sent = rb.maybe_notify_bull_transition(False)
        assert sent is False
        tg.assert_not_called()

    def test_alerts_on_on_to_off(self):
        rb.maybe_notify_bull_transition(True)  # now ON
        with patch.object(rb, "system_briefing") as tg:
            sent = rb.maybe_notify_bull_transition(False)
        assert sent is True
        tg.assert_called_once()

    def test_transition_alert_swallows_telegram_failure(self):
        with patch.object(rb, "system_briefing", side_effect=RuntimeError("tg down")):
            # Must not raise even if Telegram is down.
            sent = rb.maybe_notify_bull_transition(True)
        assert sent is True


# ---------------------------------------------------------------------------
# REQ-036-2 (e): prompt context line present (grep verification)
# ---------------------------------------------------------------------------
class TestBullPromptInjection:
    def _prompt(self, name: str) -> str:
        return (Path(rb.__file__).resolve().parent / "prompts" / name).read_text(encoding="utf-8")

    def test_decision_jinja_has_bull_line(self):
        text = self._prompt("decision.jinja")
        assert "bull_mode_active" in text
        assert "강세장" in text or "불장" in text

    def test_risk_jinja_has_bull_line(self):
        text = self._prompt("risk.jinja")
        assert "bull_mode_active" in text


# ---------------------------------------------------------------------------
# REQ-036-2 (a/c/d): decision/risk wiring — bull ctx injected only when active
# ---------------------------------------------------------------------------
def _persona_result(response_json):
    return SimpleNamespace(
        persona_run_id=123, response_json=response_json,
        input_tokens=1, output_tokens=1, cost_krw=0.0,
    )


class TestDecisionBullWiring:
    def test_bull_context_injected_when_paper_bull_no_defense(self):
        res = _persona_result({"signals": []})
        state = {"trading_mode": "paper", "late_cycle_defense_active": False}
        with (
            patch.object(decision_persona, "is_cli_mode_active", return_value=True),
            patch.object(decision_persona, "call_persona_via_cli", return_value=res),
            patch.object(decision_persona, "render_prompt", return_value="SYS") as render,
            patch.object(decision_persona, "get_effective_regime", return_value=_BULL_RO),
            patch.object(decision_persona, "get_system_state", return_value=state),
            patch.object(decision_persona, "_stamp_regime_at_decision"),
            patch.object(decision_persona, "connection"),
        ):
            decision_persona.run({"today": "2026-05-29"})
        ctx = render.call_args.kwargs
        assert ctx["bull_mode_active"] is True
        assert ctx["bull_target_holdings_min"] == 1
        assert ctx["bull_target_holdings_max"] == 2
        assert ctx["bull_cash_target_min"] == 10
        assert ctx["bull_cash_target_max"] == 20

    def test_bull_context_off_in_live_even_when_regime_bull(self):
        # R-M2 negative test: live + bull regime -> aggressive NOT applied;
        # falls back to SPEC-035 conservative bull (cash floor 20%).
        res = _persona_result({"signals": []})
        state = {"trading_mode": "live", "late_cycle_defense_active": False}
        with (
            patch.object(decision_persona, "is_cli_mode_active", return_value=True),
            patch.object(decision_persona, "call_persona_via_cli", return_value=res),
            patch.object(decision_persona, "render_prompt", return_value="SYS") as render,
            patch.object(decision_persona, "get_effective_regime", return_value=_BULL_RO),
            patch.object(decision_persona, "get_system_state", return_value=state),
            patch.object(decision_persona, "_stamp_regime_at_decision"),
            patch.object(decision_persona, "connection"),
        ):
            decision_persona.run({"today": "2026-05-29"})
        ctx = render.call_args.kwargs
        assert ctx["bull_mode_active"] is False
        # Conservative bull floor still in effect (SPEC-035).
        assert ctx["regime_cash_floor_pct"] == 20.0

    def test_bull_context_off_when_late_cycle_defense_active(self):
        # S-3: defence forces bull OFF.
        res = _persona_result({"signals": []})
        state = {"trading_mode": "paper", "late_cycle_defense_active": True}
        with (
            patch.object(decision_persona, "is_cli_mode_active", return_value=True),
            patch.object(decision_persona, "call_persona_via_cli", return_value=res),
            patch.object(decision_persona, "render_prompt", return_value="SYS") as render,
            patch.object(decision_persona, "get_effective_regime", return_value=_BULL_RO),
            patch.object(decision_persona, "get_system_state", return_value=state),
            patch.object(decision_persona, "_stamp_regime_at_decision"),
            patch.object(decision_persona, "connection"),
        ):
            decision_persona.run({"today": "2026-05-29"})
        ctx = render.call_args.kwargs
        assert ctx["bull_mode_active"] is False

    def test_state_read_failure_falls_back_to_no_bull(self):
        # Defensive: if system_state read fails, bull mode must NOT activate.
        res = _persona_result({"signals": []})
        with (
            patch.object(decision_persona, "is_cli_mode_active", return_value=True),
            patch.object(decision_persona, "call_persona_via_cli", return_value=res),
            patch.object(decision_persona, "render_prompt", return_value="SYS") as render,
            patch.object(decision_persona, "get_effective_regime", return_value=_BULL_RO),
            patch.object(decision_persona, "get_system_state", side_effect=RuntimeError("db down")),
            patch.object(decision_persona, "_stamp_regime_at_decision"),
            patch.object(decision_persona, "connection"),
        ):
            decision_persona.run({"today": "2026-05-29"})
        ctx = render.call_args.kwargs
        assert ctx["bull_mode_active"] is False


class TestRiskBullWiring:
    def test_risk_injects_bull_context_when_active(self):
        res = _persona_result({"verdict": "APPROVE", "rationale": "ok"})
        state = {"trading_mode": "paper", "late_cycle_defense_active": False}
        with (
            patch.object(risk_persona, "is_cli_mode_active", return_value=True),
            patch.object(risk_persona, "call_persona_via_cli", return_value=res),
            patch.object(risk_persona, "render_prompt", return_value="SYS") as render,
            patch.object(risk_persona, "get_effective_regime", return_value=_BULL_RO),
            patch.object(risk_persona, "get_system_state", return_value=state),
            patch.object(risk_persona, "_stamp_regime_at_decision"),
            patch.object(risk_persona, "connection"),
            patch.object(risk_persona, "audit"),
        ):
            risk_persona.run({"today": "2026-05-29", "decision_signals": []}, decision_id=5)
        ctx = render.call_args.kwargs
        assert ctx["bull_mode_active"] is True
        assert ctx["bull_sector_cap_uplift_pct"] == 10.0
