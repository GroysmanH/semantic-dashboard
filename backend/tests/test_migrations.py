"""Transactional startup migrations and their final schema contract."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.migrations import (
    MigrationChecksumError,
    MigrationDiscoveryError,
    MigrationError,
    discover_migrations,
    run_migrations,
)


@pytest.fixture(scope="session")
def admin_dsn() -> str:
    return os.environ["ADMIN_URL"]


@pytest.fixture
def disposable_admin_dsn(admin_dsn: str):
    """A unique database: a killed test cannot poison the development ledger."""
    database_name = f"migration_test_{uuid4().hex}"
    with psycopg.connect(admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    database_dsn = make_conninfo(admin_dsn, dbname=database_name)
    try:
        with psycopg.connect(database_dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("CREATE SCHEMA app")
        yield database_dsn
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(database_name)
                )
            )


def _dsn_for_role(database_dsn: str, role_dsn: str) -> str:
    database_name = conninfo_to_dict(database_dsn)["dbname"]
    return make_conninfo(role_dsn, dbname=database_name)


def _create_legacy_app_schema(database_dsn: str) -> None:
    with psycopg.connect(database_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("CREATE TABLE app.board (id uuid PRIMARY KEY)")
        cur.execute("CREATE TABLE app.card (id uuid PRIMARY KEY)")
        # This mirrors the old, over-broad grant which the runner must repair.
        cur.execute("GRANT USAGE, CREATE ON SCHEMA app TO app_rw")
        cur.execute("GRANT ALL ON ALL TABLES IN SCHEMA app TO app_rw")
        cur.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA app GRANT ALL ON TABLES TO app_rw"
        )


def _default_acl_privileges(database_dsn: str, object_type: str) -> set[str]:
    with psycopg.connect(database_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT privilege_record.privilege_type
              FROM pg_default_acl default_record
              JOIN pg_namespace namespace_record
                ON namespace_record.oid = default_record.defaclnamespace
              CROSS JOIN LATERAL aclexplode(default_record.defaclacl) privilege_record
             WHERE namespace_record.nspname = 'app'
               AND default_record.defaclobjtype = %s
               AND privilege_record.grantee = (SELECT oid FROM pg_roles WHERE rolname = 'app_rw')
            """,
            (object_type,),
        )
        return {row[0] for row in cur.fetchall()}


@pytest.fixture
def migrated_admin_dsn(disposable_admin_dsn: str) -> str:
    _create_legacy_app_schema(disposable_admin_dsn)
    run_migrations(disposable_admin_dsn, _production_migration_dir())
    return disposable_admin_dsn


@pytest.fixture
def migrated_app_dsn(migrated_admin_dsn: str) -> str:
    return _dsn_for_role(migrated_admin_dsn, os.environ["APP_URL"])


def _write(directory: Path, name: str, sql: str | bytes) -> Path:
    path = directory / name
    if isinstance(sql, bytes):
        path.write_bytes(sql)
    else:
        path.write_text(sql, encoding="utf-8")
    return path


def _production_migration_dir() -> Path:
    container_path = Path("/db/migrations")
    if container_path.exists():
        return container_path
    return Path(__file__).parents[2] / "db" / "migrations"


def test_discover_migrations_orders_numeric_versions_and_hashes_bytes(tmp_path: Path):
    second = _write(tmp_path, "0002_second.sql", "SELECT 2;\n")
    first = _write(tmp_path, "0001_first.sql", "SELECT 1;\n")

    migrations = discover_migrations(tmp_path)

    assert [migration.version for migration in migrations] == ["0001", "0002"]
    assert [migration.path for migration in migrations] == [first, second]
    assert migrations[0].checksum == (
        "b4e0497804e46e0a0b0b8c31975b062152d551bac49c3c2e80932567b4085dcd"
    )


def test_discover_migrations_rejects_duplicate_version_prefixes(tmp_path: Path):
    _write(tmp_path, "0001_first.sql", "SELECT 1;")
    _write(tmp_path, "0001_again.sql", "SELECT 2;")

    with pytest.raises(MigrationDiscoveryError, match="duplicate migration version 0001"):
        discover_migrations(tmp_path)


def test_discover_migrations_rejects_non_utf8_before_connecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _write(tmp_path, "0001_invalid.sql", b"SELECT '\xff';")
    connected = False

    def unexpected_connect(*args, **kwargs):
        nonlocal connected
        connected = True
        raise AssertionError("database connection opened before file validation")

    monkeypatch.setattr(psycopg, "connect", unexpected_connect)

    with pytest.raises(MigrationDiscoveryError, match="UTF-8"):
        run_migrations("postgresql://unused", tmp_path)
    assert connected is False


def test_run_migrations_applies_complete_sql_once(
    tmp_path: Path, disposable_admin_dsn: str
):
    version = "9101"
    table = f"migration_once_{uuid4().hex}"
    _write(
        tmp_path,
        f"{version}_once.sql",
        "DO $$ BEGIN PERFORM 1; PERFORM 2; END $$;\n"
        f'CREATE TABLE app."{table}" (id integer PRIMARY KEY);',
    )
    assert run_migrations(disposable_admin_dsn, tmp_path) == [version]
    assert run_migrations(disposable_admin_dsn, tmp_path) == []
    with psycopg.connect(disposable_admin_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM app.schema_migration WHERE version = %s",
            (version,),
        )
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT to_regclass(%s)", (f"app.{table}",))
        assert cur.fetchone()[0] == f"app.{table}"


def test_run_migrations_rejects_checksum_drift(
    tmp_path: Path, disposable_admin_dsn: str
):
    version = "9102"
    table = f"migration_drift_{uuid4().hex}"
    path = _write(
        tmp_path,
        f"{version}_drift.sql",
        f'CREATE TABLE app."{table}" (id integer);',
    )
    assert run_migrations(disposable_admin_dsn, tmp_path) == [version]
    path.write_text(f'CREATE TABLE app."{table}" (id bigint);', encoding="utf-8")

    with pytest.raises(MigrationChecksumError, match=version):
        run_migrations(disposable_admin_dsn, tmp_path)


def test_invalid_sql_rolls_back_all_ddl_and_version_rows(
    tmp_path: Path, disposable_admin_dsn: str
):
    versions = ["9103", "9104"]
    table = f"migration_rollback_{uuid4().hex}"
    _write(
        tmp_path,
        f"{versions[0]}_create.sql",
        f'CREATE TABLE app."{table}" (id integer);',
    )
    _write(tmp_path, f"{versions[1]}_invalid.sql", "SELECT no_such_migration_fn();")
    with pytest.raises(psycopg.errors.UndefinedFunction):
        run_migrations(disposable_admin_dsn, tmp_path)

    with psycopg.connect(disposable_admin_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f"app.{table}",))
        assert cur.fetchone()[0] is None
        cur.execute("SELECT to_regclass('app.schema_migration')")
        assert cur.fetchone()[0] is None


def test_concurrent_runners_apply_a_version_only_once(
    tmp_path: Path, disposable_admin_dsn: str
):
    version = "9105"
    table = f"migration_concurrent_{uuid4().hex}"
    _write(
        tmp_path,
        f"{version}_concurrent.sql",
        "SELECT pg_sleep(0.1);\n"
        f'CREATE TABLE app."{table}" (id integer PRIMARY KEY);',
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: run_migrations(disposable_admin_dsn, tmp_path), range(2)
            )
        )

    assert sorted(results, key=len) == [[], [version]]
    with psycopg.connect(disposable_admin_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM app.schema_migration WHERE version = %s",
            (version,),
        )
        assert cur.fetchone()[0] == 1


def test_runner_rejects_applied_versions_missing_from_disk_before_applying(
    tmp_path: Path, disposable_admin_dsn: str
):
    applied_path = _write(tmp_path, "9106_applied.sql", "SELECT 1;")
    assert run_migrations(disposable_admin_dsn, tmp_path) == ["9106"]
    applied_path.unlink()
    pending_version = "9107"
    table = f"migration_after_orphan_{uuid4().hex}"
    _write(
        tmp_path,
        f"{pending_version}_must_not_apply.sql",
        f'CREATE TABLE app."{table}" (id integer);',
    )
    with pytest.raises(MigrationDiscoveryError, match="missing from disk.*9106"):
        run_migrations(disposable_admin_dsn, tmp_path)
    with psycopg.connect(disposable_admin_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f"app.{table}",))
        assert cur.fetchone()[0] is None
        cur.execute(
            "SELECT count(*) FROM app.schema_migration WHERE version = %s",
            (pending_version,),
        )
        assert cur.fetchone()[0] == 0


def test_global_chat_migration_converges_on_the_final_schema(
    migrated_admin_dsn: str,
):
    migration_dir = _production_migration_dir()
    run_migrations(migrated_admin_dsn, migration_dir)

    with psycopg.connect(migrated_admin_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
              FROM information_schema.tables
             WHERE table_schema = 'app' AND table_name LIKE 'chat_%'
             ORDER BY table_name
            """
        )
        assert [row[0] for row in cur.fetchall()] == [
            "chat_action",
            "chat_action_item",
            "chat_event",
            "chat_message",
            "chat_plan",
            "chat_thread",
            "chat_transient_result",
        ]
        cur.execute(
            """
            SELECT table_name, column_name
              FROM information_schema.columns
             WHERE table_schema = 'app'
               AND ((table_name = 'board' AND column_name IN ('revision', 'deleted_at'))
                 OR (table_name = 'card' AND column_name = 'deleted_at'))
             ORDER BY table_name, column_name
            """
        )
        assert cur.fetchall() == [
            ("board", "deleted_at"),
            ("board", "revision"),
            ("card", "deleted_at"),
        ]
        cur.execute(
            """
            SELECT count(*)
              FROM pg_constraint constraint_record
              JOIN pg_class source_table ON source_table.oid = constraint_record.conrelid
              JOIN pg_namespace namespace_record ON namespace_record.oid = source_table.relnamespace
             WHERE namespace_record.nspname = 'app'
               AND source_table.relname LIKE 'chat_%'
               AND constraint_record.contype = 'c'
            """
        )
        assert cur.fetchone()[0] >= 5
        cur.execute(
            """
            SELECT count(*)
              FROM pg_constraint constraint_record
              JOIN pg_class source_table ON source_table.oid = constraint_record.conrelid
              JOIN pg_class target_table ON target_table.oid = constraint_record.confrelid
              JOIN pg_namespace namespace_record ON namespace_record.oid = source_table.relnamespace
             WHERE namespace_record.nspname = 'app'
               AND source_table.relname LIKE 'chat_%'
               AND target_table.relname IN ('board', 'card')
            """
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT has_sequence_privilege('app_rw', 'app.chat_event_id_seq', 'USAGE')"
        )
        assert cur.fetchone()[0] is True


def test_migration_ledger_is_admin_owned_exact_and_hidden_from_app_role(
    tmp_path: Path, disposable_admin_dsn: str
):
    with psycopg.connect(disposable_admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("GRANT USAGE, CREATE ON SCHEMA app TO app_rw")
        cur.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA app GRANT ALL ON TABLES TO app_rw"
        )
    run_migrations(disposable_admin_dsn, tmp_path)

    with psycopg.connect(disposable_admin_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT current_user, owner_record.rolname, table_record.relkind,
                   table_record.relrowsecurity, table_record.relforcerowsecurity
              FROM pg_class table_record
              JOIN pg_namespace namespace_record ON namespace_record.oid = table_record.relnamespace
              JOIN pg_roles owner_record ON owner_record.oid = table_record.relowner
             WHERE namespace_record.nspname = 'app'
               AND table_record.relname = 'schema_migration'
            """
        )
        current_user, owner, relkind, rls, force_rls = cur.fetchone()
        assert owner == current_user
        assert (relkind, rls, force_rls) == ("r", False, False)
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable, column_default
              FROM information_schema.columns
             WHERE table_schema = 'app' AND table_name = 'schema_migration'
             ORDER BY ordinal_position
            """
        )
        assert cur.fetchall() == [
            ("version", "text", "NO", None),
            ("checksum", "text", "NO", None),
            ("applied_at", "timestamp with time zone", "NO", "now()"),
        ]

    app_database_dsn = _dsn_for_role(disposable_admin_dsn, os.environ["APP_URL"])
    forbidden = [
        "SELECT * FROM app.schema_migration",
        "INSERT INTO app.schema_migration (version, checksum) VALUES ('9991', 'x')",
        "UPDATE app.schema_migration SET checksum = 'x'",
        "DELETE FROM app.schema_migration",
        "TRUNCATE app.schema_migration",
    ]
    with psycopg.connect(app_database_dsn, autocommit=True) as conn, conn.cursor() as cur:
        for statement in forbidden:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute(statement)


def test_runner_rejects_a_wrong_owner_ledger_without_adopting_it(
    tmp_path: Path, disposable_admin_dsn: str
):
    with psycopg.connect(disposable_admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE app.schema_migration (
                version text PRIMARY KEY,
                checksum text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute("ALTER TABLE app.schema_migration OWNER TO app_rw")

    with pytest.raises(MigrationError, match="owner"):
        run_migrations(disposable_admin_dsn, tmp_path)

    with psycopg.connect(disposable_admin_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT owner_record.rolname
              FROM pg_class table_record
              JOIN pg_namespace namespace_record ON namespace_record.oid = table_record.relnamespace
              JOIN pg_roles owner_record ON owner_record.oid = table_record.relowner
             WHERE namespace_record.nspname = 'app'
               AND table_record.relname = 'schema_migration'
            """
        )
        assert cur.fetchone()[0] == "app_rw"


def test_runner_rejects_a_ledger_with_a_user_trigger(
    tmp_path: Path, disposable_admin_dsn: str
):
    with psycopg.connect(disposable_admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE app.schema_migration (
                version text PRIMARY KEY,
                checksum text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            CREATE FUNCTION public.poison_migration_ledger()
            RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$
            """
        )
        cur.execute(
            """
            CREATE TRIGGER poison_migration_ledger
            BEFORE INSERT ON app.schema_migration
            FOR EACH ROW EXECUTE FUNCTION public.poison_migration_ledger()
            """
        )

    with pytest.raises(MigrationError, match="trigger"):
        run_migrations(disposable_admin_dsn, tmp_path)


@pytest.mark.parametrize(
    "tamper_sql,error",
    [
        ("ALTER TABLE app.schema_migration ADD COLUMN extra text", "columns"),
        ("ALTER TABLE app.schema_migration ENABLE ROW LEVEL SECURITY", "row security"),
        (
            "CREATE POLICY poison_ledger ON app.schema_migration USING (true)",
            "policy",
        ),
        (
            "CREATE RULE poison_ledger AS ON INSERT TO app.schema_migration "
            "DO INSTEAD NOTHING",
            "rewrite rule",
        ),
        ("GRANT SELECT (version) ON app.schema_migration TO app_rw", "column ACL"),
    ],
)
def test_runner_rejects_ledger_catalog_tampering(
    tmp_path: Path,
    disposable_admin_dsn: str,
    tamper_sql: str,
    error: str,
):
    with psycopg.connect(disposable_admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE app.schema_migration (
                version text PRIMARY KEY,
                checksum text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(tamper_sql)

    with pytest.raises(MigrationError, match=error):
        run_migrations(disposable_admin_dsn, tmp_path)


def test_runner_revokes_app_schema_create_before_migrations(
    migrated_admin_dsn: str, migrated_app_dsn: str
):
    with psycopg.connect(migrated_admin_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT has_schema_privilege('app_rw', 'app', 'CREATE')")
        assert cur.fetchone()[0] is False
    with psycopg.connect(migrated_app_dsn, autocommit=True) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("CREATE TABLE app.untrusted_runtime_table (id integer)")


def test_postflight_rejects_an_app_owned_adopted_chat_table(
    disposable_admin_dsn: str,
):
    _create_legacy_app_schema(disposable_admin_dsn)
    app_database_dsn = _dsn_for_role(disposable_admin_dsn, os.environ["APP_URL"])
    with psycopg.connect(app_database_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE app.chat_thread (
                id uuid PRIMARY KEY,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )

    with pytest.raises(MigrationError, match="chat_thread.*owner"):
        run_migrations(disposable_admin_dsn, _production_migration_dir())

    with psycopg.connect(disposable_admin_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('app.schema_migration')")
        assert cur.fetchone()[0] is None


def test_postflight_rejects_an_app_owned_chat_event_sequence(
    disposable_admin_dsn: str,
):
    init_dir = Path("/db/init")
    if not init_dir.exists():
        init_dir = Path(__file__).parents[2] / "db" / "init"
    with psycopg.connect(disposable_admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute((init_dir / "03_app.sql").read_text(encoding="utf-8"))
        cur.execute("ALTER SEQUENCE app.chat_event_id_seq OWNED BY NONE")
        cur.execute("ALTER SEQUENCE app.chat_event_id_seq OWNER TO app_rw")

    with pytest.raises(MigrationError, match="chat_event_id_seq.*owner"):
        run_migrations(disposable_admin_dsn, _production_migration_dir())

    with psycopg.connect(disposable_admin_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('app.schema_migration')")
        assert cur.fetchone()[0] is None


def test_postflight_rejects_wrong_pending_plan_index_definition(
    disposable_admin_dsn: str,
):
    init_dir = Path("/db/init")
    if not init_dir.exists():
        init_dir = Path(__file__).parents[2] / "db" / "init"
    with psycopg.connect(disposable_admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute((init_dir / "03_app.sql").read_text(encoding="utf-8"))
        cur.execute("DROP INDEX app.chat_plan_one_pending_per_thread_idx")
        cur.execute(
            "CREATE UNIQUE INDEX chat_plan_one_pending_per_thread_idx "
            "ON app.chat_plan (thread_id)"
        )

    with pytest.raises(MigrationError, match="chat_plan_one_pending_per_thread_idx"):
        run_migrations(disposable_admin_dsn, _production_migration_dir())

    with psycopg.connect(disposable_admin_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('app.schema_migration')")
        assert cur.fetchone()[0] is None


def test_upgrade_installs_table_and_sequence_default_privileges(
    migrated_admin_dsn: str,
):
    assert _default_acl_privileges(migrated_admin_dsn, "r") == {
        "DELETE",
        "INSERT",
        "REFERENCES",
        "SELECT",
        "TRIGGER",
        "TRUNCATE",
        "UPDATE",
    }
    assert _default_acl_privileges(migrated_admin_dsn, "S") == {
        "SELECT",
        "UPDATE",
        "USAGE",
    }


def test_retention_function_is_admin_owned_invoker_only_and_not_replaceable(
    migrated_admin_dsn: str, migrated_app_dsn: str
):
    with psycopg.connect(migrated_admin_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT current_user, owner_record.rolname, function_record.prosecdef,
                   has_function_privilege(
                       'app_rw',
                       'app.set_chat_action_detached_retention()',
                       'EXECUTE'
                   )
              FROM pg_proc function_record
              JOIN pg_namespace namespace_record ON namespace_record.oid = function_record.pronamespace
              JOIN pg_roles owner_record ON owner_record.oid = function_record.proowner
             WHERE namespace_record.nspname = 'app'
               AND function_record.proname = 'set_chat_action_detached_retention'
               AND pg_get_function_identity_arguments(function_record.oid) = ''
            """
        )
        current_user, owner, security_definer, app_can_execute = cur.fetchone()
        assert owner == current_user
        assert security_definer is False
        assert app_can_execute is False

    with psycopg.connect(migrated_app_dsn, autocommit=True) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                """
                CREATE OR REPLACE FUNCTION app.set_chat_action_detached_retention()
                RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$
                """
            )


def test_migration_rejects_a_wrong_owner_retention_function(
    disposable_admin_dsn: str,
):
    _create_legacy_app_schema(disposable_admin_dsn)
    with psycopg.connect(disposable_admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE FUNCTION app.set_chat_action_detached_retention()
            RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$
            """
        )
        cur.execute(
            "ALTER FUNCTION app.set_chat_action_detached_retention() OWNER TO app_rw"
        )

    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="owner"):
        run_migrations(disposable_admin_dsn, _production_migration_dir())


def test_fresh_install_sql_and_upgrade_migration_converge_securely(
    disposable_admin_dsn: str,
):
    init_dir = Path("/db/init")
    if not init_dir.exists():
        init_dir = Path(__file__).parents[2] / "db" / "init"
    with psycopg.connect(disposable_admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("CREATE SCHEMA stg")
        cur.execute("CREATE SCHEMA ddh")
        cur.execute((init_dir / "03_app.sql").read_text(encoding="utf-8"))
        cur.execute((init_dir / "04_grants.sql").read_text(encoding="utf-8"))

    # Every shipped migration, whatever the set currently is. Pinning the
    # literal ["0001"] made adding a migration a test failure rather than a
    # test, while saying nothing about whether it applied cleanly.
    expected = sorted(p.stem.split("_")[0]
                      for p in _production_migration_dir().glob("*.sql"))
    assert run_migrations(disposable_admin_dsn,
                          _production_migration_dir()) == expected
    # Idempotent: a second pass over a converged database applies nothing.
    assert run_migrations(disposable_admin_dsn, _production_migration_dir()) == []

    with psycopg.connect(disposable_admin_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT has_schema_privilege('app_rw', 'app', 'CREATE')")
        assert cur.fetchone()[0] is False
        cur.execute(
            """
            SELECT count(*)
              FROM information_schema.tables
             WHERE table_schema = 'app' AND table_name LIKE 'chat_%'
            """
        )
        assert cur.fetchone()[0] == 7
        cur.execute(
            """
            SELECT owner_record.rolname = current_user,
                   function_record.prosecdef,
                   has_function_privilege(
                       'app_rw', 'app.set_chat_action_detached_retention()', 'EXECUTE'
                   )
              FROM pg_proc function_record
              JOIN pg_namespace namespace_record ON namespace_record.oid = function_record.pronamespace
              JOIN pg_roles owner_record ON owner_record.oid = function_record.proowner
             WHERE namespace_record.nspname = 'app'
               AND function_record.proname = 'set_chat_action_detached_retention'
            """
        )
        assert cur.fetchone() == (True, False, False)
    assert _default_acl_privileges(disposable_admin_dsn, "r") == {
        "DELETE",
        "INSERT",
        "REFERENCES",
        "SELECT",
        "TRIGGER",
        "TRUNCATE",
        "UPDATE",
    }
    assert _default_acl_privileges(disposable_admin_dsn, "S") == {
        "SELECT",
        "UPDATE",
        "USAGE",
    }


def test_deleting_a_thread_detaches_every_action_status_and_retains_children(
    migrated_admin_dsn: str,
):
    statuses = [
        "queued",
        "running",
        "completed",
        "completed_with_errors",
        "stopped",
        "failed",
        "cancelled",
        "undone",
    ]
    action_ids: list[str] = []
    thread_ids: list[str] = []
    try:
        with psycopg.connect(
            migrated_admin_dsn, autocommit=True
        ) as conn, conn.cursor() as cur:
            for ordinal, status in enumerate(statuses):
                thread_id = str(uuid4())
                thread_ids.append(thread_id)
                action_id = str(uuid4())
                action_ids.append(action_id)
                cur.execute("INSERT INTO app.chat_thread (id) VALUES (%s)", (thread_id,))
                cur.execute(
                    """
                    INSERT INTO app.chat_action
                        (id, thread_id, plan_id, provider, model, effects, status)
                    VALUES (%s, %s, %s, 'nvidia', 'deepseek', %s, %s)
                    """,
                    (action_id, thread_id, str(uuid4()), Jsonb({}), status),
                )
                cur.execute(
                    """
                    INSERT INTO app.chat_action_item
                        (id, action_id, ordinal, request, status)
                    VALUES (%s, %s, %s, %s, 'queued')
                    """,
                    (str(uuid4()), action_id, ordinal, Jsonb({"question": "safe"})),
                )
                cur.execute(
                    "INSERT INTO app.chat_event (action_id, kind, payload) VALUES (%s, 'plan', %s)",
                    (action_id, Jsonb({"version": 1})),
                )
                cur.execute("DELETE FROM app.chat_thread WHERE id = %s", (thread_id,))

            cur.execute(
                """
                SELECT id::text, thread_id, updated_at, purge_after,
                       purge_after - updated_at
                  FROM app.chat_action
                 WHERE id = ANY(%s::uuid[])
                """,
                (action_ids,),
            )
            detached = cur.fetchall()
            assert len(detached) == len(statuses)
            assert all(row[1] is None for row in detached)
            assert all(row[3] is not None for row in detached)
            assert all(row[4] == timedelta(days=30) for row in detached)
            cur.execute(
                "SELECT count(*) FROM app.chat_action_item WHERE action_id = ANY(%s::uuid[])",
                (action_ids,),
            )
            assert cur.fetchone()[0] == len(statuses)
            cur.execute(
                "SELECT count(*) FROM app.chat_event WHERE action_id = ANY(%s::uuid[])",
                (action_ids,),
            )
            assert cur.fetchone()[0] == len(statuses)
    finally:
        with psycopg.connect(
            migrated_admin_dsn, autocommit=True
        ) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM app.chat_action WHERE id = ANY(%s::uuid[])", (action_ids,))
            cur.execute("DELETE FROM app.chat_thread WHERE id = ANY(%s::uuid[])", (thread_ids,))


def test_explicit_detachment_retention_is_preserved_and_capped(
    migrated_admin_dsn: str,
):
    thread_id = str(uuid4())
    action_id = str(uuid4())
    updated_at = datetime.now(timezone.utc).replace(microsecond=0)
    purge_after = updated_at + timedelta(days=7)
    with psycopg.connect(
        migrated_admin_dsn, autocommit=True
    ) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO app.chat_thread (id) VALUES (%s)", (thread_id,))
        cur.execute(
            """
            INSERT INTO app.chat_action
                (id, thread_id, plan_id, provider, model, effects, status)
            VALUES (%s, %s, %s, 'nvidia', 'deepseek', %s, 'completed')
            """,
            (action_id, thread_id, str(uuid4()), Jsonb({})),
        )
        try:
            cur.execute(
                """
                UPDATE app.chat_action
                   SET thread_id = NULL, updated_at = %s, purge_after = %s
                 WHERE id = %s
                RETURNING updated_at, purge_after
                """,
                (updated_at, purge_after, action_id),
            )
            assert cur.fetchone() == (updated_at, purge_after)

            with pytest.raises(psycopg.errors.CheckViolation):
                cur.execute(
                    """
                    INSERT INTO app.chat_action
                        (id, plan_id, provider, model, effects, status,
                         updated_at, purge_after)
                    VALUES (%s, %s, 'nvidia', 'deepseek', %s, 'completed', %s, %s)
                    """,
                    (
                        str(uuid4()),
                        str(uuid4()),
                        Jsonb({}),
                        updated_at,
                        updated_at + timedelta(days=31),
                    ),
                )
        finally:
            cur.execute("DELETE FROM app.chat_action WHERE id = %s", (action_id,))
            cur.execute("DELETE FROM app.chat_thread WHERE id = %s", (thread_id,))


def test_chat_foreign_keys_and_indexes_match_retention_contract(
    migrated_admin_dsn: str,
):
    with psycopg.connect(migrated_admin_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT source_table.relname, target_table.relname, constraint_record.confdeltype
              FROM pg_constraint constraint_record
              JOIN pg_class source_table ON source_table.oid = constraint_record.conrelid
              JOIN pg_class target_table ON target_table.oid = constraint_record.confrelid
              JOIN pg_namespace namespace_record ON namespace_record.oid = source_table.relnamespace
             WHERE namespace_record.nspname = 'app'
               AND source_table.relname LIKE 'chat_%'
               AND constraint_record.contype = 'f'
             ORDER BY source_table.relname, target_table.relname
            """
        )
        assert cur.fetchall() == [
            ("chat_action", "chat_thread", "n"),
            ("chat_action_item", "chat_action", "c"),
            ("chat_event", "chat_action", "c"),
            ("chat_message", "chat_thread", "c"),
            ("chat_plan", "chat_thread", "c"),
            ("chat_transient_result", "chat_thread", "c"),
        ]
        cur.execute(
            """
            SELECT indexname, indexdef
              FROM pg_indexes
             WHERE schemaname = 'app' AND tablename IN ('chat_plan', 'chat_action_item')
            """
        )
        indexes = dict(cur.fetchall())
        assert "chat_plan_one_pending_per_thread_idx" in indexes
        assert "WHERE (status = 'pending'::text)" in indexes[
            "chat_plan_one_pending_per_thread_idx"
        ]
        assert "chat_action_item_action_ordinal_idx" not in indexes
        cur.execute(
            """
            SELECT pg_get_constraintdef(constraint_record.oid)
              FROM pg_constraint constraint_record
              JOIN pg_class table_record ON table_record.oid = constraint_record.conrelid
              JOIN pg_namespace namespace_record ON namespace_record.oid = table_record.relnamespace
             WHERE namespace_record.nspname = 'app'
               AND table_record.relname = 'chat_action'
               AND constraint_record.conname = 'chat_action_retention_check'
            """
        )
        assert "30 days" in cur.fetchone()[0]


def test_chat_configuration_defaults_are_privacy_preserving():
    fields = Settings.model_fields

    assert fields["migration_dir"].default == Path("/db/migrations")
    assert fields["chat_enabled"].default is False
    assert fields["chat_sees_data"].default is False
    assert fields["chat_max_rows"].default == 2_000
    assert fields["chat_max_context_chars"].default == 60_000
    # Messages, not exchanges. Six was three turns, which loses a
    # clarifying question and its answer inside one short back-and-forth.
    assert fields["chat_history_turns"].default == 12
    assert fields["chat_transient_ttl_seconds"].default == 900
    assert fields["chat_tombstone_days"].default == 30


@pytest.mark.parametrize(
    "field,value",
    [
        ("chat_max_rows", 0),
        ("chat_max_context_chars", 0),
        ("chat_history_turns", 0),
        ("chat_transient_ttl_seconds", 0),
        ("chat_tombstone_days", 0),
        ("chat_tombstone_days", 31),
    ],
)
def test_chat_configuration_rejects_invalid_limits(field: str, value: int):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_lifespan_runs_migrations_before_opening_pools(monkeypatch: pytest.MonkeyPatch):
    from app import main

    calls: list[str] = []
    monkeypatch.setattr(main, "run_migrations", lambda *_: calls.append("migrate"))
    monkeypatch.setattr(main, "open_pools", lambda: calls.append("open"))
    monkeypatch.setattr(main, "ensure_seeded", lambda: calls.append("seed"))
    monkeypatch.setattr(main, "close_pools", lambda: calls.append("close"))

    async def exercise_lifespan() -> None:
        async with main.lifespan(main.app):
            assert calls == ["migrate", "open", "seed"]

    asyncio.run(exercise_lifespan())
    assert calls == ["migrate", "open", "seed", "close"]


@pytest.mark.parametrize("failure", ["open", "seed", "body"])
def test_lifespan_closes_partially_open_pools_on_every_failure(
    monkeypatch: pytest.MonkeyPatch, failure: str
):
    from app import main

    calls: list[str] = []
    monkeypatch.setattr(main, "run_migrations", lambda *_: calls.append("migrate"))

    def open_pools() -> None:
        calls.append("open")
        if failure == "open":
            raise RuntimeError("open failed after partial initialization")

    def seed() -> None:
        calls.append("seed")
        if failure == "seed":
            raise RuntimeError("seed failed")

    monkeypatch.setattr(main, "open_pools", open_pools)
    monkeypatch.setattr(main, "ensure_seeded", seed)
    monkeypatch.setattr(main, "close_pools", lambda: calls.append("close"))

    async def exercise_lifespan() -> None:
        async with main.lifespan(main.app):
            calls.append("body")
            if failure == "body":
                raise RuntimeError("request loop failed")

    with pytest.raises(RuntimeError):
        asyncio.run(exercise_lifespan())
    assert calls[0:2] == ["migrate", "open"]
    assert calls[-1] == "close"
