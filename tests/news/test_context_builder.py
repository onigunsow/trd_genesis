"""Tests for Module 6: Context Builder (SPEC-TRADING-013 AC-6-*)."""

from __future__ import annotations

from datetime import UTC

from trading.news.context_builder import (
    MACRO_SECTORS,
    SECTOR_DISPLAY_NAMES,
    _format_article_line,
)

# SPEC-TRADING-060 REQ-060-1: TICKER_SECTOR_MAP / get_sector_for_ticker 제거됨.
# 하드코딩 철폐 → resolve_ticker_sector (ticker_metadata + YAML 매핑) 로 대체.


def test_macro_sectors_defined():
    """AC-6-1: Macro sectors include macro_economy, finance_banking, energy_commodities."""
    assert "macro_economy" in MACRO_SECTORS
    assert "finance_banking" in MACRO_SECTORS
    assert "energy_commodities" in MACRO_SECTORS


def test_ticker_sector_resolver_exists():
    """SPEC-TRADING-060: TICKER_SECTOR_MAP 제거 → resolve_ticker_sector 이용 가능."""
    # context_builder 에서 하드코딩 제거, ticker_sector 모듈로 위임
    from trading.news.ticker_sector import resolve_ticker_sector

    assert callable(resolve_ticker_sector)


def test_ticker_sector_map_removed():
    """SPEC-TRADING-060: TICKER_SECTOR_MAP / get_sector_for_ticker 제거 확인 (REQ-060-1)."""
    import trading.news.context_builder as cb

    assert not hasattr(cb, "TICKER_SECTOR_MAP"), "TICKER_SECTOR_MAP 미제거"
    assert not hasattr(cb, "get_sector_for_ticker"), "get_sector_for_ticker 미제거"


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
    from datetime import datetime

    article = {
        "title": "HSBC Takes Hit - WSJ",
        "source_name": "WSJ",
        "published_at": datetime(2026, 5, 5, 9, 0, tzinfo=UTC),
        "summary": '<a href="https://news.google.com/rss/articles/CBMi">WSJ Markets</a>',
    }
    result = _format_article_line(article)
    # HTML garbage summary (< 30 chars after stripping) should NOT appear
    assert "<a " not in result
    assert "Summary:" not in result


def test_characterize_format_article_redundant_summary_suppressed():
    """Summary that repeats title content is suppressed (Google News pattern)."""
    from datetime import datetime

    article = {
        "title": "Trump broke OPEC. He may regret it - Reuters",
        "source_name": "Reuters Business",
        "published_at": datetime(2026, 5, 5, 6, 0, tzinfo=UTC),
        "summary": "Trump broke OPEC. He may regret it Reuters",
    }
    result = _format_article_line(article)
    # Redundant summary (title repeated) should NOT be shown
    assert "Summary:" not in result


def test_characterize_format_article_real_summary_shown():
    """Articles with real long summaries still display after HTML stripping."""
    from datetime import datetime

    real_summary = "Federal Reserve holds rates steady amid inflation concerns, " * 2
    article = {
        "title": "Fed Holds Rates",
        "source_name": "Reuters",
        "published_at": datetime(2026, 5, 5, 9, 0, tzinfo=UTC),
        "summary": f"<p>{real_summary}</p>",
    }
    result = _format_article_line(article)
    assert "Summary:" in result
    assert "<p>" not in result
    assert "Federal Reserve" in result


def test_characterize_format_article_no_summary_no_body():
    """Articles with no summary and no body show title only."""
    from datetime import datetime

    article = {
        "title": "Breaking News",
        "source_name": "AP",
        "published_at": datetime(2026, 5, 5, 9, 0, tzinfo=UTC),
        "summary": None,
        "body_text": None,
    }
    result = _format_article_line(article)
    assert "Breaking News" in result
    assert "Summary:" not in result
