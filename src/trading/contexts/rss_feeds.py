"""RSS feed catalog (SPEC-TRADING-007 plan.md Tier 1~6).

2026-05-04 박세훈 님 동의된 매트릭스. Tier 4(Google News)는 정책 모니터링 필요.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    tier: int
    category: str   # 'economy' | 'stock' | 'finance' | 'industry' | 'official' | 'global'
    notes: str = ""


# Tier 1 — 검증된 한국 매체 RSS (경제·금융 특화)
TIER1_KOREA_PRESS: tuple[Feed, ...] = (
    Feed("한국경제 경제", "http://rss.hankyung.com/economy.xml", 1, "economy"),
    Feed("한국경제 증시", "http://rss.hankyung.com/stock.xml", 1, "stock"),
    Feed("한국경제 산업", "http://rss.hankyung.com/industry.xml", 1, "industry"),
    Feed("매일경제 경제", "http://file.mk.co.kr/news/rss/rss_30100041.xml", 1, "economy"),
    Feed("파이낸셜뉴스 증시", "http://www.fnnews.com/rss/fn_realnews_stock.xml", 1, "stock"),
    Feed("파이낸셜뉴스 금융", "http://www.fnnews.com/rss/fn_realnews_finance.xml", 1, "finance"),
    Feed("헤럴드경제 증시", "http://biz.heraldm.com/rss/010106000000.xml", 1, "stock"),
    Feed("조선비즈 마켓", "http://biz.chosun.com/site/data/rss/market.xml", 1, "stock"),
    Feed("조선비즈 정책·금융", "http://biz.chosun.com/site/data/rss/policybank.xml", 1, "finance"),
    Feed("중앙 경제", "http://rss.joinsmsn.com/joins_money_list.xml", 1, "economy"),
)

# Tier 2 — 한국 공식
TIER2_KOREA_OFFICIAL: tuple[Feed, ...] = (
    Feed("금융위원회", "https://www.fsc.go.kr/ut060101", 2, "official",
         notes="페이지 형식, RSS 검증 필요"),
    Feed("정책브리핑", "https://www.korea.kr/etc/rss.do", 2, "official"),
)

# Tier 3 — 글로벌 공식
TIER3_GLOBAL_OFFICIAL: tuple[Feed, ...] = (
    Feed("Federal Reserve press", "https://www.federalreserve.gov/feeds/press_all.xml", 3, "global"),
)

# Tier 4 — 글로벌 마켓 (Google News query, 정책 모니터링 대상)
TIER4_GLOBAL_NEWS: tuple[Feed, ...] = (
    Feed(
        "Reuters Korea/Asia",
        "https://news.google.com/rss/search?q=site:reuters.com+(korea+OR+asia)+(market+OR+stock+OR+economy)&hl=en-KR&gl=KR&ceid=KR:en",
        4, "global", notes="Google News query — 월 1회 헬스체크",
    ),
    Feed(
        "Bloomberg Korea",
        "https://news.google.com/rss/search?q=site:bloomberg.com+korea+market&hl=en-KR&gl=KR&ceid=KR:en",
        4, "global", notes="Google News query",
    ),
    Feed(
        "FT Markets headlines",
        "https://news.google.com/rss/search?q=site:ft.com+market&hl=en-KR&gl=KR&ceid=KR:en",
        4, "global", notes="Google News query — 헤드라인만",
    ),
    Feed(
        "WSJ Markets headlines",
        "https://news.google.com/rss/search?q=site:wsj.com+market&hl=en-KR&gl=KR&ceid=KR:en",
        4, "global", notes="Google News query — 헤드라인만",
    ),
    Feed(
        "Geopolitics global",
        "https://news.google.com/rss/search?q=geopolitical+(oil+OR+OPEC+OR+iran+OR+china)&hl=en&ceid=US:en",
        4, "global", notes="Google News 키워드 query",
    ),
)


def all_news_feeds() -> tuple[Feed, ...]:
    """All feeds used by macro_news.md builder."""
    return TIER1_KOREA_PRESS + TIER2_KOREA_OFFICIAL + TIER3_GLOBAL_OFFICIAL + TIER4_GLOBAL_NEWS


def tier4_only() -> tuple[Feed, ...]:
    """Tier 4 only — used by monthly health check."""
    return TIER4_GLOBAL_NEWS


# HTTP 식별
USER_AGENT = "trading-bot/0.1 (personal use; non-commercial)"
HTTP_TIMEOUT = 15.0
POLITE_DELAY_SECONDS = 1.0
