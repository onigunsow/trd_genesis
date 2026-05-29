"""SPEC-TRADING-036 REQ-036-3 — late-cycle defence state helpers.

Covers the thin session helpers that read/write the system_state defence flag
and insert late_cycle_events rows. DB calls are mocked — we assert the SQL/params
shape, not a live database.

@MX:SPEC: SPEC-TRADING-036
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from trading.db import session


def _conn_cm(cur):
    cm = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False
    return cm


class TestSetLateCycleDefense:
    def test_activate_sets_active_level_and_entered_at(self):
        with patch.object(session, "update_system_state") as upd:
            session.set_late_cycle_defense(
                active=True, level="severe", entered_at=datetime(2026, 5, 29, tzinfo=UTC)
            )
        upd.assert_called_once()
        kwargs = upd.call_args.kwargs
        assert kwargs["late_cycle_defense_active"] is True
        assert kwargs["late_cycle_level"] == "severe"
        assert kwargs["late_cycle_entered_at"] == datetime(2026, 5, 29, tzinfo=UTC)

    def test_clear_sets_inactive_and_nulls(self):
        with patch.object(session, "update_system_state") as upd:
            session.set_late_cycle_defense(active=False, level=None, entered_at=None)
        kwargs = upd.call_args.kwargs
        assert kwargs["late_cycle_defense_active"] is False
        assert kwargs["late_cycle_level"] is None
        assert kwargs["late_cycle_entered_at"] is None


class TestLogLateCycleEvent:
    def test_inserts_event_row(self):
        cur = MagicMock()
        with patch.object(session, "connection", return_value=_conn_cm(cur)):
            session.log_late_cycle_event(
                event_type="trigger", signal_name="margin", value=41.0,
                unit="조원", level="severe",
            )
        cur.execute.assert_called_once()
        sql, params = cur.execute.call_args.args
        assert "late_cycle_events" in sql
        assert params[0] == "trigger"
        assert params[1] == "margin"
        assert params[2] == 41.0
        assert params[4] == "severe"

    def test_value_none_allowed(self):
        cur = MagicMock()
        with patch.object(session, "connection", return_value=_conn_cm(cur)):
            session.log_late_cycle_event(
                event_type="clear", signal_name="margin", value=None,
                unit="", level=None,
            )
        _sql, params = cur.execute.call_args.args
        assert params[2] is None
        assert params[4] is None
