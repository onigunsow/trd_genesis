"""SPEC-TRADING-037 REQ-037-2 — exit-backtest CLI pure-helper tests.

Covers the look-ahead-free helpers in the CLI script that do not touch the DB
or network (ATR% derivation, grid parsing, price/ATR assembly with a mocked
cache). The full ``main()`` path (DB persistence) is operational and exercised
separately at runtime.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch


def _bars(closes, *, lows=None, highs=None):
    out = []
    for i, c in enumerate(closes):
        out.append({
            "ts": date(2020, 1, 1),
            "open": c,
            "high": highs[i] if highs else c,
            "low": lows[i] if lows else c,
            "close": c,
        })
    return out


class TestExitBacktestHelpers:
    def test_parse_floats(self):
        from trading.scripts.exit_backtest import _parse_floats

        assert _parse_floats("1.0,1.5,2.0") == [1.0, 1.5, 2.0]
        assert _parse_floats("-5,-7,-10") == [-5.0, -7.0, -10.0]
        assert _parse_floats(" 2.0 , 3.0 ") == [2.0, 3.0]

    def test_atr_pct_from_bars_positive(self):
        from trading.scripts.exit_backtest import _atr_pct_from_bars

        bars = _bars([100.0, 102.0, 101.0],
                     lows=[100.0, 99.0, 100.0],
                     highs=[100.0, 103.0, 102.0])
        atr_pct = _atr_pct_from_bars(bars)
        assert atr_pct is not None
        assert atr_pct > 0

    def test_atr_pct_from_bars_insufficient(self):
        from trading.scripts.exit_backtest import _atr_pct_from_bars

        assert _atr_pct_from_bars([]) is None
        assert _atr_pct_from_bars(_bars([100.0])) is None

    def test_load_universe_prices_skips_empty(self):
        from trading.scripts import exit_backtest as eb

        def fake_cached(source, sym, start, end):
            if sym == "EMPTY":
                return []
            return _bars([100.0, 101.0, 102.0],
                         lows=[99.0, 100.0, 101.0],
                         highs=[101.0, 102.0, 103.0])

        with patch.object(eb, "cached_ohlcv", side_effect=fake_cached):
            price_data, atr_by_symbol = eb._load_universe_prices(
                "pykrx", ["GOOD", "EMPTY"], date(2015, 1, 1), date(2020, 1, 1),
            )

        assert "GOOD" in price_data
        assert "EMPTY" not in price_data
        assert atr_by_symbol["GOOD"] > 0

    def test_resolve_symbols_explicit(self):
        from trading.scripts.exit_backtest import _resolve_symbols

        assert _resolve_symbols("005930, 000660") == ["005930", "000660"]

    def test_resolve_symbols_default_uses_universe(self):
        from trading.scripts import exit_backtest as eb

        with patch(
            "trading.data.kospi200_backfill.kospi200_universe",
            return_value=["1001", "005930"],
        ):
            assert eb._resolve_symbols(None) == ["1001", "005930"]
