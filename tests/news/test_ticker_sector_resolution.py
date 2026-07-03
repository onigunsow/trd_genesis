"""SPEC-TRADING-060 M1/M4: 티커→섹터 단일 진실원천 해소 테스트.

news_sector_map (YAML) + ticker_metadata 기반 resolve_ticker_sector().
하드코딩 TICKER_SECTOR_MAP 폐기 검증.
"""

from __future__ import annotations

from unittest.mock import patch

# ---------------------------------------------------------------------------
# news_sector() 단위테스트 (data/sector_taxonomy.py 확장)
# ---------------------------------------------------------------------------


class TestNewsSector:
    """data.sector_taxonomy.news_sector() 함수 단위테스트."""

    def test_금융_maps_to_finance_banking(self):
        """업종명 '금융' → finance_banking (명확 매핑)."""
        from trading.data.sector_taxonomy import news_sector

        assert news_sector("금융") == "finance_banking"

    def test_전기가스_maps_to_energy_commodities(self):
        """업종명 '전기·가스' → energy_commodities (가운뎃점 포함 실 리터럴)."""
        from trading.data.sector_taxonomy import news_sector

        assert news_sector("전기·가스") == "energy_commodities"

    def test_제약_maps_to_biotech_pharma(self):
        """업종명 '제약' → biotech_pharma."""
        from trading.data.sector_taxonomy import news_sector

        assert news_sector("제약") == "biotech_pharma"

    def test_금속_maps_to_steel_materials(self):
        """업종명 '금속' → steel_materials."""
        from trading.data.sector_taxonomy import news_sector

        assert news_sector("금속") == "steel_materials"

    def test_IT서비스_maps_to_it_ai(self):
        """업종명 'IT 서비스' → it_ai (스페이스 포함 실 리터럴)."""
        from trading.data.sector_taxonomy import news_sector

        assert news_sector("IT 서비스") == "it_ai"

    def test_유통_maps_to_retail_consumer(self):
        """업종명 '유통' → retail_consumer."""
        from trading.data.sector_taxonomy import news_sector

        assert news_sector("유통") == "retail_consumer"

    def test_전기전자_maps_to_None(self):
        """업종명 '전기·전자' → None (D13 모호 → 미매핑)."""
        from trading.data.sector_taxonomy import news_sector

        assert news_sector("전기·전자") is None

    def test_화학_maps_to_None(self):
        """업종명 '화학' → None (모호)."""
        from trading.data.sector_taxonomy import news_sector

        assert news_sector("화학") is None

    def test_통신_maps_to_None(self):
        """업종명 '통신' → None (모호)."""
        from trading.data.sector_taxonomy import news_sector

        assert news_sector("통신") is None

    def test_운송장비부품_maps_to_None(self):
        """업종명 '운송장비·부품' → None (모호)."""
        from trading.data.sector_taxonomy import news_sector

        assert news_sector("운송장비·부품") is None

    def test_unknown_industry_maps_to_None(self):
        """존재하지 않는 업종명 → None (가짜 캐치올 금지)."""
        from trading.data.sector_taxonomy import news_sector

        assert news_sector("없는업종명xyz") is None

    def test_empty_string_maps_to_None(self):
        """빈 문자열 → None."""
        from trading.data.sector_taxonomy import news_sector

        assert news_sector("") is None

    def test_old_literal_전기가스업_maps_to_None(self):
        """옛 리터럴 '전기가스업'(가운뎃점 없음) → None (YAML 실 리터럴과 불일치)."""
        from trading.data.sector_taxonomy import news_sector

        # 실 ticker_metadata 리터럴은 '전기·가스'(가운뎃점·단형)
        assert news_sector("전기가스업") is None

    def test_old_literal_금융업_maps_to_None(self):
        """옛 리터럴 '금융업' → None (실 리터럴은 '금융')."""
        from trading.data.sector_taxonomy import news_sector

        assert news_sector("금융업") is None

    def test_none_input_maps_to_None(self):
        """None 입력 → None."""
        from trading.data.sector_taxonomy import news_sector

        assert news_sector(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# resolve_ticker_sector() 단위테스트 (news/ticker_sector.py)
# ---------------------------------------------------------------------------


def _make_db_row(sector_val: str | None, name: str = "") -> dict:
    """DB 조회 결과를 흉내내는 dict."""
    return {"sector": sector_val, "name": name}


class TestResolveTickerSector:
    """news.ticker_sector.resolve_ticker_sector() 단위테스트.

    DB 조회를 mock 으로 교체해 순수 로직 검증.
    """

    def _resolve_with_mock_row(self, ticker: str, row: dict | None) -> str | None:
        """mock DB row 로 resolve_ticker_sector 실행."""
        from trading.news import ticker_sector as ts_mod

        with patch.object(ts_mod, "_lookup_ticker_metadata", return_value=row):
            return ts_mod.resolve_ticker_sector(ticker)

    def test_015760_전기가스_resolves_energy_commodities(self):
        """015760 한국전력: 업종명 '전기·가스' → energy_commodities."""
        row = _make_db_row("전기·가스", "한국전력")
        result = self._resolve_with_mock_row("015760", row)
        assert result == "energy_commodities"

    def test_316140_금융_resolves_finance_banking(self):
        """316140 우리금융지주: 업종명 '금융' → finance_banking."""
        row = _make_db_row("금융", "우리금융지주")
        result = self._resolve_with_mock_row("316140", row)
        assert result == "finance_banking"

    def test_unmapped_sector_returns_None(self):
        """업종명 '전기·전자' → None (모호, 하드코딩 캐치올 금지)."""
        row = _make_db_row("전기·전자", "")
        result = self._resolve_with_mock_row("005930", row)
        assert result is None

    def test_unknown_ticker_returns_None(self):
        """ticker_metadata 미존재 티커 → None."""
        result = self._resolve_with_mock_row("999999", None)
        assert result is None

    def test_empty_sector_returns_None(self):
        """업종명 빈 문자열 → None."""
        row = _make_db_row("", "어떤기업")
        result = self._resolve_with_mock_row("000001", row)
        assert result is None

    def test_no_crash_on_exception(self):
        """DB 예외 발생 시 None 반환, 크래시 없음."""
        from trading.news import ticker_sector as ts_mod

        with patch.object(ts_mod, "_lookup_ticker_metadata", side_effect=RuntimeError("db error")):
            result = ts_mod.resolve_ticker_sector("999999")
        assert result is None


# ---------------------------------------------------------------------------
# TICKER_SECTOR_MAP 삭제 확인 (REQ-060-5)
# ---------------------------------------------------------------------------


class TestTickerSectorMapRemoved:
    """context_builder 와 relevance 에서 TICKER_SECTOR_MAP 이 제거됐는지 검증."""

    def test_TICKER_SECTOR_MAP_removed_from_context_builder(self):
        """context_builder 모듈에 TICKER_SECTOR_MAP 이 없어야 한다."""
        import trading.news.context_builder as cb

        assert not hasattr(cb, "TICKER_SECTOR_MAP"), (
            "TICKER_SECTOR_MAP 가 context_builder.py 에 아직 남아 있음 — REQ-060-1 미이행"
        )

    def test_get_sector_for_ticker_removed_from_context_builder(self):
        """get_sector_for_ticker 가 context_builder 에서 제거돼야 한다."""
        import trading.news.context_builder as cb

        assert not hasattr(cb, "get_sector_for_ticker"), (
            "get_sector_for_ticker 가 context_builder.py 에 아직 남아 있음"
        )
