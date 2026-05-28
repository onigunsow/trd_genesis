"""SPEC-TRADING-029 v0.2.0 — ticker-name resolver (REQ-029-9).

``trading.data.ticker_names.ticker_name(ticker)`` resolves a KRX display name
via pykrx with an in-memory lru_cache, falling back to the static
``context.TICKER_NAMES`` dict and finally to ``None``.

All tests mock pykrx so the suite stays offline, and clear the lru_cache before
each scenario so cached results from a previous test do not leak.
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset the lru_cache between tests (cached names would leak otherwise)."""
    from trading.data.ticker_names import ticker_name

    ticker_name.cache_clear()
    yield
    ticker_name.cache_clear()


def _install_fake_pykrx(monkeypatch, get_name: MagicMock) -> None:
    """Install a fake ``pykrx.stock`` module exposing ``get_market_ticker_name``."""
    pykrx = ModuleType("pykrx")
    stock = ModuleType("pykrx.stock")
    stock.get_market_ticker_name = get_name  # type: ignore[attr-defined]
    pykrx.stock = stock  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pykrx", pykrx)
    monkeypatch.setitem(sys.modules, "pykrx.stock", stock)


class TestTickerNameResolution:
    def test_resolves_name_via_pykrx(self, monkeypatch):
        """AC-029-13: pykrx returns the display name."""
        from trading.data.ticker_names import ticker_name

        get_name = MagicMock(return_value="삼성전자")
        _install_fake_pykrx(monkeypatch, get_name)

        assert ticker_name("005930") == "삼성전자"
        get_name.assert_called_once_with("005930")

    def test_lru_cache_calls_pykrx_once(self, monkeypatch):
        """EC-029-9: repeated calls for the same ticker hit pykrx only once."""
        from trading.data.ticker_names import ticker_name

        get_name = MagicMock(return_value="삼성전자")
        _install_fake_pykrx(monkeypatch, get_name)

        for _ in range(5):
            assert ticker_name("005930") == "삼성전자"
        assert get_name.call_count == 1


class TestTickerNameFallback:
    def test_falls_back_to_static_dict_on_pykrx_error(self, monkeypatch):
        """AC-029-13 fallback: pykrx raises → use context.TICKER_NAMES."""
        from trading.data.ticker_names import ticker_name

        get_name = MagicMock(side_effect=RuntimeError("network down"))
        _install_fake_pykrx(monkeypatch, get_name)

        # 000660 is present in the static context.TICKER_NAMES dict.
        assert ticker_name("000660") == "SK하이닉스"

    def test_returns_none_when_unknown_everywhere(self, monkeypatch):
        """AC-029-13: pykrx empty + not in static dict → None (graceful)."""
        from trading.data.ticker_names import ticker_name

        get_name = MagicMock(return_value="")  # pykrx returns empty for unknown
        _install_fake_pykrx(monkeypatch, get_name)

        assert ticker_name("999999") is None

    def test_falls_back_when_pykrx_not_importable(self, monkeypatch):
        """If pykrx import fails entirely, the static dict still works."""
        from trading.data.ticker_names import ticker_name

        # Force the pykrx import to fail by removing it from sys.modules and
        # blocking re-import.
        monkeypatch.setitem(sys.modules, "pykrx", None)
        assert ticker_name("005930") == "삼성전자"
