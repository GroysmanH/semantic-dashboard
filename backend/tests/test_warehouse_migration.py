"""Fresh and upgraded warehouses converge on one operational schema."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest

from app.migrations import run_migrations


LEGACY_WAREHOUSE_SQL = """
CREATE TABLE ddh.dim_wells (
    well_id integer PRIMARY KEY,
    well_name text NOT NULL,
    region_name text NOT NULL,
    field_name text NOT NULL,
    spud_date date,
    well_type text,
    latitude numeric(9, 6),
    longitude numeric(9, 6)
);
CREATE TABLE ddh.fct_well_interventions (
    intervention_id integer PRIMARY KEY,
    well_id integer NOT NULL,
    intervention_date date NOT NULL,
    intervention_type text NOT NULL,
    status text NOT NULL,
    net_gain_bbl numeric(12, 2),
    cost_usd numeric(14, 2),
    contractor text
);
CREATE TABLE ddh.fct_production_daily (
    well_id integer NOT NULL,
    reading_date date NOT NULL,
    oil_bbl numeric(12, 2),
    gas_mcf numeric(12, 2),
    water_bbl numeric(12, 2),
    downtime_hours numeric(6, 2),
    PRIMARY KEY (well_id, reading_date)
);
CREATE TABLE stg.wells_raw (
    well_id text, wellname text, region text, fieldname text,
    spud_dt text, welltype text
);
CREATE TABLE stg.interventions_raw (
    job_id text, well_id text, job_dt text, job_type text, stat text,
    gain text, cost text, contractor text
);
"""

WAREHOUSE_TABLES = (
    "dim_wells",
    "fct_downtime_events",
    "fct_field_targets_monthly",
    "fct_production_daily",
    "fct_well_interventions",
    "mart_production_performance_monthly",
)
STAGING_TABLES = (
    "downtime_events_raw",
    "field_targets_monthly_raw",
    "interventions_raw",
    "production_daily_raw",
    "wells_raw",
)


def _repo_file(relative: str) -> Path:
    container = Path("/") / relative
    return container if container.exists() else Path(__file__).parents[2] / relative


def _migration_dir() -> Path:
    return _repo_file("db/migrations")


def _init(name: str) -> str:
    return _repo_file(f"db/init/{name}").read_text(encoding="utf-8")


def _new_database(admin_dsn: str, prefix: str) -> tuple[str, str]:
    name = f"{prefix}_{uuid4().hex}"
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return name, make_conninfo(admin_dsn, dbname=name)


@pytest.fixture
def warehouse_schema_dsns():
    admin_dsn = os.environ["ADMIN_URL"]
    databases = [
        _new_database(admin_dsn, "warehouse_fresh"),
        _new_database(admin_dsn, "warehouse_upgrade"),
    ]
    fresh_dsn, upgrade_dsn = databases[0][1], databases[1][1]
    try:
        with psycopg.connect(fresh_dsn, autocommit=True) as conn:
            for name in ("01_schemas.sql", "02_warehouse.sql", "03_app.sql", "04_grants.sql"):
                conn.execute(_init(name))
        # Capture the init contract before any migration can repair it.  The
        # convergence assertion below compares this immutable snapshot with
        # the upgraded legacy warehouse.
        fresh_init_signature = _warehouse_signature(fresh_dsn)

        with psycopg.connect(upgrade_dsn, autocommit=True) as conn:
            conn.execute(_init("01_schemas.sql"))
            conn.execute(LEGACY_WAREHOUSE_SQL)
            conn.execute(_init("03_app.sql"))
            conn.execute(_init("04_grants.sql"))
            conn.execute(
                """
                INSERT INTO ddh.fct_well_interventions
                    (intervention_id, well_id, intervention_date,
                     intervention_type, status)
                VALUES
                    (1, 1, DATE '2025-01-01', 'WORKOVER', '1'),
                    (2, 1, DATE '2025-01-02', 'WORKOVER', '2'),
                    (3, 1, DATE '2025-01-03', 'WORKOVER', '3');
                INSERT INTO stg.interventions_raw
                    (job_id, well_id, job_dt, job_type, stat)
                VALUES
                    ('1', '1', '2025-01-01', 'WORKOVER', '1'),
                    ('2', '1', '2025-01-02', 'WORKOVER', '2'),
                    ('3', '1', '2025-01-03', 'WORKOVER', '3');
                """
            )
        run_migrations(upgrade_dsn, _migration_dir())
        yield fresh_dsn, upgrade_dsn, fresh_init_signature
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            for name, _dsn in databases:
                conn.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
                )


def _warehouse_signature(dsn: str):
    with psycopg.connect(dsn) as conn:
        columns = conn.execute(
            """
            SELECT table_schema, table_name, column_name, data_type,
                   is_nullable, numeric_precision, numeric_scale
              FROM information_schema.columns
             WHERE (table_schema = 'ddh' AND table_name = ANY(%s))
                OR (table_schema = 'stg' AND table_name = ANY(%s))
             ORDER BY table_schema, table_name, ordinal_position
            """,
            (list(WAREHOUSE_TABLES), list(STAGING_TABLES)),
        ).fetchall()
        indexes = conn.execute(
            """
            SELECT schemaname, tablename, indexname, indexdef
              FROM pg_indexes
             WHERE schemaname = 'ddh' AND tablename = ANY(%s)
             ORDER BY tablename, indexname
            """,
            (list(WAREHOUSE_TABLES),),
        ).fetchall()
    return columns, indexes


def test_fresh_ddl_and_upgrade_migration_have_identical_warehouse_shapes(
    warehouse_schema_dsns,
):
    _fresh_dsn, upgrade_dsn, fresh_init_signature = warehouse_schema_dsns

    upgraded = _warehouse_signature(upgrade_dsn)
    assert fresh_init_signature == upgraded
    assert {(row[0], row[1]) for row in fresh_init_signature[0]} == {
        *{("ddh", table) for table in WAREHOUSE_TABLES},
        *{("stg", table) for table in STAGING_TABLES},
    }
    dim_columns = {
        row[2] for row in fresh_init_signature[0]
        if row[0:2] == ("ddh", "dim_wells")
    }
    assert {
        "asset_name", "operator_name", "basin_name",
        "operating_status", "lift_method",
    } <= dim_columns


def test_upgrade_keeps_raw_codes_and_maps_only_the_curated_status(
    warehouse_schema_dsns,
):
    _fresh_dsn, upgrade_dsn, _fresh_init_signature = warehouse_schema_dsns

    with psycopg.connect(upgrade_dsn) as conn:
        raw = conn.execute(
            "SELECT stat FROM stg.interventions_raw ORDER BY job_id"
        ).fetchall()
        curated = conn.execute(
            "SELECT status FROM ddh.fct_well_interventions ORDER BY intervention_id"
        ).fetchall()

    assert raw == [("1",), ("2",), ("3",)]
    assert curated == [("COMPLETED",), ("CANCELLED",), ("IN_PROGRESS",)]


def test_operational_migration_sql_is_itself_idempotent_and_readable(
    warehouse_schema_dsns,
):
    fresh_dsn, _upgrade_dsn, fresh_init_signature = warehouse_schema_dsns
    migration = _migration_dir() / "0004_realistic_warehouse.sql"

    with psycopg.connect(fresh_dsn, autocommit=True) as conn:
        conn.execute(migration.read_text(encoding="utf-8"))
        conn.execute(migration.read_text(encoding="utf-8"))
        rows = conn.execute(
            """
            SELECT table_name
              FROM information_schema.tables
             WHERE table_schema = 'ddh' AND table_name = ANY(%s)
             ORDER BY table_name
            """,
            (list(WAREHOUSE_TABLES),),
        ).fetchall()
        privileges = [
            conn.execute(
                "SELECT has_table_privilege('warehouse_ro', %s, 'SELECT')",
                (f"ddh.{table}",),
            ).fetchone()[0]
            for table in WAREHOUSE_TABLES
        ]

    assert _warehouse_signature(fresh_dsn) == fresh_init_signature
    assert rows == [(table,) for table in WAREHOUSE_TABLES]
    assert all(privileges)
