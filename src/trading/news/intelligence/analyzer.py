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
from trading.personas.base import KRW_PER_USD, block_if_cli_only_mode

LOG = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5"
BATCH_SIZE = 5  # Reduced from 10: Haiku produces more reliable JSON with smaller batches
MAX_ARTICLES_PER_RUN = 100
HAIKU_TIMEOUT = 30.0
RETRY_DELAY = 5.0

# SPEC-TRADING-062 REQ-062-C1: 호스트 CLI 청킹 단위. 2026-07-08 인시던트에서
# 94~98개 단일배치가 거의 100% 스크램블됐다 — 작은 청크로 나눠 모델이 idx/
# title_head 대응을 유지하도록 한다. 시장 종속 값 아님(REQ-062-C5).
HOST_CHUNK_SIZE = 20

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

# SPEC-TRADING-062 REQ-062-B2: content-anchor(title_head) 불일치 임계.
# 이 값을 초과하는 결과 개수가 나오면 배치 전체를 fail-closed 로 거부한다.
ANCHOR_MISMATCH_MAX = 1

# Valid classifications
VALID_CLASSIFICATIONS = frozenset({
    "macro_market_moving", "sector_specific", "company_specific", "noise",
})

# SPEC-TRADING-026 A2: canonical sectors the analyzer may assign (content-based
# override of the feed sector). Sourced from the single source of truth.
from trading.news.sources import SECTORS as _SECTORS  # noqa: E402

VALID_SECTORS = frozenset(_SECTORS)


def _corrected_sector(result: dict, current_sector: str) -> str | None:
    """Return the LLM's content-derived sector if it is valid and differs.

    SPEC-026 A2: applied on import to correct feed-mislabelled article sectors.
    Returns None when the sector is missing, invalid, or already correct.
    """
    sec = str(result.get("sector", "") or "").strip()
    if sec in VALID_SECTORS and sec != current_sector:
        return sec
    return None


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


@block_if_cli_only_mode
def _call_haiku(batch: list[dict]) -> tuple[list[dict] | None, int, int]:
    """Call Haiku API with a batch of articles.

    Returns: (parsed_results, input_tokens, output_tokens) or (None, 0, 0) on failure.

    SPEC-TRADING-016 REQ-016-1-3: This is the *fallback* path for the news
    analyzer; the primary path is the host-side Claude CLI driven by
    ``scripts/analyze_news.sh``. The ``@block_if_cli_only_mode`` decorator
    ensures we never quietly burn the Anthropic budget while the system
    operator believes the CLI-only flag is in effect. Decorator chosen over
    a CLI bridge migration because this path has a bespoke token-accounting
    and DB schema (news_articles, not persona_runs) that does not fit the
    persona pipeline.
    """
    s = get_settings()
    if s.anthropic.api_key is None:
        raise RuntimeError("ANTHROPIC_API_KEY missing")
    client = Anthropic(api_key=s.anthropic.api_key.get_secret_value())

    prompt = build_analysis_prompt(batch)

    # REQ-053-F1: messages.create 직전 PAID_CALL 계측 (5지점 #1, analyzer haiku)
    from trading.personas.base import _log_paid_call
    _log_paid_call(
        persona="news_analyzer", path="analyzer_haiku", model=HAIKU_MODEL, reason="news_analysis"
    )
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
    """Validate and normalize parsed JSON into analysis results.

    REQ-061-1: 각 결과는 echo된 정수 idx(1-based, article_ids 에 대응)를
    반드시 포함해야 한다. idx 가 없거나 정수가 아닌 개별 결과는 이 단계에서
    폐기한다(배치 전체 거부는 아님 — 배치 전체의 완전성/중복 검증은
    ``_align_results_to_articles`` 가 담당한다, REQ-061-3).

    ``expected_count`` 로 앞에서 자르지 않는다: 위치 기반 절단은 재정렬된
    응답에서 진짜 초과(extra)/중복(duplicate) idx 를 감추어 정렬 오염을
    통과시킬 수 있다(RC5). 완전성 판정은 idx 집합 대조로만 한다.
    """
    # Handle single object instead of array
    if isinstance(data, dict):
        data = [data]

    if not isinstance(data, list):
        return None

    validated = []
    for item in data:
        if not isinstance(item, dict):
            continue

        # REQ-061-1: 안정적 식별자(idx) 필수 — echo 안 된 개별 결과는 폐기
        idx = item.get("idx")
        if not isinstance(idx, int) or isinstance(idx, bool):
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

        # SPEC-026 A2: content-derived sector ("" when missing/invalid; the
        # caller keeps the existing article sector in that case).
        sector = str(item.get("sector", "") or "").strip()
        if sector not in VALID_SECTORS:
            sector = ""

        # SPEC-TRADING-062 REQ-062-B1/B3: content-anchor 필드. echo 안 된(구버전)
        # 응답은 None 으로 두고 _anchor_mismatch_count 가 그 결과를 대조에서
        # 제외한다(하위호환, 앵커 부재만으로는 거부하지 않는다).
        title_head = item.get("title_head")
        if not isinstance(title_head, str):
            title_head = None

        validated.append({
            "idx": idx,
            "summary_2line": str(implication),
            "impact_score": impact,
            "keywords": keywords,
            "sentiment": sentiment,
            "classification": classification,
            "sector": sector,
            "title_head": title_head,
        })

    return validated if validated else None


# @MX:ANCHOR: [AUTO] news_analysis 저장 경로 3곳(_store_results/import_host_results/
# repair.import_repair_results)의 유일한 정렬 진실원천 — 위치 매핑 재도입 금지.
# @MX:REASON: RC1/RC2(analyzer.py 히스토리) — 위치(enumerate) 매핑이 결과 재정렬 시
# classification/sentiment/impact/keywords 전체를 오염시켰다. fan_in=3. idx 집합
# 완전성만으로는 불충분함이 2026-07-08 확인됨(idx는 완전한 순열이되 내용이 뒤바뀐
# 제2 실패모드) — 3곳 모두 이 함수 다음에 _verify_content_anchor 를 반드시 호출해
# content-anchor(title_head)까지 대조해야 진짜 "정렬 진실"이 완성된다(SPEC-062).
# @MX:SPEC: SPEC-TRADING-061 REQ-061-2/3, SPEC-TRADING-062 REQ-062-B2/B4
def _align_results_to_articles(
    results: list[dict], article_ids: list[int]
) -> list[tuple[int, dict]] | None:
    """echo된 idx(1-based)로 결과를 article_id 에 정렬 매핑한다(REQ-061-2).

    순수 함수 — DB/네트워크 접근 없음, 시장 종속 로직 없음(REQ-061-6, US
    시장 재사용 대비). 리스트 위치(enumerate)로 매핑하지 않는다.

    idx 집합이 정확히 {1..len(article_ids)} 와 일치하지 않으면(누락/초과/
    중복/범위밖/idx 없음) 배치 전체를 거부하고 None 을 반환한다 — 짝지어진
    일부만 저장하는 위치 폴백은 없다(REQ-061-3 fail-closed).

    Returns:
        [(article_id, result), ...] 를 idx 오름차순으로 정렬해 반환하거나,
        정렬 검증 실패 시 None.
    """
    n = len(article_ids)
    if n == 0 or not results:
        return None

    expected = set(range(1, n + 1))
    seen: set[int] = set()
    idx_to_result: dict[int, dict] = {}
    for result in results:
        idx = result.get("idx")
        if not isinstance(idx, int) or isinstance(idx, bool):
            return None  # no-id — echo 안 된 결과가 섞여 있으면 전체 거부
        if idx in seen or idx not in expected:
            return None  # duplicate 또는 extra/out-of-range
        seen.add(idx)
        idx_to_result[idx] = result

    if seen != expected:
        return None  # missing — 일부 idx 가 아예 돌아오지 않음

    return [(article_ids[idx - 1], idx_to_result[idx]) for idx in sorted(seen)]


def _alignment_reject_reasons(results: list[dict], article_ids: list[int]) -> dict:
    """정렬 거부 사유 집계(감사 로그 관측성용, REQ-061-3).

    ``_align_results_to_articles`` 가 None 을 반환했을 때만 호출한다. 순수
    함수 — 원인(missing/extra/duplicate/no_idx)을 개수로 집계해 반환한다.
    """
    n = len(article_ids)
    expected = set(range(1, n + 1))
    idx_values: list[int] = []
    no_idx = 0
    for result in results:
        idx = result.get("idx")
        if isinstance(idx, int) and not isinstance(idx, bool):
            idx_values.append(idx)
        else:
            no_idx += 1
    seen = set(idx_values)
    return {
        "expected_count": n,
        "matched_count": 0,
        "rejected_count": len(results),
        "no_idx_count": no_idx,
        "duplicate_count": len(idx_values) - len(seen),
        "missing_count": len(expected - seen),
        "extra_count": len(seen - expected),
    }


def _normalize_title_head(text: str) -> str:
    """공백을 정규화(연속 공백 -> 1칸, 양끝 trim)한 뒤 앞 12자를 rstrip해 반환한다.

    REQ-062-B2 앵커 비교 단위. 두 값 모두 이 함수를 거친 뒤 비교한다.
    slice 후 rstrip: 제목의 12번째 문자가 공백이면 모델은 후행 공백 없이
    echo하므로(2026-07-09 라이브 오탐 5~6/20 전수 확인), 절단 경계의 후행
    공백은 비교에서 제외해야 완벽 정렬 배치를 거부하지 않는다.
    """
    collapsed = re.sub(r"\s+", " ", (text or "")).strip()
    return collapsed[:12].rstrip()


# @MX:SPEC: SPEC-TRADING-062 REQ-062-B2/B4
def _anchor_mismatch_count(
    aligned: list[tuple[int, dict]], article_titles: dict[int, str]
) -> int:
    """idx 정렬된 (article_id, result) 쌍에서 title_head 앵커 불일치 개수를 센다.

    idx 집합 완전성(REQ-061-3)만으로는 "idx는 완전한 순열이되 내용은 뒤바뀐"
    2026-07-08 제2 실패모드를 잡지 못한다. 각 결과가 echo한 title_head 를
    매핑된 기사의 실제 제목 앞부분과 대조해 그 오염을 탐지한다.

    순수 함수 — DB/네트워크 접근 없음(REQ-062-B4), 시장 종속 로직 없음.
    title_head 가 없는(구버전) 결과는 비교하지 않는다(REQ-062-B3, 하위호환).
    """
    mismatches = 0
    for article_id, result in aligned:
        title_head = result.get("title_head")
        if not title_head:
            continue
        expected = _normalize_title_head(article_titles.get(article_id, ""))
        got = _normalize_title_head(title_head)
        if got != expected:
            mismatches += 1
    return mismatches


def _title_head_missing_ratio(aligned: list[tuple[int, dict]]) -> float:
    """정렬된 결과 중 title_head 를 결여한 비율(REQ-062-B3 관측용, 순수 함수)."""
    if not aligned:
        return 0.0
    missing = sum(1 for _, result in aligned if not result.get("title_head"))
    return missing / len(aligned)


def _anchor_reject_reasons(article_ids: list[int], mismatch_count: int) -> dict:
    """앵커 불일치로 인한 거부 사유(REQ-062-B2, 감사 로그 관측성용).

    idx 정렬 자체는 성공했음(existing reason fields 는 0/전량 형태로 유지)을
    명시하면서 anchor_mismatch_count 를 덧붙인다.
    """
    n = len(article_ids)
    return {
        "expected_count": n,
        "matched_count": n,  # idx 정렬은 통과 — 문제는 content-anchor 뿐
        "rejected_count": n,
        "no_idx_count": 0,
        "duplicate_count": 0,
        "missing_count": 0,
        "extra_count": 0,
        "anchor_mismatch_count": mismatch_count,
    }


def _verify_content_anchor(
    aligned: list[tuple[int, dict]],
    article_titles: dict[int, str],
    article_ids: list[int],
    *,
    path: str,
    actor: str = "analyzer",
    chunk_id: str | None = None,
) -> bool:
    """content-anchor(title_head) 검증 후 필요 시 감사·로그를 남긴다(REQ-062-B2/B4).

    저장 경로(_store_results/import_host_results/repair.import_repair_results/
    청크 import)가 idx 정렬 성공 직후 공통으로 호출하는 얇은 래퍼 — 순수 계산은
    ``_anchor_mismatch_count`` 가 전담하고, 이 함수는 그 결과를 감사로그로
    옮기는 부수효과만 담당한다.

    ``chunk_id`` 는 SPEC-TRADING-062 Stage2(REQ-062-C3) 청크 import 전용 —
    지정되면 ALIGN_REJECT 감사 상세에 포함한다. 기존 호출자(기본값 None)는
    동작 변화 없음.

    Returns:
        True  — 임계 초과, 호출자는 저장을 중단하고 0건으로 처리해야 한다.
        False — 통과, 호출자는 저장을 계속 진행한다.
    """
    if _title_head_missing_ratio(aligned) > 0.5:
        LOG.warning(
            "배치 과반이 title_head 앵커를 결여 — 구버전 응답 의심(path=%s)", path,
        )

    mismatch_count = _anchor_mismatch_count(aligned, article_titles)
    if mismatch_count > ANCHOR_MISMATCH_MAX:
        reasons = _anchor_reject_reasons(article_ids, mismatch_count)
        details = {"path": path, **reasons}
        if chunk_id is not None:
            details["chunk_id"] = chunk_id
        audit("NEWS_INTEL_ALIGN_REJECT", actor=actor, details=details)
        LOG.warning(
            "content-anchor(title_head) 불일치 임계 초과 — fail-closed 저장 거부"
            "(0건): mismatch=%d/%d (path=%s)",
            mismatch_count, len(aligned), path,
        )
        return True
    return False


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
    """Store analysis results in the news_analysis table.

    REQ-061-2: echo된 idx로 article_id 에 정렬 매핑한다(RC2 — 과거에는
    ``enumerate`` 위치로 매핑해 결과 순서가 뒤섞이면 전 필드가 오염됐다).
    REQ-061-3: 정렬 검증 실패 시 아무것도 저장하지 않는다(fail-closed).
    """
    article_ids = [a["id"] for a in articles]
    aligned = _align_results_to_articles(results, article_ids)
    if aligned is None:
        reasons = _alignment_reject_reasons(results, article_ids)
        audit("NEWS_INTEL_ALIGN_REJECT", actor="analyzer", details={
            "path": "haiku_store", **reasons,
        })
        LOG.warning(
            "Haiku 결과 정렬 검증 실패 — fail-closed 저장 거부(0건): %s", reasons,
        )
        return []

    article_map = {a["id"]: a for a in articles}

    # SPEC-TRADING-062 REQ-062-B2: idx 정렬은 통과했으나 content-anchor
    # (title_head)가 불일치하는 제2 실패모드 검증.
    article_titles = {aid: a.get("title", "") for aid, a in article_map.items()}
    if _verify_content_anchor(aligned, article_titles, article_ids, path="haiku_store"):
        return []

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
        for article_id, result in aligned:
            article = article_map.get(article_id, {})

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

            # SPEC-026 A2: correct the article's feed-derived sector from content.
            new_sector = _corrected_sector(result, article.get("sector", ""))
            if new_sector:
                cur.execute(
                    "UPDATE news_articles SET sector = %s WHERE id = %s",
                    (new_sector, article_id),
                )

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
# Host CLI bridge: export / import via shared volume (SPEC-TRADING-062 Stage2
# — 청크 단위 배선, REQ-062-C1~C4)
# ---------------------------------------------------------------------------


def _clear_stale_chunk_files(chunks_dir: Path, results_dir: Path) -> None:
    """새 배치를 쓰기 전 이전 청크 대기열/결과 잔재를 제거한다(REQ-062-C1).

    이전 사이클에서 아직 소비되지 않은 청크가 있어도, 그 기사들은 아직
    news_analysis 에 없으므로(import 되지 않았다는 뜻) 다음 export 의
    get_unanalyzed_articles 조회에 다시 포함된다 — 데이터 손실 없이 안전하게
    덮어쓸 수 있다(기존 단일파일 PENDING_FILE 무조건 덮어쓰기와 동일 정책의
    다중파일 일반화).
    """
    for d in (chunks_dir, results_dir):
        if d.exists():
            for f in d.glob("*.json"):
                f.unlink(missing_ok=True)


def _persist_cli_results(
    aligned: list[tuple[int, dict]], article_map: dict[int, dict],
) -> int:
    """정렬된 (article_id, result) 쌍을 news_analysis 에 저장한다(CLI 경로 공통).

    레거시 단일배치 경로와 Stage2 청크 경로가 공유하는 저장 루프 —
    model_used="claude-cli", 비용 0(Max 구독), REQ-026-A2 섹터 보정 포함.
    """
    stored_count = 0
    sql = """
        INSERT INTO news_analysis
            (article_id, summary_2line, impact_score, keywords, sentiment,
             classification, model_used, token_input, token_output, cost_krw)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (article_id) DO NOTHING
    """
    with connection() as conn, conn.cursor() as cur:
        for aid, result in aligned:
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

            # SPEC-026 A2: correct the article's feed-derived sector from content.
            new_sector = _corrected_sector(result, article_map.get(aid, {}).get("sector", ""))
            if new_sector:
                cur.execute(
                    "UPDATE news_articles SET sector = %s WHERE id = %s",
                    (new_sector, aid),
                )

            stored_count += 1
    return stored_count


def export_pending_for_host(
    *,
    sector: str | None = None,
    max_articles: int = MAX_ARTICLES_PER_RUN,
) -> int:
    """Export unanalyzed articles as chunked Claude CLI prompts (REQ-062-C1).

    SPEC-TRADING-062 Stage2: real (non-noise) articles are split into chunks
    of at most HOST_CHUNK_SIZE. Each chunk gets its own prompt file under
    data/pending_chunks/ (local [1..n] article labels, own article_ids) plus
    a single data/pending_metadata.json describing all chunks. This replaces
    the previous single 100-article prompt, which the model scrambled almost
    100% of the time at that size (2026-07-08 incident).

    Pre-filtered noise articles are stored directly (no CLI call needed).
    New exports never write the legacy single-prompt PENDING_FILE format
    (REQ-062-C4) — that path is retained only for draining leftovers from
    before this deploy (see import_host_results).

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

    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    chunks_dir = _DATA_DIR / "pending_chunks"
    results_dir = _DATA_DIR / "analysis_chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    _clear_stale_chunk_files(chunks_dir, results_dir)

    exported_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    chunk_entries: list[dict] = []
    for chunk_index, start in enumerate(range(0, len(real_articles), HOST_CHUNK_SIZE)):
        chunk_articles = real_articles[start:start + HOST_CHUNK_SIZE]
        chunk_id = f"{chunk_index:02d}"

        # Build the chunk's own prompt — build_analysis_prompt labels [1..n]
        # local to THIS chunk only, never the global batch position.
        batch_data = _prepare_batch(chunk_articles)
        user_prompt = build_analysis_prompt(batch_data)
        full_prompt = f"{ARTICLE_ANALYSIS_SYSTEM}\n\n---\n\n{user_prompt}"
        article_ids = [art["id"] for art in chunk_articles]

        (chunks_dir / f"chunk_{chunk_id}.json").write_text(json.dumps({
            "chunk_id": chunk_id,
            "prompt": full_prompt,
            "article_ids": article_ids,
            "exported_at": exported_at,
        }, ensure_ascii=False, indent=2))
        chunk_entries.append({"chunk_id": chunk_id, "article_ids": article_ids})

    meta_file = _DATA_DIR / "pending_metadata.json"
    meta_file.write_text(json.dumps({
        "chunks": chunk_entries,
        "exported_at": exported_at,
        "count": len(real_articles),
    }, ensure_ascii=False))

    audit("NEWS_INTEL_EXPORT_PENDING", actor="analyzer", details={
        "articles_exported": len(real_articles),
        "noise_prefiltered": len(noise_articles),
        "chunks": len(chunk_entries),
    })
    LOG.info(
        "export_pending: wrote %d articles across %d chunk(s) to %s (%d noise pre-filtered)",
        len(real_articles), len(chunk_entries), chunks_dir, len(noise_articles),
    )
    return len(real_articles)


def _import_legacy_single_batch() -> int:
    """전환기 레거시 단일배치 잔여 처리(REQ-062-C4).

    data/analysis_results.json(호스트가 남긴 단일 응답)이 남아있는 동안만
    동작하는, Stage2 이전 프로토콜 그대로의 경로다. Stage2 export 는 더 이상
    이 파일을 쓰지 않으므로(REQ-062-C4), 배포 시점에 이미 대기 중이던 배치를
    1회 흡수하고 나면 자연히 no-op 이 된다.
    """
    if not RESULTS_FILE.exists() or RESULTS_FILE.stat().st_size == 0:
        LOG.debug("import_results(legacy): no results file found")
        return 0

    meta_file = _DATA_DIR / "pending_metadata.json"

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
        LOG.warning("import_results(legacy): results file exists but no article_ids metadata found")
        articles = get_unanalyzed_articles(limit=MAX_ARTICLES_PER_RUN)
        article_ids = [a["id"] for a in articles if not is_noise_title(a["title"])]
        if not article_ids:
            LOG.warning("import_results(legacy): cannot determine which articles to map results to")
            return 0

    raw_text = RESULTS_FILE.read_text().strip()
    if not raw_text:
        LOG.warning("import_results(legacy): results file is empty")
        return 0

    results = _parse_analysis_response(raw_text, len(article_ids))
    if results is None:
        LOG.error("import_results(legacy): failed to parse CLI response (len=%d)", len(raw_text))
        audit("NEWS_INTEL_IMPORT_PARSE_FAIL", actor="analyzer", details={
            "raw_length": len(raw_text),
            "first_200": raw_text[:200],
        })
        return 0

    article_map = _fetch_articles_by_ids(article_ids)

    articles_for_check = [article_map.get(aid, {"title": ""}) for aid in article_ids]
    results = _apply_quality_checks(articles_for_check, results)

    # REQ-061-2/3: echo된 idx로 article_id 에 정렬 매핑(RC1 — 과거에는
    # enumerate 위치로 매핑해 결과 순서가 뒤섞이면 전 필드가 오염됐다).
    aligned = _align_results_to_articles(results, article_ids)
    if aligned is None:
        reasons = _alignment_reject_reasons(results, article_ids)
        audit("NEWS_INTEL_ALIGN_REJECT", actor="analyzer", details={
            "path": "cli_import", **reasons,
        })
        LOG.error(
            "import_results(legacy): 정렬 검증 실패 — fail-closed 저장 거부(0건): %s", reasons,
        )
        RESULTS_FILE.unlink(missing_ok=True)
        meta_file.unlink(missing_ok=True)
        PENDING_FILE.unlink(missing_ok=True)
        return 0

    # SPEC-TRADING-062 REQ-062-B2: idx 정렬은 통과했으나 content-anchor
    # (title_head)가 불일치하는 제2 실패모드 검증(2026-07-08 인시던트).
    article_titles = {aid: article_map.get(aid, {}).get("title", "") for aid in article_ids}
    if _verify_content_anchor(aligned, article_titles, article_ids, path="cli_import"):
        RESULTS_FILE.unlink(missing_ok=True)
        meta_file.unlink(missing_ok=True)
        PENDING_FILE.unlink(missing_ok=True)
        return 0

    stored_count = _persist_cli_results(aligned, article_map)

    RESULTS_FILE.unlink(missing_ok=True)
    meta_file.unlink(missing_ok=True)
    PENDING_FILE.unlink(missing_ok=True)

    audit("NEWS_INTEL_IMPORT_OK", actor="analyzer", details={
        "articles_imported": stored_count,
        "results_parsed": len(results),
        "expected_count": len(article_ids),
    })
    LOG.info("import_results(legacy): stored %d analysis results from host CLI", stored_count)
    return stored_count


def _import_one_chunk(
    chunk_id: str, article_ids: list[int], result_file: Path,
) -> tuple[int, bool]:
    """청크 하나를 파싱 -> 정렬 -> content-anchor -> 저장까지 독립 처리한다.

    REQ-062-C3: 거부는 청크 전체 단위(fail-closed) — idx 정렬/content-anchor
    실패 시 이 청크는 0건 저장하지만 다른 청크의 저장에는 영향을 주지 않는다.

    Returns:
        (저장된 기사 수, 거부 여부).
    """
    raw_text = result_file.read_text().strip()
    if not raw_text:
        return 0, True

    results = _parse_analysis_response(raw_text, len(article_ids))
    if results is None:
        audit("NEWS_INTEL_IMPORT_PARSE_FAIL", actor="analyzer", details={
            "path": "cli_import_chunk", "chunk_id": chunk_id,
            "raw_length": len(raw_text),
        })
        LOG.error(
            "import_results(chunk=%s): 파싱 실패(len=%d)", chunk_id, len(raw_text),
        )
        return 0, True

    article_map = _fetch_articles_by_ids(article_ids)
    articles_for_check = [article_map.get(aid, {"title": ""}) for aid in article_ids]
    results = _apply_quality_checks(articles_for_check, results)

    aligned = _align_results_to_articles(results, article_ids)
    if aligned is None:
        reasons = _alignment_reject_reasons(results, article_ids)
        audit("NEWS_INTEL_ALIGN_REJECT", actor="analyzer", details={
            "path": "cli_import_chunk", "chunk_id": chunk_id, **reasons,
        })
        LOG.error(
            "import_results(chunk=%s): 정렬 검증 실패 — fail-closed 거부(0건): %s",
            chunk_id, reasons,
        )
        return 0, True

    article_titles = {aid: article_map.get(aid, {}).get("title", "") for aid in article_ids}
    if _verify_content_anchor(
        aligned, article_titles, article_ids,
        path="cli_import_chunk", chunk_id=chunk_id,
    ):
        return 0, True

    stored = _persist_cli_results(aligned, article_map)
    return stored, False


def _import_chunk_results() -> int:
    """호스트가 생성한 청크 결과(REQ-062-C1~C3)를 청크 단위로 독립 import한다.

    각 청크는 idx 정렬 -> content-anchor -> 저장까지 독립적으로 처리되어,
    한 청크가 오염(스크램블)돼도 다른 청크는 정상 저장된다(fail-closed 격리).
    아직 결과가 도착하지 않은 청크는 건드리지 않고, 메타데이터에 남겨 다음
    슬롯에서 재시도한다 — 완전히 해소된 청크만 메타데이터에서 제거한다.
    """
    meta_file = _DATA_DIR / "pending_metadata.json"
    results_dir = _DATA_DIR / "analysis_chunks"

    if not meta_file.exists():
        return 0
    try:
        meta = json.loads(meta_file.read_text())
    except (json.JSONDecodeError, OSError):
        return 0

    chunk_entries = meta.get("chunks")
    if not chunk_entries:
        return 0

    chunks_ok = 0
    chunks_rejected = 0
    articles_imported = 0
    articles_rejected = 0
    unresolved_entries: list[dict] = []

    for entry in chunk_entries:
        chunk_id = entry.get("chunk_id")
        article_ids = entry.get("article_ids") or []
        if not chunk_id or not article_ids:
            continue

        result_file = results_dir / f"result_{chunk_id}.json"
        if not result_file.exists() or result_file.stat().st_size == 0:
            # REQ-062-C3(c): 호스트가 이 청크를 아직 처리하지 못함 — 다음
            # 슬롯 재시도 대상으로 메타데이터에 남긴다. 다른 청크는 계속 진행.
            unresolved_entries.append(entry)
            continue

        stored, rejected = _import_one_chunk(chunk_id, article_ids, result_file)
        result_file.unlink(missing_ok=True)  # 성공/거부 무관하게 소비 완료(REQ-062-C3)
        if rejected:
            chunks_rejected += 1
            articles_rejected += len(article_ids)
        else:
            chunks_ok += 1
            articles_imported += stored

    if unresolved_entries:
        meta_file.write_text(json.dumps({
            "chunks": unresolved_entries,
            "exported_at": meta.get("exported_at"),
            "count": sum(len(e.get("article_ids") or []) for e in unresolved_entries),
        }, ensure_ascii=False))
    else:
        meta_file.unlink(missing_ok=True)

    if chunks_ok or chunks_rejected:
        audit("NEWS_INTEL_IMPORT_OK", actor="analyzer", details={
            "chunks_ok": chunks_ok,
            "chunks_rejected": chunks_rejected,
            "articles_imported": articles_imported,
            "articles_rejected": articles_rejected,
        })
        LOG.info(
            "import_results(chunked): chunks_ok=%d chunks_rejected=%d "
            "articles_imported=%d articles_rejected=%d",
            chunks_ok, chunks_rejected, articles_imported, articles_rejected,
        )

    return articles_imported


def import_host_results() -> int:
    """Import analysis results produced by the host Claude CLI.

    SPEC-TRADING-062 Stage2 (REQ-062-C1~C4): the host now answers in small
    per-chunk prompts (HOST_CHUNK_SIZE articles each) instead of one large
    batch, so a single scrambled chunk can no longer wipe out the whole
    cycle — each chunk is aligned / content-anchor-verified / stored
    independently (_import_chunk_results). Any legacy single-batch leftover
    from before this deploy (data/analysis_results.json + old-format
    metadata) is drained once via the pre-Stage2 path (REQ-062-C4).

    Returns the total number of articles successfully imported this call.
    """
    total = 0
    total += _import_legacy_single_batch()
    total += _import_chunk_results()
    return total


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
