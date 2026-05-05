"""Article Analyzer — Haiku LLM batch analysis (SPEC-TRADING-014 Module 1).

Processes unanalyzed articles from news_articles using Claude Haiku 4.5.
Batches of 10 articles per API call, max 100 articles per run.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from anthropic import Anthropic

from trading.config import get_settings
from trading.db.session import audit, connection
from trading.news.intelligence.models import AnalysisResult
from trading.news.intelligence.prompts import (
    ARTICLE_ANALYSIS_SYSTEM,
    build_analysis_prompt,
)
from trading.personas.base import KRW_PER_USD

LOG = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5"
BATCH_SIZE = 10
MAX_ARTICLES_PER_RUN = 100
HAIKU_TIMEOUT = 30.0
RETRY_DELAY = 5.0

# Haiku pricing: input $0.80/M, output $4.00/M
HAIKU_IN_RATE = 0.80
HAIKU_OUT_RATE = 4.0


@dataclass
class AnalysisRunMetrics:
    """Metrics for a single analysis run."""

    articles_processed: int = 0
    articles_deferred: int = 0
    batches_sent: int = 0
    haiku_calls_succeeded: int = 0
    haiku_calls_failed: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_krw: float = 0.0
    duration_seconds: float = 0.0


def get_unanalyzed_articles(
    *,
    sector: str | None = None,
    limit: int = MAX_ARTICLES_PER_RUN,
) -> list[dict]:
    """Fetch articles not yet analyzed, ordered by published_at DESC.

    REQ-INTEL-01-6: Articles where id NOT IN (SELECT article_id FROM news_analysis).
    """
    sql = """
        SELECT a.id, a.title, a.source_name, a.sector, a.body_text, a.summary,
               a.published_at
          FROM news_articles a
         WHERE NOT EXISTS (
             SELECT 1 FROM news_analysis na WHERE na.article_id = a.id
         )
    """
    params: list = []
    if sector:
        sql += " AND a.sector = %s"
        params.append(sector)
    sql += " ORDER BY a.published_at DESC LIMIT %s"
    params.append(limit)

    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def _prepare_batch(articles: list[dict]) -> list[dict]:
    """Prepare article batch for the prompt (title + source + body excerpt)."""
    batch = []
    for art in articles:
        body = art.get("body_text") or art.get("summary") or ""
        batch.append({
            "title": art["title"],
            "source_name": art["source_name"],
            "sector": art["sector"],
            "body_excerpt": body[:1000],
        })
    return batch


def _call_haiku(batch: list[dict]) -> tuple[list[dict] | None, int, int]:
    """Call Haiku API with a batch of articles.

    Returns: (parsed_results, input_tokens, output_tokens) or (None, 0, 0) on failure.
    """
    s = get_settings()
    if s.anthropic.api_key is None:
        raise RuntimeError("ANTHROPIC_API_KEY missing")
    client = Anthropic(api_key=s.anthropic.api_key.get_secret_value())

    prompt = build_analysis_prompt(batch)

    response = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=2048,
        system=ARTICLE_ANALYSIS_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    in_tok = response.usage.input_tokens if response.usage else 0
    out_tok = response.usage.output_tokens if response.usage else 0

    # Extract text response
    text = ""
    for blk in response.content:
        if getattr(blk, "type", "") == "text":
            text += blk.text

    # Parse JSON array from response
    results = _parse_analysis_response(text, len(batch))
    return results, in_tok, out_tok


def _parse_analysis_response(text: str, expected_count: int) -> list[dict] | None:
    """Parse the JSON array response from Haiku."""
    text = text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON array in the text
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            LOG.warning("No JSON array found in Haiku response")
            return None
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            LOG.warning("Failed to parse JSON from Haiku response")
            return None

    if not isinstance(data, list):
        LOG.warning("Haiku response is not a JSON array")
        return None

    # Validate and normalize each result
    validated = []
    for item in data[:expected_count]:
        if not isinstance(item, dict):
            continue
        # Validate required fields
        summary = item.get("summary_2line", "")
        impact = item.get("impact_score", 3)
        keywords = item.get("keywords", [])
        sentiment = item.get("sentiment", "neutral")

        # Clamp impact score
        if not isinstance(impact, int):
            try:
                impact = int(impact)
            except (ValueError, TypeError):
                impact = 3
        impact = max(1, min(5, impact))

        # Validate sentiment
        if sentiment not in ("positive", "neutral", "negative"):
            sentiment = "neutral"

        # Ensure keywords is a list of strings
        if not isinstance(keywords, list):
            keywords = []
        keywords = [str(k) for k in keywords[:5]]

        validated.append({
            "summary_2line": str(summary),
            "impact_score": impact,
            "keywords": keywords,
            "sentiment": sentiment,
        })

    return validated if validated else None


def _store_results(
    articles: list[dict],
    results: list[dict],
    in_tok: int,
    out_tok: int,
) -> list[AnalysisResult]:
    """Store analysis results in the news_analysis table."""
    cost_per_article = (
        (in_tok / 1_000_000) * HAIKU_IN_RATE
        + (out_tok / 1_000_000) * HAIKU_OUT_RATE
    ) * KRW_PER_USD / len(results) if results else 0.0

    stored: list[AnalysisResult] = []
    sql = """
        INSERT INTO news_analysis
            (article_id, summary_2line, impact_score, keywords, sentiment,
             model_used, token_input, token_output, cost_krw)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (article_id) DO NOTHING
    """

    with connection() as conn, conn.cursor() as cur:
        for i, result in enumerate(results):
            if i >= len(articles):
                break
            article = articles[i]
            article_id = article["id"]

            cur.execute(sql, (
                article_id,
                result["summary_2line"],
                result["impact_score"],
                result["keywords"],
                result["sentiment"],
                HAIKU_MODEL,
                in_tok // len(results),
                out_tok // len(results),
                cost_per_article,
            ))

            stored.append(AnalysisResult(
                article_id=article_id,
                summary_2line=result["summary_2line"],
                impact_score=result["impact_score"],
                keywords=result["keywords"],
                sentiment=result["sentiment"],
                token_input=in_tok // len(results),
                token_output=out_tok // len(results),
                cost_krw=cost_per_article,
            ))

    return stored


def analyze_articles(
    *,
    sector: str | None = None,
    force: bool = False,
    max_articles: int = MAX_ARTICLES_PER_RUN,
) -> AnalysisRunMetrics:
    """Run article analysis pipeline.

    REQ-INTEL-01-7: Max 100 articles per run.
    REQ-INTEL-01-8: Retry once on API failure, then skip batch.
    """
    start_time = time.time()
    metrics = AnalysisRunMetrics()

    if force:
        # Re-analyze: delete existing analyses for today's articles, then fetch all
        _clear_today_analyses(sector)

    articles = get_unanalyzed_articles(sector=sector, limit=max_articles)
    total_available = len(articles)

    if not articles:
        LOG.info("No unanalyzed articles found")
        metrics.duration_seconds = time.time() - start_time
        return metrics

    # Cap at MAX_ARTICLES_PER_RUN
    if total_available > max_articles:
        metrics.articles_deferred = total_available - max_articles
        articles = articles[:max_articles]

    # Process in batches
    for batch_start in range(0, len(articles), BATCH_SIZE):
        batch_articles = articles[batch_start:batch_start + BATCH_SIZE]
        batch_data = _prepare_batch(batch_articles)
        metrics.batches_sent += 1

        results = None
        in_tok = 0
        out_tok = 0

        # First attempt
        try:
            results, in_tok, out_tok = _call_haiku(batch_data)
        except Exception as e:  # noqa: BLE001
            LOG.warning("Haiku batch %d failed (attempt 1): %s", batch_start // BATCH_SIZE + 1, e)
            # REQ-INTEL-01-8: Retry after 5 seconds
            time.sleep(RETRY_DELAY)
            try:
                results, in_tok, out_tok = _call_haiku(batch_data)
            except Exception as e2:  # noqa: BLE001
                LOG.error("Haiku batch %d failed (attempt 2): %s", batch_start // BATCH_SIZE + 1, e2)
                metrics.haiku_calls_failed += 1
                audit("NEWS_INTEL_HAIKU_FAIL", actor="analyzer", details={
                    "batch_index": batch_start // BATCH_SIZE,
                    "error": str(e2),
                    "articles_in_batch": len(batch_articles),
                })
                continue

        if results is None:
            metrics.haiku_calls_failed += 1
            LOG.warning("Haiku returned unparseable response for batch %d", batch_start // BATCH_SIZE + 1)
            continue

        # Store results
        stored = _store_results(batch_articles, results, in_tok, out_tok)
        metrics.haiku_calls_succeeded += 1
        metrics.articles_processed += len(stored)
        metrics.total_input_tokens += in_tok
        metrics.total_output_tokens += out_tok

        batch_cost = (
            (in_tok / 1_000_000) * HAIKU_IN_RATE
            + (out_tok / 1_000_000) * HAIKU_OUT_RATE
        ) * KRW_PER_USD
        metrics.total_cost_krw += batch_cost

    metrics.duration_seconds = time.time() - start_time

    # Audit log
    audit("NEWS_INTEL_ANALYZE_OK", actor="analyzer", details={
        "articles_processed": metrics.articles_processed,
        "articles_deferred": metrics.articles_deferred,
        "batches_sent": metrics.batches_sent,
        "haiku_calls_succeeded": metrics.haiku_calls_succeeded,
        "haiku_calls_failed": metrics.haiku_calls_failed,
        "total_input_tokens": metrics.total_input_tokens,
        "total_output_tokens": metrics.total_output_tokens,
        "total_cost_krw": round(metrics.total_cost_krw, 2),
        "duration_seconds": round(metrics.duration_seconds, 2),
    })

    LOG.info(
        "Analysis complete: %d articles, %d batches, %.1f KRW, %.1fs",
        metrics.articles_processed, metrics.batches_sent,
        metrics.total_cost_krw, metrics.duration_seconds,
    )
    return metrics


def _clear_today_analyses(sector: str | None = None) -> int:
    """Delete today's analysis entries for re-analysis (--force mode)."""
    sql = """
        DELETE FROM news_analysis na
        USING news_articles a
        WHERE na.article_id = a.id
          AND a.published_at >= CURRENT_DATE
    """
    params: list = []
    if sector:
        sql += " AND a.sector = %s"
        params.append(sector)

    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        deleted = cur.rowcount
    LOG.info("Cleared %d existing analyses (force mode)", deleted)
    return deleted
