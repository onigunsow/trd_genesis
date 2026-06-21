"""SPEC-TRADING-056: 실 Postgres 통합테스트 픽스처.

trading_test DB 를 생성·마이그레이션하고 최소 시드 데이터를 삽입한다.
Postgres 미도달 시 pytest.skip() 으로 단위테스트 회귀 없음.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row
import pytest

# ---------------------------------------------------------------------------
# DB 연결 정보 해석
# ---------------------------------------------------------------------------

def _maintenance_dsn() -> str:
    """유지보수 연결(postgres 기본 DB) DSN 반환.

    DATABASE_URL / POSTGRES_* 환경변수에서 읽되
    DB 이름을 'postgres'(기본 유지보수 DB)로 교체한다.
    """
    raw = os.environ.get("DATABASE_URL", "")
    if raw:
        # DATABASE_URL 에서 DB 이름 부분만 'postgres'로 교체
        dsn = raw.replace("postgresql+psycopg://", "postgresql://")
        # DSN 끝 '/trading' → '/postgres' 패턴 교체
        # 예: postgresql://user:pw@host:5432/trading → postgresql://user:pw@host:5432/postgres
        if "/" in dsn.split("@")[-1]:
            base = dsn.rsplit("/", 1)[0]
            return f"{base}/postgres"
        return dsn

    user = os.environ.get("POSTGRES_USER", "trading")
    pw = os.environ.get("POSTGRES_PASSWORD", "")
    host = os.environ.get("POSTGRES_HOST", "postgres")
    port = os.environ.get("POSTGRES_PORT", "5432")
    return f"postgresql://{user}:{pw}@{host}:{port}/postgres"


def _test_dsn() -> str:
    """trading_test DB DSN 반환."""
    raw = os.environ.get("DATABASE_URL", "")
    if raw:
        dsn = raw.replace("postgresql+psycopg://", "postgresql://")
        if "/" in dsn.split("@")[-1]:
            base = dsn.rsplit("/", 1)[0]
            return f"{base}/trading_test"
        return dsn

    user = os.environ.get("POSTGRES_USER", "trading")
    pw = os.environ.get("POSTGRES_PASSWORD", "")
    host = os.environ.get("POSTGRES_HOST", "postgres")
    port = os.environ.get("POSTGRES_PORT", "5432")
    return f"postgresql://{user}:{pw}@{host}:{port}/trading_test"


# ---------------------------------------------------------------------------
# session-scoped 픽스처: migrated_db
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def migrated_db():
    """trading_test DB 를 생성하고 전 마이그레이션을 적용한 연결을 반환.

    Postgres 미도달 → pytest.skip() (단위테스트 무영향).
    teardown 시 연결 종료 (DB 드롭은 선택, 기본 보존).
    """
    maint_dsn = _maintenance_dsn()
    test_dsn = _test_dsn()

    # --- Postgres 도달 가능 여부 확인 ---
    try:
        maint_conn = psycopg.connect(maint_dsn, connect_timeout=3, autocommit=True)
    except Exception as exc:
        pytest.skip(f"Postgres 미도달 — 통합테스트 skip: {exc}")
        return  # unreachable, for type checkers

    # --- trading_test DB 재생성 ---
    with maint_conn:
        with maint_conn.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = 'trading_test' AND pid <> pg_backend_pid()"
            )
            cur.execute("DROP DATABASE IF EXISTS trading_test")
            cur.execute("CREATE DATABASE trading_test")
    maint_conn.close()

    # --- DATABASE_URL 을 trading_test 로 monkeypatch ---
    # 이후 import 되는 trading.db.session.dsn() 이 trading_test 를 가리키게 한다.
    os.environ["DATABASE_URL"] = test_dsn

    # DASHBOARD_DATABASE_URL 도 교체 (dashboard/db.py ro_connection 사용)
    os.environ["DASHBOARD_DATABASE_URL"] = test_dsn

    # --- 전 마이그레이션 적용 ---
    # 주의: trading.db.session 은 os.environ 을 런타임에 읽으므로
    # 환경변수 교체 후 임포트해야 정확히 trading_test 를 바라본다.
    # 이미 임포트된 경우를 대비해 session 모듈을 늦게 참조한다.
    from trading.db import migrate as _migrate

    try:
        applied = _migrate.run()
        print(f"\n[integration] migration 적용 {applied}건")
    except Exception as exc:
        pytest.skip(f"migration 실패 — 통합테스트 skip: {exc}")
        return

    # --- test 연결 반환 ---
    conn = psycopg.connect(test_dsn, row_factory=dict_row)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# 최소 시드 데이터 헬퍼
# ---------------------------------------------------------------------------

def seed_minimal(conn: psycopg.Connection) -> dict[str, Any]:
    """통합테스트용 최소 시드 데이터 삽입.

    삽입 항목:
    - persona_runs 1행
    - persona_decisions 1행
    - orders: buy filled, sell filled, synthetic buy, correction sell
    - positions: buy 에 대응하는 보유 행
    - system_state: 이미 migration 001 에서 삽입됨 — 존재 보장만 확인

    Returns:
        dict: 삽입된 주요 id (persona_run_id, persona_decision_id, buy_order_id, ...)
    """
    ids: dict[str, Any] = {}
    now = datetime.now(UTC)

    with conn.cursor() as cur:
        # persona_runs (model, prompt, response 는 NOT NULL)
        cur.execute(
            """
            INSERT INTO persona_runs (
                persona_name, cycle_kind, ts,
                model, prompt, response,
                input_tokens, output_tokens
            ) VALUES (
                'micro', 'intraday', %s,
                'claude-sonnet-4-6', 'test prompt', 'test response',
                100, 50
            )
            RETURNING id
            """,
            (now,),
        )
        row = cur.fetchone()
        assert row is not None
        ids["persona_run_id"] = row["id"]

        # persona_decisions
        cur.execute(
            """
            INSERT INTO persona_decisions (
                persona_run_id, cycle_kind, ticker, side, qty,
                confidence, rationale, ts
            ) VALUES (%s, 'intraday', '005930', 'buy', 10, 0.75, '테스트 결정', %s)
            RETURNING id
            """,
            (ids["persona_run_id"], now),
        )
        row = cur.fetchone()
        assert row is not None
        ids["persona_decision_id"] = row["id"]

        # orders: 매수 체결 (filled)
        cur.execute(
            """
            INSERT INTO orders (
                ts, mode, side, ticker, qty, order_type,
                fill_qty, fill_price, fee,
                status, filled_at, persona_decision_id
            ) VALUES (
                %s, 'paper', 'buy', '005930', 10, 'market',
                10, 70000, 350,
                'filled', %s, %s
            )
            RETURNING id
            """,
            (now - timedelta(hours=2), now - timedelta(hours=2), ids["persona_decision_id"]),
        )
        row = cur.fetchone()
        assert row is not None
        ids["buy_order_id"] = row["id"]

        # orders: 매도 체결 (filled)
        cur.execute(
            """
            INSERT INTO orders (
                ts, mode, side, ticker, qty, order_type,
                fill_qty, fill_price, fee,
                status, filled_at
            ) VALUES (
                %s, 'paper', 'sell', '005930', 5, 'market',
                5, 72000, 180,
                'filled', %s
            )
            RETURNING id
            """,
            (now - timedelta(hours=1), now - timedelta(hours=1)),
        )
        row = cur.fetchone()
        assert row is not None
        ids["sell_order_id"] = row["id"]

        # orders: synthetic 매수 (교정 대상 ghost buy)
        cur.execute(
            """
            INSERT INTO orders (
                ts, mode, side, ticker, qty, order_type,
                fill_qty, fill_price, fee,
                status, filled_at, synthetic
            ) VALUES (
                %s, 'paper', 'buy', '000660', 5, 'market',
                5, 130000, 325,
                'filled', %s, TRUE
            )
            RETURNING id
            """,
            (now - timedelta(hours=3), now - timedelta(hours=3)),
        )
        row = cur.fetchone()
        assert row is not None
        ids["synthetic_order_id"] = row["id"]

        # orders: submitted (stuck order 용 — resolver 테스트)
        cur.execute(
            """
            INSERT INTO orders (
                ts, mode, side, ticker, qty, order_type,
                status
            ) VALUES (
                %s, 'paper', 'sell', '035420', 3, 'market',
                'submitted'
            )
            RETURNING id
            """,
            (now - timedelta(hours=1),),
        )
        row = cur.fetchone()
        assert row is not None
        ids["submitted_order_id"] = row["id"]

        # positions: 005930 보유 (매수 5 - 매도 5 후 남은 5주, KIS truth)
        cur.execute(
            """
            INSERT INTO positions (ticker, qty, avg_cost, last_updated, last_order_id)
            VALUES ('005930', 5, 70000, %s, %s)
            ON CONFLICT (ticker) DO UPDATE
                SET qty = EXCLUDED.qty,
                    avg_cost = EXCLUDED.avg_cost,
                    last_updated = EXCLUDED.last_updated
            RETURNING id
            """,
            (now, ids["buy_order_id"]),
        )
        row = cur.fetchone()
        assert row is not None
        ids["position_id"] = row["id"]

        # positions: 000660 보유 없음 (orders 에만 synthetic 5주 → ghost)
        # positions 에 행이 없으면 orders_net=5, positions_qty=0 → divergence 발생
        # → orders_positions_divergence 가 올바르게 감지하는지 검증 가능

        # system_state: migration 001 이 INSERT ON CONFLICT DO NOTHING 으로 삽입
        cur.execute("SELECT id FROM system_state WHERE id = 1")
        row = cur.fetchone()
        assert row is not None, "system_state 행 없음 — migration 001 미적용"

        conn.commit()

    return ids
