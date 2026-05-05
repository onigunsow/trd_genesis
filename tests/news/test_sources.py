"""Tests for Module 1: Source Catalog (SPEC-TRADING-013 AC-1-*)."""

from __future__ import annotations

from dataclasses import fields
from datetime import date

from trading.news.sources import (
    SECTORS,
    NewsSource,
    all_sources,
    get_sources_by_language,
    get_sources_by_sector,
    get_sources_by_type,
)


def test_news_source_is_frozen_dataclass():
    """AC-1-1: NewsSource is a frozen dataclass with required fields."""
    source = all_sources()[0]
    assert isinstance(source, NewsSource)
    # Frozen check
    try:
        source.name = "changed"  # type: ignore[misc]
        assert False, "Should raise FrozenInstanceError"
    except Exception:
        pass
    # Field validation
    field_names = {f.name for f in fields(NewsSource)}
    required = {"name", "url", "source_type", "sector", "language", "notes", "last_verified"}
    assert required.issubset(field_names)


def test_sector_completeness():
    """AC-1-2: Exactly 12 sectors defined."""
    assert len(SECTORS) == 12
    expected = {
        "macro_economy", "stock_market", "semiconductor", "biotech_pharma",
        "energy_commodities", "it_ai", "finance_banking", "auto_ev_battery",
        "steel_materials", "retail_consumer", "gaming_entertainment", "defense_aerospace",
    }
    assert set(SECTORS) == expected


def test_source_count_validation():
    """AC-1-3: Exactly 42 sources — 31 RSS + 11 web."""
    sources = all_sources()
    assert len(sources) == 42
    rss = [s for s in sources if s.source_type == "rss"]
    web = [s for s in sources if s.source_type == "web"]
    assert len(rss) == 31
    assert len(web) == 11


def test_sources_by_sector():
    """AC-1-4: Sector filtering returns non-empty correct results."""
    semi = get_sources_by_sector("semiconductor")
    assert len(semi) > 0
    assert all(s.sector == "semiconductor" for s in semi)


def test_sources_by_type():
    """AC-1-5: Type filtering returns correct counts."""
    web = get_sources_by_type("web")
    assert len(web) == 11
    assert all(s.source_type == "web" for s in web)

    rss = get_sources_by_type("rss")
    assert len(rss) == 31
    assert all(s.source_type == "rss" for s in rss)


def test_sources_by_language():
    """AC-1-6: Language filtering returns appropriate subsets."""
    ko = get_sources_by_language("ko")
    en = get_sources_by_language("en")
    assert len(ko) > 0
    assert len(en) > 0
    assert all(s.language == "ko" for s in ko)
    assert all(s.language == "en" for s in en)
    assert len(ko) + len(en) == 42


def test_all_sources_have_valid_sectors():
    """All sources reference one of the 12 defined sectors."""
    for source in all_sources():
        assert source.sector in SECTORS, f"{source.name} has invalid sector: {source.sector}"


def test_all_sources_have_urls():
    """Every source has a non-empty URL."""
    for source in all_sources():
        assert source.url, f"{source.name} has empty URL"
        assert source.url.startswith("http"), f"{source.name} URL not HTTP: {source.url}"


def test_last_verified_date():
    """AC-1-1: last_verified field present with valid date."""
    for source in all_sources():
        assert isinstance(source.last_verified, date)
