"""Sector context builder — generates macro_news.md and micro_news.md (SPEC-TRADING-013 Module 6).

Pure code, NO LLM calls. Generates headline aggregation + formatting from DB.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from trading.contexts.utils import atomic_write, contexts_dir, now_kst_str
from trading.db.session import audit
from trading.news.storage import get_articles_by_sector, get_articles_multi_sector

LOG = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")

# Macro sectors (REQ-NEWS-06-2)
MACRO_SECTORS = ["macro_economy", "finance_banking", "energy_commodities"]

# Sector display names for markdown headers
SECTOR_DISPLAY_NAMES: dict[str, str] = {
    "macro_economy": "Global Macro Economy",
    "stock_market": "Stock Market",
    "semiconductor": "Semiconductor",
    "biotech_pharma": "Biotech & Pharma",
    "energy_commodities": "Energy & Commodities",
    "it_ai": "IT & AI",
    "finance_banking": "Finance & Banking",
    "auto_ev_battery": "Auto / EV / Battery",
    "steel_materials": "Steel & Materials",
    "retail_consumer": "Retail & Consumer",
    "gaming_entertainment": "Gaming & Entertainment",
    "defense_aerospace": "Defense & Aerospace",
}

# Ticker-to-sector mapping (REQ-NEWS-06-7)
# Known Korean tickers mapped to sectors
TICKER_SECTOR_MAP: dict[str, str] = {
    # Semiconductor
    "005930": "semiconductor",   # Samsung Electronics
    "000660": "semiconductor",   # SK Hynix
    "042700": "semiconductor",   # Hanmi Semiconductor
    # Biotech/Pharma
    "068270": "biotech_pharma",  # Celltrion
    "207940": "biotech_pharma",  # Samsung Biologics
    "091990": "biotech_pharma",  # Celltrion Healthcare
    # IT/AI
    "035420": "it_ai",           # NAVER
    "035720": "it_ai",           # Kakao
    "036570": "it_ai",           # NCsoft
    # Auto/EV/Battery
    "373220": "auto_ev_battery", # LG Energy Solution
    "006400": "auto_ev_battery", # Samsung SDI
    "005380": "auto_ev_battery", # Hyundai Motor
    # Finance
    "105560": "finance_banking", # KB Financial
    "055550": "finance_banking", # Shinhan Financial
    # Energy
    "051910": "energy_commodities",  # LG Chem
    # Steel
    "005490": "steel_materials",     # POSCO
    # Retail
    "004170": "retail_consumer",     # Shinsegae
    # Gaming
    "263750": "gaming_entertainment", # Pearl Abyss
    "112040": "gaming_entertainment", # Wemade
    # Defense
    "012450": "defense_aerospace",   # Hanwha Aerospace
    "047810": "defense_aerospace",   # Korea Aerospace
}


def get_sector_for_ticker(ticker: str) -> str:
    """Map ticker to sector. Unknown tickers default to stock_market (REQ-NEWS-06-7)."""
    return TICKER_SECTOR_MAP.get(ticker, "stock_market")


def _format_article_line(article: dict[str, Any]) -> str:
    """Format a single article as markdown bullet."""
    title = article.get("title", "")
    source = article.get("source_name", "")
    pub = article.get("published_at")
    if isinstance(pub, datetime):
        date_str = pub.astimezone(KST).strftime("%m/%d %H:%M")
    else:
        date_str = ""
    return f"- {title} | {source} | {date_str}"


def _format_sector_section(sector: str, articles: list[dict[str, Any]]) -> str:
    """Format a sector section with header and article list."""
    display_name = SECTOR_DISPLAY_NAMES.get(sector, sector)
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")

    if not articles:
        return f"## {display_name} [DATA UNAVAILABLE]\n"

    header = f"## {display_name} ({len(articles)} articles, last updated {now})"
    lines = [header, ""]
    for article in articles:
        lines.append(_format_article_line(article))
    lines.append("")
    return "\n".join(lines)


def build_macro_news() -> str:
    """Generate macro_news.md from macro/finance/energy sector articles.

    - Last 24 hours (crawled 7x/day, so always fresh)
    - English-language prioritized
    - Maximum 50 headlines total
    - Grouped by sector
    - Each build OVERWRITES previous content (snapshot, not log)
    """
    updated_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    parts = [
        f"# Macro News (Sector-based) - {datetime.now(KST).date().isoformat()}",
        f"_Generated: {now_kst_str()} | Source: news_articles DB (42 sources)_",
        f"_Last Updated: {updated_at}_",
        "",
    ]

    total_count = 0
    max_total = 50
    remaining = max_total

    for sector in MACRO_SECTORS:
        if remaining <= 0:
            break
        # English prioritized for macro (last 24h window)
        articles = get_articles_by_sector(
            sector, days=1, language="en", limit=remaining,
        )
        # Supplement with Korean if needed
        if len(articles) < min(remaining, 20):
            more = get_articles_by_sector(
                sector, days=1, language="ko", limit=min(remaining, 20) - len(articles),
            )
            articles.extend(more)

        parts.append(_format_sector_section(sector, articles))
        total_count += len(articles)
        remaining -= len(articles)

    parts.append("---")
    parts.append(f"_Total: {total_count} headlines | Sectors: {len(MACRO_SECTORS)} | No LLM_")

    return "\n".join(parts)


def build_micro_news(watchlist: list[str] | None = None) -> str:
    """Generate micro_news.md with sector-specific news for watchlist tickers.

    - Last 24 hours (crawled 7x/day, so always fresh)
    - Korean-language prioritized for domestic tickers
    - Maximum 30 headlines per sector
    - Empty watchlist -> full coverage mode (REQ-NEWS-06-5)
    - Each build OVERWRITES previous content (snapshot, not log)
    """
    from trading.news.sources import SECTORS

    updated_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    parts = [
        f"# Micro News (Sector-specific) - {datetime.now(KST).date().isoformat()}",
        f"_Generated: {now_kst_str()} | Source: news_articles DB_",
        f"_Last Updated: {updated_at}_",
        "",
    ]

    # Determine sectors from watchlist
    if watchlist:
        target_sectors = list(set(get_sector_for_ticker(t) for t in watchlist))
    else:
        # Full coverage mode when watchlist is empty
        target_sectors = list(SECTORS)

    # Query articles for target sectors (last 24h window)
    sector_articles = get_articles_multi_sector(
        target_sectors, days=1, language_priority="ko", limit_per_sector=30,
    )

    total_count = 0
    for sector in target_sectors:
        articles = sector_articles.get(sector, [])
        parts.append(_format_sector_section(sector, articles))
        total_count += len(articles)

    parts.append("---")
    parts.append(
        f"_Total: {total_count} headlines | "
        f"Sectors: {len(target_sectors)} | "
        f"Watchlist: {len(watchlist) if watchlist else 'full coverage'} | No LLM_"
    )

    return "\n".join(parts)


def write_macro_news() -> int:
    """Build and write macro_news.md to data/contexts/."""
    target = contexts_dir() / "macro_news.md"
    try:
        content = build_macro_news()
        if not content.strip():
            raise RuntimeError("macro_news builder returned empty content")
        atomic_write(target, content)
        audit("NEWS_CONTEXT_BUILD_OK", actor="cron.news", details={
            "name": "macro_news", "path": str(target), "bytes": len(content),
        })
        LOG.info("macro_news.md built: %d bytes", len(content))
        return 0
    except Exception as e:  # noqa: BLE001
        LOG.exception("macro_news.md build failed")
        audit("NEWS_CONTEXT_BUILD_FAIL", actor="cron.news", details={
            "name": "macro_news", "error": str(e),
        })
        return 1


def write_micro_news(watchlist: list[str] | None = None) -> int:
    """Build and write micro_news.md to data/contexts/."""
    target = contexts_dir() / "micro_news.md"
    try:
        content = build_micro_news(watchlist)
        if not content.strip():
            raise RuntimeError("micro_news builder returned empty content")
        atomic_write(target, content)
        audit("NEWS_CONTEXT_BUILD_OK", actor="cron.news", details={
            "name": "micro_news", "path": str(target), "bytes": len(content),
        })
        LOG.info("micro_news.md built: %d bytes", len(content))
        return 0
    except Exception as e:  # noqa: BLE001
        LOG.exception("micro_news.md build failed")
        audit("NEWS_CONTEXT_BUILD_FAIL", actor="cron.news", details={
            "name": "micro_news", "error": str(e),
        })
        return 1
