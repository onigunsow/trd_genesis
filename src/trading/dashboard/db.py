"""SPEC-TRADING-047 M1: 읽기 전용 DB 연결 헬퍼.

DASHBOARD_DATABASE_URL → dashboard_ro 역할 DSN 우선.
DATABASE_URL → 폴백 (동일 DB, 읽기 전용 역할로 접속해야 함).
POSTGRES_* → 환경변수 직접 조합 폴백.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.adapt import Loader
from psycopg.rows import dict_row


# SPEC-050: Pydantic/FastAPI v2 는 DB NUMERIC(Decimal)을 JSON 문자열로 직렬화하여
# 프론트의 숫자 연산(.toFixed 등)을 깨뜨린다. 읽기 전용 대시보드 표시 용도이므로
# NUMERIC 을 float 로 로드해 JSON 숫자로 내보낸다(표시 정밀도 충분).
class _NumericFloatLoader(Loader):
    def load(self, data):  # type: ignore[override]
        if data is None:
            return None
        return float(bytes(data).decode())


def ro_dsn() -> str:
    """대시보드 전용 읽기 전용 DSN 반환.

    우선순위:
    1. DASHBOARD_DATABASE_URL
    2. DATABASE_URL
    3. POSTGRES_* 환경변수 조합
    """
    raw = os.environ.get("DASHBOARD_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if raw:
        return raw.replace("postgresql+psycopg://", "postgresql://")

    user = os.environ.get("POSTGRES_USER", "dashboard_ro")
    pw = os.environ.get("POSTGRES_PASSWORD", "")
    db = os.environ.get("POSTGRES_DB", "trading")
    host = os.environ.get("POSTGRES_HOST", "postgres")
    port = os.environ.get("POSTGRES_PORT", "5432")
    return f"postgresql://{user}:{pw}@{host}:{port}/{db}"


@contextmanager
def ro_connection(autocommit: bool = False) -> Iterator[psycopg.Connection]:
    """읽기 전용 psycopg 연결 컨텍스트 매니저."""
    conn = psycopg.connect(ro_dsn(), autocommit=autocommit, row_factory=dict_row)
    # NUMERIC → float (위 _NumericFloatLoader 주석 참조)
    conn.adapters.register_loader("numeric", _NumericFloatLoader)
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()
