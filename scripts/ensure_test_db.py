"""Give the test suite a database of its own, idempotently.

The suite pointed at the same database as the browser, and
`conftest.no_leftover_boards` deletes every board that appeared during a
run. A board created in a tab while `make test` was running was, as far as
that fixture could tell, one the tests had made. It went.

Nothing about that is fixable inside the fixture: it cannot distinguish a
board a test created from one a person created, because nothing in the row
says which. The fix is for the suite to be looking at a different database.

Run before pytest, every time, and cheap when there is nothing to do. It
deliberately does not use `make reset`: the whole point is to add a
database without destroying the one with somebody's dashboards in it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

TEST_DB = "semantic_test"
INIT = Path("/db/init")
# 00_roles.sh is skipped: roles are cluster-wide and already exist. The
# rest is the same DDL the real database was built from, so the two cannot
# drift into testing one schema and shipping another.
INIT_FILES = ("01_schemas.sql", "02_warehouse.sql", "03_app.sql",
              "04_grants.sql")


def _dsn_for(admin_url: str, database: str) -> str:
    return make_conninfo(admin_url, dbname=database)


def _exists(admin_url: str, database: str) -> bool:
    with psycopg.connect(admin_url, autocommit=True) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
        ).fetchone()
        return row is not None


def _create(admin_url: str, database: str) -> None:
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(
            sql.Identifier(database)))


def _has_schema(dsn: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT to_regclass('app.board')").fetchone()
        return row is not None and row[0] is not None


def _apply_ddl(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        for name in INIT_FILES:
            conn.execute((INIT / name).read_text())


def _warehouse_is_empty(dsn: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT count(*) FROM ddh.fct_production_daily").fetchone()
        return not row or row[0] == 0


def main() -> int:
    admin_url = os.environ["ADMIN_URL"]
    if conninfo_to_dict(admin_url).get("dbname") == TEST_DB:
        # Already pointed at it, so there is nothing to bootstrap.
        return 0

    if not _exists(admin_url, TEST_DB):
        print(f"creating {TEST_DB}", file=sys.stderr)
        _create(admin_url, TEST_DB)

    dsn = _dsn_for(admin_url, TEST_DB)
    if not _has_schema(dsn):
        print(f"applying the schema to {TEST_DB}", file=sys.stderr)
        _apply_ddl(dsn)

    if _warehouse_is_empty(dsn):
        # The render and compile tests execute real SQL against real rows.
        # Seeding is the slow step and runs once per database, ever.
        print(f"seeding {TEST_DB}", file=sys.stderr)
        subprocess.run([sys.executable, "/db/seed/seed.py"], check=True,
                       env={**os.environ, "ADMIN_URL": dsn})

    # Migrations are part of the schema, and the suite asserts that a fresh
    # install and a migrated one converge. Applying them here is what makes
    # that true of the database the suite actually uses.
    sys.path.insert(0, "/app")
    from app.migrations import run_migrations       # noqa: E402

    applied = run_migrations(dsn, Path("/db/migrations"))
    if applied:
        print(f"applied {len(applied)} migration(s) to {TEST_DB}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
