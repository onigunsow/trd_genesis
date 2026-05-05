"""Source catalog — 40 verified news sources across 12 sectors (SPEC-TRADING-013).

Replaces the legacy src/trading/contexts/rss_feeds.py tier-based system.
All sources verified accessible via httpx from Docker container on 2026-05-05.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

# 12 sector categories
SECTORS: tuple[str, ...] = (
    "macro_economy",
    "stock_market",
    "semiconductor",
    "biotech_pharma",
    "energy_commodities",
    "it_ai",
    "finance_banking",
    "auto_ev_battery",
    "steel_materials",
    "retail_consumer",
    "gaming_entertainment",
    "defense_aerospace",
)


@dataclass(frozen=True)
class NewsSource:
    """A single news source definition."""

    name: str
    url: str
    source_type: Literal["rss", "web"]
    sector: str
    language: Literal["en", "ko"]
    notes: str = ""
    last_verified: date = date(2026, 5, 5)


# ──────────────────────────────────────────────────────────────────────────────
# RSS Sources (31 feeds)
# ──────────────────────────────────────────────────────────────────────────────

_RSS_SOURCES: tuple[NewsSource, ...] = (
    # --- macro_economy (5) ---
    NewsSource(
        "Federal Reserve Press", "https://www.federalreserve.gov/feeds/press_all.xml",
        "rss", "macro_economy", "en", "US monetary policy, FOMC statements",
    ),
    NewsSource(
        "Reuters Business", "https://news.google.com/rss/search?q=site:reuters.com+economy+OR+market&hl=en&ceid=US:en",
        "rss", "macro_economy", "en", "Google News proxy for Reuters business",
    ),
    NewsSource(
        "Bloomberg Markets", "https://news.google.com/rss/search?q=site:bloomberg.com+market+OR+economy&hl=en&ceid=US:en",
        "rss", "macro_economy", "en", "Google News proxy for Bloomberg",
    ),
    NewsSource(
        "FT Markets", "https://news.google.com/rss/search?q=site:ft.com+market+OR+economy&hl=en&ceid=US:en",
        "rss", "macro_economy", "en", "Google News proxy for Financial Times",
    ),
    NewsSource(
        "WSJ Markets", "https://news.google.com/rss/search?q=site:wsj.com+market+OR+economy&hl=en&ceid=US:en",
        "rss", "macro_economy", "en", "Google News proxy for Wall Street Journal",
    ),
    # --- stock_market (5) ---
    NewsSource(
        "한국경제 실시간", "https://www.hankyung.com/feed/all-news",
        "rss", "stock_market", "ko", "Korean economy and stocks",
    ),
    NewsSource(
        "매일경제 증권", "https://www.mk.co.kr/rss/30100041/",
        "rss", "stock_market", "ko", "Maeil Business stock section",
    ),
    NewsSource(
        "파이낸셜뉴스 증시", "https://www.fnnews.com/rss/r20/fn_realnews_stock.xml",
        "rss", "stock_market", "ko", "Financial News stocks",
    ),
    NewsSource(
        "연합뉴스 경제", "https://www.yna.co.kr/rss/economy.xml",
        "rss", "stock_market", "ko", "Yonhap economy section",
    ),
    NewsSource(
        "서울경제 경제", "https://www.sedaily.com/Rss/Economy",
        "rss", "stock_market", "ko", "Seoul Economic Daily",
    ),
    # --- semiconductor (3) ---
    NewsSource(
        "Tom's Hardware", "https://www.tomshardware.com/feeds/all",
        "rss", "semiconductor", "en", "Hardware and chip news",
    ),
    NewsSource(
        "AnandTech/Chips", "https://news.google.com/rss/search?q=semiconductor+OR+TSMC+OR+Samsung+foundry&hl=en&ceid=US:en",
        "rss", "semiconductor", "en", "Google News semiconductor aggregation",
    ),
    NewsSource(
        "전자신문 반도체", "https://rss.etnews.com/Section901.xml",
        "rss", "semiconductor", "ko", "ETNews semiconductor section",
    ),
    # --- biotech_pharma (3) ---
    NewsSource(
        "BioSpace", "https://www.biospace.com/news.rss",
        "rss", "biotech_pharma", "en", "Biotech/pharma global news",
    ),
    NewsSource(
        "FiercePharma", "https://www.fiercepharma.com/rss/xml",
        "rss", "biotech_pharma", "en", "Pharmaceutical industry news",
    ),
    NewsSource(
        "약업신문", "https://news.google.com/rss/search?q=site:yakup.com&hl=ko&ceid=KR:ko",
        "rss", "biotech_pharma", "ko", "Google News proxy for yakup.com (pharma)",
    ),
    # --- energy_commodities (3) ---
    NewsSource(
        "OilPrice.com", "https://oilprice.com/rss/main",
        "rss", "energy_commodities", "en", "Oil and energy markets",
    ),
    NewsSource(
        "Geopolitics Energy", "https://news.google.com/rss/search?q=geopolitical+oil+OR+OPEC+OR+LNG&hl=en&ceid=US:en",
        "rss", "energy_commodities", "en", "Geopolitical energy impact",
    ),
    NewsSource(
        "파이낸셜뉴스 경제", "https://www.fnnews.com/rss/r20/fn_realnews_economy.xml",
        "rss", "energy_commodities", "ko", "FN economy (energy overlap)",
    ),
    # --- it_ai (3) ---
    NewsSource(
        "TechCrunch", "https://techcrunch.com/feed/",
        "rss", "it_ai", "en", "Tech and AI startup news",
    ),
    NewsSource(
        "The Verge", "https://www.theverge.com/rss/index.xml",
        "rss", "it_ai", "en", "Consumer tech and AI",
    ),
    NewsSource(
        "전자신문 IT", "https://rss.etnews.com/Section902.xml",
        "rss", "it_ai", "ko", "ETNews IT section",
    ),
    # --- finance_banking (3) ---
    NewsSource(
        "파이낸셜뉴스 금융", "https://www.fnnews.com/rss/r20/fn_realnews_finance.xml",
        "rss", "finance_banking", "ko", "Financial News banking section",
    ),
    NewsSource(
        "한국은행 통화정책", "https://news.google.com/rss/search?q=%ED%95%9C%EA%B5%AD%EC%9D%80%ED%96%89+OR+%EA%B8%B0%EC%A4%80%EA%B8%88%EB%A6%AC+OR+%ED%86%B5%ED%99%94%EC%A0%95%EC%B1%85&hl=ko&ceid=KR:ko",
        "rss", "macro_economy", "ko", "Google News proxy for BOK monetary policy",
    ),
    NewsSource(
        "Global Finance", "https://news.google.com/rss/search?q=banking+OR+fintech+global&hl=en&ceid=US:en",
        "rss", "finance_banking", "en", "Google News global finance",
    ),
    # --- auto_ev_battery (3) ---
    NewsSource(
        "Electrek", "https://electrek.co/feed/",
        "rss", "auto_ev_battery", "en", "EV and battery news",
    ),
    NewsSource(
        "전자신문 자동차", "https://rss.etnews.com/Section903.xml",
        "rss", "auto_ev_battery", "ko", "ETNews automotive/EV",
    ),
    NewsSource(
        "전기차시대 (EVPOST)", "https://www.evpost.co.kr/wp/feed/",
        "rss", "auto_ev_battery", "ko", "Korean EV industry WordPress feed",
    ),
    # --- steel_materials (1) ---
    NewsSource(
        "스틸데일리", "https://www.steeldaily.co.kr/rss/allArticle.xml",
        "rss", "steel_materials", "ko", "Korean steel industry daily",
    ),
    # --- retail_consumer (1) ---
    NewsSource(
        "Retail Dive", "https://www.retaildive.com/feeds/news/",
        "rss", "retail_consumer", "en", "Retail and consumer industry",
    ),
    # --- gaming_entertainment (1) ---
    NewsSource(
        "게임메카 RSS", "https://www.gamemeca.com/rss.php",
        "rss", "gaming_entertainment", "ko", "Korean game industry",
    ),
    # --- defense_aerospace (1) ---
    NewsSource(
        "Defense News", "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml",
        "rss", "defense_aerospace", "en", "Global defense and aerospace",
    ),
)

# ──────────────────────────────────────────────────────────────────────────────
# Web Scraping Sources (8 sites)
# ──────────────────────────────────────────────────────────────────────────────

_WEB_SOURCES: tuple[NewsSource, ...] = (
    # --- semiconductor (1) ---
    NewsSource(
        "디일렉 (The Elec)", "https://www.thelec.kr/news/articleList.html?sc_section_code=S1N1",
        "web", "semiconductor", "ko", "Korean display/semiconductor exclusive",
    ),
    # --- biotech_pharma (1) ---
    NewsSource(
        "바이오타임즈", "https://www.biotimes.co.kr/news/articleList.html?sc_section_code=S1N2",
        "web", "biotech_pharma", "ko", "Korean biotech/pharma news portal",
    ),
    # --- finance_banking (1) ---
    NewsSource(
        "한국금융신문 웹", "https://www.fntimes.com/html/index.php",
        "web", "finance_banking", "ko", "Korea Financial Times homepage headlines",
    ),
    # --- energy_commodities (1) ---
    NewsSource(
        "에너지신문", "https://www.energy-news.co.kr/news/articleList.html?sc_section_code=S1N1",
        "web", "energy_commodities", "ko", "Korean energy industry news",
    ),
    # --- it_ai (1) ---
    NewsSource(
        "인공지능신문", "https://www.aitimes.kr/news/articleList.html?sc_section_code=S1N1",
        "web", "it_ai", "ko", "Korean AI industry news",
    ),
    # --- steel_materials (1) ---
    NewsSource(
        "철강금속신문", "https://www.snmnews.com/news/articleList.html?sc_section_code=S1N1",
        "web", "steel_materials", "ko", "Korean steel/metals industry",
    ),
    # --- gaming_entertainment (1) ---
    NewsSource(
        "게임메카 웹", "https://www.gamemeca.com/news.php",
        "web", "gaming_entertainment", "ko", "Game Meca news page",
    ),
    # --- stock_market (1) ---
    NewsSource(
        "네이버증권 뉴스", "https://finance.naver.com/news/mainnews.naver",
        "web", "stock_market", "ko", "Naver Finance main news",
    ),
    # --- macro_economy (1) ---
    # NOTE: 한국은행 보도자료 removed — site under maintenance ("콘텐츠 준비중"),
    #       JS-rendered AJAX content, requires Playwright. Replaced by RSS proxy above.
    # NOTE: 국방일보 removed — SPA with zero static content, requires Playwright.
    #       Defense sector covered by Defense News RSS source.
)

# Combined full catalog
_ALL_SOURCES: tuple[NewsSource, ...] = _RSS_SOURCES + _WEB_SOURCES


# ──────────────────────────────────────────────────────────────────────────────
# Helper functions (REQ-NEWS-01-5)
# ──────────────────────────────────────────────────────────────────────────────

def all_sources() -> list[NewsSource]:
    """Return all 40 sources (32 RSS + 8 web)."""
    return list(_ALL_SOURCES)


def get_sources_by_sector(sector: str) -> list[NewsSource]:
    """Filter sources by sector name."""
    return [s for s in _ALL_SOURCES if s.sector == sector]


def get_sources_by_type(source_type: str) -> list[NewsSource]:
    """Filter sources by type: 'rss' or 'web'."""
    return [s for s in _ALL_SOURCES if s.source_type == source_type]


def get_sources_by_language(lang: str) -> list[NewsSource]:
    """Filter sources by language: 'en' or 'ko'."""
    return [s for s in _ALL_SOURCES if s.language == lang]
