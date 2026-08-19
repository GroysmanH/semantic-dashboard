"""Transforms, derived measures, and the arithmetic that has to be right.

These execute rather than asserting golden strings. A window function that
partitions wrongly produces perfectly valid SQL and perfectly wrong
numbers, and no string comparison catches that.
"""

import pytest

from app.semantic.compile import compile_query
from app.semantic.query import SemanticQuery
from app.semantic.validate import QueryValidationError


def q(**kw):
    return SemanticQuery.model_validate({"entity": "production", **kw})


def run(layer, warehouse_conn, **kw):
    compiled = compile_query(q(**kw), layer)
    with warehouse_conn.cursor() as cur:
        cur.execute(compiled.sql, compiled.params)
        return compiled, cur.fetchall()


# -- every transform executes and names its own column --------------------

CASES = {
    "percent_of_total": (
        {"name": "oil", "transform": "percent_of_total"}, "oil_percent_of_total",
        [{"field": "region"}]),
    "previous_period": (
        {"name": "gas", "transform": "previous_period"}, "gas_previous_period",
        [{"field": "reading_date", "grain": "month"}]),
    "period_change": (
        {"name": "oil", "transform": "period_change"}, "oil_period_change",
        [{"field": "reading_date", "grain": "month"}]),
    "period_change_pct": (
        {"name": "gas", "transform": "period_change_pct"}, "gas_period_change_pct",
        [{"field": "reading_date", "grain": "month"}]),
    "cumulative": (
        {"name": "oil", "transform": "cumulative"}, "oil_cumulative",
        [{"field": "reading_date", "grain": "month"}]),
    "moving_average": (
        {"name": "oil", "transform": "moving_average", "window": 3}, "oil_ma3",
        [{"field": "reading_date", "grain": "month"}]),
    "rank": (
        {"name": "oil", "transform": "rank"}, "oil_rank",
        [{"field": "field_name"}]),
    "ratio": (
        {"name": "gas", "transform": "ratio", "per": "oil"}, "gas_per_oil",
        [{"field": "field_name"}]),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_transform_compiles_and_executes(layer, warehouse_conn, name):
    measure, column, dims = CASES[name]
    compiled, rows = run(layer, warehouse_conn, measures=[measure], dimensions=dims)
    assert column in compiled.columns
    assert compiled.column_kinds[column] == "quantitative"
    assert rows


def test_a_transformed_measure_does_not_collide_with_its_base(layer, warehouse_conn):
    """`oil` and its running total are two columns, not one overwriting the
    other, which is why output_name exists at all."""
    compiled, rows = run(layer, warehouse_conn,
                         measures=["oil", {"name": "oil", "transform": "cumulative"}],
                         dimensions=[{"field": "reading_date", "grain": "month"}])
    assert compiled.columns == ["reading_date", "oil", "oil_cumulative"]
    assert len(rows[0]) == 3


def test_moving_average_binds_its_window_to_the_right_placeholder(layer, warehouse_conn):
    """The outer SELECT is written before the subquery it wraps, so its
    parameters lead. Appending them instead would misbind only this
    transform -- the one that carries a parameter."""
    compiled, rows = run(layer, warehouse_conn,
                         measures=[{"name": "oil", "transform": "moving_average",
                                    "window": 3}],
                         dimensions=[{"field": "reading_date", "grain": "month"}])
    assert compiled.params[0] == 2          # ROWS BETWEEN 2 PRECEDING
    assert compiled.params[1] == "month"    # the inner date_trunc grain
    assert rows


# -- the correctness that string tests cannot see -------------------------

def test_a_series_lags_against_its_own_previous_period(layer, warehouse_conn):
    """Partitioning is the single most likely way this is quietly wrong: a
    per-region series must step through its own months, not through the
    interleaved rows of every region."""
    _, rows = run(layer, warehouse_conn,
                  measures=["gas", {"name": "gas", "transform": "previous_period"}],
                  dimensions=[{"field": "reading_date", "grain": "month"},
                              {"field": "region"}],
                  limit=500)

    by_region: dict[str, list] = {}
    for reading_date, region, gas, prev in rows:
        by_region.setdefault(region, []).append((reading_date, gas, prev))

    assert len(by_region) == 5
    for series in by_region.values():
        series.sort()
        # Exactly one NULL per region, and it is that region's own first row.
        assert sum(1 for _, _, p in series if p is None) == 1
        assert series[0][2] is None
        for i in range(1, len(series)):
            assert series[i][2] == series[i - 1][1]


def test_water_cut_is_a_ratio_of_sums_not_a_sum_of_ratios(layer, warehouse_conn):
    """The classic silent-wrongness bug in derived measures. Both forms are
    valid SQL; they differ here by four orders of magnitude."""
    compiled, rows = run(layer, warehouse_conn, measures=["water_cut"],
                         dimensions=[{"field": "region"}])
    got = {region: float(value) for region, value in rows}

    with warehouse_conn.cursor() as cur:
        cur.execute("""
            SELECT w.region_name,
                   SUM(p.water_bbl) / (SUM(p.oil_bbl) + SUM(p.water_bbl))
            FROM ddh.fct_production_daily p
            LEFT JOIN ddh.dim_wells w ON w.well_id = p.well_id
            GROUP BY 1""")
        control = {region: float(value) for region, value in cur.fetchall()}

    assert got == pytest.approx(control)
    # A water cut is a fraction. The sum-of-ratios form returns thousands.
    assert all(0 < v < 1 for v in got.values())


def test_a_zero_denominator_blanks_a_point_rather_than_failing(layer, warehouse_conn):
    """One bad grouping should not take the whole card down."""
    _, rows = run(layer, warehouse_conn,
                  measures=[{"name": "oil", "transform": "ratio", "per": "downtime"}],
                  dimensions=[{"field": "well_name"}], limit=300)
    assert rows


def test_uptime_uses_the_declared_constant(layer):
    compiled = compile_query(q(measures=["uptime_pct"],
                               dimensions=[{"field": "region"}]), layer)
    assert "24" in compiled.sql
    assert "NULLIF" in compiled.sql


# -- transform arguments are checked, not assumed -------------------------

@pytest.mark.parametrize("measure,reason", [
    ({"name": "oil", "transform": "ratio"}, "ratio_needs_denominator"),
    ({"name": "oil", "transform": "ratio", "per": "nope"}, "unknown_measure"),
    ({"name": "oil", "transform": "ratio", "per": "oil"}, "degenerate_ratio"),
    ({"name": "oil", "transform": "moving_average"}, "window_required"),
    ({"name": "oil", "transform": "cumulative", "window": 3},
     "window_without_moving_average"),
    ({"name": "oil", "transform": "cumulative", "per": "gas"}, "per_without_ratio"),
    ({"name": "oil", "per": "gas"}, "transform_argument_without_transform"),
])
def test_transform_arguments_are_validated(layer, measure, reason):
    with pytest.raises(QueryValidationError) as e:
        compile_query(q(measures=[measure],
                        dimensions=[{"field": "reading_date", "grain": "month"}]),
                      layer)
    assert e.value.reason == reason


def test_a_period_transform_without_a_time_axis_is_refused(layer):
    with pytest.raises(QueryValidationError) as e:
        compile_query(q(measures=[{"name": "oil", "transform": "cumulative"}],
                        dimensions=[{"field": "region"}]), layer)
    assert e.value.reason == "transform_needs_time"
    assert "date dimension" in e.value.detail


def test_a_share_without_a_grouping_is_refused(layer):
    with pytest.raises(QueryValidationError) as e:
        compile_query(q(measures=[{"name": "oil", "transform": "percent_of_total"}]),
                      layer)
    assert e.value.reason == "transform_needs_grouping"


def test_a_derived_measure_cannot_be_filtered_on(layer):
    with pytest.raises(QueryValidationError) as e:
        compile_query(q(measures=["oil"], dimensions=[{"field": "region"}],
                        filters=[{"field": "water_cut", "op": "=", "value": 1}]),
                      layer)
    assert e.value.reason == "filter_on_derived"


def test_ordering_uses_the_transformed_output_name(layer, warehouse_conn):
    _, rows = run(layer, warehouse_conn,
                  measures=[{"name": "oil", "transform": "cumulative"}],
                  dimensions=[{"field": "reading_date", "grain": "month"}],
                  order_by=[{"field": "oil_cumulative", "dir": "desc"}])
    assert rows

    with pytest.raises(QueryValidationError) as e:
        compile_query(q(measures=[{"name": "oil", "transform": "cumulative"}],
                        dimensions=[{"field": "reading_date", "grain": "month"}],
                        order_by=[{"field": "oil", "dir": "desc"}]), layer)
    assert e.value.reason == "order_by_unselected"


# -- injection, on the new paths ------------------------------------------

def test_no_user_value_reaches_the_sql_string_through_a_transform(layer):
    compiled = compile_query(
        q(measures=[{"name": "oil", "transform": "moving_average", "window": 4}],
          dimensions=[{"field": "reading_date", "grain": "month"}],
          filters=[{"field": "region", "op": "=",
                    "value": "'; DROP TABLE ddh.dim_wells; --"}]), layer)
    assert "DROP TABLE" not in compiled.sql
    assert "'; DROP TABLE ddh.dim_wells; --" in compiled.params
