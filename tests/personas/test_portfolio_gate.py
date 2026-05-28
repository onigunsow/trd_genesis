"""SPEC-TRADING-034 — dormant portfolio persona cycle wiring tests.

Covers the shared portfolio-adjustment gate (`_apply_portfolio_adjustment`) and
the portfolio persona CLI conversion (REQ-034-9). The gate is a buy-only,
binding sizing layer inserted between decision and risk/execute in all three
cycles.

Test isolation: portfolio.run / call_persona_via_cli / is_cli_mode_active /
system_briefing / audit are mocked. No network, no real `claude -p`, no DB/KIS.

@MX:SPEC: SPEC-TRADING-034
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from unittest.mock import patch

from trading.personas import portfolio_gate
from trading.personas.base import PersonaResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _buy(ticker: str, qty: int, sid: int) -> tuple[dict, int]:
    return ({"ticker": ticker, "side": "buy", "qty": qty}, sid)


def _sell(ticker: str, qty: int, sid: int) -> tuple[dict, int]:
    return ({"ticker": ticker, "side": "sell", "qty": qty}, sid)


def _persona_result(response_json) -> PersonaResult:
    """Build a minimal PersonaResult carrying response_json (the only field the
    gate reads)."""
    return PersonaResult(
        persona_run_id=1,
        response_text="",
        response_json=response_json,
        input_tokens=0,
        output_tokens=0,
        cost_krw=0.0,
        latency_ms=0,
    )


def _split(pairs):
    """Split list[(signal, sid)] into (signals, sig_ids)."""
    signals = [p[0] for p in pairs]
    sig_ids = [p[1] for p in pairs]
    return signals, sig_ids


# Common kwargs for _apply_portfolio_adjustment with holdings >= 5.
def _kwargs(holdings_count: int = 6):
    return {
        "holdings": [{"ticker": "000001"}] * holdings_count,
        "holdings_count": holdings_count,
        "total_assets": 10_000_000,
        "cash_pct": 30.0,
        "today": "2026-05-28",
        "cycle_kind": "pre_market",
    }


# ===========================================================================
# Pure apply-mapping unit tests (no portfolio.run; exercise the pure helper)
# ===========================================================================


class TestApplyMappingPure:
    """REQ-034-2/3/4 — the pure adjusted/rejected apply-mapping logic."""

    def test_qty_adjusted_reduces_qty(self):
        buys = [_buy("X", 10, 1)]
        out_buys, dropped = portfolio_gate._apply_mapping(
            buys,
            adjusted={"X": {"ticker": "X", "qty_adjusted": 4}},
            rejected=set(),
        )
        assert len(out_buys) == 1
        sig, sid = out_buys[0]
        assert sig["qty"] == 4
        assert sid == 1
        assert dropped == []

    def test_rejected_drops_buy_and_sig_id(self):
        buys = [_buy("Y", 5, 2)]
        out_buys, dropped = portfolio_gate._apply_mapping(
            buys, adjusted={}, rejected={"Y"},
        )
        assert out_buys == []
        assert dropped == [2]

    def test_qty_adjusted_zero_drops(self):
        buys = [_buy("X", 10, 1)]
        out_buys, dropped = portfolio_gate._apply_mapping(
            buys,
            adjusted={"X": {"ticker": "X", "qty_adjusted": 0}},
            rejected=set(),
        )
        assert out_buys == []
        assert dropped == [1]

    def test_qty_adjusted_negative_drops(self):
        buys = [_buy("X", 10, 1)]
        out_buys, dropped = portfolio_gate._apply_mapping(
            buys,
            adjusted={"X": {"ticker": "X", "qty_adjusted": -3}},
            rejected=set(),
        )
        assert out_buys == []
        assert dropped == [1]

    def test_unmatched_adjusted_ticker_ignored(self):
        buys = [_buy("X", 10, 1)]
        out_buys, dropped = portfolio_gate._apply_mapping(
            buys,
            adjusted={"999999": {"ticker": "999999", "qty_adjusted": 1}},
            rejected=set(),
        )
        assert len(out_buys) == 1
        assert out_buys[0][0]["qty"] == 10  # unchanged
        assert dropped == []

    def test_missing_qty_adjusted_key_leaves_unchanged(self):
        # Defensive: adjusted entry without qty_adjusted -> no-op (A-4).
        buys = [_buy("X", 10, 1)]
        out_buys, dropped = portfolio_gate._apply_mapping(
            buys,
            adjusted={"X": {"ticker": "X"}},
            rejected=set(),
        )
        assert out_buys[0][0]["qty"] == 10
        assert dropped == []

    def test_reject_takes_priority_over_adjust(self):
        buys = [_buy("X", 10, 1)]
        out_buys, dropped = portfolio_gate._apply_mapping(
            buys,
            adjusted={"X": {"ticker": "X", "qty_adjusted": 4}},
            rejected={"X"},
        )
        assert out_buys == []
        assert dropped == [1]


# ===========================================================================
# _apply_portfolio_adjustment integration (mock portfolio.run)
# ===========================================================================


class TestGateAdjustments:
    """REQ-034-1/2 — holdings>=5 + buys -> portfolio runs and adjusts."""

    def test_qty_reduced_when_portfolio_reduces(self):
        # AC-1
        signals, sig_ids = _split([_buy("X", 10, 11)])
        pj = {
            "adjusted_signals": [
                {"ticker": "X", "side": "buy", "qty_original": 10,
                 "qty_adjusted": 4, "rationale": "섹터 편중"},
            ],
            "rejected": [],
        }
        with (
            patch.object(portfolio_gate.portfolio, "run",
                         return_value=_persona_result(pj)) as m_run,
            patch.object(portfolio_gate.tg, "system_briefing") as m_brief,
            patch.object(portfolio_gate, "audit") as m_audit,
        ):
            new_signals, new_sig_ids = portfolio_gate._apply_portfolio_adjustment(
                signals, sig_ids, **_kwargs(),
            )
        assert m_run.call_count == 1
        # buy-only input: decision_signals contains exactly the buy signal
        passed_input = m_run.call_args.args[0]
        assert [s["ticker"] for s in passed_input["decision_signals"]] == ["X"]
        # qty bound to adjusted value
        assert new_signals[0]["qty"] == 4
        assert new_sig_ids == [11]
        # AC-9: non-trivial adjustment -> telegram + audit
        assert m_brief.call_count == 1
        assert m_brief.call_args.args[0] == "포트폴리오 조정"
        assert m_audit.call_count == 1
        assert m_audit.call_args.args[0] == "PORTFOLIO_ADJUSTMENT"

    def test_rejected_buy_dropped_and_recorded(self):
        # AC-2
        signals, sig_ids = _split([_buy("Y", 5, 22)])
        pj = {"adjusted_signals": [], "rejected": [{"ticker": "Y", "reason": "섹터 편중"}]}
        res_rejected: list[int] = []
        with (
            patch.object(portfolio_gate.portfolio, "run",
                         return_value=_persona_result(pj)),
            patch.object(portfolio_gate.tg, "system_briefing"),
            patch.object(portfolio_gate, "audit") as m_audit,
        ):
            new_signals, new_sig_ids = portfolio_gate._apply_portfolio_adjustment(
                signals, sig_ids, res_rejected=res_rejected, **_kwargs(),
            )
        assert new_signals == []
        assert new_sig_ids == []
        assert res_rejected == [22]  # appended to res.rejected
        # audit records the rejection
        details = m_audit.call_args.kwargs.get("details") or m_audit.call_args.args[2]
        assert any(r.get("ticker") == "Y" for r in details["rejected"])

    def test_qty_adjusted_zero_drops_via_gate(self):
        # AC-6
        signals, sig_ids = _split([_buy("X", 10, 1)])
        pj = {
            "adjusted_signals": [{"ticker": "X", "qty_original": 10, "qty_adjusted": 0}],
            "rejected": [],
        }
        res_rejected: list[int] = []
        with (
            patch.object(portfolio_gate.portfolio, "run",
                         return_value=_persona_result(pj)),
            patch.object(portfolio_gate.tg, "system_briefing"),
            patch.object(portfolio_gate, "audit"),
        ):
            new_signals, new_sig_ids = portfolio_gate._apply_portfolio_adjustment(
                signals, sig_ids, res_rejected=res_rejected, **_kwargs(),
            )
        assert new_signals == []
        assert new_sig_ids == []
        assert res_rejected == [1]

    def test_unmatched_ticker_ignored_via_gate(self):
        # AC-7
        signals, sig_ids = _split([_buy("X", 10, 1)])
        pj = {
            "adjusted_signals": [{"ticker": "999999", "qty_adjusted": 1}],
            "rejected": [{"ticker": "888888", "reason": "x"}],
        }
        with (
            patch.object(portfolio_gate.portfolio, "run",
                         return_value=_persona_result(pj)),
            patch.object(portfolio_gate.tg, "system_briefing") as m_brief,
            patch.object(portfolio_gate, "audit") as m_audit,
        ):
            new_signals, new_sig_ids = portfolio_gate._apply_portfolio_adjustment(
                signals, sig_ids, **_kwargs(),
            )
        assert new_signals[0]["qty"] == 10  # unchanged
        assert new_sig_ids == [1]
        # no non-trivial change -> no telegram/audit
        assert m_brief.call_count == 0
        assert m_audit.call_count == 0


class TestGateSkipAndBuyOnly:
    """REQ-034-4/5 — sells untouched, holdings<5 skips."""

    def test_holdings_below_5_skips_portfolio(self):
        # AC-4
        signals, sig_ids = _split([_buy("X", 10, 1), _buy("Z", 2, 2)])
        with patch.object(portfolio_gate.portfolio, "run") as m_run:
            new_signals, new_sig_ids = portfolio_gate._apply_portfolio_adjustment(
                signals, sig_ids, **_kwargs(holdings_count=3),
            )
        assert m_run.call_count == 0  # Sonnet cost 0
        assert new_signals == signals
        assert new_sig_ids == sig_ids

    def test_no_buys_skips_portfolio(self):
        signals, sig_ids = _split([_sell("S", 3, 9)])
        with patch.object(portfolio_gate.portfolio, "run") as m_run:
            new_signals, new_sig_ids = portfolio_gate._apply_portfolio_adjustment(
                signals, sig_ids, **_kwargs(),
            )
        assert m_run.call_count == 0
        assert new_signals == signals
        assert new_sig_ids == sig_ids

    def test_sells_untouched_buy_only_input(self):
        # AC-3
        signals, sig_ids = _split([_buy("X", 10, 1), _sell("S", 3, 2)])
        pj = {
            "adjusted_signals": [{"ticker": "X", "qty_adjusted": 4}],
            "rejected": [],
        }
        with (
            patch.object(portfolio_gate.portfolio, "run",
                         return_value=_persona_result(pj)) as m_run,
            patch.object(portfolio_gate.tg, "system_briefing"),
            patch.object(portfolio_gate, "audit"),
        ):
            new_signals, new_sig_ids = portfolio_gate._apply_portfolio_adjustment(
                signals, sig_ids, **_kwargs(),
            )
        # buy-only input to persona (sell S not passed)
        passed_input = m_run.call_args.args[0]
        assert [s["ticker"] for s in passed_input["decision_signals"]] == ["X"]
        # sell S preserved unchanged, alignment kept
        sell_idx = new_signals.index(next(s for s in new_signals if s["side"] == "sell"))
        assert new_signals[sell_idx]["qty"] == 3
        assert new_sig_ids[sell_idx] == 2
        # buy X adjusted to 4
        buy_sig = next(s for s in new_signals if s["side"] == "buy")
        assert buy_sig["qty"] == 4

    def test_alignment_preserved_multiple_signals(self):
        signals, sig_ids = _split([
            _buy("X", 10, 1), _sell("S", 3, 2), _buy("W", 7, 3),
        ])
        pj = {"adjusted_signals": [{"ticker": "W", "qty_adjusted": 2}], "rejected": []}
        with (
            patch.object(portfolio_gate.portfolio, "run",
                         return_value=_persona_result(pj)),
            patch.object(portfolio_gate.tg, "system_briefing"),
            patch.object(portfolio_gate, "audit"),
        ):
            new_signals, new_sig_ids = portfolio_gate._apply_portfolio_adjustment(
                signals, sig_ids, **_kwargs(),
            )
        # Every signal still paired with its original sig_id.
        for sig, sid in zip(new_signals, new_sig_ids, strict=True):
            if sig["ticker"] == "X":
                assert sid == 1
            elif sig["ticker"] == "S":
                assert sid == 2
                assert sig["qty"] == 3
            elif sig["ticker"] == "W":
                assert sid == 3
                assert sig["qty"] == 2


class TestGateFailSafe:
    """REQ-034-6 — persona failure -> unadjusted fallback + notify."""

    def test_exception_falls_back_unadjusted(self):
        # AC-5 (a)
        signals, sig_ids = _split([_buy("X", 10, 1)])
        with (
            patch.object(portfolio_gate.portfolio, "run",
                         side_effect=RuntimeError("ANTHROPIC_API_KEY missing")),
            patch.object(portfolio_gate.tg, "system_error") as m_err,
        ):
            new_signals, new_sig_ids = portfolio_gate._apply_portfolio_adjustment(
                signals, sig_ids, **_kwargs(),
            )
        assert new_signals == signals
        assert new_sig_ids == sig_ids
        assert m_err.call_count == 1  # operator notified

    def test_response_json_none_falls_back(self):
        # AC-5 (b)
        signals, sig_ids = _split([_buy("X", 10, 1)])
        with (
            patch.object(portfolio_gate.portfolio, "run",
                         return_value=_persona_result(None)),
            patch.object(portfolio_gate.tg, "system_error"),
        ):
            new_signals, new_sig_ids = portfolio_gate._apply_portfolio_adjustment(
                signals, sig_ids, **_kwargs(),
            )
        assert new_signals == signals
        assert new_sig_ids == sig_ids

    def test_missing_required_keys_falls_back(self):
        # AC-5 (c): response_json missing both adjusted_signals and rejected
        signals, sig_ids = _split([_buy("X", 10, 1)])
        with (
            patch.object(portfolio_gate.portfolio, "run",
                         return_value=_persona_result({"unexpected": 1})),
            patch.object(portfolio_gate.tg, "system_error"),
        ):
            new_signals, new_sig_ids = portfolio_gate._apply_portfolio_adjustment(
                signals, sig_ids, **_kwargs(),
            )
        assert new_signals == signals
        assert new_sig_ids == sig_ids

    def test_telegram_failure_swallowed(self):
        # AC-5: telegram send failure must not raise out of the gate
        signals, sig_ids = _split([_buy("X", 10, 1)])
        with (
            patch.object(portfolio_gate.portfolio, "run",
                         side_effect=RuntimeError("boom")),
            patch.object(portfolio_gate.tg, "system_error",
                         side_effect=RuntimeError("telegram down")),
        ):
            new_signals, new_sig_ids = portfolio_gate._apply_portfolio_adjustment(
                signals, sig_ids, **_kwargs(),
            )
        assert new_signals == signals
        assert new_sig_ids == sig_ids


# ===========================================================================
# AC-12 — portfolio.run CLI conversion (REQ-034-9)
# ===========================================================================


class TestPortfolioCliConversion:
    """REQ-034-9 / AC-12 — cli_only_mode routes via call_persona_via_cli."""

    def test_cli_mode_uses_via_cli_not_api(self):
        from trading.personas import portfolio

        pj = {"adjusted_signals": [], "rejected": []}
        with (
            patch.object(portfolio, "is_cli_mode_active", return_value=True),
            patch.object(portfolio, "call_persona_via_cli",
                         return_value=_persona_result(pj)) as m_cli,
            patch.object(portfolio, "call_persona") as m_api,
        ):
            res = portfolio.run(
                {"today": "2026-05-28", "decision_signals": [],
                 "holdings": [], "holdings_count": 6,
                 "total_assets": 1, "cash_pct": 30.0},
                cycle_kind="pre_market",
            )
        assert m_cli.call_count == 1
        assert m_api.call_count == 0  # no paid API call
        assert m_cli.call_args.kwargs.get("expect_json") is True
        assert res.response_json == pj

    def test_api_mode_uses_call_persona(self):
        from trading.personas import portfolio

        pj = {"adjusted_signals": [], "rejected": []}
        with (
            patch.object(portfolio, "is_cli_mode_active", return_value=False),
            patch.object(portfolio, "call_persona_via_cli") as m_cli,
            patch.object(portfolio, "call_persona",
                         return_value=_persona_result(pj)) as m_api,
        ):
            res = portfolio.run(
                {"today": "2026-05-28", "decision_signals": [],
                 "holdings": [], "holdings_count": 6,
                 "total_assets": 1, "cash_pct": 30.0},
                cycle_kind="pre_market",
            )
        assert m_api.call_count == 1
        assert m_cli.call_count == 0
        assert m_api.call_args.kwargs.get("expect_json") is True
        assert res.response_json == pj


# ===========================================================================
# AC-8 — three-cycle insertion (static AST verification, matches
# test_halt_gate_throttle precedent — no full cycle stand-up)
# ===========================================================================


def _cycle_calls(func) -> list[str]:
    src = textwrap.dedent(inspect.getsource(func))
    tree = ast.parse(src)
    out: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            out.append(n.func.id)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            base = n.func.value
            if isinstance(base, ast.Name):
                out.append(f"{base.id}.{n.func.attr}")
    return out


class TestThreeCycleInsertion:
    """AC-8 — all three cycles call the gate."""

    def test_all_cycles_call_apply_portfolio_adjustment(self):
        from trading.personas import orchestrator as orch

        for func in (orch.run_pre_market_cycle,
                     orch.run_intraday_cycle,
                     orch.run_event_trigger_cycle):
            calls = _cycle_calls(func)
            assert "_apply_portfolio_adjustment" in calls, (
                f"{func.__name__} must call _apply_portfolio_adjustment; got {calls}"
            )

    def test_gate_called_before_execute_loop(self):
        """The gate must precede the `for sig, decision_id in zip(...)` loop so
        the loop iterates the adjusted lists."""
        from trading.personas import orchestrator as orch

        for func in (orch.run_pre_market_cycle,
                     orch.run_intraday_cycle,
                     orch.run_event_trigger_cycle):
            src = textwrap.dedent(inspect.getsource(func))
            tree = ast.parse(src)
            gate_line = None
            loop_line = None
            for n in ast.walk(tree):
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                        and n.func.id == "_apply_portfolio_adjustment"):
                    gate_line = n.lineno
                if isinstance(n, ast.For):
                    # the execute loop iterates zip(signals, sig_ids, ...)
                    it = n.iter
                    if (isinstance(it, ast.Call) and isinstance(it.func, ast.Name)
                            and it.func.id == "zip"):
                        args = it.args
                        names = {a.id for a in args if isinstance(a, ast.Name)}
                        if {"signals", "sig_ids"} <= names and loop_line is None:
                            loop_line = n.lineno
            assert gate_line is not None, f"{func.__name__}: no gate call"
            assert loop_line is not None, f"{func.__name__}: no execute loop"
            assert gate_line < loop_line, (
                f"{func.__name__}: gate (line {gate_line}) must precede execute "
                f"loop (line {loop_line})"
            )
