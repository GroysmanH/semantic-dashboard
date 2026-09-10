"""Provision the opt-in realistic warehouse in its own database."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import os
from pathlib import Path
import subprocess
import sys
import time

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo


REALISTIC_DB = "semantic_realistic"
INIT_FILES = (
    "01_schemas.sql",
    "02_warehouse.sql",
    "03_app.sql",
    "04_grants.sql",
)


@dataclass(frozen=True)
class ProfileExpectations:
    production_start: date
    production_end: date
    well_count: int
    producer_count: int
    injector_count: int
    observation_count: int
    field_count: int
    wells_per_field: int
    intervention_count: int
    production_count: int
    downtime_event_count: int
    target_count: int
    performance_count: int

    @classmethod
    def realistic(cls, end_date: date) -> "ProfileExpectations":
        start_date = end_date.replace(year=end_date.year - 8)
        last_included = end_date.fromordinal(end_date.toordinal() - 1)
        month_count = (
            (last_included.year - start_date.year) * 12
            + last_included.month - start_date.month
            + 1
        )
        return cls(
            production_start=start_date,
            production_end=end_date,
            well_count=600,
            producer_count=480,
            injector_count=90,
            observation_count=30,
            field_count=24,
            wells_per_field=25,
            intervention_count=16_000,
            production_count=480 * (end_date - start_date).days,
            downtime_event_count=12_000,
            target_count=24 * month_count,
            performance_count=24 * month_count,
        )

    @classmethod
    def from_seed_config(cls, config) -> "ProfileExpectations":
        """Build expectations from a duck-typed config for focused tests."""
        return cls(
            production_start=config.production_start,
            production_end=config.production_end,
            well_count=config.well_count,
            producer_count=config.producer_count,
            injector_count=config.injector_count,
            observation_count=config.observation_count,
            field_count=config.field_count,
            wells_per_field=config.well_count // config.field_count,
            intervention_count=config.intervention_count,
            production_count=config.production_row_count,
            downtime_event_count=config.downtime_event_count,
            target_count=config.target_row_count,
            performance_count=config.target_row_count,
        )

    @property
    def first_month(self) -> date:
        return self.production_start.replace(day=1)

    @property
    def last_day(self) -> date:
        return self.production_end.fromordinal(self.production_end.toordinal() - 1)

    @property
    def last_month(self) -> date:
        return self.last_day.replace(day=1)

    @property
    def raw_code_counts(self) -> tuple[int, int, int]:
        coded = self.intervention_count // 100
        cycles, remainder = divmod(coded, 3)
        return (
            cycles + (1 if remainder >= 1 else 0),
            cycles + (1 if remainder >= 2 else 0),
            cycles,
        )


def require_confirmation(value: str) -> None:
    if value != REALISTIC_DB:
        raise ValueError(
            "realistic seeding is opt-in; rerun with CONFIRM=semantic_realistic"
        )


def _paths() -> tuple[Path, Path, Path, Path]:
    if Path("/db/init").exists():
        return (
            Path("/db/init"), Path("/db/migrations"),
            Path("/db/seed/seed.py"), Path("/app"),
        )
    root = Path(__file__).parents[1]
    return root / "db/init", root / "db/migrations", root / "db/seed/seed.py", root / "backend"


def _database_exists(admin_url: str) -> bool:
    with psycopg.connect(admin_url, autocommit=True) as conn:
        return conn.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = %s)",
            (REALISTIC_DB,),
        ).fetchone()[0]


def _create_database(admin_url: str) -> None:
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(REALISTIC_DB)))


def _has_schema(dsn: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT to_regclass('app.board')").fetchone()
        return row is not None and row[0] is not None


def _apply_fresh_ddl(dsn: str, init_dir: Path) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        for name in INIT_FILES:
            conn.execute((init_dir / name).read_text(encoding="utf-8"))


def _warehouse_has_rows(dsn: str) -> bool:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            """
            SELECT EXISTS (SELECT 1 FROM ddh.dim_wells)
                OR EXISTS (SELECT 1 FROM ddh.fct_well_interventions)
                OR EXISTS (SELECT 1 FROM ddh.fct_production_daily)
                OR EXISTS (SELECT 1 FROM ddh.fct_downtime_events)
                OR EXISTS (SELECT 1 FROM ddh.fct_field_targets_monthly)
                OR EXISTS (SELECT 1 FROM ddh.mart_production_performance_monthly)
                OR EXISTS (SELECT 1 FROM stg.wells_raw)
                OR EXISTS (SELECT 1 FROM stg.interventions_raw)
                OR EXISTS (SELECT 1 FROM stg.production_daily_raw)
                OR EXISTS (SELECT 1 FROM stg.downtime_events_raw)
                OR EXISTS (SELECT 1 FROM stg.field_targets_monthly_raw)
            """
        ).fetchone()[0]


def _mirror_signatures_match(conn: psycopg.Connection) -> bool:
    """Compare landed and curated mirrors without materializing their rows."""
    statements = (
        """
        SELECT
          (SELECT COALESCE(sum(hashtextextended(concat_ws(E'\\x1f',
                    coalesce(well_id, '<NULL>'), coalesce(wellname, '<NULL>'),
                    coalesce(region, '<NULL>'), coalesce(fieldname, '<NULL>'),
                    coalesce(spud_dt, '<NULL>'), coalesce(welltype, '<NULL>'),
                    coalesce(asset, '<NULL>'), coalesce(operator_name, '<NULL>'),
                    coalesce(basin, '<NULL>'), coalesce(op_status, '<NULL>'),
                    coalesce(lift, '<NULL>')), 42)::numeric), 0)
             FROM stg.wells_raw)
          =
          (SELECT COALESCE(sum(hashtextextended(concat_ws(E'\\x1f',
                    well_id::text, well_name, region_name, field_name,
                    coalesce(spud_date::text, '<NULL>'),
                    coalesce(well_type, '<NULL>'), coalesce(asset_name, '<NULL>'),
                    coalesce(operator_name, '<NULL>'), coalesce(basin_name, '<NULL>'),
                    coalesce(operating_status, '<NULL>'), coalesce(lift_method, '<NULL>')),
                    42)::numeric), 0)
             FROM ddh.dim_wells)
        """,
        """
        SELECT
          (SELECT COALESCE(sum(hashtextextended(concat_ws(E'\\x1f',
                    coalesce(job_id, '<NULL>'), coalesce(well_id, '<NULL>'),
                    coalesce(job_dt, '<NULL>'), coalesce(job_type, '<NULL>'),
                    coalesce(CASE stat
                        WHEN '1' THEN 'COMPLETED'
                        WHEN '2' THEN 'CANCELLED'
                        WHEN '3' THEN 'IN_PROGRESS'
                        ELSE upper(stat)
                    END, '<NULL>'),
                    coalesce(CASE
                        WHEN gain ~ '^[+-]?[0-9]+([.][0-9]+)?$'
                        THEN trim_scale(gain::numeric)::text
                        ELSE '<INVALID:' || gain || '>'
                    END, '<NULL>'),
                    coalesce(CASE
                        WHEN cost ~ '^[+-]?[0-9]+([.][0-9]+)?$'
                        THEN trim_scale(cost::numeric)::text
                        ELSE '<INVALID:' || cost || '>'
                    END, '<NULL>'),
                    coalesce(contractor, '<NULL>')), 42)::numeric), 0)
             FROM stg.interventions_raw)
          =
          (SELECT COALESCE(sum(hashtextextended(concat_ws(E'\\x1f',
                    intervention_id::text, well_id::text, intervention_date::text,
                    intervention_type, status,
                    coalesce(trim_scale(net_gain_bbl)::text, '<NULL>'),
                    coalesce(trim_scale(cost_usd)::text, '<NULL>'),
                    coalesce(contractor, '<NULL>')),
                    42)::numeric), 0)
             FROM ddh.fct_well_interventions)
        """,
        """
        SELECT
          (SELECT COALESCE(sum(hashtextextended(concat_ws(E'\\x1f',
                    coalesce(well_id, '<NULL>'), coalesce(reading_dt, '<NULL>'),
                    coalesce(oil_vol, '<NULL>'), coalesce(gas_vol, '<NULL>'),
                    coalesce(water_vol, '<NULL>'), coalesce(down_hrs, '<NULL>')),
                    42)::numeric), 0)
             FROM stg.production_daily_raw)
          =
          (SELECT COALESCE(sum(hashtextextended(concat_ws(E'\\x1f',
                    well_id::text, reading_date::text, coalesce(oil_bbl::text, '<NULL>'),
                    coalesce(gas_mcf::text, '<NULL>'), coalesce(water_bbl::text, '<NULL>'),
                    coalesce(downtime_hours::text, '<NULL>')), 42)::numeric), 0)
             FROM ddh.fct_production_daily)
        """,
        """
        SELECT
          (SELECT COALESCE(sum(hashtextextended(concat_ws(E'\\x1f',
                    coalesce(event_id, '<NULL>'), coalesce(well_id, '<NULL>'),
                    coalesce(start_ts, '<NULL>'), coalesce(end_ts, '<NULL>'),
                    coalesce(CASE
                        WHEN duration_hrs ~ '^[+-]?[0-9]+([.][0-9]+)?$'
                        THEN trim_scale(duration_hrs::numeric)::text
                        ELSE '<INVALID:' || duration_hrs || '>'
                    END, '<NULL>'),
                    coalesce(reason, '<NULL>'), coalesce(plan_stat, '<NULL>')),
                    42)::numeric), 0)
             FROM stg.downtime_events_raw)
          =
          (SELECT COALESCE(sum(hashtextextended(concat_ws(E'\\x1f',
                    event_id::text, well_id::text, event_start::text, event_end::text,
                    trim_scale(duration_hours)::text,
                    downtime_category, planning_status), 42)::numeric), 0)
             FROM ddh.fct_downtime_events)
        """,
        """
        SELECT
          (SELECT COALESCE(sum(hashtextextended(concat_ws(E'\\x1f',
                    coalesce(fieldname, '<NULL>'), coalesce(target_dt, '<NULL>'),
                    coalesce(asset, '<NULL>'), coalesce(region, '<NULL>'),
                    coalesce(operator, '<NULL>'), coalesce(basin, '<NULL>'),
                    coalesce(CASE
                        WHEN oil_target ~ '^[+-]?[0-9]+([.][0-9]+)?$'
                        THEN trim_scale(oil_target::numeric)::text
                        ELSE '<INVALID:' || oil_target || '>'
                    END, '<NULL>')),
                    42)::numeric), 0)
             FROM stg.field_targets_monthly_raw)
          =
          (SELECT COALESCE(sum(hashtextextended(concat_ws(E'\\x1f',
                    field_name, target_month::text, asset_name, region_name,
                    operator_name, basin_name, trim_scale(target_oil_bbl)::text),
                    42)::numeric), 0)
             FROM ddh.fct_field_targets_monthly)
        """,
    )
    return all(bool(conn.execute(statement).fetchone()[0]) for statement in statements)


def _performance_mart_matches(conn: psycopg.Connection) -> bool:
    """Rebuild every mart value from its independent source facts.

    Internal arithmetic checks cannot detect a row whose actual, target,
    variance and attainment were all changed coherently.  This comparison
    also catches missing and extra rows through the full outer join.
    """
    return bool(conn.execute(
        """
        WITH actuals AS (
          SELECT wells.field_name,
                 date_trunc('month', production.reading_date)::date AS month,
                 round(sum(coalesce(production.oil_bbl, 0)), 2) AS actual
            FROM ddh.fct_production_daily production
            JOIN ddh.dim_wells wells USING (well_id)
           GROUP BY wells.field_name,
                    date_trunc('month', production.reading_date)::date
        ), expected AS (
          SELECT coalesce(targets.field_name, actuals.field_name) AS field_name,
                 coalesce(targets.target_month, actuals.month) AS month,
                 targets.asset_name,
                 targets.region_name,
                 targets.operator_name,
                 targets.basin_name,
                 round(coalesce(actuals.actual, 0), 2) AS actual_oil_bbl,
                 targets.target_oil_bbl,
                 round(coalesce(actuals.actual, 0) - targets.target_oil_bbl, 2)
                   AS variance_oil_bbl,
                 round(coalesce(actuals.actual, 0)
                       / targets.target_oil_bbl * 100, 4) AS attainment_pct
            FROM ddh.fct_field_targets_monthly targets
            FULL OUTER JOIN actuals
              ON actuals.field_name = targets.field_name
             AND actuals.month = targets.target_month
        )
        SELECT NOT EXISTS (
          SELECT 1
            FROM expected
            FULL OUTER JOIN ddh.mart_production_performance_monthly mart
              ON mart.field_name = expected.field_name
             AND mart.performance_month = expected.month
           WHERE expected.field_name IS NULL
              OR mart.field_name IS NULL
              OR mart.asset_name IS DISTINCT FROM expected.asset_name
              OR mart.region_name IS DISTINCT FROM expected.region_name
              OR mart.operator_name IS DISTINCT FROM expected.operator_name
              OR mart.basin_name IS DISTINCT FROM expected.basin_name
              OR mart.actual_oil_bbl IS DISTINCT FROM expected.actual_oil_bbl
              OR mart.target_oil_bbl IS DISTINCT FROM expected.target_oil_bbl
              OR mart.variance_oil_bbl IS DISTINCT FROM expected.variance_oil_bbl
              OR mart.attainment_pct IS DISTINCT FROM expected.attainment_pct
        )
        """
    ).fetchone()[0])


def _is_complete_profile(
    dsn: str,
    requested: date | ProfileExpectations,
) -> bool:
    expected = (
        requested
        if isinstance(requested, ProfileExpectations)
        else ProfileExpectations.realistic(requested)
    )
    code_1, code_2, code_3 = expected.raw_code_counts
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
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
              (SELECT count(*) FROM stg.field_targets_monthly_raw),
              (SELECT count(*) FROM ddh.dim_wells WHERE well_type = 'PRODUCER'),
              (SELECT count(*) FROM ddh.dim_wells WHERE well_type = 'INJECTOR'),
              (SELECT count(*) FROM ddh.dim_wells WHERE well_type = 'OBSERVATION'),
              (SELECT count(*) FROM ddh.dim_wells
                WHERE well_type NOT IN ('PRODUCER', 'INJECTOR', 'OBSERVATION')),
              (SELECT count(DISTINCT field_name) FROM ddh.dim_wells),
              (SELECT count(*) FROM (
                 SELECT field_name FROM ddh.dim_wells
                  GROUP BY field_name HAVING count(*) <> %s
               ) wrong_field_sizes),
              (SELECT min(reading_date) FROM ddh.fct_production_daily),
              (SELECT max(reading_date) FROM ddh.fct_production_daily),
              (SELECT min(target_month) FROM ddh.fct_field_targets_monthly),
              (SELECT max(target_month) FROM ddh.fct_field_targets_monthly),
              (SELECT count(*) FROM stg.interventions_raw WHERE stat = '1'),
              (SELECT count(*) FROM stg.interventions_raw WHERE stat = '2'),
              (SELECT count(*) FROM stg.interventions_raw WHERE stat = '3'),
              (SELECT count(*)
                 FROM stg.interventions_raw raw
                 LEFT JOIN ddh.fct_well_interventions curated
                   ON raw.job_id = curated.intervention_id::text
                WHERE curated.intervention_id IS NULL
                   OR curated.status IS DISTINCT FROM CASE raw.stat
                       WHEN '1' THEN 'COMPLETED'
                       WHEN '2' THEN 'CANCELLED'
                       WHEN '3' THEN 'IN_PROGRESS'
                       ELSE upper(raw.stat)
                   END),
              (SELECT count(*) FROM ddh.fct_well_interventions
                WHERE status NOT IN ('COMPLETED', 'CANCELLED', 'IN_PROGRESS')),
              (SELECT count(*) FROM ddh.dim_wells
                WHERE asset_name IS NULL OR operator_name IS NULL
                   OR basin_name IS NULL OR operating_status IS NULL
                   OR lift_method IS NULL),
              (SELECT count(*) FROM ddh.mart_production_performance_monthly
                WHERE variance_oil_bbl <> actual_oil_bbl - target_oil_bbl
                   OR attainment_pct <> round(actual_oil_bbl / target_oil_bbl * 100, 4))
              ,(SELECT count(*)
                  FROM ddh.fct_production_daily fact
                  JOIN ddh.dim_wells well USING (well_id)
                 WHERE fact.reading_date < well.spud_date)
              ,(SELECT count(*)
                  FROM ddh.fct_well_interventions fact
                  JOIN ddh.dim_wells well USING (well_id)
                 WHERE fact.intervention_date < well.spud_date)
              ,(SELECT count(*)
                  FROM ddh.fct_downtime_events fact
                  JOIN ddh.dim_wells well USING (well_id)
                 WHERE fact.event_start::date < well.spud_date)
            """,
            (expected.wells_per_field,),
        ).fetchone()
        core_matches = row == (
            expected.well_count,
            expected.intervention_count,
            expected.production_count,
            expected.downtime_event_count,
            expected.target_count,
            expected.performance_count,
            expected.well_count,
            expected.intervention_count,
            expected.production_count,
            expected.downtime_event_count,
            expected.target_count,
            expected.producer_count,
            expected.injector_count,
            expected.observation_count,
            0,
            expected.field_count,
            0,
            expected.production_start,
            expected.last_day,
            expected.first_month,
            expected.last_month,
            code_1,
            code_2,
            code_3,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        )
        return (core_matches and _mirror_signatures_match(conn)
                and _performance_mart_matches(conn))


def validate_reuse_state(dsn: str, expected: ProfileExpectations) -> bool:
    """Return True only for an exact reusable profile; never mutate data."""
    if not _warehouse_has_rows(dsn):
        return False
    if _is_complete_profile(dsn, expected):
        return True
    raise RuntimeError(
        f"{REALISTIC_DB} contains data that is not the requested complete "
        "profile; refusing to truncate it"
    )


def provision(admin_url: str, end_date: date) -> bool:
    """Create/seed the dedicated database; return False when already complete."""
    init_dir, migration_dir, seed_script, app_dir = _paths()
    if not _database_exists(admin_url):
        _create_database(admin_url)
        print(f"created {REALISTIC_DB}", file=sys.stderr)
    dsn = make_conninfo(admin_url, dbname=REALISTIC_DB)
    if not _has_schema(dsn):
        _apply_fresh_ddl(dsn, init_dir)
        print(f"applied fresh schema to {REALISTIC_DB}", file=sys.stderr)

    sys.path.insert(0, str(app_dir))
    from app.migrations import run_migrations  # noqa: PLC0415

    run_migrations(dsn, migration_dir)
    expected = ProfileExpectations.realistic(end_date)
    if validate_reuse_state(dsn, expected):
        print(f"{REALISTIC_DB} already has the complete realistic profile")
        return False

    command = [
        sys.executable,
        str(seed_script),
        "--profile",
        "realistic",
        "--confirm",
        REALISTIC_DB,
        "--end-date",
        end_date.isoformat(),
    ]
    subprocess.run(
        command,
        check=True,
        env={**os.environ, "ADMIN_URL": dsn},
    )
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", default="")
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2026, 8, 1))
    args = parser.parse_args(argv)
    try:
        # This gate intentionally precedes even reading ADMIN_URL.
        require_confirmation(args.confirm)
        admin_url = os.environ["ADMIN_URL"]
        started = time.perf_counter()
        changed = provision(admin_url, args.end_date)
        print(
            f"realistic database {'seeded' if changed else 'reused'} in "
            f"{time.perf_counter() - started:.3f}s"
        )
        return 0
    except (KeyError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
