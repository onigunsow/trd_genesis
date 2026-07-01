"""sector_taxonomy 모듈 TDD 테스트.

RED-first: sector_taxonomy.py 모듈이 없는 상태에서 먼저 작성.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import yaml

# ---------------------------------------------------------------------------
# YAML 파일 자체 파싱 테스트
# ---------------------------------------------------------------------------


class TestYamlFile:
    """실제 YAML 파일이 파싱 가능하고 KR 키를 포함하는지 확인."""

    def test_yaml_파싱_가능하고_KR_키_존재(self):
        """sector_taxonomy.yaml 이 파싱되고 'KR' 키를 포함한다."""
        yaml_path = (
            Path(__file__).parent.parent.parent
            / "src" / "trading" / "data" / "sector_taxonomy.yaml"
        )
        assert yaml_path.exists(), f"YAML 파일 미존재: {yaml_path}"

        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        assert data is not None
        assert "KR" in data, "'KR' 키가 YAML에 없음"


# ---------------------------------------------------------------------------
# sector_column
# ---------------------------------------------------------------------------


class TestSectorColumn:
    """sector_column() API 테스트."""

    def test_KR_기본_업종명_반환(self):
        """market='KR' 이면 '업종명' 반환."""
        from trading.data import sector_taxonomy

        assert sector_taxonomy.sector_column("KR") == "업종명"

    def test_기본_market_KR(self):
        """인수 생략 시 기본 active_market() = 'KR' 적용 → '업종명'."""
        from trading.data import sector_taxonomy

        with patch.object(sector_taxonomy, "active_market", return_value="KR"):
            assert sector_taxonomy.sector_column() == "업종명"

    def test_알수없는_market_None_반환(self):
        """설정에 없는 시장이면 None 반환."""
        from trading.data import sector_taxonomy

        assert sector_taxonomy.sector_column("ZZ") is None


# ---------------------------------------------------------------------------
# unknown_label
# ---------------------------------------------------------------------------


class TestUnknownLabel:
    """unknown_label() API 테스트."""

    def test_KR_미분류_반환(self):
        """market='KR' 이면 '미분류' 반환."""
        from trading.data import sector_taxonomy

        assert sector_taxonomy.unknown_label("KR") == "미분류"

    def test_알수없는_market_Unknown_폴백(self):
        """설정에 없는 시장이면 최종 폴백 'Unknown'."""
        from trading.data import sector_taxonomy

        assert sector_taxonomy.unknown_label("ZZ") == "Unknown"


# ---------------------------------------------------------------------------
# normalize_sector
# ---------------------------------------------------------------------------


class TestNormalizeSector:
    """normalize_sector() 정규화 규칙 테스트."""

    def test_금융_계열_5종_금융으로_정규화(self):
        """금융/기타금융/증권/보험/은행 → 모두 '금융'."""
        from trading.data import sector_taxonomy

        for raw in ["금융", "기타금융", "증권", "보험", "은행"]:
            result = sector_taxonomy.normalize_sector(raw, "KR")
            assert result == "금융", f"'{raw}' 정규화 실패: got '{result}'"

    def test_비금융_섹터_그대로_통과(self):
        """그룹에 속하지 않는 업종명은 원본 그대로 반환."""
        from trading.data import sector_taxonomy

        assert sector_taxonomy.normalize_sector("전기·전자", "KR") == "전기·전자"

    def test_빈문자열_unknown_label_반환(self):
        """빈 문자열 → KR unknown_label '미분류'."""
        from trading.data import sector_taxonomy

        assert sector_taxonomy.normalize_sector("", "KR") == "미분류"

    def test_None_unknown_label_반환(self):
        """None → KR unknown_label '미분류'."""
        from trading.data import sector_taxonomy

        assert sector_taxonomy.normalize_sector(None, "KR") == "미분류"

    def test_nan_문자열_unknown_label_반환(self):
        """'nan' 문자열 → KR unknown_label '미분류'."""
        from trading.data import sector_taxonomy

        assert sector_taxonomy.normalize_sector("nan", "KR") == "미분류"

    def test_알수없는_market_raw_폴백_예외없음(self):
        """알 수 없는 market 이어도 예외 없이 raw 또는 unknown 폴백 반환."""
        from trading.data import sector_taxonomy

        result = sector_taxonomy.normalize_sector("임의업종", "ZZ")
        # 예외 없이 반환만 하면 됨 (raw 또는 unknown 중 하나)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# active_market
# ---------------------------------------------------------------------------


class TestActiveMarket:
    """active_market() 환경변수 동작 테스트."""

    def test_기본값_KR(self):
        """TRADING_MARKET 미설정 시 기본값 'KR'."""
        from trading.data import sector_taxonomy

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TRADING_MARKET", None)
            assert sector_taxonomy.active_market() == "KR"

    def test_환경변수_US_반환(self):
        """TRADING_MARKET=US 설정 시 'US' 반환."""
        from trading.data import sector_taxonomy

        with patch.dict(os.environ, {"TRADING_MARKET": "US"}):
            assert sector_taxonomy.active_market() == "US"

    def test_소문자_환경변수_대문자로_정규화(self):
        """TRADING_MARKET=kr → 'KR' (upper 처리)."""
        from trading.data import sector_taxonomy

        with patch.dict(os.environ, {"TRADING_MARKET": "kr"}):
            assert sector_taxonomy.active_market() == "KR"
