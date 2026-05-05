"""News crawling infrastructure (SPEC-TRADING-013).

Modules:
    sources         — 42-source catalog across 12 sectors
    rss_fetcher     — Async RSS fetching with feedparser
    web_scraper     — httpx + BeautifulSoup headline extraction
    normalizer      — Unified Article dataclass with dedup
    storage         — Postgres news_articles persistence
    context_builder — Sector-aware .md context file generation
    health          — Per-source availability monitoring
    crawler         — Orchestrator coordinating all modules
"""
