"""SPEC-TRADING-047 M1: dashboard read-only DSN helper tests.

RED phase — tests written before implementation.
"""

from __future__ import annotations

import os
from unittest.mock import patch


class TestReadOnlyDsn:
    """dashboard_ro DSN resolution."""

    def test_uses_dashboard_database_url_env(self) -> None:
        """DASHBOARD_DATABASE_URL takes priority when set."""
        from trading.dashboard.db import ro_dsn

        with patch.dict(os.environ, {"DASHBOARD_DATABASE_URL": "postgresql://ro:pw@db:5432/trading"}):
            result = ro_dsn()
        assert result == "postgresql://ro:pw@db:5432/trading"

    def test_strips_sqlalchemy_prefix(self) -> None:
        """SQLAlchemy-style prefix is stripped for psycopg compatibility."""
        from trading.dashboard.db import ro_dsn

        with patch.dict(
            os.environ,
            {"DASHBOARD_DATABASE_URL": "postgresql+psycopg://ro:pw@db:5432/trading"},
        ):
            result = ro_dsn()
        assert result == "postgresql://ro:pw@db:5432/trading"

    def test_falls_back_to_database_url_env(self) -> None:
        """Falls back to DATABASE_URL when DASHBOARD_DATABASE_URL is absent."""
        from trading.dashboard.db import ro_dsn

        env = {
            "DATABASE_URL": "postgresql://user:pw@postgres:5432/trading",
        }
        with patch.dict(os.environ, env, clear=True):
            result = ro_dsn()
        assert "postgresql://" in result
        assert "trading" in result

    def test_builds_from_postgres_env_vars(self) -> None:
        """Builds DSN from POSTGRES_* env vars when no URL env is set."""
        from trading.dashboard.db import ro_dsn

        env = {
            "POSTGRES_USER": "dashboard_ro",
            "POSTGRES_PASSWORD": "secret",
            "POSTGRES_DB": "trading",
            "POSTGRES_HOST": "myhost",
        }
        with patch.dict(os.environ, env, clear=True):
            result = ro_dsn()
        assert "dashboard_ro" in result
        assert "myhost" in result
        assert "trading" in result
