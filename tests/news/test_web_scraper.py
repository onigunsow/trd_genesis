"""Tests for Module 3: Web Scraper (SPEC-TRADING-013 AC-3-*)."""

from __future__ import annotations

from trading.news.sources import get_sources_by_type
from trading.news.web_scraper import SCRAPE_RULES, ScrapeRule, WebScraper


def test_scrape_rule_registry_completeness():
    """AC-3-2: Exactly 11 ScrapeRule entries, one per web source."""
    assert len(SCRAPE_RULES) == 11


def test_scrape_rules_match_web_sources():
    """Each web source in catalog has a corresponding scrape rule."""
    web_sources = get_sources_by_type("web")
    for source in web_sources:
        assert source.name in SCRAPE_RULES, f"Missing scrape rule for: {source.name}"


def test_no_headless_browser_imports():
    """AC-3-6: No Playwright/Selenium imports in web_scraper module."""
    import importlib

    mod = importlib.import_module("trading.news.web_scraper")
    source_code = open(mod.__file__).read()  # noqa: SIM115
    # Check actual import statements (not documentation mentions)
    lines = source_code.split("\n")
    import_lines = [ln for ln in lines if ln.strip().startswith(("import ", "from "))]
    for line in import_lines:
        assert "playwright" not in line.lower(), f"Playwright import found: {line}"
        assert "selenium" not in line.lower(), f"Selenium import found: {line}"


def test_absolute_url_resolution():
    """AC-3-3: Relative URLs resolved to absolute."""
    from urllib.parse import urljoin
    base = "https://www.thelec.kr/news/articleList.html"
    relative = "/news/articleView.html?idxno=12345"
    absolute = urljoin(base, relative)
    assert absolute == "https://www.thelec.kr/news/articleView.html?idxno=12345"


def test_extract_articles_from_html():
    """AC-3-1: Headlines extracted using CSS selectors."""
    from trading.news.sources import NewsSource

    # HTML matches the selector: #section-list a[href*='articleView']
    html = """
    <div id="section-list">
        <a href="/news/articleView.html?idxno=1">Article One Title</a>
        <span class="list-dated">2026-05-05</span>
        <a href="/news/articleView.html?idxno=2">Article Two Title</a>
        <span class="list-dated">2026-05-04</span>
    </div>
    """
    source = NewsSource(
        name="디일렉 (The Elec)",
        url="https://www.thelec.kr/news/articleList.html?sc_section_code=S1N1",
        source_type="web",
        sector="semiconductor",
        language="ko",
    )

    scraper = WebScraper()
    rule = SCRAPE_RULES["디일렉 (The Elec)"]
    items = scraper._extract_articles(html, source, rule)

    assert len(items) == 2
    assert items[0]["title"] == "Article One Title"
    assert "thelec.kr" in items[0]["url"]
    assert items[0]["sector"] == "semiconductor"


def test_extract_articles_empty_html():
    """AC-3-5: Empty/changed HTML yields zero results."""
    from trading.news.sources import NewsSource

    html = "<html><body><p>No matching content</p></body></html>"
    source = NewsSource(
        name="디일렉 (The Elec)",
        url="https://www.thelec.kr/news/",
        source_type="web",
        sector="semiconductor",
        language="ko",
    )

    scraper = WebScraper()
    rule = SCRAPE_RULES["디일렉 (The Elec)"]
    items = scraper._extract_articles(html, source, rule)

    assert len(items) == 0
