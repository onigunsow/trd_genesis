"""Haiku prompt templates for article analysis (SPEC-TRADING-014 Module 1).

The prompt instructs Haiku to analyze from a Korean stock market investor's
perspective, focusing on market impact rather than general news value.
"""

from __future__ import annotations

ARTICLE_ANALYSIS_SYSTEM = """\
You are a financial news analyst for Korean stock market investors.

For each article provided, analyze and return:
1. summary_2line: Exactly 2 Korean sentences. First sentence: key fact. \
Second sentence: market implication for Korean investors.
2. impact_score: Integer 1-5.
   1 = negligible market impact
   2 = minor sector impact
   3 = moderate sector/market impact
   4 = significant market impact
   5 = critical systemic impact
3. keywords: 3-5 Korean keywords most relevant to market impact.
4. sentiment: One of "positive", "neutral", "negative" \
(from stock market impact perspective, not general tone).

IMPORTANT:
- Respond ONLY with a JSON array.
- Each element corresponds to the article at the same index.
- Use the exact field names: summary_2line, impact_score, keywords, sentiment.
- summary_2line must be a single string with two sentences separated by a newline character.
"""


def build_analysis_prompt(articles: list[dict]) -> str:
    """Build the user message for a batch of articles.

    Each article dict should have: title, source_name, sector, body_excerpt.
    """
    lines = ["Analyze the following articles:\n"]
    for i, art in enumerate(articles, 1):
        title = art.get("title", "")
        source = art.get("source_name", "")
        sector = art.get("sector", "")
        body = art.get("body_excerpt", "")
        lines.append(f"[{i}] Title: {title}")
        lines.append(f"    Source: {source} | Sector: {sector}")
        if body:
            lines.append(f"    Body: {body[:1000]}")
        lines.append("")
    return "\n".join(lines)
