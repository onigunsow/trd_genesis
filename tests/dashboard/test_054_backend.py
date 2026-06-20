"""SPEC-TRADING-054 M1/M1.5 백엔드 테스트.

TDD RED-GREEN: 마이그레이션 스키마, reconcile writer, 신규 엔드포인트,
CSV 행 동수, sortino 노출, 섹터 미분류 폴백을 검증한다.
DB 호출은 전부 모킹 — 실 Postgres 불필요.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from io import StringIO
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# 헬퍼: FakeConnection / FakeCursor (conftest 패턴 재사용)
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []
        self.executed: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


class _FakeConn:
    def __init__(self, cursor: _FakeCursor | None = None) -> None:
        self._cursor = cursor or _FakeCursor()
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def _ro_patch(rows: list[dict[str, Any]]):
    """ro_connection 을 fake rows 로 패치."""
    @contextmanager
    def _conn(autocommit: bool = False):
        yield _FakeConn(_FakeCursor(rows))
    return patch("trading.dashboard.queries.ro_connection", side_effect=_conn)


def _multi_ro_patch(rows_list: list[list[dict[str, Any]]]):
    """ro_connection 을 호출 순서별 rows 로 패치."""
    idx = [0]
    @contextmanager
    def _conn(autocommit: bool = False):
        i = min(idx[0], len(rows_list) - 1)
        idx[0] += 1
        yield _FakeConn(_FakeCursor(rows_list[i]))
    return patch("trading.dashboard.queries.ro_connection", side_effect=_conn)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from trading.dashboard.app import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# AC-4: sortino 노출 (REQ-054-A4)
# ---------------------------------------------------------------------------

class TestSortino:
    def test_scorecard_includes_sortino(self, client):
        """GET /api/scorecard 응답에 sortino 필드가 존재해야 한다."""
        mock_sc = MagicMock()
        mock_sc.verdict = "NO-GO"
        mock_sc.grade = "F"
        mock_sc.reasons = ["insufficient data"]

        mock_analytics = MagicMock()
        mock_analytics.n_closed = 3
        mock_analytics.win_rate = 0.33
        mock_analytics.expectancy_adj = -5000.0
        mock_analytics.profit_factor_adj = 0.5
        mock_analytics.sortino = 1.23  # edge 에서 이미 계산된 값

        mock_bm = MagicMock()
        mock_bm.available = False
        mock_bm.alpha_pct = None

        mock_tw = MagicMock()
        mock_tw.available = False
        mock_tw.cagr = None
        mock_tw.mdd = None
        mock_tw.sharpe = None

        with (
            patch("trading.edge.roundtrips.compute_roundtrips", return_value=MagicMock(roundtrips=[])),
            patch("trading.edge.analytics.from_result", return_value=mock_analytics),
            patch("trading.edge.benchmark.compute", return_value=mock_bm),
            patch("trading.edge.scorecard.decide", return_value=mock_sc),
            patch("trading.edge.report.load_equity_snapshots", return_value=[]),
            patch("trading.edge.analytics.time_weighted_metrics", return_value=mock_tw),
        ):
            resp = client.get("/api/scorecard")

        assert resp.status_code == 200
        data = resp.json()
        assert "sortino" in data, "sortino 필드 누락"
        assert data["sortino"] == pytest.approx(1.23)

    def test_sortino_comes_from_edge_not_recomputed(self):
        """fetch_scorecard_with_sortino 가 analytics.sortino 를 그대로 반환함을 검증."""
        from trading.dashboard.queries import fetch_scorecard_with_sortino

        mock_analytics = MagicMock()
        mock_analytics.sortino = 2.55
        mock_analytics.n_closed = 5
        mock_analytics.win_rate = 0.6
        mock_analytics.expectancy_adj = 10000.0
        mock_analytics.profit_factor_adj = 1.5

        mock_bm = MagicMock()
        mock_bm.available = False

        mock_sc = MagicMock()
        mock_sc.verdict = "GO"
        mock_sc.grade = "B"
        mock_sc.reasons = []

        mock_tw = MagicMock()
        mock_tw.available = False

        with (
            patch("trading.edge.roundtrips.compute_roundtrips", return_value=MagicMock(roundtrips=[])),
            patch("trading.edge.analytics.from_result", return_value=mock_analytics),
            patch("trading.edge.benchmark.compute", return_value=mock_bm),
            patch("trading.edge.scorecard.decide", return_value=mock_sc),
            patch("trading.edge.report.load_equity_snapshots", return_value=[]),
            patch("trading.edge.analytics.time_weighted_metrics", return_value=mock_tw),
        ):
            result = fetch_scorecard_with_sortino()

        assert result["sortino"] == pytest.approx(2.55)


# ---------------------------------------------------------------------------
# AC-1: /api/roundtrips (REQ-054-A1, A6)
# ---------------------------------------------------------------------------

class TestRoundtripsEndpoint:
    def _make_rt(self, ticker="005930", persona="micro"):
        """테스트용 RoundTrip 모킹 객체."""
        rt = MagicMock()
        rt.ticker = ticker
        rt.entry_date = date(2026, 5, 1)
        rt.exit_date = date(2026, 5, 10)
        rt.qty = 10
        rt.entry_price = 70000.0
        rt.exit_price = 77000.0
        rt.net_pnl = 69890.0
        rt.return_pct = 9.97
        rt.entry_fee = 100.0
        rt.exit_fee = 110.0
        rt.fees = 210.0
        rt.holding_days = 9
        rt.confidence = 0.8
        rt.verdict = "APPROVE"
        rt.persona = persona
        rt.is_win = True
        return rt

    def test_returns_roundtrip_fields(self, client):
        """응답에 필수 16개 필드가 모두 있어야 한다."""
        mock_result = MagicMock()
        mock_result.roundtrips = [self._make_rt()]

        with patch("trading.edge.roundtrips.compute_roundtrips", return_value=mock_result):
            resp = client.get("/api/roundtrips")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        row = data[0]
        required = {
            "ticker", "entry_date", "exit_date", "qty",
            "entry_price", "exit_price", "net_pnl", "return_pct",
            "entry_fee", "exit_fee", "fees", "holding_days",
            "confidence", "verdict", "persona", "is_win",
        }
        missing = required - set(row.keys())
        assert not missing, f"누락 필드: {missing}"

    def test_persona_field_present(self, client):
        """persona 필드가 채워져 있어야 한다 (ADR-001)."""
        mock_result = MagicMock()
        mock_result.roundtrips = [self._make_rt(persona="micro")]

        with patch("trading.edge.roundtrips.compute_roundtrips", return_value=mock_result):
            resp = client.get("/api/roundtrips")

        assert resp.json()[0]["persona"] == "micro"

    def test_calls_compute_roundtrips(self, client):
        """핸들러가 edge.roundtrips.compute_roundtrips 를 호출해야 한다 (spy)."""
        mock_result = MagicMock()
        mock_result.roundtrips = []

        with patch(
            "trading.edge.roundtrips.compute_roundtrips",
            return_value=mock_result,
        ) as spy:
            client.get("/api/roundtrips?days=30")

        spy.assert_called_once_with(30)

    def test_empty_roundtrips_returns_empty_list(self, client):
        mock_result = MagicMock()
        mock_result.roundtrips = []
        with patch("trading.edge.roundtrips.compute_roundtrips", return_value=mock_result):
            resp = client.get("/api/roundtrips")
        assert resp.json() == []


# ---------------------------------------------------------------------------
# AC-2b: reconcile writer (REQ-054-A9, ADR-004)
# ---------------------------------------------------------------------------

class TestReconcileWriter:
    """_upsert_eval_snapshot 이 결정 로직을 건드리지 않고 저장만 함을 검증."""

    def _sample_holdings(self):
        return [
            {
                "ticker": "005930",
                "qty": 10,
                "avg_cost": 70000,
                "current_price": 77000,
                "eval_amount": 770000,
                "pnl_amount": 69890,
                "pnl_pct": 9.97,
            },
            {
                "ticker": "000660",
                "qty": 5,
                "avg_cost": 100000,
                "current_price": 105000,
                "eval_amount": 525000,
                "pnl_amount": 24750,
                "pnl_pct": 4.95,
            },
        ]

    def test_upsert_called_per_ticker(self):
        """holdings 각 행마다 execute 가 호출되어야 한다."""
        from trading.kis.fills import _upsert_eval_snapshot

        executed = []

        @contextmanager
        def _fake_conn(autocommit=False):
            cur = _FakeCursor()
            # execute 를 추적
            original_execute = cur.execute
            def track(sql, params=None):
                executed.append(params)
                original_execute(sql, params)
            cur.execute = track
            yield _FakeConn(cur)

        holdings = self._sample_holdings()
        with patch("trading.kis.fills.connection", side_effect=_fake_conn):
            _upsert_eval_snapshot(holdings)

        assert len(executed) == 2, f"2 종목인데 {len(executed)} execute 호출"

    def test_upsert_failure_does_not_raise(self):
        """_upsert_eval_snapshot 실패 시 예외 전파 없음 — 트레이딩 흐름 불영향."""
        from trading.kis.fills import _upsert_eval_snapshot

        @contextmanager
        def _failing_conn(autocommit=False):
            raise RuntimeError("DB 연결 실패 시뮬레이션")
            yield  # type: ignore[misc]

        with patch("trading.kis.fills.connection", side_effect=_failing_conn):
            # 예외가 올라오면 안 된다
            _upsert_eval_snapshot(self._sample_holdings())

    def test_upsert_idempotent_on_conflict(self):
        """ON CONFLICT DO UPDATE 구문이 SQL 에 포함되어야 한다."""
        from trading.kis.fills import _upsert_eval_snapshot

        sqls_executed = []

        @contextmanager
        def _fake_conn(autocommit=False):
            cur = _FakeCursor()
            original = cur.execute
            def track(sql, params=None):
                sqls_executed.append(sql)
                original(sql, params)
            cur.execute = track
            yield _FakeConn(cur)

        with patch("trading.kis.fills.connection", side_effect=_fake_conn):
            _upsert_eval_snapshot(self._sample_holdings())

        assert sqls_executed, "execute 호출 없음"
        assert "ON CONFLICT" in sqls_executed[0].upper()

    def test_dry_run_skips_upsert(self):
        """dry_run=True 일 때 _upsert_eval_snapshot 이 호출되지 않아야 한다."""
        from trading.kis.fills import _upsert_eval_snapshot

        upsert_called = []

        def _mock_upsert(holdings):
            upsert_called.append(holdings)

        holdings = self._sample_holdings()
        mock_bal = {"holdings": holdings}
        mock_client = MagicMock()

        with (
            patch("trading.kis.fills.balance", return_value=mock_bal),
            patch("trading.kis.fills._transition_orders_fifo", return_value=0),
            patch("trading.kis.fills._mirror_positions", return_value=len(holdings)),
            patch("trading.kis.fills.connection") as mock_conn_ctx,
            patch("trading.kis.fills._upsert_eval_snapshot", side_effect=_mock_upsert),
        ):
            @contextmanager
            def _fake_conn(autocommit=False):
                yield _FakeConn()
            mock_conn_ctx.side_effect = _fake_conn

            from trading.kis.fills import reconcile_from_balance
            reconcile_from_balance(mock_client, dry_run=True)

        assert not upsert_called, "dry_run=True 인데 upsert 가 호출됨"

    def test_reconcile_decision_output_unchanged(self):
        """reconcile_from_balance 의 summary 반환값이 writer 추가 전과 동일해야 한다.

        AC-2b: 결정/사이징/리스크 출력 불변.
        """
        from trading.kis.fills import reconcile_from_balance

        holdings = self._sample_holdings()
        mock_bal = {"holdings": holdings}
        mock_client = MagicMock()

        @contextmanager
        def _fake_conn(autocommit=False):
            yield _FakeConn()

        with (
            patch("trading.kis.fills.balance", return_value=mock_bal),
            patch("trading.kis.fills._transition_orders_fifo", return_value=1),
            patch("trading.kis.fills._mirror_positions", return_value=2),
            patch("trading.kis.fills.connection", side_effect=_fake_conn),
            patch("trading.kis.fills._upsert_eval_snapshot"),  # side-effect 제거
        ):
            result = reconcile_from_balance(mock_client, dry_run=False)

        # writer 추가 전과 동일한 summary 키가 있어야 한다
        assert result["queried"] == 2
        assert result["transitioned"] == 1
        assert result["positions_synced"] == 2
        assert result["errors"] == 0
        assert result["dry_run"] is False


# ---------------------------------------------------------------------------
# AC-2 / AC-17: /api/portfolio + 섹터 미분류 폴백 (REQ-054-A2, G1)
# ---------------------------------------------------------------------------

class TestPortfolioEndpoint:
    def _snapshot_rows(self):
        return [
            {
                "ticker": "005930",
                "qty": 10,
                "avg_cost": 70000.0,
                "eval_price": 77000.0,
                "eval_amount": 770000.0,
                "unrealized_pnl": 69890.0,
                "pnl_pct": 9.97,
                "trading_day": date(2026, 6, 20),
                "sector": "전기전자",
            },
            {
                "ticker": "000660",
                "qty": 5,
                "avg_cost": 100000.0,
                "eval_price": 105000.0,
                "eval_amount": 525000.0,
                "unrealized_pnl": 24750.0,
                "pnl_pct": 4.95,
                "trading_day": date(2026, 6, 20),
                "sector": None,  # 미분류 폴백 테스트
            },
        ]

    def _equity_rows(self):
        return [{"total_assets": 2000000.0}]

    def test_portfolio_has_required_fields(self, client):
        """응답에 holdings, nav, cash_ratio, herfindahl, top3_pct, sector_breakdown 포함."""
        with (
            _multi_ro_patch([self._snapshot_rows(), self._equity_rows()]),
        ):
            resp = client.get("/api/portfolio")

        assert resp.status_code == 200
        data = resp.json()
        assert "holdings" in data
        assert "nav" in data
        assert "cash_ratio" in data
        assert "herfindahl" in data
        assert "top3_pct" in data
        assert "sector_breakdown" in data

    def test_unclassified_sector_fallback(self, client):
        """ticker_metadata 매핑 없는 종목은 '미분류' 로 집계 (REQ-054-G1)."""
        with _multi_ro_patch([self._snapshot_rows(), self._equity_rows()]):
            resp = client.get("/api/portfolio")

        holdings = resp.json()["holdings"]
        # 000660 은 sector=None → "미분류"
        ticker_000660 = next(h for h in holdings if h["ticker"] == "000660")
        assert ticker_000660["sector"] == "미분류"

    def test_sector_breakdown_includes_unclassified(self, client):
        """sector_breakdown 에 미분류 섹터가 포함되어야 한다 (조용한 누락 금지)."""
        with _multi_ro_patch([self._snapshot_rows(), self._equity_rows()]):
            resp = client.get("/api/portfolio")

        breakdown = resp.json()["sector_breakdown"]
        sectors = [s["sector"] for s in breakdown]
        assert "미분류" in sectors

    def test_weight_pct_calculation(self, client):
        """weight_pct = eval_amount / NAV * 100."""
        with _multi_ro_patch([self._snapshot_rows(), self._equity_rows()]):
            resp = client.get("/api/portfolio")

        holdings = resp.json()["holdings"]
        nav = resp.json()["nav"]
        for h in holdings:
            expected = h["eval_amount"] / nav * 100.0
            assert abs(h["weight_pct"] - expected) < 0.01

    def test_herfindahl_index_correct(self, client):
        """Herfindahl = Σ (eval_amount/NAV)^2."""
        with _multi_ro_patch([self._snapshot_rows(), self._equity_rows()]):
            resp = client.get("/api/portfolio")

        data = resp.json()
        nav = data["nav"]
        holdings = data["holdings"]
        expected_h = sum((h["eval_amount"] / nav) ** 2 for h in holdings)
        assert abs(data["herfindahl"] - expected_h) < 0.001

    def test_empty_snapshot_returns_ok(self, client):
        """스냅샷 데이터 없어도 200 반환 (빈 holdings)."""
        with _multi_ro_patch([[], self._equity_rows()]):
            resp = client.get("/api/portfolio")

        assert resp.status_code == 200
        assert resp.json()["holdings"] == []


# ---------------------------------------------------------------------------
# AC-3: /api/pnl-daily (REQ-054-A3, A8)
# ---------------------------------------------------------------------------

class TestPnlDailyEndpoint:
    def _make_rt(self, exit_date, net_pnl):
        rt = MagicMock()
        rt.exit_date = exit_date
        rt.net_pnl = net_pnl
        rt.ticker = "005930"
        return rt

    def test_daily_period_returns_rows(self, client):
        """period=daily 로 호출 시 rows 배열 반환."""
        mock_result = MagicMock()
        mock_result.roundtrips = [
            self._make_rt(date(2026, 5, 1), 10000.0),
            self._make_rt(date(2026, 5, 3), -5000.0),
        ]

        with (
            patch("trading.edge.roundtrips.compute_roundtrips", return_value=mock_result),
            patch("trading.edge.benchmark.kospi_closes", return_value={}),
        ):
            resp = client.get("/api/pnl-daily?period=daily")

        assert resp.status_code == 200
        data = resp.json()
        assert "rows" in data
        assert "benchmark_available" in data
        assert data["period"] == "daily"
        assert len(data["rows"]) == 2

    def test_kospi_unavailable_alpha_null(self, client):
        """KOSPI 데이터 없으면 benchmark_available=false, alpha_pct=null (REQ-054-A8)."""
        mock_result = MagicMock()
        mock_result.roundtrips = [self._make_rt(date(2026, 5, 1), 5000.0)]

        with (
            patch("trading.edge.roundtrips.compute_roundtrips", return_value=mock_result),
            patch("trading.edge.benchmark.kospi_closes", return_value={}),
        ):
            resp = client.get("/api/pnl-daily")

        data = resp.json()
        assert data["benchmark_available"] is False
        # alpha_pct 은 null
        for row in data["rows"]:
            assert row["alpha_pct"] is None

    def test_weekly_grouping(self, client):
        """period=weekly 로 같은 주 2건이 합산되어야 한다."""
        mock_result = MagicMock()
        # 2026-05-04(월)과 2026-05-05(화)는 같은 주 (W19)
        mock_result.roundtrips = [
            self._make_rt(date(2026, 5, 4), 3000.0),
            self._make_rt(date(2026, 5, 5), 2000.0),
        ]

        with (
            patch("trading.edge.roundtrips.compute_roundtrips", return_value=mock_result),
            patch("trading.edge.benchmark.kospi_closes", return_value={}),
        ):
            resp = client.get("/api/pnl-daily?period=weekly")

        data = resp.json()
        rows = data["rows"]
        assert len(rows) == 1, "같은 주 2건이 1행으로 합산되어야 함"
        assert rows[0]["realized_pnl"] == pytest.approx(5000.0)

    def test_cumulative_pnl_accumulates(self, client):
        """cumulative_pnl 이 날짜 순서대로 누적되어야 한다."""
        mock_result = MagicMock()
        mock_result.roundtrips = [
            self._make_rt(date(2026, 5, 1), 10000.0),
            self._make_rt(date(2026, 5, 2), -3000.0),
        ]

        with (
            patch("trading.edge.roundtrips.compute_roundtrips", return_value=mock_result),
            patch("trading.edge.benchmark.kospi_closes", return_value={}),
        ):
            resp = client.get("/api/pnl-daily?period=daily")

        rows = resp.json()["rows"]
        assert rows[0]["cumulative_pnl"] == pytest.approx(10000.0)
        assert rows[1]["cumulative_pnl"] == pytest.approx(7000.0)

    def test_invalid_period_returns_422(self, client):
        """period 값이 잘못되면 422 반환."""
        resp = client.get("/api/pnl-daily?period=hourly")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# AC-5: CSV 내보내기 단일원천 (REQ-054-A5, E2, ADR-003)
# ---------------------------------------------------------------------------

class TestCsvExport:
    def _make_rt_list(self):
        rt = MagicMock()
        rt.ticker = "005930"
        rt.entry_date = date(2026, 5, 1)
        rt.exit_date = date(2026, 5, 10)
        rt.qty = 10
        rt.entry_price = 70000.0
        rt.exit_price = 77000.0
        rt.net_pnl = 69890.0
        rt.return_pct = 9.97
        rt.entry_fee = 100.0
        rt.exit_fee = 110.0
        rt.fees = 210.0
        rt.holding_days = 9
        rt.confidence = 0.8
        rt.verdict = "APPROVE"
        rt.persona = "micro"
        rt.is_win = True
        return [rt]

    def test_content_type_is_csv(self, client):
        """Content-Type: text/csv 이어야 한다."""
        mock_result = MagicMock()
        mock_result.roundtrips = self._make_rt_list()

        with patch("trading.edge.roundtrips.compute_roundtrips", return_value=mock_result):
            resp = client.get("/api/export/roundtrips.csv")

        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]

    def test_content_disposition_attachment(self, client):
        """Content-Disposition: attachment 이어야 한다."""
        mock_result = MagicMock()
        mock_result.roundtrips = self._make_rt_list()

        with patch("trading.edge.roundtrips.compute_roundtrips", return_value=mock_result):
            resp = client.get("/api/export/roundtrips.csv")

        assert "attachment" in resp.headers.get("content-disposition", "")

    def test_csv_row_count_equals_json_row_count(self, client):
        """CSV 데이터 행 수 = /api/roundtrips JSON 행 수 (AC-5 교차검증)."""
        mock_result = MagicMock()
        mock_result.roundtrips = self._make_rt_list() * 3  # 3건

        with patch("trading.edge.roundtrips.compute_roundtrips", return_value=mock_result):
            json_resp = client.get("/api/roundtrips")
            csv_resp = client.get("/api/export/roundtrips.csv")

        json_count = len(json_resp.json())
        # CSV: 헤더 제외한 행 수
        csv_lines = [
            line for line in csv_resp.text.strip().splitlines() if line
        ]
        csv_data_rows = len(csv_lines) - 1  # 헤더 1행 제외
        assert csv_data_rows == json_count, (
            f"CSV 행={csv_data_rows}, JSON 행={json_count} 불일치"
        )

    def test_invalid_dataset_returns_404(self, client):
        """지원하지 않는 dataset 은 404 반환."""
        resp = client.get("/api/export/unknown.csv")
        assert resp.status_code == 404

    def test_portfolio_csv_row_count(self, client):
        """portfolio CSV 행 수 = /api/portfolio.holdings JSON 행 수."""
        snapshot_rows = [
            {
                "ticker": "005930", "qty": 10, "avg_cost": 70000.0,
                "eval_price": 77000.0, "eval_amount": 770000.0,
                "unrealized_pnl": 69890.0, "pnl_pct": 9.97,
                "trading_day": date(2026, 6, 20), "sector": "전기전자",
            },
        ]
        equity_rows = [{"total_assets": 2000000.0}]

        with _multi_ro_patch([snapshot_rows, equity_rows]):
            json_resp = client.get("/api/portfolio")

        with _multi_ro_patch([snapshot_rows, equity_rows]):
            csv_resp = client.get("/api/export/portfolio.csv")

        json_count = len(json_resp.json()["holdings"])
        csv_data_rows = len(
            [l for l in csv_resp.text.strip().splitlines() if l]
        ) - 1
        assert csv_data_rows == json_count


# ---------------------------------------------------------------------------
# AC-6: 대시보드 읽기전용 불변 (REQ-054-A7)
# ---------------------------------------------------------------------------

class TestDashboardReadOnly:
    def test_no_insert_in_dashboard_queries(self):
        """dashboard/queries.py 에 INSERT/UPDATE/DELETE 가 없어야 한다."""
        import re
        from pathlib import Path

        queries_path = Path("src/trading/dashboard/queries.py")
        if not queries_path.exists():
            queries_path = Path(__file__).parent.parent.parent / "src/trading/dashboard/queries.py"

        text = queries_path.read_text()
        # SQL DML 패턴 검색 (대소문자 무관)
        pattern = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+\w|DELETE\s+FROM)\b", re.IGNORECASE)
        matches = pattern.findall(text)
        assert not matches, f"dashboard/queries.py 에 쓰기 SQL 발견: {matches}"

    def test_no_insert_in_dashboard_app(self):
        """dashboard/app.py 에 직접 DB 쓰기가 없어야 한다."""
        import re
        from pathlib import Path

        app_path = Path("src/trading/dashboard/app.py")
        if not app_path.exists():
            app_path = Path(__file__).parent.parent.parent / "src/trading/dashboard/app.py"

        text = app_path.read_text()
        pattern = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+\w|DELETE\s+FROM)\b", re.IGNORECASE)
        matches = pattern.findall(text)
        assert not matches, f"dashboard/app.py 에 쓰기 SQL 발견: {matches}"


# ---------------------------------------------------------------------------
# AC-16: 503/cool_down 폴백 회귀 (REQ-054-F3)
# ---------------------------------------------------------------------------

class TestCoolDownFallback:
    def test_status_returns_200_without_cool_down_column(self, client):
        """cool_down_active 컬럼 없는 환경에서도 200 + false 반환 (기존 폴백 보존)."""
        import psycopg

        call_count = [0]

        @contextmanager
        def _conn_with_fallback(autocommit=False):
            conn = _FakeConn()
            cur = conn.cursor()

            def execute_with_error(sql, params=None):
                call_count[0] += 1
                if call_count[0] == 1:
                    # 첫 번째 execute: UndefinedColumn 시뮬레이션
                    raise psycopg.errors.UndefinedColumn(
                        "column ss.cool_down_active does not exist"
                    )
                # 두 번째 execute: 성공 (false AS cool_down_active 쿼리)
                cur._rows = [{
                    "halt_state": False,
                    "trading_mode": "paper",
                    "current_regime": "bull",
                    "current_risk_appetite": "risk-on",
                    "late_cycle_defense_active": False,
                    "late_cycle_level": None,
                    "cool_down_active": False,
                    "updated_at": datetime(2026, 6, 20, 9, 0),
                    "halt_reason": None,
                }]

            cur.execute = execute_with_error
            yield conn

        with patch("trading.dashboard.queries.ro_connection", side_effect=_conn_with_fallback):
            resp = client.get("/api/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data.get("cool_down_active") is False


# ---------------------------------------------------------------------------
# edge/roundtrips.py persona 필드 추가 (ADR-001) 단위 테스트
# ---------------------------------------------------------------------------

class TestRoundTripPersonaField:
    def _buy(self, ticker, qty, price, ts="2026-01-01T10:00:00", oid=1,
             confidence=None, verdict=None, persona=None):
        return {
            "id": oid, "ts": datetime.fromisoformat(ts), "filled_at": None,
            "side": "buy", "ticker": ticker, "fill_qty": qty, "fill_price": price,
            "fee": 0, "confidence": confidence, "verdict": verdict, "persona": persona,
        }

    def _sell(self, ticker, qty, price, ts="2026-01-10T10:00:00", oid=2):
        return {
            "id": oid, "ts": datetime.fromisoformat(ts), "filled_at": None,
            "side": "sell", "ticker": ticker, "fill_qty": qty, "fill_price": price,
            "fee": 0, "confidence": None, "verdict": None, "persona": None,
        }

    def test_persona_propagated_to_roundtrip(self):
        """매수 행의 persona 가 RoundTrip.persona 로 전달되어야 한다."""
        from trading.edge.roundtrips import build_roundtrips

        rows = [
            self._buy("005930", 10, 70000, persona="macro"),
            self._sell("005930", 10, 77000),
        ]
        result = build_roundtrips(rows)
        assert len(result.roundtrips) == 1
        assert result.roundtrips[0].persona == "macro"

    def test_persona_none_when_missing(self):
        """persona 없는 매수 행은 RoundTrip.persona = None 이어야 한다."""
        from trading.edge.roundtrips import build_roundtrips

        rows = [
            self._buy("005930", 10, 70000, persona=None),
            self._sell("005930", 10, 77000),
        ]
        result = build_roundtrips(rows)
        assert result.roundtrips[0].persona is None

    def test_existing_fields_unchanged(self):
        """persona 추가 후 기존 필드(net_pnl, is_win, holding_days)가 변하지 않아야 한다."""
        from trading.edge.roundtrips import build_roundtrips

        rows = [
            self._buy("005930", 10, 70000, ts="2026-01-01T10:00:00"),
            self._sell("005930", 10, 77000, ts="2026-01-10T10:00:00"),
        ]
        result = build_roundtrips(rows)
        rt = result.roundtrips[0]
        assert rt.net_pnl == (77000 - 70000) * 10  # 수수료 0
        assert rt.is_win is True
        assert rt.holding_days == 9

    def test_persona_propagated_across_fifo_chunks(self):
        """FIFO 분할 시 각 청크가 해당 매수 로트의 persona 를 상속해야 한다."""
        from trading.edge.roundtrips import build_roundtrips

        rows = [
            self._buy("A", 10, 100, ts="2026-01-01T10:00:00", oid=1, persona="micro"),
            self._buy("A", 10, 200, ts="2026-01-02T10:00:00", oid=2, persona="macro"),
            self._sell("A", 15, 300, ts="2026-01-05T10:00:00", oid=3),
        ]
        result = build_roundtrips(rows)
        assert len(result.roundtrips) == 2
        # FIFO: 첫 로트(micro) 10주 소진 후 두 번째(macro) 5주
        personas = {rt.entry_price: rt.persona for rt in result.roundtrips}
        assert personas[100.0] == "micro"
        assert personas[200.0] == "macro"


# ---------------------------------------------------------------------------
# 마이그레이션 스키마 검증 (SQL 파일 파싱)
# ---------------------------------------------------------------------------

class TestMigrationSchema:
    def _read_migration(self, filename: str) -> str:
        from pathlib import Path
        base = Path(__file__).parent.parent.parent
        path = base / "src" / "trading" / "db" / "migrations" / filename
        return path.read_text()

    def test_migration_035_creates_position_eval_snapshot(self):
        """035 마이그레이션이 position_eval_snapshot 테이블을 생성해야 한다."""
        sql = self._read_migration("035_position_eval_snapshot.sql")
        assert "position_eval_snapshot" in sql
        assert "IF NOT EXISTS" in sql.upper() or "CREATE TABLE" in sql.upper()

    def test_migration_035_has_required_columns(self):
        """035: trading_day, ticker, qty, avg_cost, eval_price, eval_amount,
        unrealized_pnl, pnl_pct 컬럼이 있어야 한다."""
        sql = self._read_migration("035_position_eval_snapshot.sql")
        required = [
            "trading_day", "ticker", "qty", "avg_cost",
            "eval_price", "eval_amount", "unrealized_pnl", "pnl_pct",
        ]
        for col in required:
            assert col in sql, f"035 마이그레이션에 컬럼 '{col}' 없음"

    def test_migration_035_has_primary_key(self):
        """035: PK (trading_day, ticker) 이 있어야 한다."""
        sql = self._read_migration("035_position_eval_snapshot.sql")
        assert "PRIMARY KEY" in sql.upper()
        assert "trading_day" in sql and "ticker" in sql

    def test_migration_036_creates_ticker_metadata(self):
        """036 마이그레이션이 ticker_metadata 테이블을 생성해야 한다."""
        sql = self._read_migration("036_ticker_metadata.sql")
        assert "ticker_metadata" in sql
        assert "IF NOT EXISTS" in sql.upper() or "CREATE TABLE" in sql.upper()

    def test_migration_036_has_required_columns(self):
        """036: ticker, sector, industry 컬럼이 있어야 한다."""
        sql = self._read_migration("036_ticker_metadata.sql")
        for col in ("ticker", "sector", "industry"):
            assert col in sql, f"036 마이그레이션에 컬럼 '{col}' 없음"

    def test_migration_036_ticker_is_primary_key(self):
        """036: ticker 가 PRIMARY KEY 이어야 한다."""
        sql = self._read_migration("036_ticker_metadata.sql")
        assert "PRIMARY KEY" in sql.upper()

    def test_migration_035_grants_to_dashboard_ro(self):
        """035: dashboard_ro 에 SELECT 권한 부여가 있어야 한다."""
        sql = self._read_migration("035_position_eval_snapshot.sql")
        assert "dashboard_ro" in sql
        assert "GRANT" in sql.upper()

    def test_migration_036_grants_to_dashboard_ro(self):
        """036: dashboard_ro 에 SELECT 권한 부여가 있어야 한다."""
        sql = self._read_migration("036_ticker_metadata.sql")
        assert "dashboard_ro" in sql
        assert "GRANT" in sql.upper()
