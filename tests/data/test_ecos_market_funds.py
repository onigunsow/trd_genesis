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
# fetch_market_funds — converts 원 -> 조원 BEFORE caching (NUMERIC(20,8) overflow
# fix: raw 원 e.g. 3.57e13 exceeds the column's <1e12 max, so we store 35.7).
# ---------------------------------------------------------------------------
class TestFetchMarketFunds:
    def test_converts_won_to_jo_before_caching(self):
        # Raw ECOS rows are in 원 (S23E margin ~3.57e13). The cached value must
        # be the 조원-scaled value (/1e12), NOT the raw 원 (which overflows the
        # NUMERIC(20,8) column).
        raw_rows = [{"TIME": "202604", "DATA_VALUE": "35713079332408"}]  # ~3.57e13 원
        captured = {}

        def _fake_upsert(source, label, rows, *_a, **_k):
            captured[label] = list(rows)
            return len(captured[label])

        with (
            patch.object(ecos_adapter, "_fetch_raw", return_value=raw_rows),
            patch.object(ecos_adapter, "upsert_macro", side_effect=_fake_upsert),
        ):
            total = ecos_adapter.fetch_market_funds(date(2024, 1, 1), date(2026, 5, 1))

        # Both series upserted (S23E + S23A), 1 row each from the mock.
        assert total == 2
        assert set(captured) == {"S23E", "S23A"}
        stored = captured["S23E"][0]["value"]
        # Stored in 조원 (~35.71), NOT raw 원 (~3.57e13).
        assert abs(stored - 35.713079332408) < 1e-6
        assert stored < 1e12  # would not overflow NUMERIC(20,8)

    def test_conversion_divides_by_1e12_exactly_once(self):
        raw_rows = [{"TIME": "202604", "DATA_VALUE": "124800000000000"}]  # 124.8조 in 원
        captured = {}

        def _fake_upsert(source, label, rows, *_a, **_k):
            captured[label] = list(rows)
            return 1

        with (
            patch.object(ecos_adapter, "_fetch_raw", return_value=raw_rows),
            patch.object(ecos_adapter, "upsert_macro", side_effect=_fake_upsert),
        ):
            ecos_adapter.fetch_market_funds(date(2024, 1, 1), date(2026, 5, 1))

        assert abs(captured["S23A"][0]["value"] - 124.8) < 1e-6

    def test_graceful_on_fetch_error_returns_zero(self):
        def _boom(*_a, **_k):
            raise RuntimeError("ECOS down")

        with patch.object(ecos_adapter, "_fetch_raw", side_effect=_boom):
            # Must not raise — graceful (C-9).
            total = ecos_adapter.fetch_market_funds(date(2024, 1, 1), date(2026, 5, 1))
        assert total == 0


# ---------------------------------------------------------------------------
# latest_market_funds — cache now ALREADY holds 조원, so the reader returns it
# directly (no /1e12 division — that happens at write time in fetch_market_funds).
# ---------------------------------------------------------------------------
class TestLatestMarketFunds:
    def test_returns_jo_won_values_from_cache(self):
        # macro_indicators now stores 조원 directly (e.g. 35.7), so the reader
        # returns the stored value WITHOUT dividing again.
        rows = {
            "S23E": {"value": 35.7, "ts": date(2026, 4, 1)},
            "S23A": {"value": 124.8, "ts": date(2026, 4, 1)},
        }

        def _fake_latest(series_id):
            return rows.get(series_id)

        with patch.object(ecos_adapter, "_latest_macro_row", side_effect=_fake_latest):
            funds = ecos_adapter.latest_market_funds(today=date(2026, 5, 29))

        assert abs(funds["margin_jo"] - 35.7) < 0.01
        assert abs(funds["deposits_jo"] - 124.8) < 0.01

    def test_stored_value_not_re_divided(self):
        # Regression guard: a 조원-scale stored value (35.7) must NOT be divided
        # again to ~3.57e-11. The reader returns it verbatim.
        rows = {"S23E": {"value": 35.7, "ts": date(2026, 4, 1)}}

        with patch.object(ecos_adapter, "_latest_macro_row", side_effect=rows.get):
            funds = ecos_adapter.latest_market_funds(today=date(2026, 5, 29))

        assert funds["margin_jo"] == 35.7  # not re-divided

    def test_missing_cache_yields_none(self):
        with patch.object(ecos_adapter, "_latest_macro_row", return_value=None):
            funds = ecos_adapter.latest_market_funds(today=date(2026, 5, 29))
        assert funds["margin_jo"] is None
        assert funds["deposits_jo"] is None

    def test_stale_value_is_flagged(self):
        # A value older than the monthly tolerance is flagged stale.
        old = {"value": 30.0, "ts": date(2025, 1, 1)}

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
