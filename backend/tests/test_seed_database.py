"""Database boundary tests use a one-month profile, never the 1.4M-row set."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
import importlib.util
import os
from pathlib import Path
import sys
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest


def _load_seed():
    path = Path("/db/seed/seed.py")
    if not path.exists():
        path = Path(__file__).parents[2] / "db/seed/seed.py"
    spec = importlib.util.spec_from_file_location("warehouse_seed_database", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


seed = _load_seed()


def _load_provisioner():
    path = Path("/scripts/ensure_realistic_db.py")
    if not path.exists():
        path = Path(__file__).parents[2] / "scripts/ensure_realistic_db.py"
    spec = importlib.util.spec_from_file_location("realistic_reuse_guard", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


provision = _load_provisioner()


def _init(name: str) -> str:
    path = Path("/db/init") / name
    if not path.exists():
        path = Path(__file__).parents[2] / "db/init" / name
    return path.read_text(encoding="utf-8")


@pytest.fixture
def empty_warehouse_dsn():
    admin_dsn = os.environ["ADMIN_URL"]
    database = f"seed_profile_{uuid4().hex}"
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    dsn = make_conninfo(admin_dsn, dbname=database)
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            for name in ("01_schemas.sql", "02_warehouse.sql"):
                conn.execute(_init(name))
        yield dsn
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
            )


def _small_realistic_config():
    return replace(
        seed.build_seed_config("realistic"),
        production_start=date(2026, 7, 1),
        production_end=date(2026, 8, 1),
        intervention_start=date(2026, 7, 1),
        intervention_end=date(2026, 8, 1),
        intervention_count=300,
        downtime_event_count=30,
    )


def test_realistic_seed_populates_all_mirrors_and_curated_tables(
    empty_warehouse_dsn,
):
    config = _small_realistic_config()

    summary = seed.seed_database(empty_warehouse_dsn, config)

    assert summary.wells == 600
    assert summary.interventions == 300
    assert summary.production == 480 * 31
    assert summary.downtime_events == 30
    assert summary.targets == 24
    assert summary.performance == 24
    with psycopg.connect(empty_warehouse_dsn) as conn:
        counts = conn.execute(
            """
            SELECT
              (SELECT count(*) FROM stg.production_daily_raw),
              (SELECT count(*) FROM ddh.fct_production_daily),
              (SELECT count(*) FROM stg.downtime_events_raw),
              (SELECT count(*) FROM ddh.fct_downtime_events),
              (SELECT count(*) FROM stg.field_targets_monthly_raw),
              (SELECT count(*) FROM ddh.fct_field_targets_monthly),
              (SELECT count(*) FROM ddh.mart_production_performance_monthly)
            """
        ).fetchone()
        raw_codes = dict(conn.execute(
            """
            SELECT stat, count(*) FROM stg.interventions_raw
             WHERE stat IN ('1', '2', '3') GROUP BY stat ORDER BY stat
            """
        ).fetchall())
        curated_codes = conn.execute(
            """
            SELECT count(*) FROM ddh.fct_well_interventions
             WHERE status IN ('1', '2', '3')
            """
        ).fetchone()[0]
        mapped = dict(conn.execute(
            """
            SELECT status, count(*) FROM ddh.fct_well_interventions
             WHERE intervention_id % 100 = 0 GROUP BY status ORDER BY status
            """
        ).fetchall())

    assert counts == (14_880, 14_880, 30, 30, 24, 24, 24)
    assert raw_codes == {"1": 1, "2": 1, "3": 1}
    assert curated_codes == 0
    assert mapped == {"CANCELLED": 1, "COMPLETED": 1, "IN_PROGRESS": 1}


def test_realistic_seed_refuses_to_truncate_a_populated_database(
    empty_warehouse_dsn,
):
    config = _small_realistic_config()
    seed.seed_database(empty_warehouse_dsn, config)

    with pytest.raises(RuntimeError, match="already contains warehouse data"):
        seed.seed_database(empty_warehouse_dsn, config)

    with psycopg.connect(empty_warehouse_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM ddh.dim_wells").fetchone()[0] == 600
        assert conn.execute(
            "SELECT count(*) FROM ddh.fct_production_daily"
        ).fetchone()[0] == 14_880


def _short_profile_expectations(config):
    return provision.ProfileExpectations.from_seed_config(config)


def _all_profile_counts(dsn: str) -> tuple[int, ...]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            """
            SELECT
              (SELECT count(*) FROM ddh.dim_wells),
              (SELECT count(*) FROM ddh.fct_well_interventions),
              (SELECT count(*) FROM ddh.fct_production_daily),
              (SELECT count(*) FROM ddh.fct_downtime_events),
              (SELECT count(*) FROM ddh.fct_field_targets_monthly),
              (SELECT count(*) FROM ddh.mart_production_performance_monthly),
              (SELECT count(*) FROM stg.wells_raw),
              (SELECT count(*) FROM stg.interventions_raw),
              (SELECT count(*) FROM stg.production_daily_raw),
              (SELECT count(*) FROM stg.downtime_events_raw),
              (SELECT count(*) FROM stg.field_targets_monthly_raw)
            """
        ).fetchone()


def test_reuse_guard_accepts_an_intact_short_realistic_profile(
    empty_warehouse_dsn,
):
    config = _small_realistic_config()
    seed.seed_database(empty_warehouse_dsn, config)

    assert provision.validate_reuse_state(
        empty_warehouse_dsn,
        _short_profile_expectations(config),
    ) is True


@pytest.mark.parametrize(
    ("name", "mutation"),
    [
        (
            "missing staging row",
            """
            DELETE FROM stg.downtime_events_raw
             WHERE ctid = (SELECT min(ctid) FROM stg.downtime_events_raw)
            """,
        ),
        (
            "same-count staging corruption",
            """
            UPDATE stg.production_daily_raw
               SET oil_vol = oil_vol || '-CORRUPT'
             WHERE ctid = (SELECT min(ctid) FROM stg.production_daily_raw)
            """,
        ),
        (
            "intervention numeric staging corruption",
            """
            UPDATE stg.interventions_raw
               SET cost = 'CORRUPT'
             WHERE job_id = '1'
            """,
        ),
        (
            "downtime numeric staging corruption",
            """
            UPDATE stg.downtime_events_raw
               SET duration_hrs = 'CORRUPT'
             WHERE event_id = '1'
            """,
        ),
        (
            "target numeric staging corruption",
            """
            UPDATE stg.field_targets_monthly_raw
               SET oil_target = 'CORRUPT'
             WHERE ctid = (SELECT min(ctid) FROM stg.field_targets_monthly_raw)
            """,
        ),
        (
            "wrong producer injector mix",
            """
            UPDATE ddh.dim_wells SET well_type = 'INJECTOR' WHERE well_id = 1;
            UPDATE stg.wells_raw SET welltype = 'INJECTOR' WHERE well_id = '1'
            """,
        ),
        (
            "broken curated status mapping",
            """
            UPDATE ddh.fct_well_interventions
               SET status = 'CANCELLED'
             WHERE intervention_id = 100
            """,
        ),
        (
            "coherently corrupted performance mart",
            """
            UPDATE ddh.mart_production_performance_monthly
               SET actual_oil_bbl = actual_oil_bbl + 100,
                   target_oil_bbl = target_oil_bbl + 50,
                   variance_oil_bbl = (actual_oil_bbl + 100)
                                      - (target_oil_bbl + 50),
                   attainment_pct = round(
                       (actual_oil_bbl + 100) / (target_oil_bbl + 50) * 100,
                       4
                   )
             WHERE (field_name, performance_month) = (
                 SELECT field_name, performance_month
                   FROM ddh.mart_production_performance_monthly
                  ORDER BY field_name, performance_month
                  LIMIT 1
             )
            """,
        ),
    ],
)
def test_reuse_guard_refuses_corruption_without_truncating(
    empty_warehouse_dsn,
    name,
    mutation,
):
    config = _small_realistic_config()
    seed.seed_database(empty_warehouse_dsn, config)
    with psycopg.connect(empty_warehouse_dsn) as conn:
        conn.execute(mutation)
    counts_after_corruption = _all_profile_counts(empty_warehouse_dsn)

    with pytest.raises(RuntimeError, match="refusing to truncate"):
        provision.validate_reuse_state(
            empty_warehouse_dsn,
            _short_profile_expectations(config),
        )

    assert _all_profile_counts(empty_warehouse_dsn) == counts_after_corruption, name
