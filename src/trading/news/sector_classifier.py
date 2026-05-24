"""SPEC-TRADING-026 (c3) — content-based sector reclassification.

Articles inherit the sector of the *feed* they were crawled from
(``Article.sector = source.sector``). Generic feeds ("FN economy" tagged
``energy_commodities``) and broad Google-News queries therefore mislabel
articles whose content belongs to a different sector — corrupting the
portfolio-relevance filter downstream.

``classify_sector`` inspects the article's own title/body and overrides the feed
sector ONLY when the content matches a different sector with high confidence.
The bar is deliberately high (title-weighted score >= 2, strictly beating the
feed sector's own score) so correctly-tagged articles are never re-routed.

Sectors are the canonical set from ``trading.news.sources.SECTORS``. The broad
catch-alls (``macro_economy``, ``stock_market``) intentionally have no keyword
set: we never reclassify *into* them (too noisy), only away from a wrong
specific sector into the right specific sector.
"""

from __future__ import annotations

# Distinctive, low-false-positive keywords per sector. Korean-first (most feeds
# are Korean); a few unambiguous uppercase tokens are kept for English items.
# @MX:NOTE: SPEC-TRADING-026 c3 — keep these distinctive; vague tokens (bare
# "AI", "EV") are omitted because substring matching would over-trigger.
_SECTOR_KEYWORDS: dict[str, tuple[str, ...]] = {
    "biotech_pharma": (
        "바이오", "제약", "신약", "임상", "항체", "백신", "치료제", "진단",
        "세포치료", "바이오시밀러", "식약처", "항암", "줄기세포", "FDA",
    ),
    "semiconductor": (
        "반도체", "HBM", "파운드리", "웨이퍼", "D램", "디램", "낸드",
        "메모리반도체", "TSMC", "팹리스", "노광", "후공정",
    ),
    "energy_commodities": (
        "유가", "원유", "천연가스", "OPEC", "정유", "원자재", "셰일", "LNG",
        # SPEC-026 c3 r2: Mideast/oil geopolitics → energy (the source feeds
        # themselves classify "geopolitical energy impact" here).
        "이란", "호르무즈", "우라늄", "사우디", "중동",
    ),
    # SPEC-026 c3 r2: index / pension / domestic-market stories → stock_market.
    # Specific index terms only (avoid the over-broad bare "시장"/"주식").
    "stock_market": (
        "코스피", "코스닥", "국민연금", "증시", "코스피지수", "코스닥지수",
    ),
    "auto_ev_battery": (
        "전기차", "배터리", "2차전지", "이차전지", "양극재", "음극재",
        "완성차", "전고체",
    ),
    "finance_banking": (
        "은행", "예금", "대출", "보험사", "지주", "핀테크", "카드사",
        "저축은행", "증권사",
    ),
    "it_ai": (
        "인공지능", "클라우드", "데이터센터", "소프트웨어", "플랫폼",
        "생성형", "챗봇",
    ),
    "steel_materials": ("철강", "포스코", "조선업", "석유화학", "정유화학"),
    "defense_aerospace": ("방산", "전투기", "미사일", "방위산업", "우주발사체", "위성"),
    "retail_consumer": ("백화점", "편의점", "이커머스", "면세점", "소비재"),
    "gaming_entertainment": ("웹툰", "K팝", "엔터테인먼트", "게임사"),
}

# Title hits weigh more than body hits — the headline is the strongest topic
# signal.
_TITLE_WEIGHT = 2
_BODY_WEIGHT = 1
# Minimum (weighted) score for an override. A single title keyword (=2) suffices,
# but a lone body mention (=1) does not.
_MIN_OVERRIDE_SCORE = 2


def _score(haystack_title: str, haystack_body: str, keywords: tuple[str, ...]) -> int:
    score = 0
    for kw in keywords:
        k = kw.lower()
        if k in haystack_title:
            score += _TITLE_WEIGHT
        elif k in haystack_body:
            score += _BODY_WEIGHT
    return score


def classify_sector(title: str, text: str | None, fallback: str) -> str:
    """Return the best content-derived sector, or ``fallback`` when unsure.

    Override rules (all must hold):
    - the top-scoring sector differs from ``fallback``,
    - its weighted score is >= ``_MIN_OVERRIDE_SCORE``,
    - it strictly beats the ``fallback`` sector's own score (ambiguous ties keep
      the feed sector).
    """
    t = (title or "").lower()
    b = (text or "").lower()

    scores = {
        sector: _score(t, b, kws) for sector, kws in _SECTOR_KEYWORDS.items()
    }
    best_sector = max(scores, key=lambda s: scores[s])
    best_score = scores[best_sector]
    fallback_score = scores.get(fallback, 0)

    if (
        best_score >= _MIN_OVERRIDE_SCORE
        and best_sector != fallback
        and best_score > fallback_score
    ):
        return best_sector
    return fallback
