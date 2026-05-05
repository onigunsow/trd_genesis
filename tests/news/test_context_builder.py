"""Tests for Module 6: Context Builder (SPEC-TRADING-013 AC-6-*)."""

from __future__ import annotations

from trading.news.context_builder import (
    MACRO_SECTORS,
    SECTOR_DISPLAY_NAMES,
    TICKER_SECTOR_MAP,
    get_sector_for_ticker,
)


def test_macro_sectors_defined():
    """AC-6-1: Macro sectors include macro_economy, finance_banking, energy_commodities."""
    assert "macro_economy" in MACRO_SECTORS
    assert "finance_banking" in MACRO_SECTORS
    assert "energy_commodities" in MACRO_SECTORS


def test_ticker_sector_mapping_known():
    """AC-6-6: Known ticker maps to correct sector."""
    assert get_sector_for_ticker("005930") == "semiconductor"  # Samsung
    assert get_sector_for_ticker("000660") == "semiconductor"  # SK Hynix
    assert get_sector_for_ticker("035420") == "it_ai"          # NAVER
    assert get_sector_for_ticker("373220") == "auto_ev_battery"  # LG Energy


def test_ticker_sector_mapping_unknown():
    """AC-6-7: Unknown ticker defaults to stock_market."""
    assert get_sector_for_ticker("999999") == "stock_market"
    assert get_sector_for_ticker("000000") == "stock_market"


def test_no_llm_imports():
    """AC-6-5: No LLM API imports in context_builder module."""
    import importlib
    mod = importlib.import_module("trading.news.context_builder")
    source_code = open(mod.__file__).read()
    assert "anthropic" not in source_code.lower()
    assert "openai" not in source_code.lower()
    assert "Anthropic" not in source_code


def test_sector_display_names_complete():
    """All 12 sectors have display names."""
    from trading.news.sources import SECTORS
    for sector in SECTORS:
        assert sector in SECTOR_DISPLAY_NAMES, f"Missing display name for: {sector}"
