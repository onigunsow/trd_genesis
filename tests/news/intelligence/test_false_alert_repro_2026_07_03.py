"""SPEC-TRADING-060 HARD 게이트: 2026-07-03 오경보 재현 → 발화 0 (시나리오 1).

실측 클러스터 A/B 구성으로 3중 게이트 after:
  - 클러스터 A: 다수결→stock_market(캐치올) → 섹터 자격 제외 → 발화 0
  - 클러스터 B: 다수결→energy 82% / quorum 통과 / 섹터 일치 / 코로보레이션 미확증 → 발화 0

클러스터 B 코로보레이션 gate on/off 대조로 D1 증명.
"""

from __future__ import annotations

from unittest.mock import patch

# ---------------------------------------------------------------------------
# 실측 픽스처
# ---------------------------------------------------------------------------

# 클러스터 A: 2026-07-03 01:15 — finance_banking 으로 오발화
CLUSTER_A_SECTORS = (
    ["stock_market"] * 22
    + ["semiconductor"] * 9
    + ["macro_economy"] * 2
    + ["finance_banking"] * 1
    + ["biotech_pharma"] * 1
)
CLUSTER_A_TITLES = (
    [
        "한화솔루션 석유화학 제품 가격 인하",  # stock_market 저장
        "은행권 FDI 원스톱 서비스",  # semiconductor 로 저장 (오분류)
        "임신중절수술 관련 의료 시장 동향",  # stock_market 저장
        "우리은행 삼성월렛머니, 가입자 250만명 돌파",  # stock_market 저장
    ]
    + [f"stock_market 기사 {i}" for i in range(18)]
    + [f"semi 기사 {i}" for i in range(8)]
)

# 클러스터 B: 2026-07-03 08:15 — defense_aerospace 로 오발화
CLUSTER_B_SECTORS = ["energy_commodities"] * 9 + ["defense_aerospace"] * 2
CLUSTER_B_TITLES = [
    "반도체 흔들릴 때 피난처 된 금융주",  # energy_commodities 저장 (오분류)
    "SK하이닉스 목표가 상향",  # energy_commodities 저장 (오분류)
    "S&S 철강 BSI 7월 전망",  # energy_commodities 저장
    "포스코 철 스크랩 구매",  # energy_commodities 저장
    "수전해 수소 STS 강관 납품",  # energy_commodities 저장
    "코스피 8000선 반도체 피크아웃",  # energy_commodities 저장 (오분류)
    "코스피 지수 반등 기대",  # energy_commodities 저장
    "글로벌 공급망 재편",  # energy_commodities 저장
    "원자재 시황 보합",  # energy_commodities 저장 ← 에너지 '원자재' 키워드
    "NH농협은행 농식품펀드",  # defense_aerospace 저장 (오분류)
    "AWS FDE 10억 달러 투자",  # defense_aerospace 저장 (오분류)
]
# 주의: 수용 기준에서 "원자재"는 energy_commodities 키워드.
# 그러나 클러스터 B 실측 오경보는 에너지 키워드 없어 코로보레이션 미확증이었음.
# 테스트는 실 제목 배열 그대로 사용해야 결정론 보장.
# "원자재 시황 보합" 제목은 acceptance.md 에 없는 기사지만 sectors 배열상 9번째.
# 수용 기준 step 5: "에너지 키워드(유가·원유·OPEC·정유·LNG·원자재·이란·사우디·중동)
#                  는 어느 제목에도 없음"
# → 실제 "원자재 시황 보합" 은 있을 수 있어 score(energy)>0 가능.
# 따라서 실측 acceptance.md 의 제목 목록(8개 explicit)만 사용.
CLUSTER_B_TITLES_EXACT = [
    "반도체 흔들릴 때 피난처 된 금융주",
    "SK하이닉스 목표가 상향",
    "S&S 철강 BSI 7월 전망",
    "포스코 철 스크랩 구매",
    "수전해 수소 STS 강관 납품",
    "코스피 8000선 반도체 피크아웃",
    "NH농협은행 농식품펀드",
    "AWS FDE 10억 달러 투자",
]

# 실보유: energy_commodities + finance_banking
LIVE_WATCHLIST_SECTORS = {
    "energy_commodities": ["015760"],
    "finance_banking": ["316140"],
}
LIVE_TICKERS = {
    "015760": "한국전력",
    "316140": "우리금융지주",
}


# ---------------------------------------------------------------------------
# 헬퍼: 클러스터 dict 생성
# ---------------------------------------------------------------------------


def _make_cluster(
    cid: int,
    sector: str,
    sectors: list[str],
    titles: list[str],
    impact_max: int = 5,
) -> dict:
    n = len(sectors)
    return {
        "id": cid,
        "sector": sector,
        "impact_max": impact_max,
        "article_ids": list(range(cid * 100, cid * 100 + n)),
        "representative_title": titles[0] if titles else "대표제목",
        "_test_sectors": sectors,
        "_test_titles": titles,
        "_test_kws": [[] for _ in sectors],
    }


# ---------------------------------------------------------------------------
# 오경보 재현 테스트 (HARD 게이트)
# ---------------------------------------------------------------------------


class TestFalseAlertRepro20260703:
    """2026-07-03 오경보 재현 — 발화 0 HARD 게이트."""

    def _run_tag(
        self,
        clusters: list[dict],
        live_sectors: dict,
        live_tickers: dict,
    ) -> dict:
        """tag_portfolio_relevance 를 mock DB 로 실행."""
        from trading.news.intelligence import relevance as rel

        def mock_fetch_member_sectors(cluster):
            return cluster["_test_sectors"]

        def mock_fetch_member_texts(cluster):
            return list(
                zip(
                    cluster["_test_titles"],
                    cluster["_test_kws"],
                    strict=False,
                )
            )

        def mock_fetch_member_titles_and_keywords(cluster):
            return list(
                zip(
                    cluster["_test_titles"],
                    cluster["_test_kws"],
                    strict=False,
                )
            )

        with (
            patch.object(rel, "get_watchlist_sectors", return_value=live_sectors),
            patch.object(rel, "_get_clusters_for_date", return_value=clusters),
            patch.object(rel, "_load_live_tickers", return_value=live_tickers),
            patch.object(rel, "_fetch_member_sectors", side_effect=mock_fetch_member_sectors),
            patch.object(rel, "_fetch_member_texts", side_effect=mock_fetch_member_texts),
            patch.object(
                rel,
                "_fetch_member_titles_and_keywords",
                side_effect=mock_fetch_member_titles_and_keywords,
            ),
            patch.object(rel, "_update_cluster_relevance"),
            patch.object(rel, "_send_critical_alert") as mock_alert,
            patch.object(rel, "_any_alerted_recently", return_value=False),
            patch.object(rel, "_record_alerts"),
            patch.object(rel, "audit"),
        ):
            result = rel.tag_portfolio_relevance()

        return {"result": result, "alerts_called": mock_alert.call_count}

    def test_클러스터A_발화_0(self):
        """클러스터 A: 다수결→stock_market(캐치올) → 섹터 자격 제외 → 발화 0."""
        cl_a = _make_cluster(
            1,
            "finance_banking",  # 현재 DB 태깅 (오태깅)
            CLUSTER_A_SECTORS,
            CLUSTER_A_TITLES[:35] if len(CLUSTER_A_TITLES) >= 35 else CLUSTER_A_TITLES,
        )
        out = self._run_tag([cl_a], LIVE_WATCHLIST_SECTORS, LIVE_TICKERS)
        assert out["alerts_called"] == 0, (
            f"클러스터 A: 발화 {out['alerts_called']}건 (예상 0) — "
            "다수결→stock_market 캐치올 제외 게이트 실패"
        )

    def test_클러스터B_발화_0_코로보레이션_차단(self):
        """클러스터 B: quorum 82% 통과 + 섹터 일치 → 코로보레이션 미확증으로 차단 → 발화 0.

        이 테스트가 D1 을 증명한다: 3조건(다수결+quorum+섹터일치)만으로는 오경보 재발.
        """
        cl_b = _make_cluster(
            2,
            "defense_aerospace",  # 현재 DB 태깅 (오태깅)
            CLUSTER_B_SECTORS,
            CLUSTER_B_TITLES_EXACT,
        )
        out = self._run_tag([cl_b], LIVE_WATCHLIST_SECTORS, LIVE_TICKERS)
        assert out["alerts_called"] == 0, (
            f"클러스터 B: 발화 {out['alerts_called']}건 (예상 0) — "
            "코로보레이션 게이트 실패 (D1 미증명)"
        )

    def test_클러스터AB_모두_발화_0(self):
        """클러스터 A + B 동시 평가 → 총 발화 0."""
        cl_a = _make_cluster(
            1,
            "finance_banking",
            CLUSTER_A_SECTORS,
            CLUSTER_A_TITLES,
        )
        cl_b = _make_cluster(
            2,
            "defense_aerospace",
            CLUSTER_B_SECTORS,
            CLUSTER_B_TITLES_EXACT,
        )
        out = self._run_tag([cl_a, cl_b], LIVE_WATCHLIST_SECTORS, LIVE_TICKERS)
        assert out["alerts_called"] == 0

    # -----------------------------------------------------------------------
    # D1 증명: 코로보레이션 off 시 클러스터 B 재발화
    # -----------------------------------------------------------------------

    def test_D1_코로보레이션_off_시_클러스터B_발화(self):
        """코로보레이션 게이트를 우회하면 클러스터 B 는 발화한다 (D1 증명).

        이 테스트는 '3조건만으로는 오경보'를 입증한다.
        코로보레이션 게이트가 없다면 quorum + 섹터 일치로 발화.

        D1 시뮬레이션:
        - 다수결 적용 후 클러스터 B 섹터 = energy_commodities (82% 다수결)
        - 보유 섹터 energy_commodities 일치
        - quorum = 9/11 = 82% >= 50% 통과
        - 코로보레이션만 강제 True → 발화 발생 → D1 증명
        """
        from trading.news.intelligence import relevance as rel

        # 다수결 적용 후 DB 저장 시 섹터 = energy_commodities 로 변경된 상태를 시뮬레이션
        cl_b = _make_cluster(
            2,
            "energy_commodities",  # 다수결 결과 섹터 (quorum+섹터일치 통과 조건)
            CLUSTER_B_SECTORS,
            CLUSTER_B_TITLES_EXACT,
        )

        # 코로보레이션을 항상 True 로 강제 (게이트 비활성화 시뮬레이션)
        def mock_fetch_member_sectors(cluster):
            return cluster["_test_sectors"]

        def mock_fetch_member_titles_and_keywords(cluster):
            return list(zip(cluster["_test_titles"], cluster["_test_kws"], strict=False))

        def mock_fetch_member_texts(cluster):
            return list(zip(cluster["_test_titles"], cluster["_test_kws"], strict=False))

        with (
            patch.object(rel, "get_watchlist_sectors", return_value=LIVE_WATCHLIST_SECTORS),
            patch.object(rel, "_get_clusters_for_date", return_value=[cl_b]),
            patch.object(rel, "_load_live_tickers", return_value=LIVE_TICKERS),
            patch.object(rel, "_fetch_member_sectors", side_effect=mock_fetch_member_sectors),
            patch.object(rel, "_fetch_member_texts", side_effect=mock_fetch_member_texts),
            patch.object(
                rel,
                "_fetch_member_titles_and_keywords",
                side_effect=mock_fetch_member_titles_and_keywords,
            ),
            # 코로보레이션을 강제로 True (비활성화 시뮬레이션)
            patch.object(rel, "_sector_corroborated", return_value=True),
            patch.object(rel, "_update_cluster_relevance"),
            patch.object(rel, "_send_critical_alert") as mock_alert,
            patch.object(rel, "_any_alerted_recently", return_value=False),
            patch.object(rel, "_record_alerts"),
            patch.object(rel, "audit"),
        ):
            rel.tag_portfolio_relevance()

        # 코로보레이션 없으면 재발화 → 1건 이상
        assert mock_alert.call_count >= 1, (
            "D1 증명 실패: 코로보레이션 off 시에도 발화가 없음 "
            "→ 게이트 로직이 코로보레이션 이전에 이미 차단하고 있음 (오구현 의심)"
        )


# ---------------------------------------------------------------------------
# 양성 발화 테스트 (N1-d: 정당한 알림 정상 발화 확인)
# ---------------------------------------------------------------------------


class TestPositiveFire:
    """정당한 포트폴리오 연관 뉴스 → 발화 (REQ-060-4, 시나리오 2). N1-d 필수."""

    def _run_tag(self, clusters, live_sectors, live_tickers):
        from trading.news.intelligence import relevance as rel

        def mock_fetch_member_sectors(cluster):
            return cluster.get("_test_sectors", [cluster.get("sector", "unknown")])

        def mock_fetch_member_texts(cluster):
            titles = cluster.get("_test_titles", [cluster.get("representative_title", "")])
            kws = cluster.get("_test_kws", [[] for _ in titles])
            return list(zip(titles, kws, strict=False))

        def mock_fetch_member_titles_and_keywords(cluster):
            return mock_fetch_member_texts(cluster)

        with (
            patch.object(rel, "get_watchlist_sectors", return_value=live_sectors),
            patch.object(rel, "_get_clusters_for_date", return_value=clusters),
            patch.object(rel, "_load_live_tickers", return_value=live_tickers),
            patch.object(rel, "_fetch_member_sectors", side_effect=mock_fetch_member_sectors),
            patch.object(rel, "_fetch_member_texts", side_effect=mock_fetch_member_texts),
            patch.object(
                rel,
                "_fetch_member_titles_and_keywords",
                side_effect=mock_fetch_member_titles_and_keywords,
            ),
            patch.object(rel, "_update_cluster_relevance"),
            patch.object(rel, "_send_critical_alert") as mock_alert,
            patch.object(rel, "_any_alerted_recently", return_value=False),
            patch.object(rel, "_record_alerts"),
            patch.object(rel, "audit"),
        ):
            result = rel.tag_portfolio_relevance()

        return {"result": result, "alerts_called": mock_alert.call_count}

    def test_티커직접일치_발화(self):
        """클러스터 제목에 '우리금융지주' → 티커 직접일치 → 발화 (N1-d 양성 게이트)."""
        cl = {
            "id": 10,
            "sector": "finance_banking",
            "impact_max": 5,
            "article_ids": [101, 102],
            "representative_title": "우리금융지주, 분기 실적 어닝 서프라이즈",
            "_test_sectors": ["finance_banking"] * 2,
            "_test_titles": ["우리금융지주, 분기 실적 어닝 서프라이즈", "금융주 강세"],
            "_test_kws": [[], []],
        }
        out = self._run_tag([cl], LIVE_WATCHLIST_SECTORS, LIVE_TICKERS)
        assert out["alerts_called"] == 1, (
            f"양성 발화 실패 — 티커 직접일치 경로가 작동하지 않음 (alerts={out['alerts_called']})"
        )

    def test_섹터경로_quorum_코로보레이션_발화(self):
        """finance_banking quorum 83% + 코로보레이션 확증 → 발화 (시나리오 2)."""
        titles = [
            "우리금융지주 어닝 서프라이즈",
            "은행권 실적 호조",
            "지주사 배당 확대",
            "증권사 순익 증가",
            "보험사 흑자 전환",
            "기타 금융 뉴스",
        ]
        sectors = ["finance_banking"] * 5 + ["stock_market"] * 1  # 5/6 = 83%
        cl = {
            "id": 11,
            "sector": "finance_banking",
            "impact_max": 5,
            "article_ids": list(range(200, 206)),
            "representative_title": titles[0],
            "_test_sectors": sectors,
            "_test_titles": titles,
            "_test_kws": [[] for _ in titles],
        }
        out = self._run_tag([cl], LIVE_WATCHLIST_SECTORS, LIVE_TICKERS)
        assert out["alerts_called"] == 1, (
            f"섹터 경로 발화 실패 (alerts={out['alerts_called']}) — "
            "quorum+코로보레이션+섹터일치 로직 오류"
        )
