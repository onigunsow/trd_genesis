"""SPEC-TRADING-031 — orchestrator halt-gate wiring tests.

The three halt gates (pre_market / intraday / event-trigger) sit deep in the
"Risk + execute per signal" stage, reachable only after the full Macro/Micro/
Decision persona pipeline has run. Standing up that pipeline just to exercise a
3-line gate is heavy and brittle, so the cooldown *behaviour* is verified by
exercising the helper directly (tests/risk/test_halt_notify_throttle.py).

These tests verify the gate *wiring* structurally:
- each gate routes the '매매 정지' briefing through circuit_breaker.maybe_notify_halt
  (REQ-031-1/2) and NOT through tg.system_briefing directly (REQ-031-5 / AC-5),
- each gate logs the skip on every halted cycle (REQ-031-4b / AC-4),
- each gate still returns immediately (REQ-031-4a / AC-4).

@MX:SPEC: SPEC-TRADING-031
"""

from __future__ import annotations

import ast
import inspect

from trading.personas import orchestrator as orch

_GATE_FUNCS = [
    orch.run_pre_market_cycle,
    orch.run_intraday_cycle,
    orch.run_event_trigger_cycle,
]


def _halt_gate_node(func) -> ast.If:
    """Return the `if state["halt_state"]:` If-node from a cycle function body."""
    src = inspect.getsource(func)
    tree = ast.parse(_dedent(src))
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            # Match: state["halt_state"]
            if (
                isinstance(test, ast.Subscript)
                and isinstance(test.value, ast.Name)
                and test.value.id == "state"
                and isinstance(getattr(test.slice, "value", None), str)
                and test.slice.value == "halt_state"
            ):
                return node
    raise AssertionError(f"no halt_state gate found in {func.__name__}")


def _dedent(src: str) -> str:
    import textwrap

    return textwrap.dedent(src)


def _calls(node: ast.AST) -> list[str]:
    """Return dotted call targets (e.g. 'tg.system_briefing', 'LOG.info') in node."""
    out: list[str] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute):
                base = f.value
                if isinstance(base, ast.Name):
                    out.append(f"{base.id}.{f.attr}")
                elif isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
                    out.append(f"{base.value.id}.{base.attr}.{f.attr}")
            elif isinstance(f, ast.Name):
                out.append(f.id)
    return out


class TestHaltGateRoutesThroughHelper:
    """REQ-031-1/2, AC-5 — gate sends via helper, never tg.system_briefing."""

    def test_all_gates_call_helper(self):
        for func in _GATE_FUNCS:
            gate = _halt_gate_node(func)
            calls = _calls(gate)
            assert "circuit_breaker.maybe_notify_halt" in calls, (
                f"{func.__name__} gate must call circuit_breaker.maybe_notify_halt; "
                f"found {calls}"
            )

    def test_no_gate_calls_tg_system_briefing_directly(self):
        for func in _GATE_FUNCS:
            gate = _halt_gate_node(func)
            calls = _calls(gate)
            assert "tg.system_briefing" not in calls, (
                f"{func.__name__} gate must NOT call tg.system_briefing directly "
                f"(cooldown bypass); found {calls}"
            )


class TestHaltGateLogsEverySkip:
    """REQ-031-4b, AC-4 — every halted cycle logs the skip."""

    def test_all_gates_log_skip(self):
        for func in _GATE_FUNCS:
            gate = _halt_gate_node(func)
            calls = _calls(gate)
            assert any(c in ("LOG.info", "LOG.warning") for c in calls), (
                f"{func.__name__} gate must log the skip (LOG.info); found {calls}"
            )


class TestHaltGateStillReturns:
    """REQ-031-4a, AC-4 — gate returns immediately (skips trading)."""

    def test_all_gates_return_in_body(self):
        for func in _GATE_FUNCS:
            gate = _halt_gate_node(func)
            has_return = any(isinstance(n, ast.Return) for n in ast.walk(gate))
            assert has_return, f"{func.__name__} gate must return immediately"
