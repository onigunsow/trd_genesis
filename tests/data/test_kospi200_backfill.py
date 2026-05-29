"""SPEC-TRADING-037 REQ-037-1 — 10-year KOSPI200 OHLCV backfill tests.

Phase A. Pure-unit: pykrx is never invoked. The pykrx-adapter fetch functions
are patched, and the universe fetch is patched, so the backfill logic (backoff
retry, graceful per-symbol skip, incremental resume, progress reporting) is
verified without network or DB.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch


class TestKospi200Universe:
    """REQ-037-1 (a): full KOSPI200 constituents (no top-N cap) + KOSPI index."""

    def test_universe_includes_full_constituents_and_index(self):
        from trading.data.kospi200_backfill import kospi200_universe

        # 60 fake constituents — top-50 cap must NOT apply here.
        fake = [f"{i:06d}" for i in range(60)]
        with patch(
            "trading.data.kospi200_backfill._fetch_kospi200_constituents",
            return_value=fake,
        ):
            uni = kospi200_universe()

        # All 60 constituents present (cap removed) plus the KOSPI index 1001.
        assert "1001" in uni
        assert len([t for t in uni if t != "1001"]) == 60
        assert len(uni) == 61

    def test_index_present_even_if_constituents_fail(self):
        from trading.data.kospi200_backfill import kospi200_universe

        with patch(
            "trading.data.kospi200_backfill._fetch_kospi200_constituents",
            side_effect=RuntimeError("pykrx down"),
        ):
            uni = kospi200_universe()

        # Graceful: constituents source failed, but the index is still targeted.
        assert uni == ["1001"]


class TestBackfillSymbolRetry:
    """REQ-037-1 (c): backoff retry on rate-limit/timeout/exception."""

    def test_retries_then_succeeds(self):
        from trading.data import kospi200_backfill as kb

        calls = {"n": 0}

        def flaky(symbol, default_start):
            calls["n"] += 1
            if calls["n"] < 3:
                raise TimeoutError("rate limited")
            return 42

        with (
            patch.object(kb, "_fetch_full_range", side_effect=flaky),
            patch.object(kb, "_sleep") as sleep_mock,
        ):
            rows = kb.backfill_symbol(
                "005930", date(2015, 1, 1), max_retries=3, base_delay=0.01,
            )

        assert rows == 42
        assert calls["n"] == 3       # failed twice, succeeded on third
        assert sleep_mock.call_count == 2  # backoff slept between retries

    def test_raises_after_exhausting_retries(self):
        from trading.data import kospi200_backfill as kb

        with (
            patch.object(kb, "_fetch_full_range", side_effect=TimeoutError("nope")),
            patch.object(kb, "_sleep"),
        ):
            try:
                kb.backfill_symbol(
                    "005930", date(2015, 1, 1), max_retries=2, base_delay=0.01,
                )
            except Exception:  # expected escalation past exhausted retries
                pass
            else:
                raise AssertionError("expected exception after retries exhausted")


class TestBackfillAllGraceful:
    """REQ-037-1 (c, d, e): graceful per-symbol skip, incremental, reporting."""

    def test_one_symbol_fails_others_loaded_no_abort(self):
        from trading.data import kospi200_backfill as kb

        def per_symbol(symbol, default_start, **_):
            if symbol == "BAD":
                raise RuntimeError("permanent failure")
            return 100

        with patch.object(kb, "backfill_symbol", side_effect=per_symbol):
            report = kb.backfill_all(
                ["GOOD1", "BAD", "GOOD2"],
                default_start=date(2015, 1, 1),
            )

        assert report.loaded == ["GOOD1", "GOOD2"]
        assert report.skipped == ["BAD"]
        assert report.total_rows == 200
        # Completed without raising — abort/crash never happens (negative test).

    def test_backfill_requests_full_range_from_default_start(self):
        """REQ-037-1 (d): one-shot backfill covers the FULL window.

        The full-range path is the one used (not forward-only incremental).
        """
        from trading.data import kospi200_backfill as kb

        seen = []

        def spy(symbol, default_start):
            seen.append((symbol, default_start))
            return 10

        with (
            patch.object(kb, "_fetch_full_range", side_effect=spy),
            patch.object(kb, "_sleep"),
        ):
            kb.backfill_symbol("005930", date(2015, 1, 1))

        # Full-range path is invoked with the requested default_start.
        assert seen == [("005930", date(2015, 1, 1))]

    def test_recent_only_cache_still_fetches_full_history(self):
        """REGRESSION (smoke-test bug): a symbol with a recent-only cache (e.g.
        000270 cached 2026-02-11..2026-05-29) must still request the FULL
        window from default_start — NOT resume forward from last_cached+1.

        Proves ``_fetch_full_range`` delegates to ``pykrx_adapter.fetch_ohlcv``
        with (default_start, today), and never to the forward-only
        ``fetch_incremental``.
        """
        from datetime import date as date_t

        from trading.data import kospi200_backfill as kb

        recorded: dict = {}

        def fake_fetch_ohlcv(symbol, start, end):
            recorded["args"] = (symbol, start, end)
            return 2500  # ~10y of daily bars

        with (
            patch("trading.data.kospi200_backfill.pykrx_adapter.fetch_ohlcv",
                  side_effect=fake_fetch_ohlcv),
            patch("trading.data.kospi200_backfill.pykrx_adapter.fetch_incremental",
                  side_effect=AssertionError("forward-only incremental must NOT be used")),
        ):
            rows = kb._fetch_full_range("000270", date(2015, 1, 1))

        assert rows == 2500
        sym, start, end = recorded["args"]
        assert sym == "000270"
        # Requests from default_start regardless of any existing recent cache.
        assert start == date(2015, 1, 1)
        # Through today (inclusive of the full window).
        assert end >= date_t.today()

    def test_report_logs_summary(self, caplog):
        from trading.data import kospi200_backfill as kb

        with patch.object(kb, "backfill_symbol", return_value=50):
            with caplog.at_level("INFO"):
                report = kb.backfill_all(
                    ["A", "B"], default_start=date(2015, 1, 1),
                )

        assert report.loaded == ["A", "B"]
        assert report.total_rows == 100
        # Progress/summary is logged (loaded/skipped counts + coverage).
        joined = " ".join(r.message for r in caplog.records)
        assert "backfill" in joined.lower()


class TestIndexFetchRouting:
    """REQ-037-1 (a): the KOSPI INDEX (1001) needs a different pykrx API.

    Stocks use ``stock.get_market_ohlcv`` (via ``pykrx_adapter.fetch_ohlcv``);
    indices use ``stock.get_index_ohlcv`` — calling the stock API for an index
    returns nothing (smoke test: `--symbols 1001 --years 2` -> 0 rows).
    """

    def test_is_index_detects_kospi_index(self):
        from trading.data import kospi200_backfill as kb

        assert kb._is_index(kb.KOSPI_INDEX_SYMBOL) is True
        assert kb._is_index("1001") is True
        assert kb._is_index("005930") is False
        assert kb._is_index("000270") is False

    def test_index_symbol_routes_to_index_fetch_and_upserts(self):
        """REGRESSION (smoke-test bug): 1001 must use get_index_ohlcv and upsert
        into the SAME ohlcv table — NOT the stock fetch_ohlcv path.
        """
        from datetime import date as date_t

        import pandas as pd

        from trading.data import kospi200_backfill as kb

        # Synthetic KRX index frame (Korean column names, as pykrx returns).
        idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
        df = pd.DataFrame(
            {
                "시가": [2600.0, 2610.0],
                "고가": [2620.0, 2630.0],
                "저가": [2590.0, 2600.0],
                "종가": [2615.0, 2625.0],
                "거래량": [400000, 410000],
            },
            index=idx,
        )

        captured: dict = {}

        def fake_get_index_ohlcv(start_str, end_str, code):
            captured["index_args"] = (start_str, end_str, code)
            return df

        def fake_upsert(source, symbol, rows):
            rows = list(rows)
            captured["upsert"] = (source, symbol, rows)
            return len(rows)

        import pykrx.stock as pstock

        with (
            patch.object(pstock, "get_index_ohlcv", side_effect=fake_get_index_ohlcv),
            patch("trading.data.kospi200_backfill.upsert_ohlcv", side_effect=fake_upsert),
            patch("trading.data.kospi200_backfill.pykrx_adapter.fetch_ohlcv",
                  side_effect=AssertionError("index must NOT use stock fetch_ohlcv")),
        ):
            rows = kb._fetch_full_range("1001", date(2015, 1, 1))

        # Index API was called with the full window + KOSPI code.
        start_str, end_str, code = captured["index_args"]
        assert code == "1001"
        assert start_str == "20150101"
        assert end_str >= date_t.today().strftime("%Y%m%d")

        # Upserted into the SAME ohlcv table, SAME source/schema as stocks.
        source, symbol, urows = captured["upsert"]
        assert source == kb.pykrx_adapter.SOURCE  # "pykrx"
        assert symbol == "1001"
        assert rows == 2
        assert len(urows) == 2
        first = urows[0]
        assert set(first) >= {"ts", "open", "high", "low", "close", "volume"}
        assert first["ts"] == date(2024, 1, 2)
        assert first["close"] == 2615.0
        assert first["open"] == 2600.0

    def test_stock_symbol_still_uses_stock_fetch(self):
        """A stock symbol must keep using pykrx_adapter.fetch_ohlcv."""
        import pykrx.stock as pstock

        from trading.data import kospi200_backfill as kb

        with (
            patch("trading.data.kospi200_backfill.pykrx_adapter.fetch_ohlcv",
                  return_value=1234) as stock_fetch,
            patch.object(pstock, "get_index_ohlcv",
                         side_effect=AssertionError("stock must NOT use index API")),
        ):
            rows = kb._fetch_full_range("005930", date(2015, 1, 1))

        assert rows == 1234
        assert stock_fetch.call_count == 1

    def test_index_fetch_empty_frame_returns_zero(self):
        """Graceful: an empty index frame writes nothing, returns 0."""
        import pandas as pd
        import pykrx.stock as pstock

        from trading.data import kospi200_backfill as kb

        with (
            patch.object(pstock, "get_index_ohlcv", return_value=pd.DataFrame()),
            patch("trading.data.kospi200_backfill.upsert_ohlcv",
                  side_effect=AssertionError("must not upsert an empty frame")),
        ):
            rows = kb._fetch_full_range("1001", date(2015, 1, 1))

        assert rows == 0
