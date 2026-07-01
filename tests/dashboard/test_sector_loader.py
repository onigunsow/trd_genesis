"""CHANGE C·D — sector_loader TDD 테스트.

C: _fetch_sector_map 배치 fetch(N 종목에도 pykrx 호출 최대 2회)
D: load_sector_metadata 기본 타겟에 get_data_universe() 포함
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pandas as pd


@contextmanager
def _noop_cm(*args, **kwargs):
    """_quiet_pykrx 대용 no-op 컨텍스트 매니저."""
    yield


# ---------------------------------------------------------------------------
# 헬퍼: 소규모 DataFrame (get_market_sector_classifications 반환 형식 모사)
# ---------------------------------------------------------------------------

def _make_sector_df(data: dict[str, str]) -> pd.DataFrame:
    """ticker → 업종명 딕셔너리로 pykrx 분류표 형식 DataFrame 생성.

    실제 pykrx get_market_sector_classifications 는 인덱스='종목코드',
    섹터 컬럼='업종명'(한글). 그 외 종목명/종가/시가총액 컬럼도 있으나
    _fetch_sector_map 은 업종명만 사용한다(2026-07-01 라이브 실측 반영).
    """
    df = pd.DataFrame({"업종명": list(data.values())}, index=list(data.keys()))
    df.index.name = "종목코드"
    return df


# ---------------------------------------------------------------------------
# CHANGE C: _fetch_sector_map — 배치 fetch 테스트
# ---------------------------------------------------------------------------


class TestFetchSectorMapBatch:
    """_fetch_sector_map: N 종목에도 KOSPI/KOSDAQ 각 최대 1회 호출."""

    def test_5종목에_pykrx_최대_2회(self):
        """종목 5개 → get_market_sector_classifications 총 2회 이하."""
        from trading.dashboard.sector_loader import _fetch_sector_map

        kospi_df = _make_sector_df({
            "005930": "전기·전자",
            "000660": "전기·전자",
            "035420": "IT 서비스",
        })
        kosdaq_df = _make_sector_df({
            "247540": "제약",
        })

        tickers = ["005930", "000660", "035420", "247540", "999999"]

        mock_stock = MagicMock()
        mock_stock.get_market_sector_classifications.side_effect = [kospi_df, kosdaq_df]

        with (
            patch("trading.dashboard.sector_loader._PYKRX_AVAILABLE", True),
            patch("trading.dashboard.sector_loader._pykrx_stock", mock_stock),
            patch("trading.dashboard.sector_loader._quiet_pykrx", _noop_cm),
            patch(
                "trading.dashboard.sector_loader._get_shared_breaker",
                return_value=MagicMock(check_or_raise=MagicMock(), record_success=MagicMock()),
            ),
        ):
            _fetch_sector_map(tickers)

        # KOSPI 1회 + KOSDAQ 1회 = 총 2회
        assert mock_stock.get_market_sector_classifications.call_count == 2

    def test_KOSPI_종목_섹터_올바르게_반환(self):
        """KOSPI DataFrame에서 조회한 섹터·산업군이 정확히 반환된다."""
        from trading.dashboard.sector_loader import _fetch_sector_map

        kospi_df = _make_sector_df({
            "005930": "전기·전자",
        })
        kosdaq_df = pd.DataFrame()  # 빈 KOSDAQ

        mock_stock = MagicMock()
        mock_stock.get_market_sector_classifications.side_effect = [kospi_df, kosdaq_df]

        with (
            patch("trading.dashboard.sector_loader._PYKRX_AVAILABLE", True),
            patch("trading.dashboard.sector_loader._pykrx_stock", mock_stock),
            patch("trading.dashboard.sector_loader._quiet_pykrx", _noop_cm),
            patch(
                "trading.dashboard.sector_loader._get_shared_breaker",
                return_value=MagicMock(check_or_raise=MagicMock(), record_success=MagicMock()),
            ),
        ):
            result = _fetch_sector_map(["005930"])

        assert "005930" in result
        # 비금융 업종명은 정규화 후에도 동일 → (정규화섹터, 원본업종명)
        assert result["005930"] == ("전기·전자", "전기·전자")

    def test_KOSDAQ_종목_섹터_반환(self):
        """KOSPI에 없는 종목은 KOSDAQ에서 조회한다."""
        from trading.dashboard.sector_loader import _fetch_sector_map

        kospi_df = pd.DataFrame()
        kosdaq_df = _make_sector_df({
            "247540": "제약",
        })

        mock_stock = MagicMock()
        mock_stock.get_market_sector_classifications.side_effect = [kospi_df, kosdaq_df]

        with (
            patch("trading.dashboard.sector_loader._PYKRX_AVAILABLE", True),
            patch("trading.dashboard.sector_loader._pykrx_stock", mock_stock),
            patch("trading.dashboard.sector_loader._quiet_pykrx", _noop_cm),
            patch(
                "trading.dashboard.sector_loader._get_shared_breaker",
                return_value=MagicMock(check_or_raise=MagicMock(), record_success=MagicMock()),
            ),
        ):
            result = _fetch_sector_map(["247540"])

        assert "247540" in result
        assert result["247540"][0] == "제약"

    def test_서킷브레이커_OPEN_시_pykrx_미호출(self):
        """서킷이 OPEN이면 pykrx 호출 없이 {} 반환."""
        from trading.dashboard.sector_loader import _fetch_sector_map
        from trading.data.krx_circuit_breaker import KrxCircuitOpen

        mock_breaker = MagicMock()
        mock_breaker.check_or_raise.side_effect = KrxCircuitOpen("circuit open")
        mock_stock = MagicMock()

        with (
            patch("trading.dashboard.sector_loader._PYKRX_AVAILABLE", True),
            patch("trading.dashboard.sector_loader._pykrx_stock", mock_stock),
            patch(
                "trading.dashboard.sector_loader._get_shared_breaker",
                return_value=mock_breaker,
            ),
        ):
            result = _fetch_sector_map(["005930"])

        assert result == {}
        mock_stock.get_market_sector_classifications.assert_not_called()

    def test_pykrx_미설치_빈_딕트(self):
        """pykrx 미설치 환경에서 {} 반환 (graceful skip)."""
        from trading.dashboard.sector_loader import _fetch_sector_map

        with patch("trading.dashboard.sector_loader._PYKRX_AVAILABLE", False):
            result = _fetch_sector_map(["005930"])

        assert result == {}

    def test_fetch_실패시_record_failure_호출(self):
        """pykrx 호출 실패 시 record_failure() 가 호출된다."""
        from trading.dashboard.sector_loader import _fetch_sector_map

        mock_breaker = MagicMock()
        mock_stock = MagicMock()
        mock_stock.get_market_sector_classifications.side_effect = Exception("KRX down")

        with (
            patch("trading.dashboard.sector_loader._PYKRX_AVAILABLE", True),
            patch("trading.dashboard.sector_loader._pykrx_stock", mock_stock),
            patch("trading.dashboard.sector_loader._quiet_pykrx", _noop_cm),
            patch(
                "trading.dashboard.sector_loader._get_shared_breaker",
                return_value=mock_breaker,
            ),
        ):
            result = _fetch_sector_map(["005930"])

        mock_breaker.record_failure.assert_called_once()
        assert result == {}

    def test_fetch_성공시_record_success_호출(self):
        """pykrx 호출 성공 시 record_success() 가 호출된다."""
        from trading.dashboard.sector_loader import _fetch_sector_map

        kospi_df = _make_sector_df({"005930": "전기·전자"})
        kosdaq_df = pd.DataFrame()

        mock_breaker = MagicMock()
        mock_stock = MagicMock()
        mock_stock.get_market_sector_classifications.side_effect = [kospi_df, kosdaq_df]

        with (
            patch("trading.dashboard.sector_loader._PYKRX_AVAILABLE", True),
            patch("trading.dashboard.sector_loader._pykrx_stock", mock_stock),
            patch("trading.dashboard.sector_loader._quiet_pykrx", _noop_cm),
            patch(
                "trading.dashboard.sector_loader._get_shared_breaker",
                return_value=mock_breaker,
            ),
        ):
            _fetch_sector_map(["005930"])

        mock_breaker.record_success.assert_called_once()

    def test_금융_계열_업종명_금융으로_정규화(self):
        """금융/기타금융/증권/보험/은행 업종명은 단일 '금융' 섹터로 정규화된다.

        pykrx 는 금융 계열을 5개로 분산(2026-07-01 실측)하는데, 그대로 두면
        섹터 집중 가드가 granular 라벨별 cap 만 적용해 '금융 쏠림'을 놓친다.
        """
        from trading.dashboard.sector_loader import _fetch_sector_map

        kospi_df = _make_sector_df({
            "055550": "금융",      # 신한지주
            "006840": "기타금융",  # AK홀딩스
            "039490": "증권",      # 키움증권
            "005830": "보험",      # DB손해보험
            "024110": "은행",      # 기업은행
            "005930": "전기·전자",  # 삼성전자 (비금융 대조군)
        })
        kosdaq_df = pd.DataFrame()

        mock_stock = MagicMock()
        mock_stock.get_market_sector_classifications.side_effect = [kospi_df, kosdaq_df]

        with (
            patch("trading.dashboard.sector_loader._PYKRX_AVAILABLE", True),
            patch("trading.dashboard.sector_loader._pykrx_stock", mock_stock),
            patch("trading.dashboard.sector_loader._quiet_pykrx", _noop_cm),
            patch(
                "trading.dashboard.sector_loader._get_shared_breaker",
                return_value=MagicMock(check_or_raise=MagicMock(), record_success=MagicMock()),
            ),
        ):
            result = _fetch_sector_map(
                ["055550", "006840", "039490", "005830", "024110", "005930"]
            )

        # 금융 계열 5종은 모두 '금융'으로 정규화 (원본 업종명은 industry 로 보존)
        for t in ["055550", "006840", "039490", "005830", "024110"]:
            assert result[t][0] == "금융", f"{t} 정규화 실패: {result[t]}"
        # 비금융은 그대로
        assert result["005930"][0] == "전기·전자"


# ---------------------------------------------------------------------------
# CHANGE D: load_sector_metadata 기본 타겟 = universe + db 종목 합집합
# ---------------------------------------------------------------------------


class TestLoadSectorMetadataDefaultTarget:
    """load_sector_metadata(tickers=None) 기본 타겟에 get_data_universe() 포함."""

    def test_기본_타겟에_universe_포함(self):
        """tickers=None 이면 get_data_universe()·_tickers_from_db() 합집합을 대상으로 한다."""
        from trading.dashboard.sector_loader import load_sector_metadata

        universe_tickers = ["005930", "000660", "035420"]
        db_tickers = ["055550", "035720"]

        with (
            patch(
                "trading.dashboard.sector_loader._get_universe",
                return_value=universe_tickers,
            ) as mock_universe,
            patch(
                "trading.dashboard.sector_loader._tickers_from_db",
                return_value=db_tickers,
            ) as mock_db,
            patch(
                "trading.dashboard.sector_loader._fetch_sector_map",
                return_value={},
            ) as mock_fetch,
            patch("trading.dashboard.sector_loader._upsert_ticker_metadata", return_value=0),
        ):
            load_sector_metadata()

        # universe와 db 양쪽 호출됨
        mock_universe.assert_called_once()
        mock_db.assert_called_once()

        # _fetch_sector_map 에 전달된 tickers가 합집합을 포함
        called_tickers = set(mock_fetch.call_args[0][0])
        for t in universe_tickers + db_tickers:
            assert t in called_tickers, f"{t} 가 fetch 대상에 없음"

    def test_universe_실패해도_db_종목으로_계속(self):
        """get_data_universe() 예외 발생 시 _tickers_from_db() 결과만으로 계속한다."""
        from trading.dashboard.sector_loader import load_sector_metadata

        db_tickers = ["055550"]

        with (
            patch(
                "trading.dashboard.sector_loader._get_universe",
                side_effect=Exception("universe error"),
            ),
            patch(
                "trading.dashboard.sector_loader._tickers_from_db",
                return_value=db_tickers,
            ),
            patch(
                "trading.dashboard.sector_loader._fetch_sector_map",
                return_value={},
            ) as mock_fetch,
            patch("trading.dashboard.sector_loader._upsert_ticker_metadata", return_value=0),
        ):
            load_sector_metadata()  # 예외 발생하지 않아야 함

        called_tickers = set(mock_fetch.call_args[0][0])
        assert "055550" in called_tickers

    def test_db_실패해도_universe_종목으로_계속(self):
        """_tickers_from_db() 예외 발생 시 universe 결과만으로 계속한다."""
        from trading.dashboard.sector_loader import load_sector_metadata

        universe_tickers = ["005930"]

        with (
            patch(
                "trading.dashboard.sector_loader._get_universe",
                return_value=universe_tickers,
            ),
            patch(
                "trading.dashboard.sector_loader._tickers_from_db",
                side_effect=Exception("db error"),
            ),
            patch(
                "trading.dashboard.sector_loader._fetch_sector_map",
                return_value={},
            ) as mock_fetch,
            patch("trading.dashboard.sector_loader._upsert_ticker_metadata", return_value=0),
        ):
            load_sector_metadata()  # 예외 발생하지 않아야 함

        called_tickers = set(mock_fetch.call_args[0][0])
        assert "005930" in called_tickers

    def test_명시적_tickers_인수_그대로_사용(self):
        """tickers 를 명시하면 get_data_universe·_tickers_from_db 호출 없이 그대로 사용."""
        from trading.dashboard.sector_loader import load_sector_metadata

        with (
            patch("trading.dashboard.sector_loader._get_universe") as mock_universe,
            patch("trading.dashboard.sector_loader._tickers_from_db") as mock_db,
            patch(
                "trading.dashboard.sector_loader._fetch_sector_map",
                return_value={},
            ) as mock_fetch,
            patch("trading.dashboard.sector_loader._upsert_ticker_metadata", return_value=0),
        ):
            load_sector_metadata(tickers=["005930", "000660"])

        mock_universe.assert_not_called()
        mock_db.assert_not_called()
        assert list(mock_fetch.call_args[0][0]) == ["005930", "000660"]

    def test_중복_제거_후_fetch(self):
        """universe와 db의 중복 종목은 한 번만 fetch에 전달된다."""
        from trading.dashboard.sector_loader import load_sector_metadata

        with (
            patch(
                "trading.dashboard.sector_loader._get_universe",
                return_value=["005930", "000660"],
            ),
            patch(
                "trading.dashboard.sector_loader._tickers_from_db",
                return_value=["005930", "055550"],  # 005930 중복
            ),
            patch(
                "trading.dashboard.sector_loader._fetch_sector_map",
                return_value={},
            ) as mock_fetch,
            patch("trading.dashboard.sector_loader._upsert_ticker_metadata", return_value=0),
        ):
            load_sector_metadata()

        called_tickers = mock_fetch.call_args[0][0]
        # 중복 없어야 함
        assert len(called_tickers) == len(set(called_tickers))
        assert called_tickers.count("005930") == 1
