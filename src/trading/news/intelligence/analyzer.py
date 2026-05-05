"""Article Analyzer — Haiku LLM batch analysis (SPEC-TRADING-014 Module 1).

Processes unanalyzed articles from news_articles using Claude Haiku 4.5.
Batches of 10 articles per API call, max 100 articles per run.
Includes pre-filtering for obvious noise and post-analysis quality checks.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher

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

# Pre-filter: title keywords indicating noise (PR/CSR/HR/events)
NOISE_TITLE_KEYWORDS = [
    "협약", "봉사", "기부", "후원", "사회공헌", "어린이날", "축제",
    "수상", "취임", "인사", "부고", "ESG 보고서", "사회적 책임",
    "자원봉사", "장학금", "나눔", "기념식", "체육대회", "사내",
    "임직원", "복지", "동호회", "사보", "공채", "채용설명회",
]

# Regex pattern for promotional/ad content in titles
NOISE_TITLE_PATTERNS = re.compile(
    r"(출시\s*기념|할인|이벤트|프로모션|경품|쿠폰|무료\s*체험|"
    r"신제품\s*출시.*%|얼리버드|사전\s*예약.*혜택)",
    re.IGNORECASE,
)

# Threshold for title-summary similarity check
TITLE_SIMILARITY_THRESHOLD = 0.80

# Valid classifications
VALID_CLASSIFICATIONS = frozenset({
    "macro_market_moving", "sector_specific", "company_specific", "noise",
})


@dataclass
class AnalysisRunMetrics:
    """Metrics for a single analysis run."""

    articles_processed: int = 0
    articles_deferred: int = 0
    articles_prefiltered: int = 0
    batches_sent: int = 0
    haiku_calls_succeeded: int = 0
    haiku_calls_failed: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_krw: float = 0.0
    duration_seconds: float = 0.0


def is_noise_title(title: str) -> bool:
    """Check if an article title indicates obvious noise content.

    Returns True if the title contains noise keywords (PR/CSR/HR/events)
    or matches promotional patterns. These articles skip Haiku analysis.
    """
    title_lower = title.lower()
    for keyword in NOISE_TITLE_KEYWORDS:
        if keyword.lower() in title_lower:
            return True
    if NOISE_TITLE_PATTERNS.search(title):
        return True
    return False


def check_title_similarity(title: str, implication: str) -> float:
    """Check if investment_implication is too similar to the title.

    Returns similarity ratio (0.0 to 1.0). Higher means more similar (bad).
    """
    # Normalize both strings for comparison
    title_clean = re.sub(r"[^\w\s]", "", title).strip()
    impl_clean = re.sub(r"[^\w\s]", "", implication).strip()
    return SequenceMatcher(None, title_clean, impl_clean).ratio()


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


def _store_prefiltered_noise(articles: list[dict]) -> int:
    """Store pre-filtered noise articles directly with impact=0, classification=noise.

    These articles are identified by title keywords alone and skip Haiku analysis
    to save cost.
    """
    if not articles:
        return 0

    sql = """
        INSERT INTO news_analysis
            (article_id, summary_2line, impact_score, keywords, sentiment,
             classification, model_used, token_input, token_output, cost_krw)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (article_id) DO NOTHING
    """
    stored = 0
    with connection() as conn, conn.cursor() as cur:
        for art in articles:
            cur.execute(sql, (
                art["id"],
                "(투자 관련성 없음 - 자동 필터링)",
                0,  # impact_score = 0
                [],  # no keywords
                "neutral",
                "noise",
                "pre-filter",
                0,
                0,
                0.0,
            ))
            stored += 1
    return stored


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
    """Parse the JSON array response from Haiku.

    Handles the new response format with classification and investment_implication.
    """
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

        # Extract classification (new field)
        classification = item.get("classification", "company_specific")
        if classification not in VALID_CLASSIFICATIONS:
            classification = "company_specific"

        # Extract impact score (now 0-5)
        impact = item.get("impact_score", 3)
        if not isinstance(impact, int):
            try:
                impact = int(impact)
            except (ValueError, TypeError):
                impact = 3
        impact = max(0, min(5, impact))

        # Force noise articles to impact 0
        if classification == "noise":
            impact = 0

        # Extract investment_implication (replaces summary_2line conceptually)
        # Support both field names for backward compatibility
        implication = (
            item.get("investment_implication")
            or item.get("summary_2line", "")
        )

        # Extract keywords
        keywords = item.get("keywords", [])
        if not isinstance(keywords, list):
            keywords = []
        keywords = [str(k) for k in keywords[:5]]

        # Validate sentiment
        sentiment = item.get("sentiment", "neutral")
        if sentiment not in ("positive", "neutral", "negative"):
            sentiment = "neutral"

        validated.append({
            "summary_2line": str(implication),
            "impact_score": impact,
            "keywords": keywords,
            "sentiment": sentiment,
            "classification": classification,
        })

    return validated if validated else None


def _apply_quality_checks(
    articles: list[dict],
    results: list[dict],
) -> list[dict]:
    """Apply post-analysis quality checks to Haiku results.

    Fix 4: Check if investment_implication is too similar to the article title.
    If so, penalize the impact score.
    """
    checked = []
    for i, result in enumerate(results):
        if i >= len(articles):
            break

        title = articles[i].get("title", "")
        implication = result["summary_2line"]

        # Check title-summary similarity
        if implication and title:
            similarity = check_title_similarity(title, implication)
            if similarity >= TITLE_SIMILARITY_THRESHOLD:
                LOG.warning(
                    "Haiku produced title-restating summary (sim=%.2f) for: %s",
                    similarity, title[:60],
                )
                # Penalize: reduce impact by 1, minimum 0
                result["impact_score"] = max(result["impact_score"] - 1, 0)
                # If impact dropped to 0, mark as noise
                if result["impact_score"] == 0:
                    result["classification"] = "noise"

        checked.append(result)
    return checked


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
             classification, model_used, token_input, token_output, cost_krw)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                result["classification"],
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
                classification=result["classification"],
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

    Pipeline:
    1. Fetch unanalyzed articles
    2. Pre-filter obvious noise (skip Haiku, save cost)
    3. Send remaining articles to Haiku in batches
    4. Apply quality checks (title-similarity penalty)
    5. Store results
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

    # --- Fix 2: Pre-filter obvious noise articles ---
    noise_articles = []
    haiku_articles = []
    for art in articles:
        if is_noise_title(art["title"]):
            noise_articles.append(art)
        else:
            haiku_articles.append(art)

    # Store pre-filtered noise directly (no Haiku call)
    if noise_articles:
        prefiltered_count = _store_prefiltered_noise(noise_articles)
        metrics.articles_prefiltered = prefiltered_count
        metrics.articles_processed += prefiltered_count
        LOG.info(
            "Pre-filtered %d noise articles (skipped Haiku)",
            prefiltered_count,
        )

    # Process remaining articles in batches via Haiku
    for batch_start in range(0, len(haiku_articles), BATCH_SIZE):
        batch_articles = haiku_articles[batch_start:batch_start + BATCH_SIZE]
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

        # --- Fix 4: Apply quality checks ---
        results = _apply_quality_checks(batch_articles, results)

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
        "articles_prefiltered": metrics.articles_prefiltered,
        "batches_sent": metrics.batches_sent,
        "haiku_calls_succeeded": metrics.haiku_calls_succeeded,
        "haiku_calls_failed": metrics.haiku_calls_failed,
        "total_input_tokens": metrics.total_input_tokens,
        "total_output_tokens": metrics.total_output_tokens,
        "total_cost_krw": round(metrics.total_cost_krw, 2),
        "duration_seconds": round(metrics.duration_seconds, 2),
    })

    LOG.info(
        "Analysis complete: %d articles (%d pre-filtered), %d batches, %.1f KRW, %.1fs",
        metrics.articles_processed, metrics.articles_prefiltered,
        metrics.batches_sent, metrics.total_cost_krw, metrics.duration_seconds,
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
