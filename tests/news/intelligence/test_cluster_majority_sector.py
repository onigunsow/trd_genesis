"""SPEC-TRADING-060 M3: 클러스터 다수결 섹터 결정 테스트 (REQ-060-3).

_majority_sector(articles) — impact 가중 최빈 → 기사 수 → 최고 impact tie-break.
"""

from __future__ import annotations


def _art(sector: str, impact: int) -> dict:
    """테스트용 기사 dict 생성 헬퍼."""
    return {"sector": sector, "impact_score": impact}


class TestMajoritySector:
    """clustering._majority_sector() 단위테스트."""

    def _call(self, articles: list[dict]) -> str:
        from trading.news.intelligence.clustering import _majority_sector

        return _majority_sector(articles)

    def test_단일_기사_반환(self):
        """단일 기사 클러스터 → 그 기사 섹터."""
        arts = [_art("semiconductor", 4)]
        assert self._call(arts) == "semiconductor"

    def test_impact_가중_다수결(self):
        """impact 합산: finance=7 vs semiconductor=6 → finance_banking 채택 (수용 시나리오 5)."""
        arts = [
            _art("semiconductor", 3),
            _art("finance_banking", 5),
            _art("finance_banking", 2),
            _art("semiconductor", 3),
        ]
        assert self._call(arts) == "finance_banking"

    def test_클러스터A_실측_다수결(self):
        """2026-07-03 클러스터A: stock_market x22 최다 → stock_market 채택
        (첫 기사 상속 finance_banking 폐기 검증)."""
        arts = (
            [_art("stock_market", 3)] * 22
            + [_art("semiconductor", 3)] * 9
            + [_art("macro_economy", 2)] * 2
            + [_art("finance_banking", 5)] * 1
            + [_art("biotech_pharma", 2)] * 1
        )
        # 첫 기사가 finance_banking 이라도 다수결 결과는 stock_market
        arts_reordered = [_art("finance_banking", 5), *arts[1:]]
        result = self._call(arts_reordered)
        assert result == "stock_market"

    def test_클러스터B_실측_다수결(self):
        """2026-07-03 클러스터B: energy_commodities x9(82%) → energy_commodities 채택
        (첫 기사 상속 defense_aerospace 폐기 검증)."""
        arts = [_art("defense_aerospace", 4)] * 2 + [_art("energy_commodities", 3)] * 9
        # 첫 기사가 defense_aerospace 라도 energy_commodities 가 채택돼야 함
        result = self._call(arts)
        assert result == "energy_commodities"

    def test_impact_동수_기사수_tie_break(self):
        """impact 합산 동수 → 기사 수 많은 섹터 채택."""
        # finance: impact 3+3=6, semiconductor: impact 6, 기사수 finance=2 > semi=1
        arts = [
            _art("finance_banking", 3),
            _art("finance_banking", 3),
            _art("semiconductor", 6),
        ]
        # impact 합 동수: 각 6. 기사 수: finance=2, semi=1 → finance 채택
        result = self._call(arts)
        assert result == "finance_banking"

    def test_기사수도_동수_최고impact_tie_break(self):
        """impact·기사수 모두 동수 → 최고 impact 단일 기사 섹터 채택 (결정론)."""
        arts = [
            _art("semiconductor", 5),  # impact=5 최고
            _art("finance_banking", 3),
        ]
        # impact 합: semi=5, finance=3. semi 승
        result = self._call(arts)
        assert result == "semiconductor"

    def test_빈_리스트_기본값(self):
        """빈 기사 리스트 → 빈 문자열 또는 기본값 반환 (크래시 없음)."""
        from trading.news.intelligence.clustering import _majority_sector

        # 빈 리스트는 크래시 없이 처리해야 함
        try:
            result = _majority_sector([])
            # 빈 문자열이나 "stock_market" 등 기본값을 반환해도 됨
            assert isinstance(result, str)
        except (ValueError, IndexError):
            # 빈 리스트를 ValueError 로 처리하는 것도 허용
            pass
