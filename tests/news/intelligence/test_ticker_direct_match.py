"""SPEC-TRADING-060 D9: 티커 직접일치(_ticker_direct_match) 테스트.

회사명(ticker_metadata.name) 정확 부분문자열 매칭.
빈/공백 name 가드, 분석키워드 경로, 별칭 미인식.
"""

from __future__ import annotations


def _cluster(
    titles: list[str],
    keywords_per_article: list[list[str]] | None = None,
    article_ids: list[int] | None = None,
) -> dict:
    """테스트용 클러스터 dict. article_ids 로 조회하는 구현을 위해 mock 지원."""
    ids = article_ids or list(range(1, len(titles) + 1))
    return {
        "id": 1,
        "article_ids": ids,
        "_test_titles": titles,
        "_test_keywords": keywords_per_article or [[] for _ in titles],
    }


class TestTickerDirectMatch:
    """relevance._ticker_direct_match() 단위테스트.

    DB 조회를 mock 으로 교체해 순수 로직 검증.
    """

    def _call(
        self,
        cluster: dict,
        live_tickers: dict[str, str],  # ticker → company name
    ) -> bool:
        """_ticker_direct_match 를 mock DB 데이터로 호출."""
        from unittest.mock import patch

        from trading.news.intelligence import relevance as rel_mod

        with patch.object(
            rel_mod,
            "_fetch_member_texts",
            return_value=list(
                zip(
                    cluster["_test_titles"],
                    cluster["_test_keywords"],
                    strict=False,
                )
            ),
        ):
            return rel_mod._ticker_direct_match(cluster, live_tickers)

    # -----------------------------------------------------------------------
    # 양성 케이스
    # -----------------------------------------------------------------------

    def test_회사명_제목_부분문자열_양성(self):
        """제목에 회사명이 정확 부분문자열로 포함되면 True."""
        cl = _cluster(["우리금융지주, 분기 실적 어닝 서프라이즈"])
        result = self._call(cl, {"316140": "우리금융지주"})
        assert result is True

    def test_회사명_분석키워드_양성(self):
        """분석 keywords 배열 원소에 회사명이 일치하면 True (시나리오 2b 분석키워드 경로)."""
        cl = _cluster(
            ["임의 제목"],
            keywords_per_article=[["한국전력", "전력공급"]],
        )
        result = self._call(cl, {"015760": "한국전력"})
        assert result is True

    def test_복수_티커_하나라도_히트_양성(self):
        """복수 보유 티커 중 하나라도 제목에 있으면 True."""
        cl = _cluster(["한국전력 요금 인상 발표"])
        result = self._call(cl, {"316140": "우리금융지주", "015760": "한국전력"})
        assert result is True

    # -----------------------------------------------------------------------
    # 음성 케이스 (별칭·자회사 미인식)
    # -----------------------------------------------------------------------

    def test_별칭_우리은행_미일치(self):
        """자회사 브랜드 '우리은행' 만 등장 → 보유 '우리금융지주' 미일치 → False (D9 정밀)."""
        cl = _cluster(["우리은행 삼성월렛머니, 가입자 250만명 돌파"])
        result = self._call(cl, {"316140": "우리금융지주"})
        assert result is False

    def test_미보유_회사명_False(self):
        """제목에 회사명 있으나 보유 티커에 없으면 False."""
        cl = _cluster(["포스코 철강 수출 확대"])
        result = self._call(cl, {"316140": "우리금융지주"})
        assert result is False

    def test_빈_클러스터_제목_False(self):
        """클러스터 멤버 없음 → False (크래시 없음)."""
        cl = _cluster([])
        result = self._call(cl, {"316140": "우리금융지주"})
        assert result is False

    # -----------------------------------------------------------------------
    # 빈 name 가드 (D9 보강 — 라이브 44/54행 name='')
    # -----------------------------------------------------------------------

    def test_빈_name_티커_False(self):
        """ticker_metadata.name='' 인 티커는 후보 제외 → False (전수매치 방지)."""
        cl = _cluster(["임의 제목입니다"])
        result = self._call(cl, {"005930": ""})
        assert result is False

    def test_공백뿐_name_티커_False(self):
        """name이 공백뿐(' ') 인 티커도 후보 제외 → False."""
        cl = _cluster(["임의 제목입니다"])
        result = self._call(cl, {"005930": "  "})
        assert result is False

    def test_빈name_과_채워진name_혼재_채워진것만_매칭(self):
        """빈 name 티커와 채워진 name 티커 혼재 시 채워진 것만 매칭 대상."""
        cl = _cluster(["우리금융지주 실적 발표"])
        result = self._call(
            cl,
            {"005930": "", "316140": "우리금융지주"},
        )
        assert result is True

    def test_빈name_티커만_있고_제목_무엇이든_False(self):
        """모든 보유 티커의 name 이 비어있으면 어떤 제목이어도 False."""
        cl = _cluster(["삼성전자 반도체 HBM 출하"])
        result = self._call(cl, {"005930": "", "000660": ""})
        assert result is False

    # -----------------------------------------------------------------------
    # 클러스터 A 2026-07-03 재현 (시나리오 1 결정론 보장)
    # -----------------------------------------------------------------------

    def test_클러스터A_재현_False(self):
        """2026-07-03 클러스터 A 멤버 제목 어느 것에도 실보유 회사명 없음 → False.

        실보유: 015760(한국전력)·316140(우리금융지주).
        멤버: '우리은행 삼성월렛머니', '한화솔루션', '은행권 FDI', '임신중절수술' 등.
        """
        titles = [
            "우리은행 삼성월렛머니, 가입자 250만명 돌파",
            "한화솔루션 석유화학 제품 가격 인하",
            "은행권 FDI 원스톱 서비스",
            "임신중절수술 관련 시장 동향",
            "코스피 신고가 돌파",
        ]
        cl = _cluster(titles)
        live = {"015760": "한국전력", "316140": "우리금융지주"}
        result = self._call(cl, live)
        assert result is False

    def test_클러스터B_재현_False(self):
        """2026-07-03 클러스터 B 멤버 제목에도 실보유 회사명 없음 → False."""
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
        cl = _cluster(titles)
        live = {"015760": "한국전력", "316140": "우리금융지주"}
        result = self._call(cl, live)
        assert result is False
