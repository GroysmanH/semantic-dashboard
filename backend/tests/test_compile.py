"""The compiler is deterministic, so these are ordinary golden assertions.

This is the suite that makes the whole design cheap to trust: one
semantic query, one SQL string, testable without a model in the loop.
"""

import copy

import psycopg
import pytest

from app.semantic.compile import MAX_ROWS, compile_query, data_max_ts_sql
from app.semantic.query import DimensionRef, Filter, OrderBy, SemanticQuery


@pytest.fixture
def lyr(layer):
    l = copy.deepcopy(layer)
    l["well_interventions"].dimensions["status"].confidence = "high"
    return l


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
    r = c(lyr, limit=10_000)
    assert r.params[-1] == MAX_ROWS


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


def test_data_max_ts_runs(lyr, warehouse_conn):
    stmt, params = data_max_ts_sql(lyr["well_interventions"])
    with warehouse_conn.cursor() as cur:
        cur.execute(stmt, params)
        assert cur.fetchone()[0] is not None
