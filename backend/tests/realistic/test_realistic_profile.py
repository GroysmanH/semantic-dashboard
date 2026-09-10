"""Opt-in integration and query benchmark for ``semantic_realistic`` only."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import os
from time import perf_counter

import pytest

from app.semantic.compile import compile_query
from app.semantic.query import DimensionRef, Filter, OrderBy, SemanticQuery


pytestmark = pytest.mark.skipif(
    os.environ.get("SEED_PROFILE") != "realistic",
    reason="run through `make test-realistic CONFIRM=semantic_realistic`",
)


def test_realistic_integration_is_connected_to_the_fixed_database(warehouse_conn):
    with warehouse_conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        assert cur.fetchone()[0] == "semantic_realistic"


def test_realistic_profile_has_exact_operational_counts_and_horizon(warehouse_conn):
    with warehouse_conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              (SELECT count(*) FROM ddh.dim_wells),
              (SELECT count(*) FROM ddh.fct_well_interventions),
              (SELECT count(*) FROM ddh.fct_production_daily),
              (SELECT count(*) FROM ddh.fct_downtime_events),
              (SELECT count(*) FROM ddh.fct_field_targets_monthly),
              (SELECT count(*) FROM ddh.mart_production_performance_monthly),
              (SELECT count(DISTINCT field_name) FROM ddh.dim_wells),
              (SELECT min(reading_date) FROM ddh.fct_production_daily),
              (SELECT max(reading_date) FROM ddh.fct_production_daily)
            """
        )
        assert cur.fetchone() == (
            600, 16_000, 1_402_560, 12_000, 2_304, 2_304, 24,
            date(2018, 8, 1), date(2026, 7, 31),
        )
        cur.execute(
            "SELECT well_type, count(*) FROM ddh.dim_wells GROUP BY well_type"
        )
        assert dict(cur.fetchall()) == {
            "PRODUCER": 480,
            "INJECTOR": 90,
            "OBSERVATION": 30,
        }
        cur.execute(
            """
            SELECT count(*) FROM ddh.dim_wells
             WHERE asset_name IS NULL OR operator_name IS NULL OR basin_name IS NULL
                OR operating_status IS NULL OR lift_method IS NULL
            """
        )
        assert cur.fetchone()[0] == 0


def test_raw_legacy_statuses_remain_dirty_while_curated_rows_are_mapped(
    warehouse_conn,
):
    with warehouse_conn.cursor() as cur:
        cur.execute(
            """
            SELECT stat, count(*) FROM stg.interventions_raw
             WHERE stat IN ('1', '2', '3') GROUP BY stat ORDER BY stat
            """
        )
        raw = dict(cur.fetchall())
        cur.execute(
            """
            SELECT count(*)
              FROM stg.interventions_raw raw
              JOIN ddh.fct_well_interventions curated
                ON curated.intervention_id = raw.job_id::integer
             WHERE raw.stat IN ('1', '2', '3')
               AND curated.status <> CASE raw.stat
                   WHEN '1' THEN 'COMPLETED'
                   WHEN '2' THEN 'CANCELLED'
                   WHEN '3' THEN 'IN_PROGRESS'
               END
            """
        )
        mismatches = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM ddh.fct_well_interventions WHERE status IN ('1','2','3')"
        )
        curated_codes = cur.fetchone()[0]

    assert raw == {"1": 54, "2": 53, "3": 53}
    assert mismatches == 0
    assert curated_codes == 0


def test_no_realistic_fact_precedes_its_non_orphan_well_spud(warehouse_conn):
    with warehouse_conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              (SELECT count(*)
                 FROM ddh.fct_production_daily fact
                 JOIN ddh.dim_wells well USING (well_id)
                WHERE fact.reading_date < well.spud_date),
              (SELECT count(*)
                 FROM ddh.fct_well_interventions fact
                 JOIN ddh.dim_wells well USING (well_id)
                WHERE fact.intervention_date < well.spud_date),
              (SELECT count(*)
                 FROM ddh.fct_downtime_events fact
                 JOIN ddh.dim_wells well USING (well_id)
                WHERE fact.event_start::date < well.spud_date)
            """
        )

        assert cur.fetchone() == (0, 0, 0)


def test_monthly_performance_arithmetic_and_staging_mirrors_converge(warehouse_conn):
    with warehouse_conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              (SELECT count(*) FROM stg.production_daily_raw),
              (SELECT count(*) FROM stg.downtime_events_raw),
              (SELECT count(*) FROM stg.field_targets_monthly_raw)
            """
        )
        assert cur.fetchone() == (1_402_560, 12_000, 2_304)
        cur.execute(
            """
            SELECT count(*)
              FROM ddh.mart_production_performance_monthly
             WHERE variance_oil_bbl <> actual_oil_bbl - target_oil_bbl
                OR attainment_pct <> round(actual_oil_bbl / target_oil_bbl * 100, 4)
            """
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            """
            SELECT abs(mart.actual_oil_bbl - production.actual_oil_bbl)
              FROM ddh.mart_production_performance_monthly mart
              JOIN (
                SELECT wells.field_name,
                       date_trunc('month', production.reading_date)::date AS month,
                       sum(production.oil_bbl) AS actual_oil_bbl
                  FROM ddh.fct_production_daily production
                  JOIN ddh.dim_wells wells ON wells.well_id = production.well_id
                 GROUP BY wells.field_name, month
              ) production
                ON production.field_name = mart.field_name
               AND production.month = mart.performance_month
             ORDER BY 1 DESC LIMIT 1
            """
        )
        assert cur.fetchone()[0] <= Decimal("0.01")


def _run_performance_query(layer, warehouse_conn, *, dimensions, filters):
    compiled = compile_query(
        SemanticQuery(
            entity="production_performance",
            measures=["actual_oil", "target_oil", "attainment"],
            dimensions=dimensions,
            filters=filters,
        ),
        layer,
    )
    with warehouse_conn.cursor() as cur:
        cur.execute(compiled.sql, compiled.params)
        return cur.fetchall()


def test_field_attainment_executes_as_actual_over_target(layer, warehouse_conn):
    rows = _run_performance_query(
        layer,
        warehouse_conn,
        dimensions=[
            DimensionRef(field="performance_month", grain="month"),
            DimensionRef(field="field_name"),
        ],
        filters=[
            Filter(
                field="performance_month",
                op="between",
                value=["2025-07-01", "2025-07-31"],
            ),
            Filter(field="field_name", op="=", value="Kenkiyak"),
        ],
    )

    assert len(rows) == 1
    assert rows[0][1] == "Kenkiyak"
    assert float(rows[0][-1]) == pytest.approx(98.0392, abs=0.00005)


def test_asset_month_attainment_rolls_up_as_ratio_of_sums(layer, warehouse_conn):
    rows = _run_performance_query(
        layer,
        warehouse_conn,
        dimensions=[
            DimensionRef(field="performance_month", grain="month"),
            DimensionRef(field="asset"),
        ],
        filters=[
            Filter(
                field="performance_month",
                op="between",
                value=["2025-07-01", "2025-07-31"],
            ),
            Filter(field="asset", op="=", value="Aktobe West"),
        ],
    )

    assert len(rows) == 1
    assert rows[0][1] == "Aktobe West"
    # Two fields have materially different targets.  AVG(field attainment)
    # is 103.36745 and must not satisfy this literal ratio-of-sums result.
    assert float(rows[0][-1]) == pytest.approx(101.7058, abs=0.00005)


@pytest.mark.parametrize(
    "query",
    [
        SemanticQuery(
            entity="production",
            measures=["oil"],
            dimensions=[
                DimensionRef(field="reading_date", grain="month"),
                DimensionRef(field="asset"),
            ],
            order_by=[OrderBy(field="oil", dir="desc")],
        ),
        SemanticQuery(
            entity="well_interventions",
            measures=["n_jobs", "cost", "net_gain"],
            dimensions=[DimensionRef(field="asset")],
            filters=[Filter(field="status", op="=", value="COMPLETED")],
            order_by=[OrderBy(field="net_gain", dir="desc")],
        ),
        SemanticQuery(
            entity="downtime",
            measures=["lost_hours", "event_count"],
            dimensions=[DimensionRef(field="downtime_category")],
            order_by=[OrderBy(field="lost_hours", dir="desc")],
        ),
        SemanticQuery(
            entity="production_performance",
            measures=["actual_oil", "target_oil", "variance_oil", "attainment"],
            dimensions=[
                DimensionRef(field="performance_month", grain="month"),
                DimensionRef(field="asset"),
            ],
        ),
    ],
    ids=("monthly-oil-by-asset", "completed-jobs-by-asset", "downtime-reasons", "actual-vs-target"),
)
def test_representative_manager_queries_compile_execute_and_report_timing(
    layer, warehouse_conn, query,
):
    compiled = compile_query(query, layer)
    started = perf_counter()
    with warehouse_conn.cursor() as cur:
        cur.execute(compiled.sql, compiled.params)
        rows = cur.fetchall()
    elapsed = perf_counter() - started

    assert rows
    print(f"benchmark {query.entity}: {len(rows)} rows in {elapsed:.3f}s")
