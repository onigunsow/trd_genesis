"""Portfolio Relevance Tagger (SPEC-TRADING-014 Module 4, SPEC-TRADING-060 개편).

스토리 클러스터를 현재 보유/워치리스트와 대조해 포트폴리오 연관 여부를 태깅한다.
고위험 포트폴리오 연관 클러스터에 [투자 주목] 태그를 부여한다.

SPEC-TRADING-060 변경:
- TICKER_SECTOR_MAP 하드코딩 제거 → resolve_ticker_sector() 위임
- 3중 알림 게이트: 티커 직접일치 OR (섹터일치 AND quorum >= 50% AND 코로보레이션)
- full_coverage 모드: 섹터기반 critical 알림 비활성 (D4)
- _load_watchlist_tickers: qty 컬럼 수정 (N1), 예외 폴백 [] 반환
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date

from trading.db.session import audit, connection

LOG = logging.getLogger(__name__)

# REQ-INTEL-04-3: [투자 주목] 태그 최소 임팩트 점수
IMPACT_ALERT_THRESHOLD = 4
# REQ-INTEL-04-4: 텔레그램 critical 알림 임계
IMPACT_CRITICAL_THRESHOLD = 5

# 캐치올 섹터 집합 (D10): 섹터기반 알림 자격에서 명시적 제외
_CATCHALL_SECTORS: frozenset[str] = frozenset({"stock_market", "macro_economy"})

# 코로보레이션 최소 득점 (score(S) >= 1 AND score(S) == max)
_CORROBORATION_MIN_SCORE = 1


def get_watchlist_sectors() -> dict[str, list[str]]:
    """현재 보유/워치리스트에서 섹터 → 티커 목록 매핑을 반환한다.

    SPEC-TRADING-060: TICKER_SECTOR_MAP 제거, resolve_ticker_sector 사용.
    미매핑 티커는 섹터 맵에서 제외 (가짜 캐치올 금지).

    Returns:
        {sector: [ticker1, ticker2, ...]} — 매핑 섹터 0개면 빈 dict.
    """
    from trading.news.ticker_sector import resolve_ticker_sector

    tickers = _load_watchlist_tickers()
    if not tickers:
        return {}

    sector_tickers: dict[str, list[str]] = {}
    for ticker in tickers:
        sector = resolve_ticker_sector(ticker)
        if sector is None:
            # 미매핑 티커는 제외 — 가짜 캐치올 금지
            continue
        sector_tickers.setdefault(sector, []).append(ticker)

    return sector_tickers


def _load_watchlist_tickers() -> list[str]:
    """DB 에서 실보유 티커를 조회해 반환한다.

    SPEC-TRADING-060 N1 수정:
    - 쿼리: positions.quantity → positions.qty (실 컬럼명)
    - 예외 폴백: [] 반환 (하드코딩 TICKER_SECTOR_MAP.keys() 제거)
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            # 실보유 포지션 (N1: 컬럼명 qty 사용)
            cur.execute("""
                SELECT DISTINCT ticker FROM positions
                WHERE qty > 0
            """)
            tickers = [row["ticker"] for row in cur.fetchall()]

            # 워치리스트 테이블이 있으면 추가
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = 'watchlist'
                )
            """)
            if cur.fetchone()["exists"]:
                cur.execute("SELECT DISTINCT ticker FROM watchlist WHERE active = true")
                tickers.extend(row["ticker"] for row in cur.fetchall())

            return list(set(tickers))
    except Exception:
        LOG.debug("보유 티커 DB 조회 실패 — 빈 리스트 반환")
        return []


def _load_live_tickers() -> dict[str, str]:
    """실보유+워치리스트 티커 → 회사명(ticker_metadata.name) 매핑 반환.

    _ticker_direct_match 에서 회사명 부분문자열 매칭에 사용.
    빈 name / 공백뿐 name 은 포함하지 않는다 (빈 문자열 전수매치 방지).

    Returns:
        {ticker: name} — name 이 비어 있으면 제외.
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            # N1: qty 컬럼 사용
            cur.execute("""
                SELECT DISTINCT p.ticker, COALESCE(tm.name, '') AS name
                  FROM positions p
                  LEFT JOIN ticker_metadata tm ON tm.ticker = p.ticker
                 WHERE p.qty > 0
            """)
            rows = list(cur.fetchall())

            # 워치리스트 추가
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = 'watchlist'
                )
            """)
            if cur.fetchone()["exists"]:
                cur.execute("""
                    SELECT DISTINCT w.ticker, COALESCE(tm.name, '') AS name
                      FROM watchlist w
                      LEFT JOIN ticker_metadata tm ON tm.ticker = w.ticker
                     WHERE w.active = true
                """)
                rows.extend(cur.fetchall())

        result: dict[str, str] = {}
        for row in rows:
            ticker = row["ticker"]
            name = (row["name"] or "").strip()
            if name:  # 빈 name 가드 — 전수매치 방지 (D9)
                result[ticker] = name
        return result
    except Exception:
        LOG.debug("live_tickers 조회 실패 — 빈 dict 반환")
        return {}


def _fetch_member_sectors(cluster: dict) -> list[str]:
    """클러스터 멤버 기사의 섹터 목록을 DB 에서 조회해 반환."""
    ids = cluster.get("article_ids") or []
    if not ids:
        return []
    sql = "SELECT sector FROM news_articles WHERE id = ANY(%s)"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (ids,))
        return [row["sector"] for row in cur.fetchall() if row["sector"]]


def _fetch_member_texts(cluster: dict) -> list[tuple[str, list[str]]]:
    """클러스터 멤버 기사의 (제목, keywords 배열) 쌍 목록 반환.

    _ticker_direct_match 에서 회사명 부분문자열 검사에 사용.
    """
    ids = cluster.get("article_ids") or []
    if not ids:
        return []
    sql = """
        SELECT a.title, COALESCE(na.keywords, '{}') AS keywords
          FROM news_articles a
          LEFT JOIN news_analysis na ON na.article_id = a.id
         WHERE a.id = ANY(%s)
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (ids,))
        return [(row["title"] or "", row["keywords"] or []) for row in cur.fetchall()]


def _fetch_member_titles_and_keywords(cluster: dict) -> list[tuple[str, list[str]]]:
    """코로보레이션 채점용 멤버 제목·keywords 목록 반환.

    _fetch_member_texts 와 동일 구조이나 의미적으로 분리.
    """
    return _fetch_member_texts(cluster)


def _cluster_sector_quorum(cluster: dict) -> float:
    """클러스터 섹터에 동의하는 멤버 비율(0.0~1.0)을 반환한다.

    SPEC-TRADING-060 REQ-060-4b: quorum >= 50% 조건.
    계산만, 스키마 변경 없음.

    Returns:
        0.0 (article_ids 없음 / 동의 0) ~ 1.0.
    """
    sectors = _fetch_member_sectors(cluster)
    if not sectors:
        return 0.0
    cluster_sector = cluster.get("sector", "")
    matching = sum(1 for s in sectors if s == cluster_sector)
    return matching / len(sectors)


def _sector_corroborated(cluster: dict, sector: str) -> bool:
    """멤버 제목 키워드 채점으로 승리 섹터를 독립 확증한다.

    # @MX:NOTE: [AUTO] SPEC-TRADING-060 REQ-060-4 코로보레이션 게이트.
    # @MX:REASON: sector_classifier._SECTOR_KEYWORDS 재사용(신규 세트 금지).
    #             score(S) >= 1 AND score(S) == max(전 섹터) 여야 확증.
    #             S 포함 동점은 확증, S < max 는 미확증 (D12).
    #             캐치올(stock_market·macro_economy)은 명시적 제외 (D10).
    #             채점 대상: 멤버 **제목만** (SPEC REQ-060-4 명시).
    #             news_analysis.keywords 는 채점 제외 — 기사와 정렬이 어긋난
    #             오염 표면 (2026-07-04 04:15 오경보 실측으로 확인).

    캐치올 섹터는 키워드 세트가 존재해도 즉시 False.
    stock_market 은 _SECTOR_KEYWORDS 에 '코스피'·'코스닥' 등이 있으므로
    "키워드 없어 자연히 미확증" 논리에 의존하면 안 된다 (SPEC 명시 주의).

    채점 표면: 멤버 기사의 제목(title)만. news_analysis.keywords 는 제외.
    근거: keywords 는 해당 기사가 아닌 다른 맥락의 분석 결과가 담길 수 있어
    코로보레이션 신호를 오염시킨다 (2026-07-04 04:15 클러스터 57986:
    제목에는 에너지 키워드 없으나 keywords={유가,원유,이란}이 4개 멤버에
    분포 → 오경보 유발).

    Args:
        cluster: story_clusters 행.
        sector: 확증할 섹터 키.

    Returns:
        True 이면 확증(발화 가능), False 이면 미확증(발화 억제).
    """
    # D10: 캐치올 명시 제외
    if sector in _CATCHALL_SECTORS:
        return False

    texts = _fetch_member_titles_and_keywords(cluster)
    if not texts:
        return False

    from trading.news.sector_classifier import _SECTOR_KEYWORDS

    # 모든 멤버 제목만 합산해 섹터별 키워드 채점 (REQ-060-4, SPEC-TRADING-060).
    # news_analysis.keywords 는 채점에 포함하지 않는다 — 기사와 정렬이 어긋난
    # 오염 표면이기 때문이다 (2026-07-04 04:15 오경보 실측: 클러스터 57986의
    # 7개 멤버 제목에는 에너지 키워드가 없으나 keywords={유가,원유,이란}이
    # 4개 멤버에 분포, 키워드 채점 분기가 energy_commodities score를 0→9로
    # 올려 오경보를 유발함 — 제목만 채점으로 완전 억제).
    sector_scores: dict[str, int] = {s: 0 for s in _SECTOR_KEYWORDS}
    for title, _kws in texts:
        title_lower = title.lower()
        for s, keywords in _SECTOR_KEYWORDS.items():
            for kw in keywords:
                if kw.lower() in title_lower:
                    sector_scores[s] += 2  # 제목 가중치만 (weight=2)

    target_score = sector_scores.get(sector, 0)
    if target_score < _CORROBORATION_MIN_SCORE:
        # score >= 1 조건 불충족
        return False

    max_score = max(sector_scores.values(), default=0)
    # score(S) == max 이면 확증 (동점 포함, D12)
    return target_score >= max_score


def _ticker_direct_match(
    cluster: dict,
    live_tickers: dict[str, str],
) -> bool:
    """실보유+워치리스트 회사명이 멤버 제목/분석키워드에 정확 부분문자열로 등장하면 True.

    # @MX:NOTE: [AUTO] SPEC-TRADING-060 D9 — 티커 직접일치.
    # @MX:REASON: 뉴스 모듈에 티커 추출기 없으므로 company name 부분문자열로 정의.
    #             퍼지·별칭·자회사 금지 → 재현 결정론(클러스터 A 발화 0) 불변식.
    #             빈 name 가드 필수: name='' 은 전 텍스트의 부분문자열이므로
    #             미가드 시 name 미채움 44/54 티커가 전수 발화 유발.

    Args:
        cluster: story_clusters 행.
        live_tickers: {ticker: company_name} — 빈 name 은 미포함 (호출자 책임).

    Returns:
        하나라도 히트하면 True.
    """
    # 빈 live_tickers 또는 모두 빈 name 이면 즉시 False
    valid = {t: n for t, n in live_tickers.items() if n and n.strip()}
    if not valid:
        return False

    texts = _fetch_member_texts(cluster)
    if not texts:
        return False

    for name in valid.values():
        name_stripped = name.strip()
        if not name_stripped:
            continue
        for title, kws in texts:
            # 제목 정확 부분문자열
            if name_stripped in (title or ""):
                return True
            # 분석 키워드 배열 원소 정확 일치
            if kws and name_stripped in kws:
                return True

    return False


# @MX:WARN: [AUTO] tag_portfolio_relevance 발화 게이트.
# @MX:REASON: 자본·운영자 신호 게이트. quorum·코로보레이션·티커직접·full_coverage
#             비활성 조건이 잘못되면 오경보(2026-07-03류) 재발 또는 정당 알림 누락.
#             코로보레이션은 클러스터 B 재발화를 막는 유일 장치(D1) — 분기 복잡도 주의.
# @MX:SPEC: SPEC-TRADING-060


def tag_portfolio_relevance(
    *,
    cluster_date: date | None = None,
    sector: str | None = None,
) -> dict[str, int]:
    """스토리 클러스터에 포트폴리오 연관성을 태깅한다.

    SPEC-TRADING-060 3중 게이트:
    (a) 티커 직접일치 — live holdings+watchlist 회사명 부분문자열.
    (b) 섹터 경로 — 섹터일치 AND quorum >= 50% AND 코로보레이션 확증
        (캐치올 섹터는 자격 제외, D10).

    full_coverage_mode (매핑 섹터 0개): 섹터기반 critical 알림 비활성 (D4).
    티커 직접일치는 full_coverage 에서도 동작한다.

    Returns:
        {"tagged": N, "alerts_sent": N}
    """
    if cluster_date is None:
        cluster_date = date.today()

    sector_tickers = get_watchlist_sectors()
    # D4: 매핑 섹터 0개 → full_coverage_mode 진입
    full_coverage_mode = len(sector_tickers) == 0

    # 회사명 직접일치용 live_tickers (빈 name 제외)
    live_tickers = _load_live_tickers()

    clusters = _get_clusters_for_date(cluster_date, sector)

    tagged_count = 0
    alerts_sent = 0

    for cluster in clusters:
        is_relevant = False
        relevant_tickers: list[str] = []

        # ---- 게이트 순서: 티커직접 → 섹터경로 ----

        # (a) 티커 직접일치 (섹터 판정 무관)
        if live_tickers and _ticker_direct_match(cluster, live_tickers):
            is_relevant = True
            # relevant_tickers: 빠른 구현을 위해 live_tickers 전체 사용
            relevant_tickers = list(live_tickers.keys())

        # (b) 섹터 경로 (티커 직접일치 미실패 시에도 OR 로 추가 평가)
        if not is_relevant and not full_coverage_mode:
            cluster_sector = cluster["sector"]
            # D10: 캐치올 섹터는 섹터 경로 자격 명시 제외
            if cluster_sector not in _CATCHALL_SECTORS and cluster_sector in sector_tickers:
                quorum = _cluster_sector_quorum(cluster)
                if quorum >= 0.5 and _sector_corroborated(cluster, cluster_sector):
                    is_relevant = True
                    relevant_tickers = sector_tickers[cluster_sector]

        portfolio_relevant = is_relevant
        should_tag = is_relevant and cluster["impact_max"] >= IMPACT_ALERT_THRESHOLD

        _update_cluster_relevance(
            cluster["id"],
            portfolio_relevant=portfolio_relevant,
            relevance_tickers=relevant_tickers,
        )

        if should_tag:
            tagged_count += 1

        # REQ-INTEL-04-4: impact == 5 AND portfolio_relevant → Telegram 알림
        # D4: full_coverage_mode 에서 섹터기반 발화 비활성 — 티커 직접일치만 허용
        can_alert = portfolio_relevant and cluster["impact_max"] >= IMPACT_CRITICAL_THRESHOLD
        if full_coverage_mode:
            # D4: full_coverage 에서 섹터 경로 critical 알림 비활성
            # 티커 직접일치만 허용 (live_tickers 히트 여부로 판별)
            ticker_hit = bool(live_tickers and _ticker_direct_match(cluster, live_tickers))
            can_alert = can_alert and ticker_hit

        if can_alert:
            _send_critical_alert(cluster)
            alerts_sent += 1

    result = {"tagged": tagged_count, "alerts_sent": alerts_sent}

    audit(
        "NEWS_INTEL_RELEVANCE_OK",
        actor="relevance",
        details={
            "cluster_date": str(cluster_date),
            "clusters_evaluated": len(clusters),
            "tagged_count": tagged_count,
            "alerts_sent": alerts_sent,
            "full_coverage_mode": full_coverage_mode,
        },
    )

    LOG.info(
        "Relevance tagging: %d clusters, %d tagged [투자 주목], %d alerts",
        len(clusters),
        tagged_count,
        alerts_sent,
    )
    return result


def _get_clusters_for_date(cluster_date: date, sector: str | None = None) -> list[dict]:
    """지정 날짜의 스토리 클러스터를 조회한다."""
    sql = "SELECT * FROM story_clusters WHERE cluster_date = %s"
    params: list = [cluster_date]
    if sector:
        sql += " AND sector = %s"
        params.append(sector)
    sql += " ORDER BY impact_max DESC"

    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def _update_cluster_relevance(
    cluster_id: int,
    *,
    portfolio_relevant: bool,
    relevance_tickers: list[str],
) -> None:
    """클러스터의 portfolio_relevant 플래그를 업데이트한다."""
    sql = """
        UPDATE story_clusters
           SET portfolio_relevant = %s,
               relevance_tickers = %s
         WHERE id = %s
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (portfolio_relevant, relevance_tickers, cluster_id))


# @MX:NOTE: SPEC-TRADING-026 c4 — re-clustering 은 ~3h 마다; 18h 롤링 윈도우로
# 지속 스토리의 중복 알림 억제(자정 넘어도 동일 스토리 재알림 방지).
# @MX:SPEC: SPEC-TRADING-026
_ALERT_DEDUP_WINDOW_HOURS = 18


def _alert_keys(cluster: dict) -> list[str]:
    """클러스터용 안정적 dedup 키 목록 반환 (멤버 기사 ID 기반).

    SPEC-026 c4: article_ids 기반이라 re-clustering 후에도 키가 안정적.
    article_ids 없으면 대표 제목 해시로 폴백.
    """
    ids = cluster.get("article_ids") or []
    keys = [f"art:{aid}" for aid in ids]
    if not keys:
        title = cluster.get("representative_title", "") or ""
        if title:
            keys = [hashlib.sha256(title.encode()).hexdigest()[:32]]
    return keys


def _any_alerted_recently(keys: list[str]) -> bool:
    """dedup 윈도우 내에 알림을 이미 보낸 키가 있으면 True."""
    if not keys:
        return False
    sql = (
        "SELECT 1 FROM news_alerts_sent "
        "WHERE content_hash = ANY(%s) "
        "AND alerted_at >= now() - make_interval(hours => %s) LIMIT 1"
    )
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (keys, _ALERT_DEDUP_WINDOW_HOURS))
        return cur.fetchone() is not None


def _record_alerts(keys: list[str]) -> None:
    """알림 전송 기록 (UTC 일 기준 idempotent)."""
    if not keys:
        return
    sql = "INSERT INTO news_alerts_sent (content_hash) VALUES (%s) ON CONFLICT DO NOTHING"
    with connection() as conn, conn.cursor() as cur:
        for k in keys:
            cur.execute(sql, (k,))


def _send_critical_alert(cluster: dict) -> None:
    """포트폴리오 관련 고위험 뉴스에 Telegram 알림 전송.

    REQ-INTEL-04-4: impact == 5 AND portfolio-relevant.
    SPEC-026 c4: 안정적 article ID 기반 dedup; 전송 성공 후 기록.
    텔레그램 메시지 포맷 불변 (SPEC-TRADING-060 REQ-060-5).
    """
    try:
        from trading.alerts.telegram import system_briefing

        keys = _alert_keys(cluster)
        if _any_alerted_recently(keys):
            return
        title = cluster["representative_title"]
        sector = cluster["sector"]
        msg = (
            f"[NEWS ALERT] {title} "
            f"(Impact 5/5, Sector: {sector}) "
            f"— 포트폴리오 관련 고위험 뉴스 감지"
        )
        system_briefing("News Intelligence", msg)
        _record_alerts(keys)
    except Exception as e:
        LOG.warning("critical 뉴스 알림 전송 실패: %s", e)
