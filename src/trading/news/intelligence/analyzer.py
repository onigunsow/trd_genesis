"""Article Analyzer — LLM batch analysis (SPEC-TRADING-014 Module 1).

Primary path: Claude Code CLI on host (Max subscription, zero marginal cost).
Fallback path: Anthropic Haiku API (when CLI results unavailable).

Architecture (host/container split):
  Container :05 -> export_pending_for_host() writes data/pending_analysis.json
  Host :10     -> scripts/analyze_news.sh runs `claude -p` on pending prompt
  Container :15 -> import_host_results() reads data/analysis_results.json into DB
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

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
BATCH_SIZE = 5  # Reduced from 10: Haiku produces more reliable JSON with smaller batches
MAX_ARTICLES_PER_RUN = 100
HAIKU_TIMEOUT = 30.0
RETRY_DELAY = 5.0

# Haiku pricing: input $0.80/M, output $4.00/M
HAIKU_IN_RATE = 0.80
HAIKU_OUT_RATE = 4.0

# Shared volume paths (host <-> container via ./data/ mount)
_DATA_DIR = Path(os.environ.get("TRADING_DATA_DIR", "/app/data"))
PENDING_FILE = _DATA_DIR / "pending_analysis.json"
RESULTS_FILE = _DATA_DIR / "analysis_results.json"

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
        max_tokens=4096,  # Increased from 2048: prevent truncation with complex schema
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

    Handles common Haiku output quirks:
    - Markdown code fences (```json ... ```) with or without preceding text
    - Explanatory text before/after JSON
    - Individual JSON objects instead of array
    - Truncated/incomplete JSON from token limit
    - Single object instead of array for single-item batches
    """
    text = text.strip()

    # Strategy 1: Strip markdown code fences (handles text before fences too)
    text = re.sub(r"```(?:json)?\s*\n?", "", text).strip()

    # Strategy 2: Try direct JSON parse
    data = _try_parse_json(text)
    if data is not None:
        LOG.debug("Haiku JSON parsed directly")
        return _validate_results(data, expected_count)

    # Strategy 3: Find JSON array boundaries [...]
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        data = _try_parse_json(text[start:end + 1])
        if data is not None:
            LOG.debug("Haiku JSON parsed via array boundary extraction")
            return _validate_results(data, expected_count)

        # Strategy 3b: Truncated array — find last complete object and close array
        truncated = text[start:end + 1]
        data = _try_recover_truncated_array(truncated)
        if data is not None:
            LOG.debug("Haiku JSON recovered from truncated array")
            return _validate_results(data, expected_count)

    # Strategy 4: Collect individual {...} objects (Haiku sometimes outputs them separately)
    objects = _extract_individual_objects(text)
    if objects:
        LOG.debug("Haiku JSON parsed via individual object extraction (%d objects)", len(objects))
        return _validate_results(objects, expected_count)

    # Strategy 5: Last resort — try parsing from first { to last }
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        fragment = text[first_brace:last_brace + 1]
        data = _try_parse_json(fragment)
        if data is not None:
            LOG.debug("Haiku JSON parsed via brace boundary extraction")
            return _validate_results(data if isinstance(data, list) else [data], expected_count)

    LOG.warning(
        "All JSON parse strategies failed for Haiku response (len=%d, first 200 chars: %s)",
        len(text), text[:200],
    )
    return None


def _try_parse_json(text: str) -> list[dict] | dict | None:
    """Attempt JSON parse, returning parsed data or None."""
    try:
        data = json.loads(text)
        if isinstance(data, (list, dict)):
            return data
        return None
    except (json.JSONDecodeError, ValueError):
        return None


def _try_recover_truncated_array(text: str) -> list[dict] | None:
    """Recover partial results from a truncated JSON array.

    Haiku may hit token limit mid-JSON, producing something like:
    [{...}, {...}, {"field": "val   (truncated)

    Strategy: find the last complete }, then close the array with ].
    """
    # Find the last position of "}," or "}" followed by potential whitespace before truncation
    last_complete = -1
    brace_depth = 0
    in_string = False
    escape_next = False

    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\":
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
            if brace_depth == 0:
                last_complete = i

    if last_complete > 0:
        candidate = text[:last_complete + 1] + "]"
        # Ensure it starts with [
        arr_start = candidate.find("[")
        if arr_start != -1:
            try:
                data = json.loads(candidate[arr_start:])
                if isinstance(data, list) and data:
                    return data
            except json.JSONDecodeError:
                pass
    return None


def _extract_individual_objects(text: str) -> list[dict]:
    """Extract individual JSON objects from text.

    Handles cases where Haiku outputs objects separated by newlines
    or with text between them.
    """
    objects = []
    # Match balanced braces — handles one level of nesting (keywords arrays)
    pattern = re.compile(r"\{[^{}]*(?:\[[^\[\]]*\][^{}]*)?\}")
    for match in pattern.finditer(text):
        try:
            obj = json.loads(match.group())
            if isinstance(obj, dict) and any(
                k in obj for k in ("classification", "impact_score", "investment_implication")
            ):
                objects.append(obj)
        except json.JSONDecodeError:
            continue
    return objects


def _validate_results(data: list | dict, expected_count: int) -> list[dict] | None:
    """Validate and normalize parsed JSON into analysis results."""
    # Handle single object instead of array
    if isinstance(data, dict):
        data = [data]

    if not isinstance(data, list):
        return None

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


# ---------------------------------------------------------------------------
# Host CLI bridge: export / import via shared volume
# ---------------------------------------------------------------------------

def export_pending_for_host(
    *,
    sector: str | None = None,
    max_articles: int = MAX_ARTICLES_PER_RUN,
) -> int:
    """Export unanalyzed articles as a Claude CLI prompt to the shared volume.

    Writes data/pending_analysis.json with format:
      {"prompt": "<system + user prompt>", "article_ids": [1, 2, ...]}

    The host cron job reads this file and passes the prompt to `claude -p`.
    Pre-filtered noise articles are stored directly (no CLI call needed).

    Returns the number of articles exported for host analysis.
    """
    articles = get_unanalyzed_articles(sector=sector, limit=max_articles)
    if not articles:
        LOG.info("export_pending: no unanalyzed articles")
        return 0

    # Pre-filter noise articles (same logic as analyze_articles)
    noise_articles = []
    real_articles = []
    for art in articles:
        if is_noise_title(art["title"]):
            noise_articles.append(art)
        else:
            real_articles.append(art)

    # Store pre-filtered noise directly — no need to send to host
    if noise_articles:
        count = _store_prefiltered_noise(noise_articles)
        LOG.info("export_pending: pre-filtered %d noise articles", count)

    if not real_articles:
        LOG.info("export_pending: all articles were noise, nothing to export")
        return 0

    # Build the full prompt (system instructions embedded in user message)
    batch_data = _prepare_batch(real_articles)
    user_prompt = build_analysis_prompt(batch_data)
    full_prompt = (
        f"{ARTICLE_ANALYSIS_SYSTEM}\n\n"
        f"---\n\n"
        f"{user_prompt}"
    )

    article_ids = [art["id"] for art in real_articles]

    # Write to shared volume
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "prompt": full_prompt,
        "article_ids": article_ids,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "count": len(article_ids),
    }
    PENDING_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    # Write a separate metadata file that survives the host script's rm of pending
    meta_file = _DATA_DIR / "pending_metadata.json"
    meta_file.write_text(json.dumps({
        "article_ids": article_ids,
        "exported_at": payload["exported_at"],
        "count": len(article_ids),
    }, ensure_ascii=False))

    audit("NEWS_INTEL_EXPORT_PENDING", actor="analyzer", details={
        "articles_exported": len(article_ids),
        "noise_prefiltered": len(noise_articles),
    })
    LOG.info(
        "export_pending: wrote %d articles to %s (%d noise pre-filtered)",
        len(article_ids), PENDING_FILE, len(noise_articles),
    )
    return len(article_ids)


def import_host_results() -> int:
    """Import analysis results produced by the host Claude CLI.

    Reads data/analysis_results.json (raw Claude response) and
    data/pending_analysis.json (for article_ids mapping).

    Returns the number of articles successfully imported, or 0 if no results.
    """
    if not RESULTS_FILE.exists() or RESULTS_FILE.stat().st_size == 0:
        LOG.info("import_results: no results file found")
        return 0

    # We need the article IDs from the pending file (or a companion metadata file).
    # The pending file is deleted by the host script after writing results.
    # So we store article_ids in a separate metadata file alongside the results.
    meta_file = _DATA_DIR / "pending_metadata.json"

    # Try to read article IDs from metadata or pending file
    article_ids: list[int] = []
    for candidate in [meta_file, PENDING_FILE]:
        if candidate.exists():
            try:
                meta = json.loads(candidate.read_text())
                article_ids = meta.get("article_ids", [])
                if article_ids:
                    break
            except (json.JSONDecodeError, KeyError):
                continue

    if not article_ids:
        LOG.warning("import_results: results file exists but no article_ids metadata found")
        # Try to match by fetching currently unanalyzed articles
        articles = get_unanalyzed_articles(limit=MAX_ARTICLES_PER_RUN)
        # Filter out noise (they were already stored during export)
        article_ids = [a["id"] for a in articles if not is_noise_title(a["title"])]
        if not article_ids:
            LOG.warning("import_results: cannot determine which articles to map results to")
            return 0

    # Read the raw response
    raw_text = RESULTS_FILE.read_text().strip()
    if not raw_text:
        LOG.warning("import_results: results file is empty")
        return 0

    # Parse using the same robust parser
    results = _parse_analysis_response(raw_text, len(article_ids))
    if results is None:
        LOG.error("import_results: failed to parse Claude CLI response (len=%d)", len(raw_text))
        audit("NEWS_INTEL_IMPORT_PARSE_FAIL", actor="analyzer", details={
            "raw_length": len(raw_text),
            "first_200": raw_text[:200],
        })
        return 0

    # Fetch article details for quality checks
    article_map = _fetch_articles_by_ids(article_ids)

    # Apply quality checks (title-similarity penalty)
    articles_for_check = [article_map.get(aid, {"title": ""}) for aid in article_ids]
    results = _apply_quality_checks(articles_for_check, results)

    # Store results in DB
    stored_count = 0
    sql = """
        INSERT INTO news_analysis
            (article_id, summary_2line, impact_score, keywords, sentiment,
             classification, model_used, token_input, token_output, cost_krw)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (article_id) DO NOTHING
    """
    with connection() as conn, conn.cursor() as cur:
        for i, result in enumerate(results):
            if i >= len(article_ids):
                break
            aid = article_ids[i]
            cur.execute(sql, (
                aid,
                result["summary_2line"],
                result["impact_score"],
                result["keywords"],
                result["sentiment"],
                result["classification"],
                "claude-cli",  # model_used: distinguish from haiku API
                0,  # token_input: not tracked by CLI (Max subscription)
                0,  # token_output: not tracked by CLI
                0.0,  # cost_krw: zero marginal cost with Max subscription
            ))
            stored_count += 1

    # Clean up processed files
    RESULTS_FILE.unlink(missing_ok=True)
    meta_file.unlink(missing_ok=True)
    # Pending file may already be deleted by host script
    PENDING_FILE.unlink(missing_ok=True)

    audit("NEWS_INTEL_IMPORT_OK", actor="analyzer", details={
        "articles_imported": stored_count,
        "results_parsed": len(results),
    })
    LOG.info("import_results: stored %d analysis results from host CLI", stored_count)
    return stored_count


def _fetch_articles_by_ids(article_ids: list[int]) -> dict[int, dict]:
    """Fetch article details by IDs for quality checks."""
    if not article_ids:
        return {}

    placeholders = ",".join(["%s"] * len(article_ids))
    sql = f"""
        SELECT id, title, source_name, sector, body_text, summary, published_at
          FROM news_articles
         WHERE id IN ({placeholders})
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, article_ids)
        rows = cur.fetchall()
    return {row["id"]: row for row in rows}
