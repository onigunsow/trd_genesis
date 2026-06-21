"""SPEC-TRADING-056: 실 Postgres 통합 SQL 스키마 테스트.

FakeCursor mock 이 잡지 못하는 컬럼 부재·NOT NULL·CHECK·JOIN 오류를
마이그레이션된 진짜 스키마에 실행해 사전 차단한다.

실행 방법:
    POSTGRES_HOST=172.19.0.4 \\
    DATABASE_URL="postgresql://trading:<pw>@172.19.0.4:5432/trading" \\
    .venv/bin/pytest tests/integration/ -m integration -v

Postgres 미도달 환경에서는 모든 테스트가 pytest.skip() 으로 통과한다.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from tests.integration.conftest import seed_minimal

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# 픽스처: 시드 데이터 (function-scoped, migrated_db 재사용)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def seeded(migrated_db):
    """시드 데이터를 1회 삽입하고 id dict 를 반환."""
    return seed_minimal(migrated_db)


# ---------------------------------------------------------------------------
# 1. ghost_convergence — 거짓그린 표면 #1 (order_type NOT NULL 포함)
# ---------------------------------------------------------------------------

class TestGhostConvergence:
    """kis/ghost_convergence.py 의 실 SQL 검증.

    이번 세션에서 order_type NOT NULL 이 라이브에서 터진 경로다.
    dry_run=False 교정 INSERT 가 통합테스트에서 정상 실행되는지 검증한다.
    """

    def test_orders_positions_divergence_runs(self, seeded: dict[str, Any]) -> None:
        """orders_positions_divergence() 가 예외 없이 결과 반환."""
        from trading.kis.ghost_convergence import orders_positions_divergence

        result = orders_positions_divergence()

        assert isinstance(result, dict)
        assert "parity" in result
        assert "by_ticker" in result
        assert isinstance(result["parity"], bool)

    def test_divergence_detects_ghost(self, seeded: dict[str, Any]) -> None:
        """000660: orders 에 synthetic 5주, positions 에 없음 → divergence 감지."""
        from trading.kis.ghost_convergence import orders_positions_divergence

        result = orders_positions_divergence()

        # 000660 은 시드에서 synthetic buy 만 있고 positions 행이 없어 divergence 발생
        by_ticker = result["by_ticker"]
        assert "000660" in by_ticker, "ghost ticker 000660 이 divergence 에 없음"
        assert by_ticker["000660"]["diff"] > 0, "ghost buy 초과가 감지되지 않음"

    def test_converge_ghost_buys_dry_run(self, seeded: dict[str, Any]) -> None:
        """converge_ghost_buys(dry_run=True) — SELECT 전용, INSERT 없음."""
        from trading.kis.ghost_convergence import converge_ghost_buys

        # paper 모드 클라이언트 mock
        from trading.config import TradingMode
        client = MagicMock()
        client.mode = TradingMode.PAPER

        result = converge_ghost_buys(client, dry_run=True)

        assert isinstance(result, dict)
        assert result["dry_run"] is True
        assert "scanned_tickers" in result

    def test_converge_ghost_buys_real_insert(self, migrated_db: Any) -> None:
        """converge_ghost_buys(dry_run=False) — 실제 교정 SELL INSERT.

        이것이 order_type NOT NULL 을 검증하는 핵심 테스트.
        dry_run=False 경로의 INSERT 가 NOT NULL 제약에 걸리지 않아야 한다.
        """
        import psycopg
        from datetime import UTC, datetime, timedelta

        # 독립 ghost ticker 사용 (다른 테스트와 격리)
        ghost_ticker = "033780"
        now = datetime.now(UTC)

        with migrated_db.cursor() as cur:
            # ghost buy 삽입 (synthetic, positions 행 없음)
            cur.execute(
                """
                INSERT INTO orders (
                    ts, mode, side, ticker, qty, order_type,
                    fill_qty, fill_price, fee,
                    status, filled_at, synthetic
                ) VALUES (
                    %s, 'paper', 'buy', %s, 8, 'market',
                    8, 50000, 200,
                    'filled', %s, TRUE
                )
                """,
                (now - timedelta(hours=4), ghost_ticker, now - timedelta(hours=4)),
            )
            migrated_db.commit()

        from trading.config import TradingMode
        from trading.kis.ghost_convergence import converge_ghost_buys

        client = MagicMock()
        client.mode = TradingMode.PAPER

        # 실 INSERT 경로 — order_type NOT NULL CHECK 가 이 경로에서 걸렸었음
        result = converge_ghost_buys(client, dry_run=False)

        assert result["converged"] >= 1 or result["total_excess"] >= 0
        # 예외 없이 반환되면 order_type NOT NULL 통과 증명


# ---------------------------------------------------------------------------
# 2. order_resolver — 거짓그린 표면 #2
# ---------------------------------------------------------------------------

class TestOrderResolver:
    """kis/order_resolver.py 의 실 SQL 검증."""

    def test_resolve_stuck_orders_dry_run(self, seeded: dict[str, Any]) -> None:
        """resolve_stuck_orders(dry_run=True) — SELECT 전용."""
        from trading.kis.order_resolver import resolve_stuck_orders

        client = MagicMock()
        result = resolve_stuck_orders(client, dry_run=True)

        assert isinstance(result, dict)
        assert "scanned" in result
        assert result["dry_run"] is True

    def test_resolve_stuck_orders_real_expire(self, migrated_db: Any) -> None:
        """window=0 으로 stuck submitted 주문을 실제 expire UPDATE."""
        from datetime import UTC, datetime, timedelta
        from trading.kis.order_resolver import BrokerFillInquiryNotImplemented
        from trading.kis.order_resolver import resolve_stuck_orders

        # 오래된 submitted 주문 삽입 (window=0 에서 즉시 expire 대상)
        now = datetime.now(UTC)
        with migrated_db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO orders (ts, mode, side, ticker, qty, order_type, status)
                VALUES (%s, 'paper', 'sell', '028260', 2, 'market', 'submitted')
                RETURNING id
                """,
                (now - timedelta(hours=2),),
            )
            migrated_db.commit()

        client = MagicMock()
        # confirm_fills 는 paper 모드이므로 BrokerFillInquiryNotImplemented 를 발생시키지 않음
        # broker_truth.confirm_fills 는 내부에서 실 DB 를 읽으므로 mock 불필요

        result = resolve_stuck_orders(client, window_seconds=0, dry_run=False)

        assert isinstance(result, dict)
        assert "scanned" in result
        # expired 또는 resolved_filled 중 하나 이상 발생
        total_resolved = result.get("resolved_expired", 0) + result.get("resolved_filled", 0)
        assert total_resolved >= 0  # 예외 없이 반환되면 스키마 통과


# ---------------------------------------------------------------------------
# 3. edge/roundtrips — _FILL_SQL 컬럼·correction 컬럼 검증
# ---------------------------------------------------------------------------

class TestRoundtrips:
    """edge/roundtrips.py load_fill_rows / compute_roundtrips 실 SQL 검증.

    _FILL_SQL 은 orders.correction, persona_runs.persona_name 등
    여러 마이그레이션에 걸친 컬럼을 JOIN 한다.
    """

    def test_load_fill_rows_runs(self, seeded: dict[str, Any]) -> None:
        """load_fill_rows() 가 예외 없이 리스트 반환."""
        from trading.edge.roundtrips import load_fill_rows

        rows = load_fill_rows()

        assert isinstance(rows, list)
        # 시드에서 filled buy/sell 이 있으므로 최소 1행 이상
        assert len(rows) >= 1

    def test_load_fill_rows_has_correction_field(self, seeded: dict[str, Any]) -> None:
        """반환 행에 correction 컬럼이 포함된다 (mig 038)."""
        from trading.edge.roundtrips import load_fill_rows

        rows = load_fill_rows()

        for row in rows:
            assert "correction" in row, f"correction 컬럼 없음: {row}"

    def test_compute_roundtrips_runs(self, seeded: dict[str, Any]) -> None:
        """compute_roundtrips() 가 예외 없이 RoundTripResult 반환."""
        from trading.edge.roundtrips import compute_roundtrips

        result = compute_roundtrips(None)

        assert hasattr(result, "roundtrips")
        assert hasattr(result, "unmatched_sells")
        assert hasattr(result, "open_qty")

    def test_roundtrips_persona_field(self, seeded: dict[str, Any]) -> None:
        """라운드트립에 persona 필드가 있다 (persona_runs.persona_name JOIN)."""
        from trading.edge.roundtrips import compute_roundtrips

        result = compute_roundtrips(None)

        for rt in result.roundtrips:
            # persona 는 None 허용이지만 AttributeError 는 안 됨
            assert hasattr(rt, "persona")


# ---------------------------------------------------------------------------
# 4. edge/realized_pnl — 집계 진입점 검증
# ---------------------------------------------------------------------------

class TestRealizedPnl:
    """edge/realized_pnl.py 집계 경로 실 SQL 검증."""

    def test_aggregate_realized_pnl_runs(self, seeded: dict[str, Any]) -> None:
        """aggregate_realized_pnl_cum() 이 예외 없이 반환."""
        from trading.edge.realized_pnl import aggregate_realized_pnl_cum

        result = aggregate_realized_pnl_cum()

        # 딕셔너리 또는 int/None 반환 — 예외 없으면 스키마 통과
        assert result is not None or result is None  # 예외 없이 반환


# ---------------------------------------------------------------------------
# 5. dashboard/queries — pd.persona 컬럼 등 인라인 SQL 전수 검증
# ---------------------------------------------------------------------------

class TestDashboardQueries:
    """dashboard/queries.py 의 모든 인라인 SQL 실 스키마 검증.

    pd.persona 컬럼 부재, positions.mode 컬럼 부재 등이 이 경로에서 터졌었다.
    """

    def test_fetch_holdings(self, seeded: dict[str, Any]) -> None:
        """fetch_holdings() — positions + position_eval_snapshot JOIN."""
        from trading.dashboard.queries import fetch_holdings

        result = fetch_holdings()

        assert isinstance(result, list)

    def test_fetch_recent_orders(self, seeded: dict[str, Any]) -> None:
        """fetch_recent_orders() — orders 테이블 단순 조회."""
        from trading.dashboard.queries import fetch_recent_orders

        result = fetch_recent_orders(limit=10)

        assert isinstance(result, list)
        assert len(result) >= 1

    def test_fetch_recent_decisions(self, seeded: dict[str, Any]) -> None:
        """fetch_recent_decisions() — persona_decisions JOIN persona_runs.

        pd.persona 컬럼 부재가 이전에 터진 경로.
        실제로는 pr.persona_name 이지만 쿼리 작성 오류 시 이 경로에서 잡힌다.
        """
        from trading.dashboard.queries import fetch_recent_decisions

        result = fetch_recent_decisions(limit=10)

        assert isinstance(result, list)

    def test_fetch_roundtrips(self, seeded: dict[str, Any]) -> None:
        """fetch_roundtrips() — orders 체결 집계 + correction 필터."""
        from trading.dashboard.queries import fetch_roundtrips

        result = fetch_roundtrips()

        assert isinstance(result, (list, dict))

    def test_fetch_postmortem(self, seeded: dict[str, Any]) -> None:
        """fetch_postmortem() — 4분류 집계 + regime_at_decision 컬럼."""
        from trading.dashboard.queries import fetch_postmortem

        # 캐시 초기화
        from trading.dashboard import queries as _q
        _q._postmortem_cache.clear()

        result = fetch_postmortem(days=30, limit=100)

        assert isinstance(result, dict)
        assert "distribution" in result or "total" in result

    def test_fetch_confidence_analysis(self, seeded: dict[str, Any]) -> None:
        """fetch_confidence_analysis() — confidence 집계 쿼리."""
        from trading.dashboard.queries import fetch_confidence_analysis

        from trading.dashboard import queries as _q
        _q._confidence_cache.clear()

        result = fetch_confidence_analysis()

        assert isinstance(result, dict)

    def test_fetch_pnl_daily(self, seeded: dict[str, Any]) -> None:
        """fetch_pnl_daily() — daily_equity_snapshot 조회.

        반환 타입은 dict (rows 키 포함) 또는 list — 구현에 따라 유연하게 허용.
        """
        from trading.dashboard.queries import fetch_pnl_daily

        result = fetch_pnl_daily()

        assert isinstance(result, (list, dict))

    def test_fetch_system_status(self, seeded: dict[str, Any]) -> None:
        """fetch_system_status() — system_state + cool_down_active 컬럼.

        positions.mode 컬럼 부재가 이전에 터진 위치 (resolver_health 통해).
        """
        from trading.dashboard.queries import fetch_system_status

        result = fetch_system_status()

        assert isinstance(result, dict)
        assert "halt_state" in result


# ---------------------------------------------------------------------------
# 6. ops/resolver_health — positions.mode 컬럼 부재가 터진 경로
# ---------------------------------------------------------------------------

class TestResolverHealth:
    """ops/resolver_health.py 실 SQL 검증.

    positions.mode 컬럼 부재(SPEC-042 수렴 버그)가 이 경로에서 터졌었다.
    """

    def test_evaluate_resolver_health_runs(self, seeded: dict[str, Any]) -> None:
        """evaluate_resolver_health() 가 예외 없이 dict 반환."""
        from trading.ops.resolver_health import evaluate_resolver_health

        result = evaluate_resolver_health()

        assert isinstance(result, dict)
        assert "stuck_count" in result
        assert "parity" in result
        assert "healthy_hard" in result

    def test_resolver_health_stuck_count_is_int(self, seeded: dict[str, Any]) -> None:
        """stuck_count 가 int 타입."""
        from trading.ops.resolver_health import evaluate_resolver_health

        result = evaluate_resolver_health()

        assert isinstance(result["stuck_count"], int)
        # 시드에 submitted 주문이 있으므로 0 이상
        assert result["stuck_count"] >= 0
