"""SPEC-TRADING-020 REQ-020-2: blocked_cache universe expansion tests.

These tests validate that ``refresh_blocked_tickers`` calls KIS API for the
full universe returned by ``get_data_universe()`` instead of just
``DEFAULT_WATCHLIST``. This prevents incidents like the 2026-05-12 07:33
055550 신한지주 late-block, where 055550 (in screened only) bypassed the
07:25 pre-flight check because the cron only queried 5 DEFAULT tickers.

Coverage:
- REQ-020-2 (a, c): ``tickers_to_check`` is sourced from get_data_universe()
- REQ-020-2 (b): result spans the full ≥20-ticker universe
- Scenario 4 (acceptance.md): 055550 single-ticker scenario.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _fake_quote_normal(ticker: str) -> dict[str, object]:
    """KIS current_price stub for a normal (tradeable) ticker."""
    return {"is_normal": True, "stat_cls": "00", "price": 50_000}


def _fake_quote_blocked(ticker: str, stat_cls: str = "55") -> dict[str, object]:
    """KIS current_price stub for a blocked (단기과열) ticker."""
    return {"is_normal": False, "stat_cls": stat_cls, "price": 50_000}


class TestRefreshBlockedUsesUniverse:
    """REQ-020-2 (a, c): refresh_blocked_tickers must use get_data_universe()."""

    def test_refresh_blocked_uses_get_data_universe(self, tmp_path):
        """KIS current_price is called for every ticker in get_data_universe()."""
        from trading.risk import blocked_cache as bc

        # Universe = DEFAULT (5) + extras (3) = 8 distinct tickers.
        universe = [
            "000660",
            "005930",
            "035420",
            "035720",
            "373220",  # DEFAULT
            "055550",
            "005380",
            "207940",  # extras
        ]

        called: list[str] = []

        def fake_current_price(client, ticker: str):
            called.append(ticker)
            return _fake_quote_normal(ticker)

        with (
            patch.object(bc, "get_data_universe", return_value=universe),
            patch.object(bc, "current_price", side_effect=fake_current_price),
            patch.object(bc, "KisClient", return_value=MagicMock()),
            patch.object(
                bc, "get_settings", return_value=MagicMock(trading_mode="paper")
            ),
            patch.object(bc, "CACHE_FILE", tmp_path / "blocked_tickers.json"),
        ):
            bc.refresh_blocked_tickers()

        # Every ticker from get_data_universe() must have been queried via KIS.
        assert set(called) == set(universe), (
            f"REQ-020-2 (a): expected universe={set(universe)} queried, "
            f"got {set(called)}"
        )
        # SPEC-020 REQ-020-2 (c): must NOT be limited to DEFAULT 5종 only.
        assert len(called) > 5, (
            f"REQ-020-2 (c): tickers_to_check must not be DEFAULT-only; "
            f"got {len(called)} queries"
        )

    def test_refresh_blocked_055550_scenario(self, tmp_path):
        """Scenario 4 (acceptance.md): 055550 in screened + 단기과열 -> blocked.

        Reproduction of 2026-05-12 07:33 incident: 055550 신한지주 must be
        caught by the 07:25 cron when it queries the full universe.
        """
        from trading.risk import blocked_cache as bc

        # 055550 is part of the universe (came in via screened).
        universe = [
            "000660",
            "005930",
            "035420",
            "035720",
            "373220",
            "055550",  # the late-block ticker
        ]

        def fake_current_price(client, ticker: str):
            # Today's reality: 055550 is 단기과열, others normal.
            if ticker == "055550":
                return _fake_quote_blocked(ticker, stat_cls="55")
            return _fake_quote_normal(ticker)

        cache_file = tmp_path / "blocked_tickers.json"
        with (
            patch.object(bc, "get_data_universe", return_value=universe),
            patch.object(bc, "current_price", side_effect=fake_current_price),
            patch.object(bc, "stat_cls_label", return_value="단기과열"),
            patch.object(bc, "KisClient", return_value=MagicMock()),
            patch.object(
                bc, "get_settings", return_value=MagicMock(trading_mode="paper")
            ),
            patch.object(bc, "CACHE_FILE", cache_file),
        ):
            cache = bc.refresh_blocked_tickers()

        # 055550 must appear in blocked dict (REQ-020-2, Scenario 4).
        assert "055550" in cache["blocked"], (
            "Scenario 4: 055550 should be blocked after refresh, "
            f"got blocked={list(cache['blocked'].keys())}"
        )
        # Cache file should be written.
        assert cache_file.exists()
