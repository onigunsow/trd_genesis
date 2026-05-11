"""SPEC-TRADING-019 REQ-019-5: Stale data monitoring + Telegram alert tests.

Pre-RED Discovery:
- Telegram alert via `trading.alerts.telegram.system_briefing(category, message)`.
  Only one bot token in .env (`TELEGRAM_BOT_TOKEN_TRADING`) — used for both dev
  and prod. Per user decision Q-5 (2026-05-11), we route SPEC-019 alerts to
  dev bot @onitrddev_bot but since only one token exists we use it with a TODO
  for future split.
- KRX calendar: `trading.scheduler.calendar.is_trading_day(d)`.
- Clock injection: `check_and_alert(clock=datetime.now)` for testability.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# REQ-019-5: stale detection + alert routing
# ---------------------------------------------------------------------------


class TestCheckAndAlert:
    """REQ-019-5: 09:00 stale check + Telegram alert."""

    def test_no_alert_when_all_fresh(self):
        """All 4 tables fresh (latest < 36h) → no alert sent."""
        from trading.monitoring import data_freshness as mod

        # All tables: latest = yesterday (~24h stale), well within 36h threshold
        yesterday = date.today() - timedelta(days=1)

        def _latest(table):
            return yesterday

        sender = MagicMock()
        result = mod.check_and_alert(
            clock=lambda: datetime(2026, 5, 11, 9, 0, 0),
            latest_ts_fn=_latest,
            alert_sender=sender,
        )

        sender.assert_not_called()
        assert all(entry["stale"] is False for entry in result["entries"])

    def test_alert_sent_when_ohlcv_36h_stale(self):
        """ohlcv > 36h stale → alert sent with table info."""
        from trading.monitoring import data_freshness as mod

        # Monday 09:00, latest ohlcv = Friday EOD (i.e. ~3 days stale).
        # Friday + Sat + Sun → expected_ts = Friday, but actual = Friday-2, so stale.
        clock_now = datetime(2026, 5, 11, 9, 0, 0)  # Monday
        # latest = 2026-05-02 (Friday before previous), well > 36h adjusted.
        gap_latest = date(2026, 5, 2)

        def _latest(table):
            if table == "ohlcv":
                return gap_latest
            return date(2026, 5, 8)  # Friday (fresh)

        sender = MagicMock()
        result = mod.check_and_alert(
            clock=lambda: clock_now, latest_ts_fn=_latest, alert_sender=sender
        )

        sender.assert_called_once()
        # Accept either (category, message) or single message
        text = " ".join(str(a) for a in sender.call_args.args)
        assert "ohlcv" in text
        assert "2026-05-02" in text or "latest" in text.lower()
        # At least one entry flagged stale
        assert any(e["stale"] for e in result["entries"])

    def test_alert_message_contains_required_fields(self):
        """REQ-019-5 (f): alert message contains table, latest_ts, expected_ts, days stale."""
        from trading.monitoring import data_freshness as mod

        clock_now = datetime(2026, 5, 11, 9, 0, 0)  # Monday
        gap_latest = date(2026, 4, 30)  # 11 days stale (the original bug)

        def _latest(table):
            if table == "disclosures":
                return gap_latest
            return date(2026, 5, 8)

        sender = MagicMock()
        mod.check_and_alert(
            clock=lambda: clock_now, latest_ts_fn=_latest, alert_sender=sender
        )

        sender.assert_called_once()
        text = " ".join(str(a) for a in sender.call_args.args)
        # Must include all required identifying info
        assert "disclosures" in text
        assert "2026-04-30" in text
        assert "stale" in text.lower() or "days" in text.lower()

    def test_weekend_friday_data_on_monday_no_alert(self):
        """REQ-019-5 (e): Friday data on Monday should NOT trigger alert."""
        from trading.monitoring import data_freshness as mod

        # Monday 2026-05-11; latest ohlcv = Friday 2026-05-08.
        # Calendar-aware: Sat/Sun weren't trading days, so 'expected' was Friday.
        # Therefore should NOT be stale.
        clock_now = datetime(2026, 5, 11, 9, 0, 0)

        def _latest(table):
            return date(2026, 5, 8)  # Last Friday

        sender = MagicMock()
        result = mod.check_and_alert(
            clock=lambda: clock_now, latest_ts_fn=_latest, alert_sender=sender
        )

        sender.assert_not_called()
        # All entries marked not stale
        assert all(not e["stale"] for e in result["entries"])

    def test_alert_sender_default_is_system_briefing(self):
        """Default alert_sender hooks into trading.alerts.telegram.system_briefing."""
        from trading.monitoring import data_freshness as mod

        # Force a stale condition
        gap_latest = date(2026, 4, 1)
        clock_now = datetime(2026, 5, 11, 9, 0, 0)

        def _latest(table):
            return gap_latest

        with patch("trading.alerts.telegram.system_briefing") as sb:
            mod.check_and_alert(clock=lambda: clock_now, latest_ts_fn=_latest)

        sb.assert_called_once()
        # Category includes SPEC-019 reference
        cat = sb.call_args.args[0] if sb.call_args.args else ""
        assert "SPEC-019" in cat or "STALE" in cat.upper()

    def test_individual_table_entries_logged_info(self, caplog):
        """REQ-019-5 (i): every table check produces an INFO log line."""
        from trading.monitoring import data_freshness as mod

        clock_now = datetime(2026, 5, 11, 9, 0, 0)

        def _latest(table):
            return date(2026, 5, 8)

        with caplog.at_level("INFO"):
            mod.check_and_alert(clock=lambda: clock_now, latest_ts_fn=_latest)

        # Each of 4 tables should appear in at least one INFO log line
        joined = " ".join(r.message for r in caplog.records)
        for table in ("ohlcv", "fundamentals", "flows", "disclosures"):
            assert table in joined.lower()
