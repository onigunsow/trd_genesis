"""SPEC-TRADING-054 follow-up: KIS ticker name+sector resolver TDD.

검증 범위:
- _fetch_ticker_name: KIS search-info 응답에서 hts_kor_isnm 추출
- _fetch_ticker_sector: KIS inquire-price 응답에서 bstp_kor_isnm 추출
- resolve_and_cache: 배치 upsert, 오류 격리
- lookup_names_from_db: DB 읽기 전용, KIS 호출 없음
- resolve_ticker_name: 우선순위 체인
- backfill: DB 종목 수집 + 배치 처리
- _upsert_held_ticker_names (fills.py): held 종목명 upkeep

DB 쓰기는 전부 모킹 — 실 Postgres 불필요.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# 헬퍼: KIS 응답 Fake
# ---------------------------------------------------------------------------

class _FakeResp:
    """KIS client.get() 의 가짜 응답."""
    def __init__(self, rt_cd: str = "0", raw: dict | None = None):
        self.rt_cd = rt_cd
        self.raw = raw or {}


def _search_info_resp(name: str) -> _FakeResp:
    """search-info 성공 응답 (hts_kor_isnm 포함)."""
    return _FakeResp(rt_cd="0", raw={"output1": {"hts_kor_isnm": name}})


def _inquire_price_resp(sector: str) -> _FakeResp:
    """inquire-price 성공 응답 (bstp_kor_isnm 포함)."""
    return _FakeResp(rt_cd="0", raw={"output": {"bstp_kor_isnm": sector}})


def _fail_resp() -> _FakeResp:
    """KIS 오류 응답 (rt_cd=1)."""
    return _FakeResp(rt_cd="1", raw={})


# ---------------------------------------------------------------------------
# 헬퍼: FakeConn / FakeCursor (DB 모킹)
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self._rows = rows or []
        self.executed: list[tuple] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))

    def fetchone(self) -> dict | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict]:
        return list(self._rows)

    def __enter__(self): return self
    def __exit__(self, *_): pass


class _FakeConn:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self._cursor = _FakeCursor(rows)

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None: pass
    def rollback(self) -> None: pass
    def close(self) -> None: pass

    def __enter__(self): return self
    def __exit__(self, *_): pass


def _patch_connection(rows: list[dict] | None = None):
    @contextmanager
    def _conn():
        yield _FakeConn(rows)
    return patch("trading.kis.kis_ticker_info.lookup_names_from_db.__module__", create=True), \
           patch("trading.db.session.connection", side_effect=_conn)


def _patch_ro_connection(rows: list[dict] | None = None):
    @contextmanager
    def _conn(autocommit: bool = False):
        yield _FakeConn(rows)
    return patch("trading.dashboard.db.ro_connection", side_effect=_conn)


# ---------------------------------------------------------------------------
# 1. _fetch_ticker_name
# ---------------------------------------------------------------------------

class TestFetchTickerName:
    def test_extracts_hts_kor_isnm(self):
        """search-info output1.hts_kor_isnm 을 종목명으로 반환한다."""
        from trading.kis.kis_ticker_info import _fetch_ticker_name

        mock_client = MagicMock()
        mock_client.get.return_value = _search_info_resp("신한지주")

        result = _fetch_ticker_name(mock_client, "055550")

        assert result == "신한지주"

    def test_returns_none_on_rt_cd_1(self):
        """KIS rt_cd=1 실패 시 None 반환."""
        from trading.kis.kis_ticker_info import _fetch_ticker_name

        mock_client = MagicMock()
        mock_client.get.return_value = _fail_resp()

        result = _fetch_ticker_name(mock_client, "055550")

        assert result is None

    def test_returns_none_on_exception(self):
        """네트워크 예외 시 None 반환, 배치 중단 없음."""
        from trading.kis.kis_ticker_info import _fetch_ticker_name

        mock_client = MagicMock()
        mock_client.get.side_effect = TimeoutError("KIS timeout")

        result = _fetch_ticker_name(mock_client, "055550")

        assert result is None

    def test_handles_output_as_list(self):
        """output1 이 list 일 때 첫 요소에서 추출한다."""
        from trading.kis.kis_ticker_info import _fetch_ticker_name

        mock_client = MagicMock()
        mock_client.get.return_value = _FakeResp(
            rt_cd="0",
            raw={"output1": [{"hts_kor_isnm": "삼성전자"}]},
        )

        result = _fetch_ticker_name(mock_client, "005930")

        assert result == "삼성전자"

    def test_returns_none_for_empty_name(self):
        """hts_kor_isnm 이 빈 문자열이면 None 반환."""
        from trading.kis.kis_ticker_info import _fetch_ticker_name

        mock_client = MagicMock()
        mock_client.get.return_value = _FakeResp(
            rt_cd="0",
            raw={"output1": {"hts_kor_isnm": ""}},
        )

        result = _fetch_ticker_name(mock_client, "999999")

        assert result is None


# ---------------------------------------------------------------------------
# 2. _fetch_ticker_sector
# ---------------------------------------------------------------------------

class TestFetchTickerSector:
    def test_extracts_bstp_kor_isnm(self):
        """inquire-price output.bstp_kor_isnm 을 업종으로 반환한다."""
        from trading.kis.kis_ticker_info import _fetch_ticker_sector

        mock_client = MagicMock()
        mock_client.get.return_value = _inquire_price_resp("금융")

        result = _fetch_ticker_sector(mock_client, "055550")

        assert result == "금융"

    def test_returns_none_on_failure(self):
        """KIS 오류 시 None 반환."""
        from trading.kis.kis_ticker_info import _fetch_ticker_sector

        mock_client = MagicMock()
        mock_client.get.return_value = _fail_resp()

        result = _fetch_ticker_sector(mock_client, "055550")

        assert result is None

    def test_returns_none_on_exception(self):
        """예외 시 None 반환 (격리)."""
        from trading.kis.kis_ticker_info import _fetch_ticker_sector

        mock_client = MagicMock()
        mock_client.get.side_effect = ConnectionError("timeout")

        result = _fetch_ticker_sector(mock_client, "055550")

        assert result is None


# ---------------------------------------------------------------------------
# 3. resolve_and_cache
# ---------------------------------------------------------------------------

class TestResolveAndCache:
    def test_upserts_name_and_sector(self):
        """KIS 조회 결과를 ticker_metadata 에 upsert 한다."""
        from trading.kis.kis_ticker_info import resolve_and_cache

        mock_client = MagicMock()
        # get 호출 순서: search-info(name) → inquire-price(sector) per ticker
        mock_client.get.side_effect = [
            _search_info_resp("신한지주"),  # 055550 name
            _inquire_price_resp("금융"),    # 055550 sector
        ]

        with patch("trading.kis.kis_ticker_info._upsert_ticker_info", return_value=1) as mock_upsert:
            result = resolve_and_cache(mock_client, ["055550"])

        assert result["attempted"] == 1
        assert result["upserted"] == 1
        assert result["failed"] == 0
        assert result["results"]["055550"]["name"] == "신한지주"
        assert result["results"]["055550"]["sector"] == "금융"
        mock_upsert.assert_called_once_with([("055550", "신한지주", "금융")])

    def test_partial_failure_does_not_stop_batch(self):
        """한 종목 KIS 오류가 나머지 처리를 막지 않는다."""
        from trading.kis.kis_ticker_info import resolve_and_cache

        mock_client = MagicMock()
        # 055550: name 성공, sector 성공
        # 005930: name 타임아웃, sector 성공
        mock_client.get.side_effect = [
            _search_info_resp("신한지주"),   # 055550 name
            _inquire_price_resp("금융"),     # 055550 sector
            _FakeResp(rt_cd="1"),            # 005930 name 실패
            _inquire_price_resp("반도체"),   # 005930 sector
        ]

        with patch("trading.kis.kis_ticker_info._upsert_ticker_info", return_value=2):
            result = resolve_and_cache(mock_client, ["055550", "005930"])

        assert result["attempted"] == 2
        assert result["failed"] == 0  # rt_cd=1 은 None 반환이지 예외가 아님
        assert result["results"]["055550"]["name"] == "신한지주"
        # name 빈 문자열, sector 는 채워짐
        assert result["results"]["005930"]["name"] == ""
        assert result["results"]["005930"]["sector"] == "반도체"

    def test_exception_during_fetch_counted_as_failed(self):
        """resolve_and_cache 루프에서 예외 발생 시 failed 카운트 증가."""
        from trading.kis.kis_ticker_info import resolve_and_cache
        import trading.kis.kis_ticker_info as module

        mock_client = MagicMock()
        # _fetch_ticker_name 내부 except 을 우회해 최외각 except 에 도달하도록 patch
        with patch.object(module, "_fetch_ticker_name", side_effect=RuntimeError("outer")), \
             patch.object(module, "_fetch_ticker_sector", return_value="반도체"), \
             patch.object(module, "_upsert_ticker_info", return_value=0):
            result = resolve_and_cache(mock_client, ["005930"])

        # 최외각 except 가 잡아 failed += 1
        assert result["failed"] == 1
        assert result["attempted"] == 1

    def test_empty_tickers_returns_zeros(self):
        """빈 목록 입력 시 즉시 반환."""
        from trading.kis.kis_ticker_info import resolve_and_cache

        mock_client = MagicMock()
        result = resolve_and_cache(mock_client, [])

        assert result == {"attempted": 0, "upserted": 0, "failed": 0, "results": {}}
        mock_client.get.assert_not_called()

    def test_deduplicates_tickers(self):
        """중복 종목코드는 한 번만 조회한다."""
        from trading.kis.kis_ticker_info import resolve_and_cache

        mock_client = MagicMock()
        mock_client.get.side_effect = [
            _search_info_resp("신한지주"),
            _inquire_price_resp("금융"),
        ]

        with patch("trading.kis.kis_ticker_info._upsert_ticker_info", return_value=1):
            result = resolve_and_cache(mock_client, ["055550", "055550", "055550"])

        assert result["attempted"] == 1
        assert mock_client.get.call_count == 2  # name + sector 각 1회씩


# ---------------------------------------------------------------------------
# 4. lookup_names_from_db
# ---------------------------------------------------------------------------

class TestLookupNamesFromDb:
    def test_returns_name_from_ticker_metadata(self):
        """ticker_metadata 에서 name 을 읽어 반환한다."""
        from trading.kis.kis_ticker_info import lookup_names_from_db

        rows = [{"ticker": "055550", "name": "신한지주"}]
        with _patch_ro_connection(rows):
            result = lookup_names_from_db(["055550"])

        assert result == {"055550": "신한지주"}

    def test_excludes_empty_name(self):
        """name 이 빈 문자열인 행은 결과에서 제외한다."""
        from trading.kis.kis_ticker_info import lookup_names_from_db

        rows = [
            {"ticker": "055550", "name": "신한지주"},
            {"ticker": "999999", "name": ""},
        ]
        with _patch_ro_connection(rows):
            result = lookup_names_from_db(["055550", "999999"])

        assert "999999" not in result
        assert result.get("055550") == "신한지주"

    def test_returns_empty_on_db_exception(self):
        """DB 예외 시 빈 dict 반환 (대시보드 요청 죽지 않음)."""
        from trading.kis.kis_ticker_info import lookup_names_from_db

        with patch("trading.dashboard.db.ro_connection", side_effect=RuntimeError("DB down")):
            result = lookup_names_from_db(["055550"])

        assert result == {}

    def test_empty_input_returns_empty(self):
        """빈 목록 입력 시 DB 조회 없이 빈 dict 반환."""
        from trading.kis.kis_ticker_info import lookup_names_from_db

        with patch("trading.dashboard.db.ro_connection") as mock_ro:
            result = lookup_names_from_db([])

        assert result == {}
        mock_ro.assert_not_called()

    def test_no_kis_or_pykrx_calls(self):
        """lookup_names_from_db 가 KIS/pykrx 를 호출하지 않음을 보장한다."""
        from trading.kis.kis_ticker_info import lookup_names_from_db
        import trading.kis.kis_ticker_info as module

        rows = [{"ticker": "055550", "name": "신한지주"}]
        with _patch_ro_connection(rows), \
             patch.object(module, "_fetch_ticker_name") as mock_name, \
             patch.object(module, "_fetch_ticker_sector") as mock_sector:
            lookup_names_from_db(["055550"])

        mock_name.assert_not_called()
        mock_sector.assert_not_called()


# ---------------------------------------------------------------------------
# 5. resolve_ticker_name
# ---------------------------------------------------------------------------

class TestResolveTickerName:
    def test_prefers_db_name(self):
        """DB 캐시 이름이 최우선."""
        from trading.kis.kis_ticker_info import resolve_ticker_name

        result = resolve_ticker_name("055550", db_names={"055550": "신한지주"})
        assert result == "신한지주"

    def test_falls_back_to_ticker_code(self):
        """DB 캐시 없으면 종목코드를 그대로 반환한다."""
        from trading.kis.kis_ticker_info import resolve_ticker_name

        result = resolve_ticker_name("999999", db_names={})
        assert result == "999999"

    def test_empty_db_name_falls_back(self):
        """DB 에 name 이 빈 문자열이면 코드 폴백."""
        from trading.kis.kis_ticker_info import resolve_ticker_name

        result = resolve_ticker_name("055550", db_names={"055550": ""})
        assert result == "055550"


# ---------------------------------------------------------------------------
# 6. backfill
# ---------------------------------------------------------------------------

class TestBackfill:
    def test_collects_tickers_and_resolves(self):
        """DB 에서 종목 수집 후 resolve_and_cache 를 호출한다."""
        from trading.kis.kis_ticker_info import backfill

        mock_client = MagicMock()
        db_tickers = ["055550", "005930"]

        with patch(
            "trading.kis.kis_ticker_info._collect_backfill_tickers",
            return_value=db_tickers,
        ), patch(
            "trading.kis.kis_ticker_info.resolve_and_cache",
            return_value={"attempted": 2, "upserted": 2, "failed": 0, "results": {}},
        ) as mock_resolve:
            result = backfill(client=mock_client)

        mock_resolve.assert_called_once_with(mock_client, db_tickers)
        assert result["attempted"] == 2
        assert result["upserted"] == 2

    def test_no_tickers_returns_zeros(self):
        """DB 에 종목이 없으면 KIS 조회 없이 반환."""
        from trading.kis.kis_ticker_info import backfill

        mock_client = MagicMock()

        with patch(
            "trading.kis.kis_ticker_info._collect_backfill_tickers",
            return_value=[],
        ):
            result = backfill(client=mock_client)

        assert result["attempted"] == 0


# ---------------------------------------------------------------------------
# 7. _upsert_held_ticker_names (fills.py reconcile upkeep)
# ---------------------------------------------------------------------------

class TestUpsertHeldTickerNames:
    def test_upserts_name_from_holdings(self):
        """holdings 의 name 필드를 ticker_metadata 에 upsert 한다."""
        from trading.kis.fills import _upsert_held_ticker_names

        holdings = [
            {"ticker": "055550", "name": "신한지주", "qty": 10},
            {"ticker": "005930", "name": "삼성전자", "qty": 5},
        ]

        executed: list[tuple] = []

        class _Cap(_FakeCursor):
            def execute(self, sql, params=None):
                executed.append((sql, params))

        conn = _FakeConn()
        conn._cursor = _Cap()

        with patch("trading.kis.fills.connection") as mock_conn_ctx:
            @contextmanager
            def _fake_conn():
                yield conn
            mock_conn_ctx.side_effect = _fake_conn

            _upsert_held_ticker_names(holdings)

        # ticker 2개에 대해 각각 execute 호출
        assert len(executed) == 2
        tickers_seen = [p[0] for _, p in executed]
        assert "055550" in tickers_seen
        assert "005930" in tickers_seen

    def test_skips_empty_ticker_or_name(self):
        """ticker 나 name 이 없는 항목은 건너뛴다."""
        from trading.kis.fills import _upsert_held_ticker_names

        holdings = [
            {"ticker": "", "name": "이름없음"},   # ticker 빈값 → 건너뜀
            {"ticker": "055550", "name": ""},     # name 빈값 → 건너뜀
            {"ticker": "005930", "name": "삼성전자"},  # 정상
        ]

        executed: list[tuple] = []

        class _Cap(_FakeCursor):
            def execute(self, sql, params=None):
                executed.append((sql, params))

        conn = _FakeConn()
        conn._cursor = _Cap()

        with patch("trading.kis.fills.connection") as mock_conn_ctx:
            @contextmanager
            def _fake_conn():
                yield conn
            mock_conn_ctx.side_effect = _fake_conn

            _upsert_held_ticker_names(holdings)

        assert len(executed) == 1
        assert executed[0][1][0] == "005930"

    def test_db_exception_is_isolated(self):
        """DB 예외가 발생해도 트레이딩 흐름이 중단되지 않는다."""
        from trading.kis.fills import _upsert_held_ticker_names

        holdings = [{"ticker": "055550", "name": "신한지주"}]

        with patch(
            "trading.kis.fills.connection",
            side_effect=RuntimeError("DB crash"),
        ):
            # 예외가 전파되지 않아야 함
            _upsert_held_ticker_names(holdings)


# ---------------------------------------------------------------------------
# 8. 대시보드 엔드포인트 ticker_name 보강 (통합)
# ---------------------------------------------------------------------------

class TestDashboardTickerNameEnrichment:
    """fetch_* 함수들이 ticker_name 필드를 포함하는지 검증.

    실제 DB/KIS 없이 lookup_names_from_db 만 모킹.
    """

    def _mock_lookup(self, names: dict[str, str]):
        return patch(
            "trading.dashboard.queries.lookup_names_from_db",
            return_value=names,
        )

    def test_fetch_recent_orders_includes_ticker_name(self):
        """fetch_recent_orders 응답에 ticker_name 필드가 포함된다."""
        from trading.dashboard.queries import fetch_recent_orders

        rows = [{"id": 1, "ticker": "055550", "side": "buy", "qty": 10,
                 "order_type": "market", "status": "filled", "fill_price": 50000,
                 "mode": "paper", "ts": None}]

        with patch("trading.dashboard.queries.ro_connection") as mock_ro, \
             self._mock_lookup({"055550": "신한지주"}):
            @contextmanager
            def _fake(autocommit=False):
                yield _FakeConn(rows)
            mock_ro.side_effect = _fake

            result = fetch_recent_orders(limit=10)

        assert len(result) == 1
        assert result[0]["ticker_name"] == "신한지주"

    def test_fetch_holdings_includes_ticker_name(self):
        """fetch_holdings 응답에 ticker_name 필드가 포함된다."""
        from trading.dashboard.queries import fetch_holdings

        rows = [{"ticker": "055550", "qty_net": 10,
                 "avg_fill_price": 50000, "total_cost": 500000}]

        with patch("trading.dashboard.queries.ro_connection") as mock_ro, \
             self._mock_lookup({"055550": "신한지주"}):
            @contextmanager
            def _fake(autocommit=False):
                yield _FakeConn(rows)
            mock_ro.side_effect = _fake

            result = fetch_holdings()

        assert len(result) == 1
        assert result[0]["ticker_name"] == "신한지주"

    def test_fetch_recent_decisions_includes_ticker_name(self):
        """fetch_recent_decisions 응답에 ticker_name 필드가 포함된다."""
        from trading.dashboard.queries import fetch_recent_decisions

        rows = [{"id": 1, "ts": None, "persona_name": "micro", "cycle_kind": "intraday",
                 "ticker": "055550", "side": "buy", "qty": 5, "confidence": 0.8,
                 "rationale": "test", "risk_verdict": None, "risk_rationale": None}]

        with patch("trading.dashboard.queries.ro_connection") as mock_ro, \
             self._mock_lookup({"055550": "신한지주"}):
            @contextmanager
            def _fake(autocommit=False):
                yield _FakeConn(rows)
            mock_ro.side_effect = _fake

            result = fetch_recent_decisions(limit=10)

        assert len(result) == 1
        assert result[0]["ticker_name"] == "신한지주"

    def test_fetch_roundtrips_includes_ticker_name(self):
        """fetch_roundtrips 응답에 ticker_name 필드가 포함된다."""
        from datetime import date
        from trading.dashboard.queries import fetch_roundtrips

        mock_rt = MagicMock()
        mock_rt.ticker = "055550"
        mock_rt.entry_date = date(2026, 1, 1)
        mock_rt.exit_date = date(2026, 1, 10)
        mock_rt.qty = 10
        mock_rt.entry_price = 50000
        mock_rt.exit_price = 52000
        mock_rt.net_pnl = 20000
        mock_rt.return_pct = 4.0
        mock_rt.entry_fee = 0
        mock_rt.exit_fee = 0
        mock_rt.fees = 0
        mock_rt.holding_days = 9
        mock_rt.confidence = None
        mock_rt.verdict = None
        mock_rt.persona = None
        mock_rt.is_win = True

        with patch(
            "trading.edge.roundtrips.compute_roundtrips",
            return_value=MagicMock(roundtrips=[mock_rt]),
        ), self._mock_lookup({"055550": "신한지주"}):
            result = fetch_roundtrips()

        assert len(result) == 1
        assert result[0]["ticker_name"] == "신한지주"
        assert result[0]["ticker"] == "055550"

    def test_fetch_portfolio_includes_ticker_name(self):
        """fetch_portfolio 응답 holdings 에 ticker_name 이 포함된다."""
        from datetime import date
        from trading.dashboard.queries import fetch_portfolio

        rows = [{
            "ticker": "055550",
            "qty": 10,
            "avg_cost": 50000.0,
            "eval_price": 52000.0,
            "eval_amount": 520000.0,
            "unrealized_pnl": 20000.0,
            "pnl_pct": 4.0,
            "trading_day": date(2026, 6, 20),
            "sector": "금융",
        }]

        with patch("trading.dashboard.queries.ro_connection") as mock_ro, \
             patch("trading.dashboard.queries._get_latest_equity_nav", return_value=1000000.0), \
             self._mock_lookup({"055550": "신한지주"}):
            @contextmanager
            def _fake(autocommit=False):
                yield _FakeConn(rows)
            mock_ro.side_effect = _fake

            result = fetch_portfolio()

        assert len(result["holdings"]) == 1
        assert result["holdings"][0]["ticker_name"] == "신한지주"

    def test_ticker_name_falls_back_to_code_when_not_in_db(self):
        """DB 캐시에 없는 종목은 코드 자체를 ticker_name 으로 반환한다."""
        from trading.dashboard.queries import fetch_holdings

        rows = [{"ticker": "999999", "qty_net": 5,
                 "avg_fill_price": 10000, "total_cost": 50000}]

        with patch("trading.dashboard.queries.ro_connection") as mock_ro, \
             self._mock_lookup({}):   # 빈 dict — DB 에 없음
            @contextmanager
            def _fake(autocommit=False):
                yield _FakeConn(rows)
            mock_ro.side_effect = _fake

            result = fetch_holdings()

        assert result[0]["ticker_name"] == "999999"  # 코드 폴백

    def test_no_pykrx_or_kis_in_dashboard_path(self):
        """대시보드 경로에서 pykrx / KIS 호출이 없음을 보장한다."""
        from trading.dashboard.queries import fetch_holdings
        import trading.kis.kis_ticker_info as info_module

        rows = [{"ticker": "055550", "qty_net": 10,
                 "avg_fill_price": 50000, "total_cost": 500000}]

        with patch("trading.dashboard.queries.ro_connection") as mock_ro, \
             self._mock_lookup({"055550": "신한지주"}), \
             patch.object(info_module, "_fetch_ticker_name") as mock_name, \
             patch.object(info_module, "_fetch_ticker_sector") as mock_sector:
            @contextmanager
            def _fake(autocommit=False):
                yield _FakeConn(rows)
            mock_ro.side_effect = _fake

            fetch_holdings()

        mock_name.assert_not_called()
        mock_sector.assert_not_called()
