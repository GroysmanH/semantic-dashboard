"""The compiler is deterministic, so these are ordinary golden assertions.

This is the suite that makes the whole design cheap to trust: one
semantic query, one SQL string, testable without a model in the loop.
"""

import copy

import psycopg
import pytest

from app.layer.models import Entity
from app.semantic.compile import MAX_ROWS, compile_query, data_max_ts_sql
from app.semantic.query import DimensionRef, Filter, OrderBy, SemanticQuery
from app.semantic.result import verify_result


@pytest.fixture
def lyr(layer):
    return copy.deepcopy(layer)


def c(lyr, **kw):
    base = {"entity": "well_interventions", "measures": ["net_gain"]}
    return compile_query(SemanticQuery(**{**base, **kw}), lyr)


# -- shape ---------------------------------------------------------------

def test_measures_only_has_no_group_by(lyr):
    r = c(lyr)
    assert "GROUP BY" not in r.sql
    assert 'SUM("fct_well_interventions"."net_gain_bbl") AS "net_gain"' in r.sql
    assert r.columns == ["net_gain"]
    assert r.column_kinds == {"net_gain": "quantitative"}


def test_count_star_measure(lyr):
    r = c(lyr, measures=["n_jobs"])
    assert 'COUNT(*) AS "n_jobs"' in r.sql


def test_date_grain_uses_date_trunc_with_a_bound_grain(lyr):
    r = c(lyr, dimensions=[DimensionRef(field="intervention_date", grain="month")])
    assert "date_trunc(%s," in r.sql
    # The grain is a parameter, never a literal spliced into SQL.
    assert "'month'" not in r.sql
    assert r.params.count("month") == 1
    assert "GROUP BY 1" in r.sql             # ordinal, not a restated date_trunc
    assert r.column_kinds["intervention_date"] == "temporal"


def test_via_dimension_emits_exactly_one_join(lyr):
    r = c(lyr, dimensions=[DimensionRef(field="region")])
    assert r.sql.count("LEFT JOIN") == 1
    assert r.joins_used == ["wells"]
    assert '"wells"."region_name"' in r.sql


def test_query_with_no_via_dimension_emits_no_join(lyr):
    r = c(lyr, dimensions=[DimensionRef(field="contractor")])
    assert "JOIN" not in r.sql
    assert r.joins_used == []


def test_two_via_dimensions_reuse_a_single_join(lyr):
    r = c(lyr, dimensions=[DimensionRef(field="region"),
                           DimensionRef(field="well_name")])
    assert r.sql.count("LEFT JOIN") == 1


def test_mixed_dimensions_still_emit_one_join(lyr):
    r = c(lyr, dimensions=[DimensionRef(field="region"),
                           DimensionRef(field="contractor")])
    assert r.sql.count("LEFT JOIN") == 1


def test_filter_alone_can_pull_in_a_join(lyr):
    """A join the SELECT never needed, but the WHERE does."""
    r = c(lyr, filters=[Filter(field="region", op="=", value="Atyrau")])
    assert r.sql.count("LEFT JOIN") == 1
    assert r.joins_used == ["wells"]


# -- filters -------------------------------------------------------------

def test_in_filter_uses_any_with_a_bound_list(lyr):
    r = c(lyr, filters=[Filter(field="intervention_type", op="in",
                               value=["FRAC", "WORKOVER"])])
    assert "= ANY(%s)" in r.sql
    assert ["FRAC", "WORKOVER"] in r.params


def test_between_binds_two_values_in_order(lyr):
    r = c(lyr, filters=[Filter(field="intervention_date", op="between",
                               value=["2026-01-01", "2026-06-30"])])
    assert "BETWEEN %s AND %s" in r.sql
    assert r.params[:2] == ["2026-01-01", "2026-06-30"]


def test_in_year_binds_the_year(lyr):
    r = c(lyr, filters=[Filter(field="intervention_date", op="in_year", value=2026)])
    assert "date_part('year'" in r.sql
    assert 2026 in r.params


def test_last_n_days_binds_the_interval(lyr):
    r = c(lyr, filters=[Filter(field="intervention_date", op="last_n_days", value=30)])
    assert "make_interval(days => %s)" in r.sql
    assert 30 in r.params


# -- limits and ordering -------------------------------------------------

def test_limit_is_hard_capped(lyr):
    """What a caller may see is capped. One row over that is fetched and
    never shown -- its arrival is how the card knows to say "more exist"
    instead of presenting a fraction of an answer as the answer."""
    r = c(lyr, limit=10_000)
    assert r.row_limit == MAX_ROWS
    assert r.params[-1] == MAX_ROWS + 1


def test_a_limit_beyond_the_cap_is_still_capped(lyr):
    r = c(lyr, limit=10_000)
    assert r.row_limit == MAX_ROWS


def test_order_by_renders_direction(lyr):
    r = c(lyr, dimensions=[DimensionRef(field="region")],
          order_by=[OrderBy(field="net_gain", dir="desc")])
    assert 'ORDER BY "net_gain" DESC' in r.sql


# -- the injection claim -------------------------------------------------

def test_no_user_value_appears_in_the_sql_string(lyr):
    """Values are bound, never interpolated. The filter passes validation
    because contractor declares no value domain."""
    payload = "'; DROP TABLE ddh.dim_wells; --"
    r = c(lyr, filters=[Filter(field="contractor", op="=", value=payload)])
    assert payload in r.params
    assert "DROP" not in r.sql


def test_parameter_order_matches_placeholder_order(lyr):
    """Every %s in the statement has exactly one param, in position."""
    r = c(lyr,
          dimensions=[DimensionRef(field="intervention_date", grain="month"),
                      DimensionRef(field="region")],
          filters=[Filter(field="status", op="=", value="COMPLETED"),
                   Filter(field="intervention_date", op="in_year", value=2026)],
          order_by=[OrderBy(field="net_gain")])
    assert r.sql.count("%s") == len(r.params)


# -- it actually runs ----------------------------------------------------

def test_compiled_sql_executes_against_the_warehouse(lyr, warehouse_conn):
    r = c(lyr,
          measures=["net_gain", "n_jobs"],
          dimensions=[DimensionRef(field="intervention_date", grain="month"),
                      DimensionRef(field="region")],
          filters=[Filter(field="intervention_date", op="in_year", value=2026),
                   Filter(field="status", op="=", value="COMPLETED")],
          order_by=[OrderBy(field="net_gain", dir="desc")],
          limit=50)
    with warehouse_conn.cursor() as cur:
        cur.execute(r.sql, r.params)
        rows = cur.fetchall()
        assert [d.name for d in cur.description] == r.columns
    assert 0 < len(rows) <= 50


@pytest.mark.parametrize("kw", [
    {},
    {"measures": ["n_jobs"]},
    {"dimensions": [DimensionRef(field="region")]},
    {"dimensions": [DimensionRef(field="intervention_date", grain="quarter")]},
    {"dimensions": [DimensionRef(field="intervention_date", grain="year"),
                    DimensionRef(field="intervention_type")]},
    {"filters": [Filter(field="intervention_type", op="in", value=["FRAC"])]},
    {"filters": [Filter(field="intervention_date", op="last_n_days", value=90)]},
    {"filters": [Filter(field="intervention_date", op="between",
                        value=["2025-01-01", "2025-12-31"])]},
    {"measures": ["avg_net_gain", "cost"], "dimensions": [DimensionRef(field="region")]},
])
def test_every_grammar_shape_executes(lyr, warehouse_conn, kw):
    r = c(lyr, **kw)
    with warehouse_conn.cursor() as cur:
        cur.execute(r.sql, r.params)
        cur.fetchall()


def test_production_entity_compiles_and_runs(lyr, warehouse_conn):
    r = compile_query(
        SemanticQuery(entity="production", measures=["oil", "gas"],
                      dimensions=[DimensionRef(field="reading_date", grain="month")],
                      limit=24),
        lyr)
    with warehouse_conn.cursor() as cur:
        cur.execute(r.sql, r.params)
        assert len(cur.fetchall()) > 0


def test_downtime_entity_compiles_joined_manager_dimensions(lyr):
    result = compile_query(
        SemanticQuery(
            entity="downtime",
            measures=["lost_hours", "event_count"],
            dimensions=[
                DimensionRef(field="event_start", grain="month"),
                DimensionRef(field="asset"),
            ],
        ),
        lyr,
    )

    assert 'FROM "ddh"."fct_downtime_events" AS "fct_downtime_events"' in result.sql
    assert 'SUM("fct_downtime_events"."duration_hours") AS "lost_hours"' in result.sql
    assert 'COUNT(*) AS "event_count"' in result.sql
    assert result.joins_used == ["wells"]


def test_production_performance_compiles_curated_business_measures(lyr):
    result = compile_query(
        SemanticQuery(
            entity="production_performance",
            measures=["actual_oil", "target_oil", "variance_oil", "attainment"],
            dimensions=[
                DimensionRef(field="performance_month", grain="month"),
                DimensionRef(field="asset"),
            ],
        ),
        lyr,
    )

    assert 'FROM "ddh"."mart_production_performance_monthly"' in result.sql
    assert 'SUM("mart_production_performance_monthly"."actual_oil_bbl") AS "actual_oil"' in result.sql
    assert 'SUM("mart_production_performance_monthly"."actual_oil_bbl")' in result.sql
    assert 'SUM("mart_production_performance_monthly"."target_oil_bbl")' in result.sql
    assert 'AS "attainment"' in result.sql
    assert 'AVG("mart_production_performance_monthly"."attainment_pct")' not in result.sql
    assert result.joins_used == []


def test_data_max_ts_runs(lyr, warehouse_conn):
    stmt, params = data_max_ts_sql(lyr["well_interventions"])
    with warehouse_conn.cursor() as cur:
        cur.execute(stmt, params)
        assert cur.fetchone()[0] is not None


# -- an unordered limit is a lottery -------------------------------------

def test_a_grouped_query_is_ordered_even_when_nobody_asked(lyr):
    """Measured on a real distributed warehouse: the same question, asked
    three times with no ORDER BY, returned three different hundreds of the
    same hundred-and-twenty-five companies. Not a different order -- a
    different set. Ordering by the leading measure turns an arbitrary
    hundred into a top hundred, which is at least something somebody meant
    to ask for."""
    r = c(lyr, dimensions=[DimensionRef(field="region")])
    assert 'ORDER BY "net_gain" DESC' in r.sql


def test_the_order_has_a_tiebreaker_under_the_measure(lyr):
    """Ordering by a measure is deterministic only until two rows tie."""
    r = c(lyr, dimensions=[DimensionRef(field="region")])
    assert (
        'ORDER BY "net_gain" DESC NULLS LAST, '
        '"region" COLLATE "C" ASC NULLS LAST'
    ) in r.sql


def test_an_explicit_order_is_kept_and_also_given_a_tiebreaker(lyr):
    r = c(lyr, dimensions=[DimensionRef(field="region")],
          order_by=[OrderBy(field="region", dir="asc")])
    assert 'ORDER BY "region" COLLATE "C" ASC NULLS LAST' in r.sql
    assert r.sql.count("ASC") == 1      # not repeated as its own tiebreaker


def test_compiled_nominal_order_and_validator_share_utf8_byte_order(
    warehouse_conn,
):
    """A source column's ICU collation must not make PostgreSQL and the
    Python result contract disagree on mixed Latin and Cyrillic ties."""
    values = ["я", "Z", "Á", "а", "A", "Ё", "е"]
    with warehouse_conn.cursor() as cur:
        cur.execute(
            'CREATE TEMP TABLE nominal_order_fixture '
            '(label text COLLATE "und-x-icu", amount numeric) ON COMMIT DROP'
        )
        cur.executemany(
            "INSERT INTO nominal_order_fixture (label, amount) VALUES (%s, 1)",
            [(value,) for value in values],
        )

    entity = Entity.model_validate({
        "entity": "nominal_order_fixture",
        "label": "Nominal order fixture",
        "table": "pg_temp.nominal_order_fixture",
        "dimensions": {
            "label": {"label": "label", "type": "string"},
        },
        "measures": {
            "amount": {
                "label": "amount", "agg": "sum", "column": "amount",
            },
        },
    })
    query = SemanticQuery(
        entity="nominal_order_fixture",
        measures=["amount"],
        dimensions=[DimensionRef(field="label")],
    )
    compiled = compile_query(query, {entity.name: entity})

    with warehouse_conn.cursor() as cur:
        cur.execute(compiled.sql, compiled.params)
        rows = [
            {"label": label, "amount": float(amount)}
            for label, amount in cur.fetchall()
        ]

    expected = sorted(values, key=lambda value: value.encode("utf-8"))
    assert '"label" COLLATE "C" ASC NULLS LAST' in compiled.sql
    assert [row["label"] for row in rows] == expected
    verify_result(
        compiled, rows, row_count=len(rows), truncated=False,
    )


def test_collation_is_not_applied_to_numeric_or_temporal_ordering(lyr):
    numeric = c(
        lyr,
        dimensions=[DimensionRef(field="region")],
        order_by=[OrderBy(field="net_gain", dir="desc")],
    )
    temporal = c(
        lyr,
        dimensions=[
            DimensionRef(field="intervention_date", grain="day"),
            DimensionRef(field="region"),
        ],
        order_by=[OrderBy(field="intervention_date", dir="asc")],
    )

    assert '"net_gain" DESC NULLS LAST' in numeric.sql
    assert '"net_gain" COLLATE' not in numeric.sql
    assert '"intervention_date" ASC NULLS LAST' in temporal.sql
    assert '"intervention_date" COLLATE' not in temporal.sql


def test_an_ungrouped_query_needs_no_ordering(lyr):
    """One row cannot be in the wrong order."""
    r = c(lyr, dimensions=[])
    assert "ORDER BY" not in r.sql
