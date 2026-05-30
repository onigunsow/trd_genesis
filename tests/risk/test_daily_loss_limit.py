"""SPEC-TRADING-038 REQ-038-1 — daily-loss circuit-breaker limit widened to -2.5%.

Reproduction-first (money/risk logic). These tests pin the *behaviour* of the
daily-loss hard limit, not merely the constant:

- the default threshold is now -2.5% (was -1.0%);
- a daily P&L of -2.0% no longer trips (it used to at -1.0%) — the hardening;
- a daily P&L of -2.6% still trips and records the ``daily_loss:`` breach;
- the threshold is env-overridable (``RISK_DAILY_MAX_LOSS``);
- a daily_loss trip stays NON-auto-resumable (SPEC-032 invariant, keyed on the
  ``"daily_loss"`` breach-string prefix, independent of the threshold value).

All DB reads (``daily_pnl_pct`` / ``daily_order_count_today``) are patched so the
tests are offline.
"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest

from trading.risk import auto_resume, limits


def _check(pnl_pct: float) -> limits.LimitCheck:
    """Run check_pre_order for a tiny BUY with only the daily-loss gate live.

    daily_pnl_pct is forced to ``pnl_pct``; the order-count gate is forced to 0
    and per-ticker / total / single-order are kept trivially under their limits
    so the only breach that can appear is ``daily_loss``.
    """
    with (
        patch.object(limits, "daily_pnl_pct", return_value=pnl_pct),
        patch.object(limits, "daily_order_count_today", return_value=0),
    ):
        return limits.check_pre_order(
            side="buy",
            ticker="005930",
            qty=1,
            ref_price=1,  # notional ~1 → far under single/per-ticker/total limits
            total_assets=10_000_000,
            holdings=[],
            mode="paper",
            market="KOSPI",
        )


class TestDefaultThreshold:
    def test_default_is_minus_2_5_percent(self):
        """REQ-038-1(a): the default RISK_DAILY_MAX_LOSS is -2.5% (-0.025)."""
        from trading import config

        assert config.RISK_DAILY_MAX_LOSS == pytest.approx(-0.025)

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch):
        """REQ-038-1(b): RISK_DAILY_MAX_LOSS is overridable via env var."""
        monkeypatch.setenv("RISK_DAILY_MAX_LOSS", "-0.03")
        from trading import config

        reloaded = importlib.reload(config)
        try:
            assert reloaded.RISK_DAILY_MAX_LOSS == pytest.approx(-0.03)
        finally:
            # Restore the module-level default for the rest of the session.
            monkeypatch.delenv("RISK_DAILY_MAX_LOSS", raising=False)
            importlib.reload(config)

    def test_default_when_env_absent(self, monkeypatch: pytest.MonkeyPatch):
        """REQ-038-1(b): with no env var, the -0.025 default applies."""
        monkeypatch.delenv("RISK_DAILY_MAX_LOSS", raising=False)
        from trading import config

        reloaded = importlib.reload(config)
        assert reloaded.RISK_DAILY_MAX_LOSS == pytest.approx(-0.025)


class TestTripBehaviour:
    def test_pnl_minus_2_does_not_trip(self):
        """REQ-038-1 hardening: -2.0% is between old -1% and new -2.5% → no trip."""
        chk = _check(-0.020)
        assert not any(b.startswith("daily_loss") for b in chk.breaches)
        assert chk.passed

    def test_pnl_minus_2_point_6_trips(self):
        """REQ-038-1(c): -2.6% is below the -2.5% limit → daily_loss breach recorded."""
        chk = _check(-0.026)
        assert any(b.startswith("daily_loss") for b in chk.breaches)

    def test_pnl_at_limit_trips(self):
        """Boundary: pnl exactly at the limit (<=) trips."""
        from trading import config

        chk = _check(config.RISK_DAILY_MAX_LOSS)
        assert any(b.startswith("daily_loss") for b in chk.breaches)


class TestNonAutoResumableInvariant:
    def test_daily_loss_trip_is_not_auto_resumed(self):
        """REQ-038-1(d) / SPEC-032 invariant: a daily_loss halt is never auto-resumed.

        auto_resume keys on the ``"daily_loss"`` breach-string prefix, not the
        numeric threshold — so widening the limit preserves the non-resume rule.
        """
        active_trip = {
            "reason": "pre-order limit breach",
            "breaches": ["daily_loss: 오늘 손익 -2.60% ≤ 한도 -2.50%"],
        }
        should_resume, cause, _detail = auto_resume.classify_halt(True, active_trip)
        assert should_resume is False
        assert cause == "daily_loss"

    def test_mixed_count_plus_loss_still_holds(self):
        """A mixed count+loss breach still HOLDs (loss dominates)."""
        active_trip = {
            "reason": "pre-order limit breach",
            "breaches": [
                "daily_count: 오늘 주문 10 → 한도 10",
                "daily_loss: 오늘 손익 -2.60% ≤ 한도 -2.50%",
            ],
        }
        should_resume, cause, _detail = auto_resume.classify_halt(True, active_trip)
        assert should_resume is False
        assert cause == "daily_loss"
