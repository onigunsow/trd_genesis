"""SPEC-TRADING-032 — pre-market auto-resume tests.

Verifies AC-1 .. AC-9 from .moai/specs/SPEC-TRADING-032/acceptance.md:

- AC-1: daily_count-only automatic trip -> auto resume + "자동 재개" + resumed audit.
- AC-2: single_order / per_ticker (no loss) -> auto resume.
- AC-3: daily_loss trip -> HOLD + "수동 검토 필요" + held audit.
- AC-4: manual /halt -> HOLD (capital-preservation hard rule).
- AC-5: daily_count + daily_loss mixed -> HOLD (loss present dominates).
- AC-6: not halted -> no-op, NO telegram.
- AC-7: undeterminable active trip (None / unknown reason / malformed breaches)
  -> HOLD + "수동 검토 필요".
- AC-9: the new module only calls/queries circuit_breaker.reset — it does not
  redefine trip()/reset()/limits (no-regression, asserted structurally).

classify_halt is a pure function (no I/O) tested directly for every branch.
run_premarket_auto_resume is tested with get_system_state, the active-trip fetch
helper, circuit_breaker.reset, system_briefing and audit all mocked — no network,
no DB.

@MX:SPEC: SPEC-TRADING-032
"""

from __future__ import annotations

from unittest.mock import patch

from trading.risk import auto_resume

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_DAILY_COUNT = "daily_count: 오늘 주문 10 → 한도 10"
_DAILY_LOSS = "daily_loss: 오늘 손익 -3.20% ≤ 한도 -3.00%"
_SINGLE_ORDER = "single_order: 주문금액 1,000,000 > 한도 500,000"
_PER_TICKER = "per_ticker: 005930 예상 보유(수수료 포함) 9,000,000 > 한도 8,000,000"


def _auto_trip(breaches: list) -> dict:
    """Active-trip details dict for an automatic pre-order limit breach."""
    return {"reason": "pre-order limit breach", "breaches": breaches}


def _manual_trip(reason: str = "manual /halt") -> dict:
    return {"reason": reason, "actor": "operator"}


# ===========================================================================
# classify_halt — pure-function branches
# ===========================================================================


class TestClassifyHalt:
    """REQ-032-2 / REQ-032-3 / REQ-032-4 — every classifier branch."""

    def test_not_halted_returns_no_op(self):
        """halt_state=False -> (False, 'not_halted', '')."""
        should_resume, cause, _detail = auto_resume.classify_halt(False, None)
        assert should_resume is False
        assert cause == "not_halted"

    def test_active_trip_none_undeterminable(self):
        """halt_state=True but no active trip -> HOLD, undeterminable (REQ-032-3c)."""
        should_resume, cause, _ = auto_resume.classify_halt(True, None)
        assert should_resume is False
        assert cause == "undeterminable"

    def test_manual_halt_holds(self):
        """reason startswith 'manual' -> HOLD (REQ-032-3a / AC-4)."""
        should_resume, cause, detail = auto_resume.classify_halt(
            True, _manual_trip("manual /halt")
        )
        assert should_resume is False
        assert cause == "manual"
        assert detail == "manual /halt"

    def test_manual_cli_halt_holds(self):
        """'manual cli /halt' also holds (AC-4 variant)."""
        should_resume, cause, _ = auto_resume.classify_halt(
            True, _manual_trip("manual cli /halt")
        )
        assert should_resume is False
        assert cause == "manual"

    def test_unknown_reason_holds(self):
        """reason that is neither manual nor the limit-breach literal -> HOLD."""
        should_resume, cause, _ = auto_resume.classify_halt(
            True, {"reason": "something weird", "breaches": [_DAILY_COUNT]}
        )
        assert should_resume is False
        assert cause == "unknown_reason"

    def test_breaches_missing_undeterminable(self):
        """limit-breach reason but breaches key absent -> HOLD (malformed)."""
        should_resume, cause, _ = auto_resume.classify_halt(
            True, {"reason": "pre-order limit breach"}
        )
        assert should_resume is False
        assert cause == "undeterminable"

    def test_breaches_empty_list_undeterminable(self):
        """limit-breach reason but breaches is an empty list -> HOLD."""
        should_resume, cause, _ = auto_resume.classify_halt(True, _auto_trip([]))
        assert should_resume is False
        assert cause == "undeterminable"

    def test_breaches_not_a_list_undeterminable(self):
        """breaches is a non-list (malformed) -> HOLD."""
        should_resume, cause, _ = auto_resume.classify_halt(
            True, {"reason": "pre-order limit breach", "breaches": "daily_count: ..."}
        )
        assert should_resume is False
        assert cause == "undeterminable"

    def test_daily_loss_holds(self):
        """daily_loss breach -> HOLD (REQ-032-3b / AC-3)."""
        should_resume, cause, _ = auto_resume.classify_halt(
            True, _auto_trip([_DAILY_LOSS])
        )
        assert should_resume is False
        assert cause == "daily_loss"

    def test_daily_count_plus_daily_loss_holds(self):
        """Mixed daily_count + daily_loss -> HOLD (loss dominates, AC-5)."""
        should_resume, cause, _ = auto_resume.classify_halt(
            True, _auto_trip([_DAILY_COUNT, _DAILY_LOSS])
        )
        assert should_resume is False
        assert cause == "daily_loss"

    def test_daily_count_only_resumes(self):
        """daily_count only -> RESUME (REQ-032-2 / AC-1)."""
        should_resume, cause, detail = auto_resume.classify_halt(
            True, _auto_trip([_DAILY_COUNT])
        )
        assert should_resume is True
        assert "daily_count" in cause
        assert _DAILY_COUNT in detail

    def test_single_order_only_resumes(self):
        """single_order only (no loss) -> RESUME (AC-2)."""
        should_resume, cause, _ = auto_resume.classify_halt(
            True, _auto_trip([_SINGLE_ORDER])
        )
        assert should_resume is True
        assert "single_order" in cause

    def test_per_ticker_only_resumes(self):
        """per_ticker only (no loss) -> RESUME (AC-2)."""
        should_resume, cause, _ = auto_resume.classify_halt(
            True, _auto_trip([_PER_TICKER])
        )
        assert should_resume is True
        assert "per_ticker" in cause

    def test_benign_combo_resumes(self):
        """daily_count + single_order (no loss) -> RESUME (REQ-032-2a)."""
        should_resume, cause, _ = auto_resume.classify_halt(
            True, _auto_trip([_DAILY_COUNT, _SINGLE_ORDER])
        )
        assert should_resume is True
        assert "daily_count" in cause
        assert "single_order" in cause


# ===========================================================================
# run_premarket_auto_resume — entry function I/O orchestration
# ===========================================================================


class TestRunPremarketAutoResumeResume:
    """AC-1 / AC-2 — benign automatic trip -> auto resume path."""

    def test_daily_count_resume_calls_reset_and_briefs(self):
        with (
            patch.object(
                auto_resume, "get_system_state", return_value={"halt_state": True}
            ),
            patch.object(
                auto_resume, "_fetch_active_trip", return_value=_auto_trip([_DAILY_COUNT])
            ),
            patch.object(auto_resume.circuit_breaker, "reset") as reset,
            patch.object(auto_resume, "system_briefing") as brief,
            patch.object(auto_resume, "audit") as audit,
        ):
            auto_resume.run_premarket_auto_resume()

        # reset() called exactly once with the auto-resume actor (AC-1).
        reset.assert_called_once_with(actor="auto_resume_premarket")
        # "자동 재개" briefing sent exactly once.
        brief.assert_called_once()
        assert brief.call_args.args[0] == "자동 재개"
        # audit decision = resumed.
        audit.assert_called_once()
        assert audit.call_args.args[0] == "AUTO_RESUME_PREMARKET"
        assert audit.call_args.kwargs["details"]["decision"] == "resumed"

    def test_single_order_resume(self):
        with (
            patch.object(
                auto_resume, "get_system_state", return_value={"halt_state": True}
            ),
            patch.object(
                auto_resume, "_fetch_active_trip", return_value=_auto_trip([_SINGLE_ORDER])
            ),
            patch.object(auto_resume.circuit_breaker, "reset") as reset,
            patch.object(auto_resume, "system_briefing") as brief,
            patch.object(auto_resume, "audit") as audit,
        ):
            auto_resume.run_premarket_auto_resume()

        reset.assert_called_once_with(actor="auto_resume_premarket")
        assert brief.call_args.args[0] == "자동 재개"
        assert audit.call_args.kwargs["details"]["decision"] == "resumed"


class TestRunPremarketAutoResumeHold:
    """AC-3 / AC-4 / AC-5 / AC-7 — HOLD paths -> no reset, manual-review brief."""

    def test_daily_loss_holds(self):
        with (
            patch.object(
                auto_resume, "get_system_state", return_value={"halt_state": True}
            ),
            patch.object(
                auto_resume, "_fetch_active_trip", return_value=_auto_trip([_DAILY_LOSS])
            ),
            patch.object(auto_resume.circuit_breaker, "reset") as reset,
            patch.object(auto_resume, "system_briefing") as brief,
            patch.object(auto_resume, "audit") as audit,
        ):
            auto_resume.run_premarket_auto_resume()

        reset.assert_not_called()
        brief.assert_called_once()
        assert brief.call_args.args[0] == "수동 검토 필요"
        assert audit.call_args.kwargs["details"]["decision"] == "held"
        assert audit.call_args.kwargs["details"]["cause"] == "daily_loss"

    def test_manual_halt_holds(self):
        with (
            patch.object(
                auto_resume, "get_system_state", return_value={"halt_state": True}
            ),
            patch.object(
                auto_resume, "_fetch_active_trip", return_value=_manual_trip()
            ),
            patch.object(auto_resume.circuit_breaker, "reset") as reset,
            patch.object(auto_resume, "system_briefing") as brief,
            patch.object(auto_resume, "audit") as audit,
        ):
            auto_resume.run_premarket_auto_resume()

        reset.assert_not_called()
        assert brief.call_args.args[0] == "수동 검토 필요"
        assert audit.call_args.kwargs["details"]["decision"] == "held"
        assert audit.call_args.kwargs["details"]["cause"] == "manual"

    def test_mixed_loss_holds(self):
        with (
            patch.object(
                auto_resume, "get_system_state", return_value={"halt_state": True}
            ),
            patch.object(
                auto_resume,
                "_fetch_active_trip",
                return_value=_auto_trip([_DAILY_COUNT, _DAILY_LOSS]),
            ),
            patch.object(auto_resume.circuit_breaker, "reset") as reset,
            patch.object(auto_resume, "system_briefing") as brief,
            patch.object(auto_resume, "audit"),
        ):
            auto_resume.run_premarket_auto_resume()

        reset.assert_not_called()
        assert brief.call_args.args[0] == "수동 검토 필요"

    def test_undeterminable_holds(self):
        """AC-7: halt_state=true but no active trip -> HOLD + manual-review."""
        with (
            patch.object(
                auto_resume, "get_system_state", return_value={"halt_state": True}
            ),
            patch.object(auto_resume, "_fetch_active_trip", return_value=None),
            patch.object(auto_resume.circuit_breaker, "reset") as reset,
            patch.object(auto_resume, "system_briefing") as brief,
            patch.object(auto_resume, "audit") as audit,
        ):
            auto_resume.run_premarket_auto_resume()

        reset.assert_not_called()
        assert brief.call_args.args[0] == "수동 검토 필요"
        assert audit.call_args.kwargs["details"]["decision"] == "held"


class TestRunPremarketAutoResumeNotHalted:
    """AC-6 — not halted -> no-op, NO telegram."""

    def test_not_halted_no_reset_no_telegram(self, caplog):
        with (
            patch.object(
                auto_resume, "get_system_state", return_value={"halt_state": False}
            ),
            patch.object(auto_resume, "_fetch_active_trip") as fetch,
            patch.object(auto_resume.circuit_breaker, "reset") as reset,
            patch.object(auto_resume, "system_briefing") as brief,
            patch.object(auto_resume, "audit"),
        ):
            with caplog.at_level("INFO", logger=auto_resume.LOG.name):
                auto_resume.run_premarket_auto_resume()

        reset.assert_not_called()
        # No telegram at all (REQ-032-4a / AC-6).
        brief.assert_not_called()
        # Cheap short-circuit: the active-trip query is not even issued.
        fetch.assert_not_called()
        # An INFO log line is emitted.
        assert any(r.levelname == "INFO" for r in caplog.records)

    def test_telegram_failure_is_swallowed(self):
        """REQ-032 R-6: a telegram exception must not crash the job."""
        with (
            patch.object(
                auto_resume, "get_system_state", return_value={"halt_state": True}
            ),
            patch.object(
                auto_resume, "_fetch_active_trip", return_value=_auto_trip([_DAILY_COUNT])
            ),
            patch.object(auto_resume.circuit_breaker, "reset"),
            patch.object(
                auto_resume, "system_briefing", side_effect=RuntimeError("tg down")
            ),
            patch.object(auto_resume, "audit"),
        ):
            # Must not raise.
            auto_resume.run_premarket_auto_resume()


class TestNoRegression:
    """AC-9 — the new module only calls/queries; it does not redefine risk logic."""

    def test_module_does_not_redefine_reset_or_trip(self):
        # circuit_breaker is imported and used, not shadowed.
        assert not hasattr(auto_resume, "trip")
        assert not hasattr(auto_resume, "reset")
        # The classifier/entry are the only public callables added.
        assert callable(auto_resume.classify_halt)
        assert callable(auto_resume.run_premarket_auto_resume)
