"""SPEC-TRADING-036 REQ-036-3 — late-cycle ceiling defence tests.

Covers:
- pure :func:`evaluate` over the 5 signals (margin >35/>40, deposits >140,
  V-KOSPI >=30, KOSPI daily <=-3%), with ``None`` signals skipped (graceful),
- governing-level selection when multiple signals fire,
- severe-stage forced 30%-of-quantity deleverage via the direct kis_sell bypass
  (REQ-036-3 e, Q-4),
- 24h clearance cooldown,
- the run entrypoint wiring (set flag + log event + telegram).

@MX:SPEC: SPEC-TRADING-036
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from trading.risk import late_cycle as lc


def _snap(**over):
    base = dict(margin_jo=None, deposits_jo=None, vkospi=None, kospi_daily_pct=None)
    base.update(over)
    return lc.DefenseInput(**base)


# ---------------------------------------------------------------------------
# evaluate — per-signal thresholds
# ---------------------------------------------------------------------------
class TestEvaluateThresholds:
    def test_no_signals_no_trigger(self):
        res = lc.evaluate(_snap())
        assert res.triggered is False
        assert res.level is None

    def test_margin_moderate(self):
        res = lc.evaluate(_snap(margin_jo=36.0))
        assert res.triggered is True
        assert res.level == "moderate"

    def test_margin_severe(self):
        res = lc.evaluate(_snap(margin_jo=41.0))
        assert res.level == "severe"
        assert res.forced_sell_pct == pytest.approx(0.30)

    def test_deposits_top_warning(self):
        res = lc.evaluate(_snap(deposits_jo=141.0))
        assert res.level == "top"

    def test_vkospi_does_not_trigger_in_observation_mode(self):
        # SPEC-036 observation mode (VKOSPI_TRIGGER_ENABLED=False default): a
        # V-KOSPI >= 30 must NOT trigger — the value is collected/logged only.
        assert lc.VKOSPI_TRIGGER_ENABLED is False
        res = lc.evaluate(_snap(vkospi=71.0))
        assert res.triggered is False
        assert res.level is None

    def test_kospi_flash(self):
        res = lc.evaluate(_snap(kospi_daily_pct=-3.0))
        assert res.level == "flash"

    def test_below_threshold_does_not_trigger(self):
        res = lc.evaluate(
            _snap(margin_jo=34.9, deposits_jo=139.0, vkospi=29.0, kospi_daily_pct=-2.9)
        )
        assert res.triggered is False

    def test_unavailable_signals_skipped(self):
        # margin/deposits/vkospi all None (unavailable) — only KOSPI flash floors.
        res = lc.evaluate(_snap(kospi_daily_pct=-4.0))
        assert res.triggered is True
        assert res.level == "flash"
        names = {t.signal_name for t in res.triggers}
        assert names == {"kospi_daily"}


class TestGoverningLevel:
    def test_severe_beats_moderate_and_top(self):
        # margin severe (40) + deposits top (141) — top has the higher cash floor
        # (60 vs 50) but severe is the one that forces a sell. Governing must
        # apply the strongest enforcement: cash floor = max, forced sell present.
        res = lc.evaluate(_snap(margin_jo=41.0, deposits_jo=141.0))
        assert res.cash_floor_pct == max(lc.STAGE_CASH_FLOOR["severe"], lc.STAGE_CASH_FLOOR["top"])
        assert res.forced_sell_pct == pytest.approx(0.30)
        assert res.block_new_entry is True

    def test_multiple_triggers_recorded_vkospi_gated_out(self):
        # vkospi=31 is above its (disabled) threshold but must NOT appear — the
        # other two breaches still record.
        res = lc.evaluate(_snap(margin_jo=41.0, vkospi=31.0, kospi_daily_pct=-3.5))
        names = {t.signal_name for t in res.triggers}
        assert names == {"margin", "kospi_daily"}
        assert "vkospi" not in names


# ---------------------------------------------------------------------------
# SPEC-036 observation mode: V-KOSPI trigger gate (collect/log only until
# recalibrated; the other 3 signals stay active).
# ---------------------------------------------------------------------------
class TestVkospiTriggerGate:
    def test_vkospi_alone_does_not_trigger_when_disabled(self):
        res = lc.evaluate(_snap(vkospi=71.0))
        assert res.triggered is False

    def test_vkospi_never_in_triggers_alongside_margin_breach(self):
        res = lc.evaluate(_snap(margin_jo=41.0, vkospi=71.0))
        names = {t.signal_name for t in res.triggers}
        assert names == {"margin"}
        assert "vkospi" not in names

    def test_recalibration_path_enabling_flag_restores_immediate_trigger(self, monkeypatch):
        # Flipping VKOSPI_TRIGGER_ENABLED=True (the future recalibrated state)
        # makes V-KOSPI >= VKOSPI_IMMEDIATE produce the immediate-level trigger.
        monkeypatch.setattr(lc, "VKOSPI_TRIGGER_ENABLED", True)
        res = lc.evaluate(_snap(vkospi=30.0))
        assert res.triggered is True
        assert res.level == "immediate"
        names = {t.signal_name for t in res.triggers}
        assert "vkospi" in names

    def test_enabled_but_below_threshold_does_not_trigger(self, monkeypatch):
        monkeypatch.setattr(lc, "VKOSPI_TRIGGER_ENABLED", True)
        res = lc.evaluate(_snap(vkospi=29.0))
        assert res.triggered is False


# ---------------------------------------------------------------------------
# severe forced deleverage — direct kis_sell bypass (REQ-036-3 e / Q-4)
# ---------------------------------------------------------------------------
class TestForcedDeleverage:
    def test_sells_30pct_of_each_holding_qty_direct(self):
        holdings = [
            {"ticker": "005930", "qty": 100},
            {"ticker": "000660", "qty": 9},
        ]
        client = MagicMock()
        with (
            patch.object(lc, "_build_client", return_value=client),
            patch.object(lc, "_read_holdings", return_value=holdings),
            patch.object(lc, "kis_sell") as sell,
        ):
            sold = lc.forced_deleverage(pct=0.30)

        # 30% of 100 = 30; 30% of 9 = floor(2.7)=2 -> max(1,2)=2.
        qtys = {c.kwargs["ticker"]: c.kwargs["qty"] for c in sell.call_args_list}
        assert qtys["005930"] == 30
        assert qtys["000660"] == 2
        assert sold == 2  # two tickers deleveraged
        # Direct call uses persona_decision_id=None (bypass — no decision row).
        assert all(c.kwargs.get("persona_decision_id") is None for c in sell.call_args_list)

    def test_per_ticker_error_isolation(self):
        holdings = [{"ticker": "AAA", "qty": 100}, {"ticker": "BBB", "qty": 100}]
        client = MagicMock()

        def _sell(_c, ticker, **_k):
            if ticker == "AAA":
                raise RuntimeError("lower limit locked")

        with (
            patch.object(lc, "_build_client", return_value=client),
            patch.object(lc, "_read_holdings", return_value=holdings),
            patch.object(lc, "kis_sell", side_effect=_sell) as sell,
        ):
            sold = lc.forced_deleverage(pct=0.30)
        # BBB still sells despite AAA raising.
        assert sell.call_count == 2
        assert sold == 1

    def test_skips_zero_qty(self):
        holdings = [{"ticker": "AAA", "qty": 0}]
        with (
            patch.object(lc, "_build_client", return_value=MagicMock()),
            patch.object(lc, "_read_holdings", return_value=holdings),
            patch.object(lc, "kis_sell") as sell,
        ):
            sold = lc.forced_deleverage(pct=0.30)
        sell.assert_not_called()
        assert sold == 0

    def test_does_not_raise_when_balance_read_fails(self):
        with (
            patch.object(lc, "_build_client", side_effect=RuntimeError("kis down")),
            patch.object(lc, "kis_sell") as sell,
        ):
            sold = lc.forced_deleverage(pct=0.30)
        assert sold == 0
        sell.assert_not_called()


# ---------------------------------------------------------------------------
# cooldown
# ---------------------------------------------------------------------------
class TestCooldown:
    def test_within_24h_holds_defense(self):
        entered = datetime(2026, 5, 29, 16, 5, tzinfo=UTC)
        now = entered + timedelta(hours=12)
        assert lc.cooldown_elapsed(entered, now) is False

    def test_after_24h_allows_clear(self):
        entered = datetime(2026, 5, 29, 16, 5, tzinfo=UTC)
        now = entered + timedelta(hours=25)
        assert lc.cooldown_elapsed(entered, now) is True

    def test_missing_entered_at_allows_clear(self):
        assert lc.cooldown_elapsed(None, datetime(2026, 5, 29, tzinfo=UTC)) is True


# ---------------------------------------------------------------------------
# run_late_cycle_evaluation — wiring (set flag + log + telegram)
# ---------------------------------------------------------------------------
class TestRunEntrypoint:
    def _state(self, **over):
        base = {
            "late_cycle_defense_active": False,
            "late_cycle_level": None,
            "late_cycle_entered_at": None,
        }
        base.update(over)
        return base

    def test_trigger_sets_flag_logs_and_alerts(self):
        with (
            patch.object(lc, "gather_momentum") as gm,
            patch.object(lc, "get_system_state", return_value=self._state()),
            patch.object(lc, "set_late_cycle_defense") as setf,
            patch.object(lc, "log_late_cycle_event") as logev,
            patch.object(lc, "system_briefing") as tg,
            patch.object(lc, "forced_deleverage", return_value=0) as delev,
        ):
            gm.return_value = MagicMock(
                margin_jo=41.0, deposits_jo=120.0, vkospi=None, kospi_daily_pct=-1.0,
            )
            res = lc.run_late_cycle_evaluation()

        assert res["triggered"] is True
        setf.assert_called_once()
        assert setf.call_args.kwargs["active"] is True
        assert setf.call_args.kwargs["level"] == "severe"
        # severe -> forced deleverage invoked
        delev.assert_called_once()
        assert logev.call_count >= 1
        tg.assert_called()

    def test_no_trigger_when_active_and_within_cooldown_keeps_defense(self):
        entered = datetime.now(UTC) - timedelta(hours=2)
        with (
            patch.object(lc, "gather_momentum") as gm,
            patch.object(
                lc, "get_system_state",
                return_value=self._state(
                    late_cycle_defense_active=True, late_cycle_level="severe",
                    late_cycle_entered_at=entered,
                ),
            ),
            patch.object(lc, "set_late_cycle_defense") as setf,
            patch.object(lc, "log_late_cycle_event"),
            patch.object(lc, "system_briefing"),
            patch.object(lc, "forced_deleverage", return_value=0),
        ):
            gm.return_value = MagicMock(
                margin_jo=20.0, deposits_jo=100.0, vkospi=15.0, kospi_daily_pct=0.5,
            )
            res = lc.run_late_cycle_evaluation()
        # Signals cleared but cooldown not elapsed -> stays active, no clear write.
        assert res["triggered"] is False
        assert res["cleared"] is False
        setf.assert_not_called()

    def test_clear_after_cooldown(self):
        entered = datetime.now(UTC) - timedelta(hours=30)
        with (
            patch.object(lc, "gather_momentum") as gm,
            patch.object(
                lc, "get_system_state",
                return_value=self._state(
                    late_cycle_defense_active=True, late_cycle_level="severe",
                    late_cycle_entered_at=entered,
                ),
            ),
            patch.object(lc, "set_late_cycle_defense") as setf,
            patch.object(lc, "log_late_cycle_event") as logev,
            patch.object(lc, "system_briefing") as tg,
            patch.object(lc, "forced_deleverage", return_value=0),
        ):
            gm.return_value = MagicMock(
                margin_jo=20.0, deposits_jo=100.0, vkospi=15.0, kospi_daily_pct=0.5,
            )
            res = lc.run_late_cycle_evaluation()
        assert res["cleared"] is True
        setf.assert_called_once()
        assert setf.call_args.kwargs["active"] is False
        logev.assert_called()
        tg.assert_called()

    def test_run_never_raises_on_gather_failure(self):
        with (
            patch.object(lc, "gather_momentum", side_effect=RuntimeError("down")),
            patch.object(lc, "get_system_state", return_value=self._state()),
            patch.object(lc, "set_late_cycle_defense"),
            patch.object(lc, "log_late_cycle_event"),
            patch.object(lc, "system_briefing"),
            patch.object(lc, "forced_deleverage", return_value=0),
        ):
            res = lc.run_late_cycle_evaluation()
        assert res["triggered"] is False
