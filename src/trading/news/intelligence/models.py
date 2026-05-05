"""Shared dataclasses for News Intelligence pipeline (SPEC-TRADING-014)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class AnalysisResult:
    """Per-article analysis output from Haiku."""

    article_id: int
    summary_2line: str  # investment_implication (actionable 2-line Korean)
    impact_score: int  # 0-5 (0 = noise/no investment relevance)
    keywords: list[str]
    sentiment: str  # positive | neutral | negative
    classification: str = "company_specific"  # macro_market_moving | sector_specific | company_specific | noise
    token_input: int = 0
    token_output: int = 0
    cost_krw: float = 0.0


@dataclass
class StoryCluster:
    """A group of articles covering the same event."""

    id: int | None = None
    representative_title: str = ""
    article_ids: list[int] = field(default_factory=list)
    source_count: int = 0
    impact_max: int = 0
    sector: str = ""
    keywords: list[str] = field(default_factory=list)
    sentiment_dominant: str = "neutral"
    first_published: datetime | None = None
    last_updated: datetime | None = None
    cluster_date: date | None = None
    portfolio_relevant: bool = False
    relevance_tickers: list[str] = field(default_factory=list)


@dataclass
class TrendEntry:
    """Daily or weekly keyword trend data."""

    trend_date: date
    trend_type: str  # daily | weekly
    sector: str | None
    keyword: str
    mention_count: int = 0
    sentiment_positive: int = 0
    sentiment_neutral: int = 0
    sentiment_negative: int = 0
    sentiment_avg: float | None = None
