"""SPEC-TRADING-035 REQ-035-1 — regime/risk_appetite DB caching tests.

Verifies the read helper (``get_effective_regime``), the 7-day TTL safe
fallback to 'neutral' (with a single Telegram warning), the ``update_system_state``
NOW()-field handling for ``regime_updated_at``, and the migration file shape
(idempotent guards + CHECK constraints + the four/one new columns).

DB and Telegram are mocked — no network, no live Postgres. "now" is injected so
the TTL elapsed-time logic is deterministic.

@MX:SPEC: SPEC-TRADING-035
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from trading.db import session as sess


def _now() -> datetime:
    return datetime(2026, 5, 29, 6, 10, 0, tzinfo=UTC)


_UNSET = object()


def _state(
    regime: str = "bull",
    risk: str = "risk-on",
    updated: Any = _UNSET,
) -> dict[str, Any]:
    # ``updated=None`` is an explicit "missing timestamp" case; the default
    # (_UNSET) substitutes a fresh "now" so most cases read as fresh.
    return {
        "id": 1,
        "current_regime": regime,
        "current_risk_appetite": risk,
        "regime_updated_at": _now() if updated is _UNSET else updated,
        "regime_source_run_id": 42,
    }


# ---------------------------------------------------------------------------
# get_effective_regime — fresh read
# ---------------------------------------------------------------------------
class TestGetEffectiveRegimeFresh:
    def test_fresh_returns_stored_values(self):
        fresh = _now() - timedelta(days=2)
        with (
            patch.object(sess, "get_system_state", return_value=_state(updated=fresh)),
            patch.object(sess, "_notify_regime_stale") as warn,
        ):
            regime, risk = sess.get_effective_regime(now_provider=_now)

        assert regime == "bull"
        assert risk == "risk-on"
        warn.assert_not_called()

    def test_bear_neutral_passthrough(self):
        fresh = _now() - timedelta(hours=1)
        with (
            patch.object(
                sess, "get_system_state",
                return_value=_state(regime="bear", risk="risk-off", updated=fresh),
            ),
            patch.object(sess, "_notify_regime_stale"),
        ):
            regime, risk = sess.get_effective_regime(now_provider=_now)

        assert regime == "bear"
        assert risk == "risk-off"


# ---------------------------------------------------------------------------
# get_effective_regime — TTL fallback (REQ-035-1c)
# ---------------------------------------------------------------------------
class TestGetEffectiveRegimeTTL:
    def test_stale_8_days_falls_back_to_neutral_and_warns_once(self):
        stale = _now() - timedelta(days=8)
        with (
            patch.object(sess, "get_system_state", return_value=_state(updated=stale)),
            patch.object(sess, "_notify_regime_stale") as warn,
        ):
            regime, risk = sess.get_effective_regime(now_provider=_now)

        # Read result falls back; stored value untouched (read-time only).
        assert regime == "neutral"
        assert risk == "neutral"
        warn.assert_called_once()

    def test_exactly_7_days_is_still_fresh(self):
        edge = _now() - timedelta(days=7)
        with (
            patch.object(sess, "get_system_state", return_value=_state(updated=edge)),
            patch.object(sess, "_notify_regime_stale") as warn,
        ):
            regime, _ = sess.get_effective_regime(now_provider=_now)

        assert regime == "bull"
        warn.assert_not_called()

    def test_just_past_7_days_is_stale(self):
        past = _now() - timedelta(days=7, seconds=1)
        with (
            patch.object(sess, "get_system_state", return_value=_state(updated=past)),
            patch.object(sess, "_notify_regime_stale") as warn,
        ):
            regime, _ = sess.get_effective_regime(now_provider=_now)

        assert regime == "neutral"
        warn.assert_called_once()

    def test_missing_timestamp_falls_back_safely_without_crash(self):
        with (
            patch.object(sess, "get_system_state", return_value=_state(updated=None)),
            patch.object(sess, "_notify_regime_stale"),
        ):
            regime, risk = sess.get_effective_regime(now_provider=_now)

        # No timestamp -> safe neutral fallback (defensive).
        assert regime == "neutral"
        assert risk == "neutral"

    def test_unknown_stored_regime_falls_back_to_neutral(self):
        fresh = _now() - timedelta(hours=1)
        with (
            patch.object(
                sess, "get_system_state",
                return_value=_state(regime="sideways", updated=fresh),
            ),
            patch.object(sess, "_notify_regime_stale"),
        ):
            regime, _ = sess.get_effective_regime(now_provider=_now)

        assert regime == "neutral"


# ---------------------------------------------------------------------------
# update_system_state — NOW() field handling (REQ-035-1b / R-4 / Q-2)
# ---------------------------------------------------------------------------
class TestUpdateSystemStateNowField:
    def test_regime_updated_at_renders_now_function(self):
        captured: dict[str, Any] = {}

        class _Cur:
            def execute(self, sql: str, params: Any = None) -> None:
                captured["sql"] = sql
                captured["params"] = params

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        class _Conn:
            def cursor(self):
                return _Cur()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        from contextlib import contextmanager

        @contextmanager
        def _conn(autocommit: bool = False):
            yield _Conn()

        with patch.object(sess, "connection", _conn):
            sess.update_system_state(
                current_regime="bull",
                current_risk_appetite="risk-on",
                regime_source_run_id=99,
                regime_updated_at=sess.NOW,
            )

        sql = captured["sql"]
        # regime_updated_at must be a SQL function call, not a bound parameter.
        assert "regime_updated_at = NOW()" in sql
        assert "updated_at = NOW()" in sql
        # The NOW marker value must NOT be passed as a parameter.
        assert sess.NOW not in (captured["params"] or [])
        # current_regime IS a bound parameter.
        assert "bull" in captured["params"]
        assert "risk-on" in captured["params"]
        assert 99 in captured["params"]


# ---------------------------------------------------------------------------
# Migration file shape (REQ-035-1 a/d, AC idempotency + CHECK)
# ---------------------------------------------------------------------------
class TestMigration024Shape:
    def _sql(self) -> str:
        path = (
            Path(sess.__file__).resolve().parent
            / "migrations"
            / "024_regime_awareness.sql"
        )
        return path.read_text(encoding="utf-8")

    def test_migration_file_exists(self):
        assert "024_regime_awareness" in self._sql()

    def test_adds_four_system_state_columns(self):
        sql = self._sql()
        for col in (
            "current_regime",
            "current_risk_appetite",
            "regime_updated_at",
            "regime_source_run_id",
        ):
            assert col in sql

    def test_adds_persona_runs_audit_column(self):
        assert "regime_at_decision" in self._sql()

    def test_has_check_constraints(self):
        sql = self._sql()
        assert "CHECK (current_regime IN ('bull','neutral','bear'))" in sql
        assert "current_risk_appetite IN ('risk-on','neutral','risk-off')" in sql

    def test_is_idempotent_with_information_schema_guards(self):
        sql = self._sql()
        # One guard per column added (4 system_state + 1 persona_runs).
        assert sql.count("FROM information_schema.columns") == 5
        assert "ON CONFLICT DO NOTHING" in sql

    def test_records_itself_in_schema_migrations_and_audit(self):
        sql = self._sql()
        assert "INSERT INTO schema_migrations" in sql
        assert "SCHEMA_MIGRATED" in sql

    def test_regime_source_run_id_references_persona_runs(self):
        assert "REFERENCES persona_runs(id)" in self._sql()


# ---------------------------------------------------------------------------
# Macro post-processing write-side (REQ-035-1 b/d)
# ---------------------------------------------------------------------------
class _Res:
    def __init__(self, response_json, persona_run_id=7):
        self.response_json = response_json
        self.persona_run_id = persona_run_id


class TestPersistMacroRegime:
    """REQ-035-1(b): success -> cache UPDATE with source run id + NOW stamp.
    REQ-035-1(d): missing key -> schema error, NO cache update, telegram notify.
    """

    def test_success_updates_cache_with_run_id_and_now(self):
        from trading.personas import orchestrator as orch

        res = _Res({"regime": "bull", "risk_appetite": "risk-on"}, persona_run_id=51)
        with (
            patch.object(orch, "update_system_state") as upd,
            patch.object(orch, "tg"),
        ):
            orch.persist_macro_regime(res)

        upd.assert_called_once()
        kwargs = upd.call_args.kwargs
        assert kwargs["current_regime"] == "bull"
        assert kwargs["current_risk_appetite"] == "risk-on"
        assert kwargs["regime_source_run_id"] == 51
        # regime_updated_at stamped via the NOW sentinel (not a literal datetime).
        assert kwargs["regime_updated_at"] == sess.NOW

    def test_missing_regime_key_does_not_update_and_notifies(self):
        from trading.personas import orchestrator as orch

        res = _Res({"risk_appetite": "neutral"})  # 'regime' missing (REQ-035-1d)
        with (
            patch.object(orch, "update_system_state") as upd,
            patch.object(orch, "tg") as tg,
        ):
            orch.persist_macro_regime(res)

        upd.assert_not_called()  # cache preserved (previous value retained)
        tg.system_error.assert_called_once()

    def test_missing_risk_appetite_key_does_not_update_and_notifies(self):
        from trading.personas import orchestrator as orch

        res = _Res({"regime": "bear"})  # 'risk_appetite' missing
        with (
            patch.object(orch, "update_system_state") as upd,
            patch.object(orch, "tg") as tg,
        ):
            orch.persist_macro_regime(res)

        upd.assert_not_called()
        tg.system_error.assert_called_once()

    def test_none_response_json_does_not_update_and_notifies(self):
        from trading.personas import orchestrator as orch

        res = _Res(None)
        with (
            patch.object(orch, "update_system_state") as upd,
            patch.object(orch, "tg") as tg,
        ):
            orch.persist_macro_regime(res)

        upd.assert_not_called()
        tg.system_error.assert_called_once()
