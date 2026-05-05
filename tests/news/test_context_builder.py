"""Tests for Module 6: Context Builder (SPEC-TRADING-013 AC-6-*)."""

from __future__ import annotations

from trading.news.context_builder import (
    MACRO_SECTORS,
    SECTOR_DISPLAY_NAMES,
    TICKER_SECTOR_MAP,
    _format_article_line,
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


# --- Characterization tests: HTML stripping in article formatting ---


def test_characterize_format_article_html_summary_stripped():
    """HTML in summary field is stripped when formatting article lines."""
    from datetime import datetime, timezone

    article = {
        "title": "HSBC Takes Hit - WSJ",
        "source_name": "WSJ",
        "published_at": datetime(2026, 5, 5, 9, 0, tzinfo=timezone.utc),
        "summary": '<a href="https://news.google.com/rss/articles/CBMi">WSJ Markets</a>',
    }
    result = _format_article_line(article)
    # HTML garbage summary (< 30 chars after stripping) should NOT appear
    assert "<a " not in result
    assert "Summary:" not in result


def test_characterize_format_article_redundant_summary_suppressed():
    """Summary that repeats title content is suppressed (Google News pattern)."""
    from datetime import datetime, timezone

    article = {
        "title": "Trump broke OPEC. He may regret it - Reuters",
        "source_name": "Reuters Business",
        "published_at": datetime(2026, 5, 5, 6, 0, tzinfo=timezone.utc),
        "summary": "Trump broke OPEC. He may regret it Reuters",
    }
    result = _format_article_line(article)
    # Redundant summary (title repeated) should NOT be shown
    assert "Summary:" not in result


def test_characterize_format_article_real_summary_shown():
    """Articles with real long summaries still display after HTML stripping."""
    from datetime import datetime, timezone

    real_summary = "Federal Reserve holds rates steady amid inflation concerns, " * 2
    article = {
        "title": "Fed Holds Rates",
        "source_name": "Reuters",
        "published_at": datetime(2026, 5, 5, 9, 0, tzinfo=timezone.utc),
        "summary": f"<p>{real_summary}</p>",
    }
    result = _format_article_line(article)
    assert "Summary:" in result
    assert "<p>" not in result
    assert "Federal Reserve" in result


def test_characterize_format_article_no_summary_no_body():
    """Articles with no summary and no body show title only."""
    from datetime import datetime, timezone

    article = {
        "title": "Breaking News",
        "source_name": "AP",
        "published_at": datetime(2026, 5, 5, 9, 0, tzinfo=timezone.utc),
        "summary": None,
        "body_text": None,
    }
    result = _format_article_line(article)
    assert "Breaking News" in result
    assert "Summary:" not in result
