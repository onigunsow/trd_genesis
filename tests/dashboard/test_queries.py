"""SPEC-TRADING-047/050 M1: dashboard query function tests.

RED phase — tests written before implementation.
DB is mocked via the FakeConnection/FakeCursor pattern from conftest.

SPEC-TRADING-050 M1 추가사항:
- fetch_postmortem_distribution / fetch_calibration_scores 구형 stub 테스트 제거(REQ-050-6).
- fetch_postmortem / fetch_confidence_analysis 신규 지연계산 함수 테스트 추가.
- fetch_recent_news / fetch_story_clusters / fetch_trends / fetch_pipeline 신규 테스트.
- fetch_recent_decisions / fetch_system_status / fetch_equity_curve 확장 테스트 추가.

SPEC-050 follow-up 추가:
- TestFetchPostmortem 확장: prob_* 미참조, 라운드트립 매칭, KOSPI 상대수익률, graceful 폴백.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_patch(rows: list[dict[str, Any]]):
    """Patch trading.dashboard.queries.ro_connection returning preset rows."""

    @contextmanager
    def _conn(autocommit: bool = False):
        from tests.conftest import FakeConnection, FakeCursor
        cursor = FakeCursor(rows)
        yield FakeConnection(cursor)

    return patch("trading.dashboard.queries.ro_connection", side_effect=_conn)


def _make_multi_patch(rows_list: list[list[dict[str, Any]]]):
    """각 DB 호출마다 순서대로 다른 rows 를 반환하는 패치.

    fetch_postmortem 처럼 ro_connection 을 2번 이상 호출하는 함수 테스트용.
    rows_list[0] → 첫 번째 호출, rows_list[1] → 두 번째 호출, 이후 마지막 반복.
    """
    call_count: list[int] = [0]

    @contextmanager
    def _conn(autocommit: bool = False):
        idx = min(call_count[0], len(rows_list) - 1)
        call_count[0] += 1
        from tests.conftest import FakeConnection, FakeCursor
        cursor = FakeCursor(rows_list[idx])
        yield FakeConnection(cursor)

    return patch("trading.dashboard.queries.ro_connection", side_effect=_conn)


# ---------------------------------------------------------------------------
# fetch_system_status (SPEC-047 기존 + SPEC-050 REQ-050-4 확장)
# ---------------------------------------------------------------------------

class TestFetchSystemStatus:
    """fetch_system_status returns system_state singleton."""

    def test_returns_halt_and_regime(self) -> None:
        from trading.dashboard import queries

        state_row = {
            "halt_state": True,
            "trading_mode": "paper",
            "current_regime": "bull",
            "current_risk_appetite": "risk-on",
            "late_cycle_defense_active": False,
            "updated_at": datetime(2026, 6, 14, 9, 0, tzinfo=UTC),
        }
        with _make_patch([state_row]):
            result = queries.fetch_system_status()

        assert result["halt_state"] is True
        assert result["trading_mode"] == "paper"
        assert result["current_regime"] == "bull"

    def test_missing_row_raises(self) -> None:
        from trading.dashboard import queries

        with _make_patch([]):
            with pytest.raises(RuntimeError, match="system_state"):
                queries.fetch_system_status()

    def test_returns_cool_down_and_late_cycle_fields(self) -> None:
        """AC-M1-5: status 확장 — cool_down_active / late_cycle 필드 포함."""
        from trading.dashboard import queries

        state_row = {
            "halt_state": True,
            "trading_mode": "paper",
            "current_regime": "bear",
            "current_risk_appetite": "risk-off",
            "late_cycle_defense_active": True,
            "late_cycle_level": "severe",
            "cool_down_active": True,
            "halt_reason": "daily_loss 한도 초과",
            "updated_at": datetime(2026, 6, 14, 9, 0, tzinfo=UTC),
        }
        with _make_patch([state_row]):
            result = queries.fetch_system_status()

        assert result["cool_down_active"] is True
        assert result["late_cycle_defense_active"] is True
        assert result["late_cycle_level"] == "severe"
        # halt_reason 은 None 이거나 문자열이어야 함
        assert "halt_reason" in result


# ---------------------------------------------------------------------------
# fetch_recent_decisions (SPEC-047 기존 + SPEC-050 REQ-050-3 확장)
# ---------------------------------------------------------------------------

class TestFetchRecentDecisions:
    """fetch_recent_decisions: persona_runs + decisions + risk_reviews LEFT JOIN."""

    def test_returns_list_of_dicts(self) -> None:
        from trading.dashboard import queries

        rows = [
            {
                "id": 1,
                "ts": datetime(2026, 6, 14, 9, 30, tzinfo=UTC),
                "persona_name": "decision",
                "cycle_kind": "intraday",
                "ticker": "005930",
                "side": "buy",
                "qty": 10,
                "confidence": 0.82,
                "rationale": "모멘텀 확인",
                "risk_verdict": None,
                "risk_rationale": None,
            }
        ]
        with _make_patch(rows):
            result = queries.fetch_recent_decisions(limit=20)

        assert len(result) == 1
        assert result[0]["ticker"] == "005930"
        assert result[0]["side"] == "buy"

    def test_empty_table_returns_empty_list(self) -> None:
        from trading.dashboard import queries

        with _make_patch([]):
            result = queries.fetch_recent_decisions(limit=20)

        assert result == []

    def test_includes_risk_verdict_and_rationale(self) -> None:
        """AC-M1-2: risk_reviews LEFT JOIN — verdict/rationale 포함."""
        from trading.dashboard import queries

        rows = [
            {
                "id": 10,
                "ts": datetime(2026, 6, 14, 9, 30, tzinfo=UTC),
                "persona_name": "decision",
                "cycle_kind": "intraday",
                "ticker": "000660",
                "side": "buy",
                "qty": 5,
                "confidence": 0.75,
                "rationale": "상승 모멘텀",
                "risk_verdict": "REJECT",
                "risk_rationale": "집중도 초과",
            },
            {
                "id": 11,
                "ts": datetime(2026, 6, 14, 9, 31, tzinfo=UTC),
                "persona_name": "decision",
                "cycle_kind": "intraday",
                "ticker": "005930",
                "side": "hold",
                "qty": 0,
                "confidence": 0.50,
                "rationale": "관망",
                "risk_verdict": None,       # LEFT JOIN 미매칭
                "risk_rationale": None,
            },
        ]
        with _make_patch(rows):
            result = queries.fetch_recent_decisions(limit=50)

        assert len(result) == 2
        assert result[0]["risk_verdict"] == "REJECT"
        assert result[0]["risk_rationale"] == "집중도 초과"
        # LEFT JOIN 미매칭은 null 이며 행 누락 없음
        assert result[1]["risk_verdict"] is None
        assert result[1]["risk_rationale"] is None


# ---------------------------------------------------------------------------
# fetch_recent_orders (SPEC-047 기존)
# ---------------------------------------------------------------------------

class TestFetchRecentOrders:
    """fetch_recent_orders returns orders with fill_price if available."""

    def test_returns_order_fields(self) -> None:
        from trading.dashboard import queries

        rows = [
            {
                "id": 99,
                "ts": datetime(2026, 6, 14, 9, 31, tzinfo=UTC),
                "side": "buy",
                "ticker": "005930",
                "qty": 10,
                "order_type": "market",
                "status": "filled",
                "fill_price": 75000,
                "mode": "paper",
            }
        ]
        with _make_patch(rows):
            result = queries.fetch_recent_orders(limit=50)

        assert len(result) == 1
        assert result[0]["status"] == "filled"
        assert result[0]["fill_price"] == 75000

    def test_no_secrets_in_response_fields(self) -> None:
        """응답 행에 request/response JSONB (자격증명 포함 가능) 없음 — REQ-050-8."""
        from trading.dashboard import queries

        rows = [
            {
                "id": 1,
                "ts": datetime(2026, 6, 14, tzinfo=UTC),
                "side": "buy",
                "ticker": "000660",
                "qty": 5,
                "order_type": "market",
                "status": "submitted",
                "fill_price": None,
                "mode": "paper",
                "request": {"api_key": "SECRET"},   # 응답에 포함 금지
                "response": {"token": "SECRET"},
                "kis_order_no": "ORD123456",        # 응답에 포함 금지
            }
        ]
        with _make_patch(rows):
            result = queries.fetch_recent_orders(limit=50)

        for row in result:
            assert "request" not in row
            assert "response" not in row
            assert "kis_order_no" not in row


# ---------------------------------------------------------------------------
# fetch_holdings (SPEC-047 기존)
# ---------------------------------------------------------------------------

class TestFetchHoldings:
    """fetch_holdings returns current open position summary from orders/fills."""

    def test_returns_holdings_list(self) -> None:
        from trading.dashboard import queries

        rows = [
            {
                "ticker": "005930",
                "qty_net": 30,
                "avg_fill_price": 74000,
                "total_cost": 2220000,
            }
        ]
        with _make_patch(rows):
            result = queries.fetch_holdings()

        assert len(result) == 1
        assert result[0]["ticker"] == "005930"
        assert result[0]["qty_net"] == 30

    def test_empty_positions_returns_empty_list(self) -> None:
        from trading.dashboard import queries

        with _make_patch([]):
            result = queries.fetch_holdings()

        assert result == []


# ---------------------------------------------------------------------------
# fetch_equity_curve (SPEC-047 기존 + SPEC-050 REQ-050-5 확장)
# ---------------------------------------------------------------------------

class TestFetchEquityCurve:
    """fetch_equity_curve returns daily_equity_snapshot rows + drawdown 곡선."""

    def test_returns_date_and_total_assets(self) -> None:
        from datetime import date

        from trading.dashboard import queries
        rows = [
            {"trading_day": date(2026, 6, 10), "total_assets": 10_000_000},
            {"trading_day": date(2026, 6, 11), "total_assets": 10_050_000},
        ]
        with _make_patch(rows):
            result = queries.fetch_equity_curve(days=30)

        assert len(result) == 2
        assert result[0]["trading_day"].isoformat() == "2026-06-10"
        assert result[1]["total_assets"] == 10_050_000

    def test_drawdown_series_included(self) -> None:
        """AC-M1 / REQ-050-5: drawdown(러닝 맥스 대비 낙폭) 시리즈 포함."""
        from datetime import date

        from trading.dashboard import queries

        # 최고점 10M → 9.5M 으로 낙폭 발생
        rows = [
            {"trading_day": date(2026, 6, 10), "total_assets": 10_000_000,
             "stock_eval": 0, "cash": 10_000_000, "unrealized_pnl": 0},
            {"trading_day": date(2026, 6, 11), "total_assets": 10_500_000,
             "stock_eval": 0, "cash": 10_500_000, "unrealized_pnl": 0},
            {"trading_day": date(2026, 6, 12), "total_assets": 9_975_000,
             "stock_eval": 0, "cash": 9_975_000, "unrealized_pnl": 0},
        ]
        with _make_patch(rows):
            result = queries.fetch_equity_curve(days=30)

        # 세 번째 행은 drawdown 이 음수여야 함
        assert len(result) == 3
        assert "drawdown_pct" in result[0]
        # 첫 행 drawdown = 0 (최고점)
        assert result[0]["drawdown_pct"] == pytest.approx(0.0)
        # 두 번째 행도 최고점 = 0
        assert result[1]["drawdown_pct"] == pytest.approx(0.0)
        # 세 번째 행: (9_975_000 - 10_500_000) / 10_500_000 ≈ -0.05
        assert result[2]["drawdown_pct"] < 0

    def test_empty_equity_curve_returns_empty_list(self) -> None:
        from trading.dashboard import queries

        with _make_patch([]):
            result = queries.fetch_equity_curve(days=30)

        assert result == []


# ---------------------------------------------------------------------------
# fetch_recent_news (SPEC-050 신규 REQ-050-2)
# ---------------------------------------------------------------------------

class TestFetchRecentNews:
    """fetch_recent_news: news_articles + news_analysis JOIN."""

    def test_returns_news_with_analysis_fields(self) -> None:
        """REQ-050-2: news 엔드포인트 계약 — impact_score/sentiment/keywords 포함."""
        from trading.dashboard import queries

        rows = [
            {
                "id": 1,
                "title": "삼성전자 실적 발표",
                "url": "https://example.com/1",
                "summary": "어닝 서프라이즈",
                "source_name": "한국경제",
                "sector": "반도체",
                "published_at": datetime(2026, 6, 14, 8, 0, tzinfo=UTC),
                "impact_score": 4,
                "sentiment": "positive",
                "keywords": ["삼성전자", "반도체", "실적"],
                "summary_2line": "삼성전자 2Q 실적 서프라이즈\n반도체 업황 회복세",
            }
        ]
        with _make_patch(rows):
            result = queries.fetch_recent_news(days=7, limit=20)

        assert len(result) == 1
        row = result[0]
        assert row["title"] == "삼성전자 실적 발표"
        assert row["impact_score"] == 4
        assert row["sentiment"] == "positive"
        assert "keywords" in row
        assert "summary_2line" in row

    def test_returns_empty_list_on_no_rows(self) -> None:
        from trading.dashboard import queries

        with _make_patch([]):
            result = queries.fetch_recent_news(days=7, limit=20)

        assert result == []

    def test_no_write_operations(self) -> None:
        """읽기 전용 — INSERT/UPDATE/DELETE 없음 (REQ-050-1)."""
        import inspect

        from trading.dashboard import queries

        src = inspect.getsource(queries.fetch_recent_news)
        assert "INSERT" not in src.upper()
        assert "UPDATE" not in src.upper()
        assert "DELETE" not in src.upper()


# ---------------------------------------------------------------------------
# fetch_story_clusters (SPEC-050 신규 REQ-050-2, AC-M1-1)
# ---------------------------------------------------------------------------

class TestFetchStoryClusters:
    """fetch_story_clusters: portfolio_relevant/relevance_tickers 포함."""

    def test_returns_portfolio_relevant_clusters(self) -> None:
        """AC-M1-1: portfolio_relevant/relevance_tickers 포함 확인."""
        from trading.dashboard import queries

        rows = [
            {
                "id": 1,
                "representative_title": "반도체 업황 개선",
                "sector": "반도체",
                "sentiment_dominant": "positive",
                "portfolio_relevant": True,
                "relevance_tickers": ["005930", "000660"],
                "source_count": 3,
                "impact_max": 4,
                "cluster_date": "2026-06-14",
            },
            {
                "id": 2,
                "representative_title": "경제 지표 발표",
                "sector": "경제",
                "sentiment_dominant": "neutral",
                "portfolio_relevant": False,
                "relevance_tickers": [],
                "source_count": 1,
                "impact_max": 2,
                "cluster_date": "2026-06-14",
            },
        ]
        with _make_patch(rows):
            result = queries.fetch_story_clusters(days=7, limit=20)

        assert len(result) == 2
        assert result[0]["representative_title"] == "반도체 업황 개선"
        assert result[0]["portfolio_relevant"] is True
        assert result[0]["relevance_tickers"] == ["005930", "000660"]
        assert "sentiment_dominant" in result[0]
        # portfolio_relevant=False 행도 포함 (필터는 프론트)
        assert result[1]["portfolio_relevant"] is False

    def test_empty_returns_empty_list(self) -> None:
        from trading.dashboard import queries

        with _make_patch([]):
            result = queries.fetch_story_clusters(days=7, limit=20)

        assert result == []

    def test_no_write_operations(self) -> None:
        import inspect
        import re

        from trading.dashboard import queries

        src = inspect.getsource(queries.fetch_story_clusters)
        assert "INSERT INTO" not in src.upper()
        # UPDATE DML — 컬럼명 last_updated 와 구분하기 위해 단어 경계 검사
        assert not re.search(r"\bUPDATE\s+\w", src.upper())


# ---------------------------------------------------------------------------
# fetch_trends (SPEC-050 신규 REQ-050-2, AC-M5-3)
# ---------------------------------------------------------------------------

class TestFetchTrends:
    """fetch_trends: news_trends 키워드/감성 집계."""

    def test_returns_trend_rows(self) -> None:
        from trading.dashboard import queries

        rows = [
            {
                "id": 1,
                "keyword": "반도체",
                "mention_count": 15,
                "sentiment_positive": 8,
                "sentiment_neutral": 5,
                "sentiment_negative": 2,
                "sentiment_avg": 0.6,
                "trend_type": "daily",
                "trend_date": "2026-06-14",
            }
        ]
        with _make_patch(rows):
            result = queries.fetch_trends(trend_type="daily", days=7)

        assert len(result) == 1
        assert result[0]["keyword"] == "반도체"
        assert result[0]["mention_count"] == 15
        assert "sentiment_positive" in result[0]

    def test_empty_returns_empty_list(self) -> None:
        from trading.dashboard import queries

        with _make_patch([]):
            result = queries.fetch_trends(trend_type="daily", days=7)

        assert result == []


# ---------------------------------------------------------------------------
# fetch_postmortem (SPEC-050 신규 REQ-050-6/7, AC-M1-3/4)
# ---------------------------------------------------------------------------

class TestFetchPostmortem:
    """fetch_postmortem: 어댑터 → edge.postmortem.classify_decision_outcome → aggregate."""

    # ------------------------------------------------------------------
    # 공통 결정 행 팩토리 (prob_* 컬럼 없음 — mig 033 미적용 호환)
    # ------------------------------------------------------------------
    @staticmethod
    def _decision_row(
        id_: int = 1,
        ticker: str = "005930",
        side: str = "buy",
        confidence: float = 0.85,
        regime: str = "bull",
        ts: datetime | None = None,
        persona_run_id: int = 100,
    ) -> dict[str, Any]:
        return {
            "id": id_,
            "ts": ts or datetime(2026, 6, 10, 9, 30, tzinfo=UTC),
            "persona_name": "decision",
            "cycle_kind": "intraday",
            "ticker": ticker,
            "side": side,
            "confidence": confidence,
            "rationale": "테스트 근거",
            "regime_at_decision": regime,
            "persona_run_id": persona_run_id,
            "risk_verdict": "APPROVE",
            # prob_* 컬럼 없음: mig 033 이전 환경 시뮬레이션
        }

    @staticmethod
    def _fill_rows(
        ticker: str = "005930",
        buy_price: int = 74000,
        sell_price: int = 76000,
        entry_ts: datetime | None = None,
        exit_ts: datetime | None = None,
        qty: int = 10,
    ) -> list[dict[str, Any]]:
        """매수+매도 체결 행 — build_roundtrips 용."""
        entry = entry_ts or datetime(2026, 6, 10, 9, 31, tzinfo=UTC)
        exit_ = exit_ts or datetime(2026, 6, 20, 9, 31, tzinfo=UTC)
        return [
            {
                "id": 1, "ts": entry, "filled_at": entry,
                "side": "buy", "ticker": ticker,
                "fill_qty": qty, "fill_price": buy_price, "fee": 0,
                "confidence": 0.85, "verdict": "APPROVE",
            },
            {
                "id": 2, "ts": exit_, "filled_at": exit_,
                "side": "sell", "ticker": ticker,
                "fill_qty": qty, "fill_price": sell_price, "fee": 0,
                "confidence": None, "verdict": None,
            },
        ]

    # ------------------------------------------------------------------
    # (a) prob_* 컬럼 미참조 — mig 033 없이 동작
    # ------------------------------------------------------------------

    def test_no_prob_columns_in_sql(self) -> None:
        """(a) prob_bull/prob_base/prob_bear 컬럼 미참조 → mig 033 없이도 동작."""
        import inspect

        from trading.dashboard import queries

        src = inspect.getsource(queries.fetch_postmortem)
        assert "prob_bull" not in src, "prob_bull 이 쿼리에서 제거되지 않았음"
        assert "prob_base" not in src, "prob_base 이 쿼리에서 제거되지 않았음"
        assert "prob_bear" not in src, "prob_bear 이 쿼리에서 제거되지 않았음"

    def test_returns_distribution_with_four_classes(self) -> None:
        """AC-M1-3: edge 순수 함수 결과가 JSON 으로 반환."""
        from trading.dashboard import queries

        queries._postmortem_cache.clear()

        decision_rows = [self._decision_row()]
        fill_rows: list[dict[str, Any]] = []  # 라운드트립 없음 → MISSED 경로

        with _make_multi_patch([decision_rows, fill_rows]):
            with patch("trading.edge.benchmark.kospi_closes", return_value=[]):
                result = queries.fetch_postmortem(days=30, limit=100)

        assert isinstance(result, dict)
        assert "distribution" in result
        dist = result["distribution"]
        assert isinstance(dist, dict)
        for key in ("TRUE_POSITIVE", "FALSE_POSITIVE", "REGIME_MISMATCH", "MISSED"):
            assert key in dist, f"{key} 가 distribution 에 없음"

    # ------------------------------------------------------------------
    # (b) 진입 + 수익 + 시장 대비 우위 → TRUE_POSITIVE
    # ------------------------------------------------------------------

    def test_entered_profitable_market_beating_is_true_positive(self) -> None:
        """(b) 진입 결정 + 라운드트립 수익 + KOSPI 상대 우위 → TRUE_POSITIVE."""
        from trading.dashboard import queries

        queries._postmortem_cache.clear()

        # 결정: buy 005930, confidence=0.85
        dec_ts = datetime(2026, 6, 10, 9, 30, tzinfo=UTC)
        decision_rows = [self._decision_row(ticker="005930", confidence=0.85, ts=dec_ts)]

        # 체결: 매수 74,000 → 매도 78,000 (+5.4% 수익)
        entry_ts = datetime(2026, 6, 10, 9, 31, tzinfo=UTC)
        exit_ts = datetime(2026, 6, 20, 9, 31, tzinfo=UTC)
        fills = self._fill_rows(
            ticker="005930",
            buy_price=74000, sell_price=78000,
            entry_ts=entry_ts, exit_ts=exit_ts,
        )

        # KOSPI: 동 기간 2% 상승 → 상대수익 +3.4% > 0 → TRUE_POSITIVE
        entry_date = date(2026, 6, 10)
        exit_date = date(2026, 6, 20)
        kospi_closes_mock = [
            (entry_date, 2700.0),
            (exit_date, 2754.0),  # +2%
        ]

        with _make_multi_patch([decision_rows, fills]):
            with patch("trading.edge.benchmark.kospi_closes", return_value=kospi_closes_mock):
                result = queries.fetch_postmortem(days=30, limit=100)

        dist = result["distribution"]
        assert dist["TRUE_POSITIVE"] >= 1, f"TRUE_POSITIVE 기대, distribution={dist}"

    # ------------------------------------------------------------------
    # (c) 고확신 손실 결정 → FALSE_POSITIVE
    # ------------------------------------------------------------------

    def test_confident_losing_trade_is_false_positive(self) -> None:
        """(c) confidence ≥ 0.6 + 라운드트립 손실 + KOSPI 상대수익 < 0 → FALSE_POSITIVE."""
        from trading.dashboard import queries

        queries._postmortem_cache.clear()

        dec_ts = datetime(2026, 6, 10, 9, 30, tzinfo=UTC)
        decision_rows = [self._decision_row(
            ticker="000660", confidence=0.75, ts=dec_ts, id_=2
        )]

        entry_ts = datetime(2026, 6, 10, 9, 31, tzinfo=UTC)
        exit_ts = datetime(2026, 6, 20, 9, 31, tzinfo=UTC)
        fills = self._fill_rows(
            ticker="000660",
            buy_price=60000, sell_price=55000,  # -8.3% 손실
            entry_ts=entry_ts, exit_ts=exit_ts,
        )

        # KOSPI: 동 기간 1% 상승 → 상대수익 -9.3% < 0 → FALSE_POSITIVE 조건 충족
        entry_date = date(2026, 6, 10)
        exit_date = date(2026, 6, 20)
        kospi_closes_mock = [
            (entry_date, 2700.0),
            (exit_date, 2727.0),  # +1%
        ]

        with _make_multi_patch([decision_rows, fills]):
            with patch("trading.edge.benchmark.kospi_closes", return_value=kospi_closes_mock):
                result = queries.fetch_postmortem(days=30, limit=100)

        dist = result["distribution"]
        assert dist["FALSE_POSITIVE"] >= 1, f"FALSE_POSITIVE 기대, distribution={dist}"

    # ------------------------------------------------------------------
    # (d) 미진입 결정 + 양의 KOSPI 선행 → MISSED
    # ------------------------------------------------------------------

    def test_non_entered_with_positive_forward_kospi_is_missed(self) -> None:
        """(d) hold 결정 + 이후 20거래일 KOSPI 양수 → MISSED."""
        from trading.dashboard import queries

        queries._postmortem_cache.clear()

        dec_ts = datetime(2026, 6, 10, 9, 30, tzinfo=UTC)
        decision_rows = [self._decision_row(side="hold", confidence=0.3, ts=dec_ts, id_=3)]

        fill_rows: list[dict[str, Any]] = []  # 체결 없음

        dec_date = date(2026, 6, 10)
        # 20거래일 후 약 +3% 상승 시뮬레이션
        future_date = date(2026, 7, 10)
        kospi_closes_mock = [
            (dec_date, 2700.0),
            (future_date, 2781.0),  # +3%
        ]
        # 추가 거래일 채우기 (20일 인덱스 확보)
        from datetime import timedelta
        extra = [(dec_date + timedelta(days=i), 2700.0 + i * 4) for i in range(1, 22)]
        kospi_closes_mock = sorted(set([kospi_closes_mock[0]] + extra + [kospi_closes_mock[1]]))

        with _make_multi_patch([decision_rows, fill_rows]):
            with patch("trading.edge.benchmark.kospi_closes", return_value=kospi_closes_mock):
                result = queries.fetch_postmortem(days=30, limit=100)

        dist = result["distribution"]
        assert dist["MISSED"] >= 1, f"MISSED 기대, distribution={dist}"

    # ------------------------------------------------------------------
    # (e) 페르소나 귀인
    # ------------------------------------------------------------------

    def test_returns_persona_attribution(self) -> None:
        """(e) 페르소나별 귀인 포함."""
        from trading.dashboard import queries

        queries._postmortem_cache.clear()

        decision_rows = [self._decision_row()]
        fill_rows: list[dict[str, Any]] = []

        with _make_multi_patch([decision_rows, fill_rows]):
            with patch("trading.edge.benchmark.kospi_closes", return_value=[]):
                result = queries.fetch_postmortem(days=30, limit=100)

        assert "per_persona" in result
        assert isinstance(result["per_persona"], dict)
        # 결정이 1건이므로 per_persona 에 항목 있음
        assert len(result["per_persona"]) >= 1

    # ------------------------------------------------------------------
    # (f) KOSPI 데이터 없음 → graceful 폴백 (크래시 금지)
    # ------------------------------------------------------------------

    def test_graceful_when_kospi_data_absent(self) -> None:
        """(f) KOSPI 종가 없어도 크래시 없음 — relative=0.0 폴백."""
        from trading.dashboard import queries

        queries._postmortem_cache.clear()

        decision_rows = [self._decision_row()]
        fill_rows = self._fill_rows()  # 라운드트립 있음

        # kospi_closes 가 빈 목록 반환 → relative=0.0 폴백
        with _make_multi_patch([decision_rows, fill_rows]):
            with patch("trading.edge.benchmark.kospi_closes", return_value=[]):
                result = queries.fetch_postmortem(days=30, limit=100)

        # 크래시 없이 결과 반환
        assert isinstance(result, dict)
        assert "distribution" in result
        total = sum(result["distribution"].values())
        assert total == 1, "결정 1건 → 합계 1"

    # ------------------------------------------------------------------
    # 기존 테스트 (리팩터링 후 유지)
    # ------------------------------------------------------------------

    def test_correct_fk_used(self) -> None:
        """REQ-050-6: pd.persona_run_id 올바른 FK 사용 — SQL 소스 확인."""
        import inspect

        from trading.dashboard import queries

        src = inspect.getsource(queries.fetch_postmortem)
        assert "pd.persona_run_id" in src
        assert "JOIN persona_runs pr ON pr.id = pd.persona_run_id" in src

    def test_no_write_operations(self) -> None:
        """REQ-050-1: 읽기 전용."""
        import inspect

        from trading.dashboard import queries

        src = inspect.getsource(queries.fetch_postmortem)
        assert "INSERT" not in src.upper()
        assert "UPDATE" not in src.upper()
        assert "DELETE" not in src.upper()

    def test_cache_returns_same_result_on_second_call(self) -> None:
        """AC-M1-4: 두 번째 호출은 캐시에서 응답 — DB 1회만 조회."""
        from trading.dashboard import queries

        queries._postmortem_cache.clear()

        call_count: list[int] = [0]

        @contextmanager
        def _counting_conn(autocommit: bool = False):
            call_count[0] += 1
            from tests.conftest import FakeConnection, FakeCursor
            cursor = FakeCursor([])
            yield FakeConnection(cursor)

        with patch("trading.dashboard.queries.ro_connection", side_effect=_counting_conn):
            with patch("trading.edge.benchmark.kospi_closes", return_value=[]):
                result1 = queries.fetch_postmortem(days=30, limit=100)
                result2 = queries.fetch_postmortem(days=30, limit=100)

        assert result1["distribution"] == result2["distribution"]
        # 첫 호출에서 2번(결정+체결) — 두 번째 호출은 캐시에서
        assert call_count[0] == 2


# ---------------------------------------------------------------------------
# fetch_confidence_analysis (SPEC-050 신규 REQ-050-6/7, AC-M1-3)
# ---------------------------------------------------------------------------

class TestFetchConfidenceAnalysis:
    """fetch_confidence_analysis: 어댑터 → build_roundtrips → confidence.analyze."""

    def test_returns_buckets_and_correlations(self) -> None:
        """AC-M1-3: edge 순수 함수(confidence.analyze) 결과 반환."""
        from trading.dashboard import queries

        # 캐시 초기화
        queries._confidence_cache.clear()

        # 매수→매도 라운드트립 형성 가능한 체결 행
        rows = [
            {
                "id": 1, "ts": datetime(2026, 6, 1, 9, 30, tzinfo=UTC),
                "filled_at": datetime(2026, 6, 1, 9, 31, tzinfo=UTC),
                "side": "buy", "ticker": "005930",
                "fill_qty": 10, "fill_price": 74000, "fee": 0,
                "confidence": 0.8, "verdict": "APPROVE",
            },
            {
                "id": 2, "ts": datetime(2026, 6, 10, 9, 30, tzinfo=UTC),
                "filled_at": datetime(2026, 6, 10, 9, 31, tzinfo=UTC),
                "side": "sell", "ticker": "005930",
                "fill_qty": 10, "fill_price": 76000, "fee": 0,
                "confidence": None, "verdict": None,
            },
        ]
        with _make_patch(rows):
            result = queries.fetch_confidence_analysis(days=30)

        assert isinstance(result, dict)
        assert "buckets" in result
        assert "n_with_conf" in result
        # pearson/spearman 은 n<3 이면 None
        assert "pearson" in result
        assert "spearman" in result

    def test_empty_returns_zero_buckets(self) -> None:
        from trading.dashboard import queries

        # 캐시 초기화 — 이전 테스트 캐시가 남아 있을 수 있음
        queries._confidence_cache.clear()
        with _make_patch([]):
            result = queries.fetch_confidence_analysis(days=30)

        assert isinstance(result, dict)
        assert result["n_with_conf"] == 0

    def test_no_write_operations(self) -> None:
        import inspect

        from trading.dashboard import queries

        src = inspect.getsource(queries.fetch_confidence_analysis)
        assert "INSERT" not in src.upper()
        assert "UPDATE" not in src.upper()


# ---------------------------------------------------------------------------
# fetch_pipeline (SPEC-050 신규 REQ-050-2)
# ---------------------------------------------------------------------------

class TestFetchPipeline:
    """fetch_pipeline: 최신 사이클 persona_runs 재구성."""

    def test_returns_pipeline_dict(self) -> None:
        """REQ-050-2: pipeline 엔드포인트 계약 — persona_name/cycle_kind/ts 포함."""
        from trading.dashboard import queries

        rows = [
            {
                "id": 10,
                "ts": datetime(2026, 6, 14, 9, 0, tzinfo=UTC),
                "persona_name": "macro",
                "cycle_kind": "pre_market",
                "input_tokens": 1200,
                "output_tokens": 350,
                "latency_ms": 2100,
                "error": None,
                "regime_at_decision": "bull",
            },
            {
                "id": 11,
                "ts": datetime(2026, 6, 14, 9, 1, tzinfo=UTC),
                "persona_name": "micro",
                "cycle_kind": "pre_market",
                "input_tokens": 900,
                "output_tokens": 280,
                "latency_ms": 1800,
                "error": None,
                "regime_at_decision": "bull",
            },
        ]
        with _make_patch(rows):
            result = queries.fetch_pipeline()

        assert isinstance(result, dict)
        assert "steps" in result
        steps = result["steps"]
        assert isinstance(steps, list)
        if steps:
            step = steps[0]
            assert "persona_name" in step
            assert "ts" in step

    def test_empty_persona_runs_returns_empty_steps(self) -> None:
        """E1: persona_runs 가 비어 있는 신규 환경 → 빈 steps (500 금지)."""
        from trading.dashboard import queries

        with _make_patch([]):
            result = queries.fetch_pipeline()

        assert result["steps"] == []

    def test_no_write_operations(self) -> None:
        import inspect

        from trading.dashboard import queries

        src = inspect.getsource(queries.fetch_pipeline)
        assert "INSERT" not in src.upper()
        assert "UPDATE" not in src.upper()


# ---------------------------------------------------------------------------
# Redaction (SPEC-050 REQ-050-8, AC-M1-6)
# ---------------------------------------------------------------------------

class TestRedaction:
    """민감 필드 redaction — 자격증명/KIS payload/kis_order_no 제외."""

    def test_sensitive_fields_excluded_from_orders(self) -> None:
        """AC-M1-6: request/response/kis_order_no 제외."""
        from trading.dashboard import queries

        rows = [
            {
                "id": 1,
                "ts": datetime(2026, 6, 14, tzinfo=UTC),
                "side": "buy", "ticker": "005930",
                "qty": 1, "order_type": "market",
                "status": "filled", "fill_price": 75000, "mode": "paper",
                "request": {"api_key": "SECRET_KEY"},
                "response": {"token": "SECRET_TOKEN"},
                "kis_order_no": "KIS_ORD_001",
            }
        ]
        with _make_patch(rows):
            result = queries.fetch_recent_orders(limit=10)

        assert len(result) == 1
        row = result[0]
        assert "request" not in row
        assert "response" not in row
        assert "kis_order_no" not in row

    def test_decision_rationale_confidence_exposed(self) -> None:
        """AC-M1-6: rationale/confidence/prob_*/verdict 는 노출 허용."""
        from trading.dashboard import queries

        rows = [
            {
                "id": 1,
                "ts": datetime(2026, 6, 14, 9, 30, tzinfo=UTC),
                "persona_name": "decision",
                "cycle_kind": "intraday",
                "ticker": "005930",
                "side": "buy",
                "qty": 5,
                "confidence": 0.82,
                "rationale": "상승 모멘텀 확인",
                "risk_verdict": "APPROVE",
                "risk_rationale": "집중도 허용 범위",
            }
        ]
        with _make_patch(rows):
            result = queries.fetch_recent_decisions(limit=10)

        assert len(result) == 1
        row = result[0]
        assert row["confidence"] == 0.82
        assert row["rationale"] == "상승 모멘텀 확인"
        assert row["risk_verdict"] == "APPROVE"
        assert row["risk_rationale"] == "집중도 허용 범위"
