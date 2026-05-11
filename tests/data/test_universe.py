"""SPEC-TRADING-019 REQ-019-6: Universe registry tests.

Pre-RED Discovery (2026-05-11):
- DEFAULT_WATCHLIST lives at `trading.personas.context.DEFAULT_WATCHLIST`
  (5 tickers: 005930, 000660, 035420, 035720, 373220).
- screened_tickers.json lives at `data/screened_tickers.json` and is loaded
  by `trading.screener.daily_screen.load_screened_tickers()`.
- Active holdings query pattern (from `trading.jit.pipeline.py:212`):
    SELECT DISTINCT ticker FROM positions WHERE shares > 0
- KOSPI200 source (per user decision Q-1, 2026-05-11): pykrx dynamic via
    pykrx.stock.get_index_portfolio_deposit_file("1028")
  cached for the duration of a single get_data_universe() invocation.
- Telegram client exists at `trading.alerts.telegram.system_briefing(category,
  message)`. Only one bot token in .env: TELEGRAM_BOT_TOKEN_TRADING.
"""

from __future__ import annotations

from unittest.mock import patch


class TestGetDataUniverseHappyPath:
    """REQ-019-6 (a, b): union of 4 sources, sorted + deduplicated."""

    def test_union_of_all_four_sources_sorted_dedup(self):
        """Universe is sorted, deduplicated union of 4 sources."""
        from trading.data.universe import get_data_universe

        with (
            patch(
                "trading.data.universe._read_screened_tickers",
                return_value=["005380", "009540", "161890"],
            ),
            patch(
                "trading.data.universe._read_active_holdings",
                return_value=["035720", "005380"],  # overlaps screened + default
            ),
            patch(
                "trading.data.universe._read_kospi200_top50",
                return_value=["005930", "000660", "207940", "005935", "035420"],
            ),
        ):
            result = get_data_universe()

        # Result is sorted + deduplicated
        assert result == sorted(set(result))
        # All ticker codes are 6-digit strings
        assert all(isinstance(t, str) and len(t) == 6 and t.isdigit() for t in result)
        # Contains all DEFAULT_WATCHLIST entries
        for t in ["005930", "000660", "035420", "035720", "373220"]:
            assert t in result
        # Contains screened + holdings + KOSPI200 entries
        for t in ["005380", "009540", "161890", "207940", "005935"]:
            assert t in result


class TestGetDataUniverseFallback:
    """REQ-019-6 (c, d): graceful degradation."""

    def test_screened_missing_returns_other_sources(self):
        """When screened_tickers.json absent, other sources still included."""
        from trading.data.universe import get_data_universe

        with (
            patch("trading.data.universe._read_screened_tickers", return_value=[]),
            patch(
                "trading.data.universe._read_active_holdings", return_value=["005380"]
            ),
            patch(
                "trading.data.universe._read_kospi200_top50", return_value=["207940"]
            ),
        ):
            result = get_data_universe()

        # Must include DEFAULT + holdings + kospi200, no screened
        assert "005380" in result
        assert "207940" in result
        assert "005930" in result  # DEFAULT
        # Length: 5 default + 1 holding + 1 kospi200 = 7 (no overlap)
        assert len(result) == 7

    def test_holdings_query_failure_skips_source_with_warning(self, caplog):
        """When holdings query raises, log warning and skip that source."""
        from trading.data.universe import get_data_universe

        def _raise(*_a, **_kw):
            raise RuntimeError("db connection failed")

        with (
            patch(
                "trading.data.universe._read_screened_tickers", return_value=["005380"]
            ),
            patch("trading.data.universe._read_active_holdings", side_effect=_raise),
            patch("trading.data.universe._read_kospi200_top50", return_value=[]),
        ):
            with caplog.at_level("WARNING"):
                result = get_data_universe()

        # Default + screened included; holdings skipped silently with warning
        assert "005380" in result
        assert "005930" in result
        # Some warning was logged for the holdings failure
        assert any(
            "holding" in r.message.lower() or "db" in r.message.lower()
            for r in caplog.records
        ), f"Expected holdings warning in logs, got: {[r.message for r in caplog.records]}"

    def test_all_sources_fail_returns_default_watchlist(self):
        """REQ-019-6 (c): catastrophic case — return DEFAULT, never empty."""
        from trading.data.universe import get_data_universe
        from trading.personas.context import DEFAULT_WATCHLIST

        def _raise(*_a, **_kw):
            raise RuntimeError("source down")

        with (
            patch("trading.data.universe._read_screened_tickers", side_effect=_raise),
            patch("trading.data.universe._read_active_holdings", side_effect=_raise),
            patch("trading.data.universe._read_kospi200_top50", side_effect=_raise),
        ):
            result = get_data_universe()

        # Returns DEFAULT_WATCHLIST (sorted), never empty
        assert result == sorted(set(DEFAULT_WATCHLIST))
        assert len(result) > 0

    def test_empty_default_returns_non_empty_when_other_sources_succeed(self):
        """Even if DEFAULT were temporarily empty, other sources should populate."""
        from trading.data.universe import get_data_universe

        with (
            patch(
                "trading.data.universe._read_screened_tickers", return_value=["005380"]
            ),
            patch("trading.data.universe._read_active_holdings", return_value=[]),
            patch(
                "trading.data.universe._read_kospi200_top50",
                return_value=["207940", "005930"],
            ),
        ):
            result = get_data_universe()

        # DEFAULT_WATCHLIST is always included even when other sources have data
        assert "005380" in result
        assert "207940" in result


class TestKospi200Helper:
    """REQ-019-6 (f): KOSPI200 source uses pykrx dynamically (user decision Q-1)."""

    def test_read_kospi200_invokes_pykrx_dynamic(self):
        """_read_kospi200_top50 calls pykrx.stock.get_index_portfolio_deposit_file."""
        from trading.data import universe

        fake_tickers = [f"{i:06d}" for i in range(1, 201)]
        with patch.object(
            universe, "_fetch_kospi200_from_pykrx", return_value=fake_tickers
        ) as m:
            result = universe._read_kospi200_top50()

        m.assert_called_once()
        # Truncated to top 50
        assert len(result) == 50
        assert result == fake_tickers[:50]

    def test_read_kospi200_failure_returns_empty(self, caplog):
        """When pykrx KOSPI200 fetch fails, return [] (warning logged)."""
        from trading.data import universe

        with patch.object(
            universe,
            "_fetch_kospi200_from_pykrx",
            side_effect=RuntimeError("pykrx down"),
        ):
            with caplog.at_level("WARNING"):
                result = universe._read_kospi200_top50()

        assert result == []


class TestActiveHoldingsHelper:
    """REQ-019-6 (a): active holdings via positions table."""

    def test_read_active_holdings_queries_positions(self, monkeypatch):
        """_read_active_holdings queries `positions WHERE shares > 0`."""
        from contextlib import contextmanager

        from tests.conftest import FakeConnection, FakeCursor
        from trading.data import universe

        cursor = FakeCursor(rows=[{"ticker": "005380"}, {"ticker": "035720"}])

        @contextmanager
        def _fake_conn(*_a, **_kw):
            yield FakeConnection(cursor)

        monkeypatch.setattr("trading.data.universe.connection", _fake_conn)

        result = universe._read_active_holdings()

        assert sorted(result) == ["005380", "035720"]
        # Query must filter for shares > 0
        assert "positions" in cursor.last_sql.lower()
        assert "shares" in cursor.last_sql.lower()
