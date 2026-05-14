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
    """REQ-019-6 (a, b): union of sources, sorted + deduplicated.

    SPEC-020 REQ-020-1 updates the semantics: DEFAULT_WATCHLIST is now only
    included on cold-start (empty screened), not unconditionally.
    """

    def test_union_of_sources_sorted_dedup_with_screened(self):
        """Universe is sorted, deduplicated union — DEFAULT excluded when screened non-empty."""
        from trading.data.universe import get_data_universe

        with (
            patch(
                "trading.data.universe._read_screened_tickers",
                return_value=["005380", "009540", "161890"],
            ),
            patch(
                "trading.data.universe._read_active_holdings",
                # 035720 overlaps DEFAULT but is included via holdings.
                return_value=["035720", "005380"],
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
        # Contains screened + holdings + KOSPI200 entries
        for t in ["005380", "009540", "161890", "207940", "005935"]:
            assert t in result
        # 035720 is in holdings (so included via holdings, not via DEFAULT)
        assert "035720" in result
        # 005930, 000660, 035420 are in KOSPI200 mock (included via KOSPI200, not via DEFAULT)
        assert "005930" in result
        # 373220 is ONLY in DEFAULT_WATCHLIST -- must NOT appear when screened is non-empty
        assert "373220" not in result, (
            "SPEC-020 REQ-020-1 (a): DEFAULT_WATCHLIST must NOT contribute "
            "when screened is non-empty"
        )


class TestGetDataUniverseSpec020Semantics:
    """SPEC-020 REQ-020-1: screened-first, DEFAULT-as-cold-start-fallback."""

    def test_screened_priority_excludes_default_when_screened_nonempty(self):
        """REQ-020-1 (a): When screened is non-empty, DEFAULT must be excluded.

        Validates Scenario 1 in acceptance.md.
        """
        from trading.data.universe import get_data_universe
        from trading.personas.context import DEFAULT_WATCHLIST

        # 20 screened tickers — none overlap with DEFAULT
        screened = [
            "000270",
            "005380",
            "012330",
            "017670",
            "028260",
            "032830",
            "051910",
            "055550",
            "066570",
            "086790",
            "096770",
            "105560",
            "207940",
            "247540",
            "251270",
            "316140",
            "323410",
            "352820",
            "377300",
            "393890",
        ]

        with (
            patch(
                "trading.data.universe._read_screened_tickers", return_value=screened
            ),
            patch("trading.data.universe._read_active_holdings", return_value=[]),
            patch("trading.data.universe._read_kospi200_top50", return_value=[]),
        ):
            result = get_data_universe()

        # All 20 screened present
        for t in screened:
            assert t in result, f"screened ticker {t} missing"
        # DEFAULT 5종 must NOT be in result (screened doesn't overlap)
        for t in DEFAULT_WATCHLIST:
            assert t not in result, (
                f"SPEC-020: DEFAULT ticker {t} leaked into universe "
                f"despite non-empty screened"
            )

    def test_default_fallback_when_screened_empty(self):
        """REQ-020-1 (b): When screened is empty/missing, DEFAULT is the fallback.

        Validates Scenario 2 in acceptance.md.
        """
        from trading.data.universe import get_data_universe
        from trading.personas.context import DEFAULT_WATCHLIST

        with (
            patch("trading.data.universe._read_screened_tickers", return_value=[]),
            patch("trading.data.universe._read_active_holdings", return_value=[]),
            patch("trading.data.universe._read_kospi200_top50", return_value=[]),
        ):
            result = get_data_universe()

        # Cold-start: result must be exactly DEFAULT_WATCHLIST (sorted, deduped)
        assert result == sorted(set(DEFAULT_WATCHLIST))


class TestGetDataUniverseFallback:
    """REQ-019-6 (c, d): graceful degradation."""

    def test_screened_missing_returns_other_sources(self):
        """When screened_tickers.json absent, other sources still included.

        SPEC-020 REQ-020-1 (b): When screened is empty (cold-start fallback),
        DEFAULT_WATCHLIST is included along with holdings + KOSPI200.
        """
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
        """When holdings query raises, log warning and skip that source.

        SPEC-020 update: screened is non-empty, so DEFAULT is excluded.
        Verifies graceful degradation of holdings source independently.
        """
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

        # Screened included; holdings skipped silently with warning.
        # SPEC-020: DEFAULT not present because screened is non-empty.
        assert "005380" in result
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


class TestSpec023DynamicUniverseIntegration:
    """SPEC-023 REQ-023-5: get_data_universe() includes dynamic_tickers.

    Priority order: screened > dynamic > holdings > KOSPI200 > DEFAULT.
    The result is a sorted+deduplicated union (sorted globally for stability
    with existing SPEC-019/020 invariants), but every dynamic ticker MUST
    appear in the result whenever dynamic_universe.list_active() is non-empty.
    """

    def test_dynamic_tickers_included_in_universe(self):
        """REQ-023-5 (a): dynamic_universe.list_active() contribution merged in."""
        from trading.data.universe import get_data_universe

        with (
            patch(
                "trading.data.universe._read_screened_tickers",
                return_value=["005380"],
            ),
            patch(
                "trading.data.universe._read_active_holdings", return_value=[]
            ),
            patch(
                "trading.data.universe._read_kospi200_top50", return_value=[]
            ),
            patch(
                "trading.data.universe._read_dynamic_tickers",
                return_value=["281820", "068270"],
            ),
        ):
            result = get_data_universe()

        # Dynamic tickers must surface in the universe.
        assert "281820" in result
        assert "068270" in result
        # Screened tickers preserved too.
        assert "005380" in result

    def test_dynamic_tickers_empty_falls_back_silently(self):
        """REQ-023-5 (a): when no dynamic tickers exist, behaviour is unchanged."""
        from trading.data.universe import get_data_universe

        with (
            patch(
                "trading.data.universe._read_screened_tickers",
                return_value=["005380"],
            ),
            patch(
                "trading.data.universe._read_active_holdings", return_value=[]
            ),
            patch(
                "trading.data.universe._read_kospi200_top50", return_value=[]
            ),
            patch(
                "trading.data.universe._read_dynamic_tickers", return_value=[]
            ),
        ):
            result = get_data_universe()

        assert "005380" in result

    def test_dynamic_source_failure_skipped_with_warning(self, caplog):
        """REQ-023-5 + REQ-019-6 (c) graceful degradation: dynamic source
        failure must NOT take down universe assembly."""
        from trading.data.universe import get_data_universe

        def _raise(*_a, **_kw):
            raise RuntimeError("dynamic_tickers table missing")

        with (
            patch(
                "trading.data.universe._read_screened_tickers",
                return_value=["005380"],
            ),
            patch(
                "trading.data.universe._read_active_holdings", return_value=[]
            ),
            patch(
                "trading.data.universe._read_kospi200_top50", return_value=[]
            ),
            patch(
                "trading.data.universe._read_dynamic_tickers", side_effect=_raise
            ),
        ):
            with caplog.at_level("WARNING"):
                result = get_data_universe()

        # Universe still usable.
        assert "005380" in result
        # Warning logged.
        assert any(
            "dynamic" in r.message.lower() for r in caplog.records
        ), f"Expected dynamic warning, got: {[r.message for r in caplog.records]}"


class TestActiveHoldingsHelper:
    """REQ-019-6 (a): active holdings via positions table."""

    def test_read_active_holdings_queries_positions(self, monkeypatch):
        """SPEC-022 REQ-022-2 (b): _read_active_holdings uses actual schema (qty)."""
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
        # Query must filter for the actual positions column (qty, not shares).
        assert "positions" in cursor.last_sql.lower()
        assert "qty" in cursor.last_sql.lower()
        assert (
            "shares" not in cursor.last_sql.lower()
        ), "SPEC-022 REQ-022-2: legacy 'shares' column reference must be gone"

    def test_active_holdings_query_failure_returns_empty(self, monkeypatch, caplog):
        """SPEC-022 REQ-022-2 (c): query crash (schema mismatch etc.) returns []
        with warning — never raises to caller."""
        from contextlib import contextmanager

        from trading.data import universe

        class _RaisingCursor:
            last_sql = ""

            def execute(self, sql, params=None):
                raise RuntimeError('column "shares" does not exist')

            def fetchall(self):
                return []

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        class _RaisingConn:
            def cursor(self):
                return _RaisingCursor()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        @contextmanager
        def _fake_conn(*_a, **_kw):
            yield _RaisingConn()

        monkeypatch.setattr("trading.data.universe.connection", _fake_conn)

        with caplog.at_level("WARNING"):
            result = universe._read_active_holdings()

        # Defensive guard returns empty list — does NOT raise.
        assert result == []
        # Warning was logged.
        assert any(
            "active_holdings" in r.message.lower()
            or "holding" in r.message.lower()
            or "schema" in r.message.lower()
            or "column" in r.message.lower()
            for r in caplog.records
        ), f"Expected warning, got: {[r.message for r in caplog.records]}"
