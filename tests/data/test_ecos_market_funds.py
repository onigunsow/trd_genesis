"""SPEC-TRADING-036 REQ-036-1(b) — ECOS 901Y056 신용융자/예탁금 tests.

The ECOS adapter is extended with the 증시주변자금동향 (901Y056) series:
- item S23E = 신용융자 잔고 (margin balance, 빚투)
- item S23A = 투자자 예탁금 (investor deposits)

cycle=M (monthly), unit=원. Live sanity (2026-04): margin ~35.7조, deposits
~124.8조. The reader returns the LATEST cached value in 조원 (원 / 1e12) with a
staleness marker, and never raises.

@MX:SPEC: SPEC-TRADING-036
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from trading.data import ecos_adapter


# ---------------------------------------------------------------------------
# Series definitions
# ---------------------------------------------------------------------------
class TestMarketFundsSeriesDefined:
    def test_margin_and_deposit_series_present(self):
        codes = {(s[0], s[2]): s[3] for s in ecos_adapter.MARKET_FUNDS_SERIES}
        # 901Y056 / S23E -> margin, 901Y056 / S23A -> deposits.
        assert ("901Y056", "S23E") in codes
        assert ("901Y056", "S23A") in codes

    def test_series_are_monthly(self):
        for stat, cycle, _item, _label in ecos_adapter.MARKET_FUNDS_SERIES:
            assert stat == "901Y056"
            assert cycle == "M"


# ---------------------------------------------------------------------------
# fetch_market_funds — caches both series, graceful on failure
# ---------------------------------------------------------------------------
class TestFetchMarketFunds:
    def test_fetches_each_series(self):
        calls = []

        def _fake_fetch_series(stat, cycle, item, label, start, end):
            calls.append((stat, item, label))
            return 12

        with patch.object(ecos_adapter, "fetch_series", side_effect=_fake_fetch_series):
            total = ecos_adapter.fetch_market_funds(date(2024, 1, 1), date(2026, 5, 1))
        assert total == 24  # 12 + 12
        items = {c[1] for c in calls}
        assert items == {"S23E", "S23A"}

    def test_graceful_on_fetch_error_returns_zero(self):
        def _boom(*_a, **_k):
            raise RuntimeError("ECOS down")

        with patch.object(ecos_adapter, "fetch_series", side_effect=_boom):
            # Must not raise — graceful (C-9).
            total = ecos_adapter.fetch_market_funds(date(2024, 1, 1), date(2026, 5, 1))
        assert total == 0


# ---------------------------------------------------------------------------
# latest_market_funds — reads cache, converts 원 -> 조원, staleness marker
# ---------------------------------------------------------------------------
class TestLatestMarketFunds:
    def test_returns_jo_won_values_from_cache(self):
        # macro_indicators stores raw 원; reader divides by 1e12 -> 조원.
        rows = {
            "S23E": {"value": 35.7e12, "ts": date(2026, 4, 1)},
            "S23A": {"value": 124.8e12, "ts": date(2026, 4, 1)},
        }

        def _fake_latest(series_id):
            return rows.get(series_id)

        with patch.object(ecos_adapter, "_latest_macro_row", side_effect=_fake_latest):
            funds = ecos_adapter.latest_market_funds(today=date(2026, 5, 29))

        assert abs(funds["margin_jo"] - 35.7) < 0.01
        assert abs(funds["deposits_jo"] - 124.8) < 0.01

    def test_missing_cache_yields_none(self):
        with patch.object(ecos_adapter, "_latest_macro_row", return_value=None):
            funds = ecos_adapter.latest_market_funds(today=date(2026, 5, 29))
        assert funds["margin_jo"] is None
        assert funds["deposits_jo"] is None

    def test_stale_value_is_flagged(self):
        # A value older than the monthly tolerance is flagged stale.
        old = {"value": 30.0e12, "ts": date(2025, 1, 1)}

        def _fake_latest(series_id):
            return old if series_id == "S23E" else None

        with patch.object(ecos_adapter, "_latest_macro_row", side_effect=_fake_latest):
            funds = ecos_adapter.latest_market_funds(today=date(2026, 5, 29))

        assert funds["margin_stale"] is True

    def test_does_not_raise_on_db_error(self):
        def _boom(_series_id):
            raise RuntimeError("DB down")

        with patch.object(ecos_adapter, "_latest_macro_row", side_effect=_boom):
            funds = ecos_adapter.latest_market_funds(today=date(2026, 5, 29))
        assert funds["margin_jo"] is None
        assert funds["deposits_jo"] is None
