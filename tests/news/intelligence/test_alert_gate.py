"""SPEC-TRADING-060 M3/M4: 3중 알림 게이트 단위테스트 (REQ-060-4).

quorum / 코로보레이션 / 캐치올 제외 / 동점 규칙 / full_coverage 비활성.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _cluster_with_articles(
    sector: str,
    article_sectors: list[str],
    article_titles: list[str] | None = None,
    article_keywords: list[list[str]] | None = None,
    impact_max: int = 5,
) -> dict:
    """테스트용 클러스터. article_ids 로 DB 조회를 흉내낼 메타데이터 포함."""
    n = len(article_sectors)
    titles = article_titles or [f"제목 {i}" for i in range(n)]
    kws = article_keywords or [[] for _ in range(n)]
    ids = list(range(1, n + 1))
    return {
        "id": 99,
        "sector": sector,
        "impact_max": impact_max,
        "article_ids": ids,
        "representative_title": titles[0] if titles else "대표 제목",
        "_test_sectors": article_sectors,
        "_test_titles": titles,
        "_test_kws": kws,
    }


class TestClusterSectorQuorum:
    """relevance._cluster_sector_quorum() 단위테스트."""

    def _call(self, cluster: dict) -> float:
        from trading.news.intelligence import relevance as rel

        with patch.object(
            rel,
            "_fetch_member_sectors",
            return_value=cluster["_test_sectors"],
        ):
            return rel._cluster_sector_quorum(cluster)

    def test_energy_82pct_quorum(self):
        """energy_commodities x9 / total 11 → 약 0.818 (>= 0.5)."""
        cl = _cluster_with_articles(
            "energy_commodities",
            ["energy_commodities"] * 9 + ["defense_aerospace"] * 2,
        )
        q = self._call(cl)
        assert q == pytest.approx(9 / 11)

    def test_단일_멤버_quorum_100pct(self):
        """단일 멤버 클러스터 → quorum = 1.0."""
        cl = _cluster_with_articles("semiconductor", ["semiconductor"])
        q = self._call(cl)
        assert q == pytest.approx(1.0)

    def test_낮은_quorum(self):
        """멤버 22개 중 1개만 동의 → quorum < 0.5."""
        cl = _cluster_with_articles(
            "finance_banking",
            ["stock_market"] * 22
            + ["semiconductor"] * 9
            + ["macro_economy"] * 2
            + ["finance_banking"] * 1
            + ["biotech_pharma"] * 1,
        )
        q = self._call(cl)
        # 클러스터 섹터 finance_banking 의 동의 = 1/35
        assert q == pytest.approx(1 / 35)
        assert q < 0.5

    def test_빈_article_ids_zero(self):
        """article_ids 없음 → quorum = 0.0 (섹터 발화 억제)."""
        cl = _cluster_with_articles("semiconductor", [])
        cl["article_ids"] = []
        q = self._call(cl)
        assert q == 0.0


class TestSectorCorroborated:
    """relevance._sector_corroborated() 단위테스트."""

    def _call(self, cluster: dict, sector: str) -> bool:
        from trading.news.intelligence import relevance as rel

        with patch.object(
            rel,
            "_fetch_member_titles_and_keywords",
            return_value=list(
                zip(
                    cluster["_test_titles"],
                    cluster["_test_kws"],
                    strict=False,
                )
            ),
        ):
            return rel._sector_corroborated(cluster, sector)

    # -----------------------------------------------------------------------
    # 캐치올 명시 제외 (D10)
    # -----------------------------------------------------------------------

    def test_stock_market_명시_제외(self):
        """stock_market 은 _SECTOR_KEYWORDS 에 키워드 세트가 있지만 명시적 제외."""
        # 코스피·증시 등 stock_market 키워드가 제목에 있어도 항상 False
        cl = _cluster_with_articles(
            "stock_market",
            ["stock_market"],
            article_titles=["코스피 신고가 돌파 증시 회복"],
        )
        result = self._call(cl, "stock_market")
        assert result is False

    def test_macro_economy_명시_제외(self):
        """macro_economy 도 명시적 제외."""
        cl = _cluster_with_articles(
            "macro_economy",
            ["macro_economy"],
            article_titles=["글로벌 경기 침체 우려"],
        )
        result = self._call(cl, "macro_economy")
        assert result is False

    # -----------------------------------------------------------------------
    # 클러스터 B 재현 — 코로보레이션 미확증 (D1 핵심)
    # -----------------------------------------------------------------------

    def test_클러스터B_energy_미확증(self):
        """클러스터 B: energy_commodities 섹터이지만 에너지 키워드 0 → 미확증.

        제목에 반도체·철강 키워드 → semiconductor/steel score 높음.
        energy score = 0 < 1 → False (D1 핵심 검증).
        """
        titles = [
            "반도체 흔들릴 때 피난처 된 금융주",
            "SK하이닉스 목표가 상향",
            "S&S 철강 BSI 7월 전망",
            "포스코 철 스크랩 구매",
            "수전해 수소 STS 강관 납품",
            "코스피 8000선 반도체 피크아웃",
            "NH농협은행 농식품펀드",
            "AWS FDE 10억 달러 투자",
        ]
        cl = _cluster_with_articles(
            "energy_commodities",
            ["energy_commodities"] * len(titles),
            article_titles=titles,
        )
        result = self._call(cl, "energy_commodities")
        # energy 키워드(유가·원유·OPEC 등)가 제목에 없으므로 False
        assert result is False

    def test_에너지_키워드_있으면_확증(self):
        """제목에 에너지 키워드('유가') → energy_commodities 확증."""
        cl = _cluster_with_articles(
            "energy_commodities",
            ["energy_commodities"],
            article_titles=["유가 급락 OPEC 감산 합의"],
        )
        result = self._call(cl, "energy_commodities")
        assert result is True

    def test_finance_키워드_있으면_확증(self):
        """제목에 finance 키워드('은행', '지주') → finance_banking 확증."""
        cl = _cluster_with_articles(
            "finance_banking",
            ["finance_banking"] * 5,
            article_titles=[
                "은행권 금리 인상",
                "지주사 배당 확대",
                "증권사 실적 호조",
                "예금 금리 상승",
                "보험사 손해율 개선",
            ],
        )
        result = self._call(cl, "finance_banking")
        assert result is True

    # -----------------------------------------------------------------------
    # 동점 규칙 (D12)
    # -----------------------------------------------------------------------

    def test_동점_승리섹터포함_확증(self):
        """S 가 다른 섹터와 max 공동 달성 → 확증(S 포함 동점은 확증, D12)."""
        # "반도체 HBM" → semiconductor=4, "은행권" → finance_banking=4 (은행 부분문자열 2점)
        # 둘 다 max=4 공동 달성 → finance_banking 도 확증 (D12 동점 규칙)
        cl = _cluster_with_articles(
            "finance_banking",
            ["finance_banking"],
            article_titles=["반도체 HBM 수요 증가, 은행권 대출 확대"],
        )
        result = self._call(cl, "finance_banking")
        # 동점(finance=4, semi=4) → finance 는 max 공동 달성 → True (D12)
        assert result is True

    def test_승리섹터_단독최고_확증(self):
        """S 가 단독 최고 score → 확증."""
        cl = _cluster_with_articles(
            "semiconductor",
            ["semiconductor"],
            article_titles=["반도체 HBM D램 파운드리 TSMC 웨이퍼 낸드"],
        )
        result = self._call(cl, "semiconductor")
        assert result is True

    def test_score_0이면_미확증(self):
        """S score = 0 이면 무조건 미확증 (score >= 1 조건 불충족)."""
        cl = _cluster_with_articles(
            "energy_commodities",
            ["energy_commodities"],
            article_titles=["임의 뉴스 제목"],  # 에너지 키워드 없음
        )
        result = self._call(cl, "energy_commodities")
        assert result is False


class TestFullCoverageDisabled:
    """full_coverage 모드에서 섹터기반 critical 알림 비활성 (D4)."""

    def test_매핑섹터_0개_critical_비활성(self):
        """매핑 섹터 0개 → 섹터 경로 critical 알림 비활성, 티커 직접일치만 허용.

        tag_portfolio_relevance 의 full_coverage_mode 진입 시
        impact=5 클러스터여도 섹터 경로로 발화하지 않음.
        """
        from trading.news.intelligence import relevance as rel

        # sector_tickers 빈 dict → full_coverage_mode
        cluster = {
            "id": 1,
            "sector": "semiconductor",
            "impact_max": 5,
            "article_ids": [1, 2],
            "representative_title": "반도체 뉴스",
        }

        with (
            patch.object(rel, "get_watchlist_sectors", return_value={}),
            patch.object(rel, "_get_clusters_for_date", return_value=[cluster]),
            patch.object(rel, "_ticker_direct_match", return_value=False),
            patch.object(rel, "_update_cluster_relevance"),
            patch.object(rel, "_send_critical_alert") as mock_alert,
            patch.object(rel, "audit"),
        ):
            result = rel.tag_portfolio_relevance()

        # full_coverage + 티커 직접일치 False → critical 알림 0
        mock_alert.assert_not_called()
        assert result["alerts_sent"] == 0

    def test_매핑섹터_0개_ticker_direct_True_발화(self):
        """full_coverage_mode 라도 티커 직접일치 True → 발화 허용."""
        from trading.news.intelligence import relevance as rel

        cluster = {
            "id": 1,
            "sector": "semiconductor",
            "impact_max": 5,
            "article_ids": [1],
            "representative_title": "삼성전자 실적",
        }
        live_tickers = {"005930": "삼성전자"}

        with (
            patch.object(rel, "get_watchlist_sectors", return_value={}),
            patch.object(rel, "_get_clusters_for_date", return_value=[cluster]),
            patch.object(rel, "_load_live_tickers", return_value=live_tickers),
            patch.object(rel, "_ticker_direct_match", return_value=True),
            patch.object(rel, "_update_cluster_relevance"),
            patch.object(rel, "_send_critical_alert") as mock_alert,
            patch.object(rel, "_any_alerted_recently", return_value=False),
            patch.object(rel, "_record_alerts"),
            patch.object(rel, "audit"),
        ):
            rel.tag_portfolio_relevance()

        # 티커 직접일치 True → 발화
        mock_alert.assert_called_once()
