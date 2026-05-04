"""Build macro_news.md from RSS feeds (Tier 1~4) + single Sonnet 4.6 summary.

REQ-CTX-01-5: 금 16:30 KST cron. Single LLM call. Tier 4 정책 모니터링도 같이.

Flow:
1. Fetch RSS from all configured feeds (parallel httpx, polite delay).
2. Filter: items published within last 7 days, deduplicate by title.
3. Build prompt with title + source + published_at (NO body).
4. Single Sonnet 4.6 call → macro_news.md content (5~7 headline summary in Korean).
5. atomic_write.

Tier 4 health: track per-feed success rate; on 3 consecutive failures, mark
T4 disabled in audit_log. Re-enable only via manual SQL.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import feedparser
import httpx
from anthropic import Anthropic

from trading.config import get_settings
from trading.contexts.rss_feeds import (
    HTTP_TIMEOUT,
    POLITE_DELAY_SECONDS,
    USER_AGENT,
    Feed,
    all_news_feeds,
    tier4_only,
)
from trading.contexts.utils import contexts_dir, guarded_build, now_kst_str
from trading.db.session import audit, connection

LOG = logging.getLogger(__name__)
MAX_AGE_DAYS = 7
MAX_ITEMS_PER_FEED = 8
MAX_TOTAL_ITEMS = 60


def _fetch_feed(feed: Feed) -> list[dict[str, Any]]:
    """Fetch + parse a single RSS. Returns list of {title, link, published, source, tier}."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, */*"}
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as c:
            r = c.get(feed.url, headers=headers)
        r.raise_for_status()
        parsed = feedparser.parse(r.content)
    except Exception as e:  # noqa: BLE001
        LOG.warning("feed fetch failed: %s — %s", feed.name, e)
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - MAX_AGE_DAYS * 86400
    items: list[dict[str, Any]] = []
    for entry in (parsed.entries or [])[:MAX_ITEMS_PER_FEED]:
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        # published parsing
        ts = None
        if getattr(entry, "published_parsed", None):
            try:
                ts = time.mktime(entry.published_parsed)
            except Exception:  # noqa: BLE001
                ts = None
        if ts and ts < cutoff:
            continue
        items.append({
            "title": title[:200],
            "link": entry.get("link", "")[:300],
            "published": entry.get("published", entry.get("updated", "")),
            "source": feed.name,
            "tier": feed.tier,
        })
    return items


def _fetch_all() -> tuple[list[dict[str, Any]], dict[str, bool]]:
    """Fetch all configured feeds. Return (items, t4_health) — t4_health[feed.name] = success."""
    items: list[dict[str, Any]] = []
    t4_health: dict[str, bool] = {}
    seen_titles: set[str] = set()

    for feed in all_news_feeds():
        time.sleep(POLITE_DELAY_SECONDS)
        feed_items = _fetch_feed(feed)
        if feed.tier == 4:
            t4_health[feed.name] = bool(feed_items)
        for it in feed_items:
            if it["title"] not in seen_titles:
                seen_titles.add(it["title"])
                items.append(it)
        if len(items) >= MAX_TOTAL_ITEMS:
            break
    items.sort(key=lambda x: (x["tier"], x["source"]))
    return items[:MAX_TOTAL_ITEMS], t4_health


def _record_t4_health(t4_health: dict[str, bool]) -> None:
    """Audit Tier 4 result and check 3-consecutive-failure rule per feed."""
    for name, ok in t4_health.items():
        audit(
            "TIER4_HEALTH_OK" if ok else "TIER4_HEALTH_FAIL",
            actor="cron.macro_news",
            details={"feed": name},
        )

    # Count consecutive failures per feed (last 3 health entries).
    sql = """
        WITH ranked AS (
            SELECT details->>'feed' AS feed, event_type, ts,
                   ROW_NUMBER() OVER (PARTITION BY details->>'feed' ORDER BY ts DESC) AS rn
              FROM audit_log
             WHERE event_type IN ('TIER4_HEALTH_OK','TIER4_HEALTH_FAIL')
               AND details->>'feed' IS NOT NULL
        )
        SELECT feed,
               SUM(CASE WHEN event_type='TIER4_HEALTH_FAIL' THEN 1 ELSE 0 END) AS fails,
               COUNT(*) AS total
          FROM ranked WHERE rn <= 3
         GROUP BY feed
        HAVING SUM(CASE WHEN event_type='TIER4_HEALTH_FAIL' THEN 1 ELSE 0 END) >= 3
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        breaches = list(cur.fetchall())
    if breaches:
        from trading.alerts.telegram import system_briefing
        names = ", ".join(b["feed"] for b in breaches)
        system_briefing(
            "Tier 4 정책 변경 의심",
            f"3회 연속 실패: {names}\nGoogle News URL/QPS 변경 가능성. 수동 검토 필요.",
        )


def _llm_summary(items: list[dict[str, Any]]) -> str:
    s = get_settings()
    if s.anthropic.api_key is None:
        return "_(ANTHROPIC_API_KEY 없음 — LLM 요약 생략)_\n\n" + _items_table(items)

    client = Anthropic(api_key=s.anthropic.api_key.get_secret_value())
    headlines_md = "\n".join(
        f"- [{it['source']} ({it['tier']})] {it['title']}" for it in items
    )
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=(
            "당신은 한국 주식 매매 시스템의 매크로 페르소나가 참고할 주간 글로벌 뉴스 큐레이터입니다. "
            "RSS 헤드라인 목록을 받아 한국어로 5~7개 핵심 인사이트로 요약하세요. "
            "원칙: "
            "(1) 환각 금지 — 헤드라인에 없는 내용 만들지 마세요. "
            "(2) 분류 — 지정학·정책·환율·유가·반도체·금리 카테고리 균형. "
            "(3) 출처 표기 — 각 인사이트 끝에 (출처1, 출처2) 식. "
            "(4) 한국 시장 영향 — 가능한 경우 한 줄로 함의 추가. "
            "(5) 모든 금액은 원화(₩)로 표시. USD($) 표기 금지."
        ),
        messages=[{"role": "user", "content": headlines_md}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _items_table(items: list[dict[str, Any]]) -> str:
    if not items:
        return "_(피드 항목 없음)_"
    out = ["| 출처 | Tier | 제목 | 발행 |", "|---|---|---|---|"]
    for it in items:
        title = it["title"].replace("|", "│")
        out.append(f"| {it['source']} | {it['tier']} | {title} | {it['published']} |")
    return "\n".join(out)


def build() -> str:
    items, t4_health = _fetch_all()
    _record_t4_health(t4_health)

    summary = _llm_summary(items) if items else "_(헤드라인 수집 실패)_"

    parts = [
        f"# Macro News · {datetime.now().date().isoformat()}",
        f"_생성: {now_kst_str()} · 주간 갱신 (금 16:30 KST cron)_",
        f"_헤드라인: {len(items)}건 · Tier 4 health: {sum(t4_health.values())}/{len(t4_health)} OK_",
        "",
        "## 주간 핵심 요약 (Sonnet 4.6)",
        "",
        summary,
        "",
        "---",
        "## 원본 헤드라인",
        "",
        _items_table(items),
        "",
        "---",
        "_RSS Tier 1~4 (한국 매체 + 공식 + Google News 우회). 본문 fetch 안 함, 헤드라인만._",
    ]
    return "\n".join(parts)


def main() -> int:
    target = contexts_dir() / "macro_news.md"
    return 0 if guarded_build("macro_news", build, target) else 1


if __name__ == "__main__":
    raise SystemExit(main())
