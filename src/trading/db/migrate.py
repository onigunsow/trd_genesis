"""Apply SQL migrations from db/migrations/ directory.

The first migration (001) is auto-applied by docker-entrypoint-initdb.d on
empty postgres init. Subsequent migrations need explicit run via:
    docker compose exec app trading migrate
"""

from __future__ import annotations

import logging
from pathlib import Path

from trading.db.session import connection

LOG = logging.getLogger(__name__)


def applied_versions() -> set[str]:
    """Return set of migrations already applied (from schema_migrations)."""
    with connection() as conn, conn.cursor() as cur:
        # Table may not exist yet (e.g., only 001 applied via init script).
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'schema_migrations')"
        )
        row = cur.fetchone()
        if not row or not row["exists"]:
            return set()
        cur.execute("SELECT version FROM schema_migrations")
        return {row["version"] for row in cur.fetchall()}


def migrations_dir() -> Path:
    return Path(__file__).resolve().parent / "migrations"


def pending() -> list[Path]:
    """Return SQL files not yet applied, ordered by filename."""
    applied = applied_versions()
    files = sorted(migrations_dir().glob("[0-9][0-9][0-9]_*.sql"))
    return [f for f in files if f.stem not in applied]


def apply_one(path: Path) -> None:
    """Apply a single SQL file. The file is expected to record itself in schema_migrations."""
    sql = path.read_text(encoding="utf-8")
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
    LOG.info("applied %s", path.stem)


def run() -> int:
    """Apply all pending migrations. Returns count applied."""
    files = pending()
    for f in files:
        apply_one(f)
        print(f"applied {f.stem}")
    if not files:
        print("no pending migrations")
    return len(files)


if __name__ == "__main__":
    run()
