"""Realignment repair for already-polluted news_analysis rows.

SPEC-TRADING-061 REQ-061-4: 2026-07-07 이전 위치 기반 매핑(RC1/RC2, analyzer.py
참조)으로 저장된 news_analysis 행은 classification/sentiment/impact/keywords 가
엉뚱한 기사에 붙어 있을 수 있다. 이 모듈은 그 오염 구간을 --since 로 한정해
재분석·재정렬 덮어쓰기(UPSERT)하는 CLI-only, 멱등 엔트리포인트를 제공한다.

비용 0 원칙(strict_cost_zero): 이 모듈은 Anthropic API 를 직접 호출하지
않는다 — 기존 host CLI 배치 큐(export -> host `claude -p` -> import)를
재사용한다(analyzer.py 의 export_pending_for_host/import_host_results 와
동일한 공유볼륨 파일을 사용).

운영 주의: export_repair_batch/import_repair_results 는 PENDING_FILE/
RESULTS_FILE 을 정규 :05/:15 크론(scheduled_export/scheduled_import)과
공유한다. 정규 크론과 동시에 실행하면 같은 파일을 두고 경합한다 — 운영자가
수동으로, 조용한 시간대에만 실행한다(REQ-061-4, CLI-only, 자동 배선 없음).
"""

from __future__ import annotations

import json
import logging

from trading.db.session import audit, connection
from trading.news.intelligence.analyzer import (
    _DATA_DIR,
    ARTICLE_ANALYSIS_SYSTEM,
    MAX_ARTICLES_PER_RUN,
    PENDING_FILE,
    RESULTS_FILE,
    _align_results_to_articles,
    _alignment_reject_reasons,
    _corrected_sector,
    _fetch_articles_by_ids,
    _parse_analysis_response,
    _prepare_batch,
    build_analysis_prompt,
)

LOG = logging.getLogger(__name__)

REPAIR_META_FILE = _DATA_DIR / "repair_pending_metadata.json"


def _fetch_repair_targets(
    *, since: str, model_used: str, max_articles: int,
) -> list[dict]:
    """since 이후 발행 & model_used 로 분석된 news_analysis 대상 기사 조회."""
    sql = """
        SELECT a.id, a.title, a.source_name, a.sector, a.body_text, a.summary,
               a.published_at
          FROM news_articles a
          JOIN news_analysis na ON na.article_id = a.id
         WHERE na.model_used = %s
           AND a.published_at >= %s
         ORDER BY a.published_at DESC
         LIMIT %s
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (model_used, since, max_articles))
        return list(cur.fetchall())


def export_repair_batch(
    *,
    since: str,
    model_used: str = "claude-cli",
    max_articles: int = MAX_ARTICLES_PER_RUN,
) -> int:
    """오염 의심 구간을 재분석용으로 export한다(기존 host CLI 큐 재사용).

    REQ-061-4: 대상 = model_used 로 이미 분석된, since 이후 발행 기사. 정규
    export_pending_for_host 와 달리 "미분석" 조건이 아니라 "이미 분석됨(재수리
    대상)" 조건으로 조회한다.

    Returns:
        export된 기사 수 (0 이면 대상 없음).
    """
    articles = _fetch_repair_targets(
        since=since, model_used=model_used, max_articles=max_articles,
    )
    if not articles:
        LOG.info(
            "export_repair_batch: since=%s model_used=%s 대상 없음", since, model_used,
        )
        return 0

    batch_data = _prepare_batch(articles)
    user_prompt = build_analysis_prompt(batch_data)
    full_prompt = f"{ARTICLE_ANALYSIS_SYSTEM}\n\n---\n\n{user_prompt}"

    article_ids = [a["id"] for a in articles]

    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "prompt": full_prompt,
        "article_ids": article_ids,
        "since": since,
        "model_used": model_used,
        "count": len(article_ids),
    }
    PENDING_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    REPAIR_META_FILE.write_text(json.dumps({
        "article_ids": article_ids,
        "since": since,
        "model_used": model_used,
        "count": len(article_ids),
    }, ensure_ascii=False))

    audit("NEWS_INTEL_REPAIR_EXPORT", actor="repair", details={
        "articles_exported": len(article_ids),
        "since": since,
        "model_used": model_used,
    })
    LOG.info(
        "export_repair_batch: %d개 기사 재분석 대기열 기록 완료(since=%s, model_used=%s)",
        len(article_ids), since, model_used,
    )
    return len(article_ids)


def import_repair_results() -> int:
    """host CLI 재분석 결과를 import 하고 UPSERT 로 덮어쓴다(REQ-061-4).

    REQ-061-3 의 fail-closed 정렬검증을 통과한 경우에만 UPSERT한다 — 실패 시
    기존(오염 가능) 행을 그대로 보존한다(다른 오염으로 대체하지 않음).
    멱등: 같은 (article_id, 결과) 재실행은 UPDATE 만 반복하고 부작용이 없다.
    """
    if not RESULTS_FILE.exists() or RESULTS_FILE.stat().st_size == 0:
        LOG.info("import_repair_results: 결과 파일 없음")
        return 0

    if not REPAIR_META_FILE.exists():
        LOG.warning(
            "import_repair_results: 메타데이터 없음 — export_repair_batch 선행 필요"
        )
        return 0

    meta = json.loads(REPAIR_META_FILE.read_text())
    article_ids: list[int] = meta.get("article_ids", [])
    if not article_ids:
        LOG.warning("import_repair_results: article_ids 비어있음")
        return 0

    raw_text = RESULTS_FILE.read_text().strip()
    if not raw_text:
        LOG.warning("import_repair_results: 결과 파일이 비어있음")
        return 0

    results = _parse_analysis_response(raw_text, len(article_ids))
    if results is None:
        LOG.error("import_repair_results: 파싱 실패(len=%d)", len(raw_text))
        audit("NEWS_INTEL_REPAIR_PARSE_FAIL", actor="repair", details={
            "raw_length": len(raw_text),
        })
        return 0

    article_map = _fetch_articles_by_ids(article_ids)

    aligned = _align_results_to_articles(results, article_ids)
    if aligned is None:
        reasons = _alignment_reject_reasons(results, article_ids)
        audit("NEWS_INTEL_ALIGN_REJECT", actor="repair", details={
            "path": "repair_import", **reasons,
        })
        LOG.error(
            "import_repair_results: 정렬 검증 실패(fail-closed, 덮어쓰기 0건): %s",
            reasons,
        )
        return 0

    sql = """
        INSERT INTO news_analysis
            (article_id, summary_2line, impact_score, keywords, sentiment,
             classification, model_used, token_input, token_output, cost_krw)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (article_id) DO UPDATE SET
            summary_2line = EXCLUDED.summary_2line,
            impact_score = EXCLUDED.impact_score,
            keywords = EXCLUDED.keywords,
            sentiment = EXCLUDED.sentiment,
            classification = EXCLUDED.classification,
            model_used = EXCLUDED.model_used,
            token_input = EXCLUDED.token_input,
            token_output = EXCLUDED.token_output,
            cost_krw = EXCLUDED.cost_krw,
            analyzed_at = NOW()
    """

    repaired = 0
    with connection() as conn, conn.cursor() as cur:
        for aid, result in aligned:
            cur.execute(sql, (
                aid,
                result["summary_2line"],
                result["impact_score"],
                result["keywords"],
                result["sentiment"],
                result["classification"],
                "claude-cli",
                0,
                0,
                0.0,
            ))

            new_sector = _corrected_sector(result, article_map.get(aid, {}).get("sector", ""))
            if new_sector:
                cur.execute(
                    "UPDATE news_articles SET sector = %s WHERE id = %s",
                    (new_sector, aid),
                )

            repaired += 1

    RESULTS_FILE.unlink(missing_ok=True)
    REPAIR_META_FILE.unlink(missing_ok=True)
    PENDING_FILE.unlink(missing_ok=True)

    audit("NEWS_INTEL_REPAIR_IMPORT_OK", actor="repair", details={
        "rows_repaired": repaired,
        "since": meta.get("since"),
        "model_used": meta.get("model_used"),
    })
    LOG.info("import_repair_results: %d개 행 재정렬 덮어쓰기 완료", repaired)
    return repaired
