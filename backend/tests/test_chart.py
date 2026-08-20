"""The chart builder is a pure function of the compiled query. These tests
are what let the design trust a chart it never asked a model to draw."""

import copy
from datetime import date

import pytest

from app.semantic.chart import build_spec
from app.semantic.compile import compile_query
from app.semantic.query import DimensionRef, Filter, OrderBy, SemanticQuery


@pytest.fixture
def lyr(layer):
    l = copy.deepcopy(layer)
    l["well_interventions"].dimensions["status"].confidence = "high"
    return l


def spec_for(lyr, hint=None, rows=None, **kw):
    base = {"entity": "well_interventions", "measures": ["net_gain"]}
    compiled = compile_query(SemanticQuery(**{**base, **kw}), lyr)
    return build_spec(compiled, rows, hint), compiled


def walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


# -- one row per shape ---------------------------------------------------

def test_no_dimensions_is_a_big_number(lyr):
    r, _ = spec_for(lyr)
    assert r.chart_type == "big_number"
    assert r.spec["usermeta"]["presentation"] == "kpi"
    assert r.spec["layer"][0]["mark"]["type"] == "text"


def test_one_temporal_dimension_is_a_line(lyr):
    r, _ = spec_for(lyr, dimensions=[DimensionRef(field="intervention_date", grain="month")])
    assert r.chart_type == "line"
    assert r.spec["encoding"]["x"]["type"] == "temporal"
    assert r.spec["layer"][0]["encoding"]["y"]["field"] == "net_gain"
    assert r.spec["layer"][-1]["mark"]["type"] == "rule"
    assert r.spec["layer"][-1]["params"][0]["select"]["nearest"] is True


def test_one_nominal_dimension_is_a_sorted_bar(lyr):
    r, _ = spec_for(lyr, dimensions=[DimensionRef(field="region")])
    assert r.chart_type == "bar"
    assert r.spec["encoding"]["y"]["type"] == "nominal"
    assert r.spec["encoding"]["y"]["sort"] == "-x"
    assert r.spec["encoding"]["x"]["field"] == "net_gain"
    assert r.spec["mark"]["size"] <= 22


def test_ascending_order_by_flips_the_sort(lyr):
    r, _ = spec_for(lyr, dimensions=[DimensionRef(field="region")],
                    order_by=[OrderBy(field="net_gain", dir="asc")])
    assert r.spec["encoding"]["y"]["sort"] == "x"


def test_several_measures_over_time_use_independent_small_multiples(lyr):
    r, _ = spec_for(lyr, measures=["net_gain", "cost"],
                    dimensions=[DimensionRef(field="intervention_date", grain="month")])
    assert r.spec["transform"][0]["fold"] == ["net_gain", "cost"]
    assert r.spec["facet"]["row"]["field"] == "measure"
    assert r.spec["spec"]["layer"][0]["encoding"]["y"]["field"] == "value"
    assert r.spec["resolve"]["scale"]["y"] == "independent"


def test_several_measures_over_categories_use_independent_small_multiples(lyr):
    r, _ = spec_for(lyr, measures=["net_gain", "cost"],
                    dimensions=[DimensionRef(field="region")])
    assert r.spec["facet"]["row"]["field"] == "measure"
    assert r.spec["spec"]["encoding"]["y"]["field"] == "region"
    assert r.spec["spec"]["encoding"]["x"]["field"] == "value"
    assert r.spec["resolve"]["scale"]["x"] == "independent"


def test_temporal_plus_nominal_is_a_multi_series_line(lyr):
    r, _ = spec_for(lyr, dimensions=[DimensionRef(field="intervention_date", grain="month"),
                                     DimensionRef(field="region")])
    assert r.chart_type == "line"
    assert r.spec["layer"][0]["encoding"]["color"]["field"] == "region"


def test_temporal_plus_nominal_with_two_measures_facets_by_row(lyr):
    """A requested measure is never silently dropped."""
    r, _ = spec_for(lyr, measures=["net_gain", "cost"],
                    dimensions=[DimensionRef(field="intervention_date", grain="month"),
                                DimensionRef(field="region")])
    assert r.spec["facet"]["row"]["field"] == "measure"
    assert r.spec["transform"][0]["fold"] == ["net_gain", "cost"]
    assert r.spec["spec"]["layer"][0]["encoding"]["y"]["field"] == "value"
    assert r.spec["resolve"]["scale"]["y"] == "independent"


def test_a_small_multi_series_trend_has_a_shared_non_aggregating_tooltip(lyr):
    rows = [
        {"intervention_date": "2026-01-01", "region": "North", "net_gain": 10},
        {"intervention_date": "2026-01-01", "region": "South", "net_gain": 20},
        {"intervention_date": "2026-02-01", "region": "North", "net_gain": 12},
        {"intervention_date": "2026-02-01", "region": "South", "net_gain": 18},
    ]
    r, _ = spec_for(
        lyr,
        rows=rows,
        dimensions=[DimensionRef(field="intervention_date", grain="month"),
                    DimensionRef(field="region")],
    )
    rule = r.spec["layer"][-1]
    assert not any("pivot" in transform for node in walk(r.spec)
                   for transform in node.get("transform", []) if isinstance(transform, dict))
    assert [item.get("title") for item in rule["encoding"]["tooltip"]] == [
        "intervention date", "North", "South"]
    tooltip_fields = [item["field"] for item in rule["encoding"]["tooltip"]][1:]
    assert all(field.startswith("__tooltip_") for field in tooltip_fields)
    assert r.chart_rows is not None
    assert {r.chart_rows[0][field] for field in tooltip_fields} == {10, 20}


def test_the_crosshair_is_hidden_until_a_date_is_hovered(lyr):
    r, _ = spec_for(
        lyr,
        rows=[{"intervention_date": "2026-01-01", "net_gain": 10},
              {"intervention_date": "2026-02-01", "net_gain": 12}],
        dimensions=[DimensionRef(field="intervention_date", grain="month")],
    )
    rule = r.spec["layer"][-1]
    assert rule["encoding"]["opacity"] == {
        "condition": {"param": "hover", "empty": False, "value": 0.62},
        "value": 0,
    }


def test_a_dense_trend_falls_back_to_nearest_series_tooltip(lyr):
    rows = [
        {"intervention_date": date_, "region": f"Region {i}", "net_gain": i}
        for date_ in ("2026-01-01", "2026-02-01") for i in range(9)
    ]
    r, _ = spec_for(
        lyr,
        rows=rows,
        dimensions=[DimensionRef(field="intervention_date", grain="month"),
                    DimensionRef(field="region")],
    )
    hit = r.spec["layer"][1]
    assert hit["mark"]["opacity"] == 0
    assert hit["params"][0]["select"]["fields"] == ["intervention_date", "region"]
    assert not any("pivot" in transform for node in walk(r.spec)
                   for transform in node.get("transform", []) if isinstance(transform, dict))


def test_too_many_line_series_are_refused_instead_of_becoming_spaghetti(lyr):
    rows = [
        {
            "intervention_date": date_,
            "region": f"Region {index}",
            "net_gain": index,
        }
        for date_ in ("2026-01-01", "2026-02-01")
        for index in range(25)
    ]
    result, _ = spec_for(
        lyr,
        rows=rows,
        dimensions=[
            DimensionRef(field="intervention_date", grain="month"),
            DimensionRef(field="region"),
        ],
    )

    assert result.chart_type == "unplottable"
    assert "25 series" in result.error


@pytest.mark.parametrize("hint", ["stacked_bar", "normalised_bar"])
def test_too_many_stack_components_are_refused_instead_of_flooding_the_legend(
        lyr, hint):
    rows = [
        {
            "intervention_date": date_,
            "region": f"Region {index}",
            "net_gain": index,
        }
        for date_ in ("2026-01-01", "2026-02-01")
        for index in range(9)
    ]
    result, _ = spec_for(
        lyr,
        hint=hint,
        rows=rows,
        dimensions=[
            DimensionRef(field="intervention_date", grain="month"),
            DimensionRef(field="region"),
        ],
    )

    assert result.chart_type == "unplottable"
    assert "9 series" in result.error


def test_chart_title_is_not_repeated_inside_the_plot(lyr):
    base = {"entity": "well_interventions", "measures": ["net_gain"]}
    compiled = compile_query(SemanticQuery(**base), lyr)
    r = build_spec(compiled, title="Already in the card header")
    assert "title" not in r.spec


def test_two_nominal_dimensions_are_a_heatmap(lyr):
    r, _ = spec_for(lyr, dimensions=[DimensionRef(field="region"),
                                     DimensionRef(field="intervention_type")])
    assert r.chart_type == "heatmap"
    assert r.spec["mark"]["type"] == "rect"
    assert r.spec["encoding"]["color"]["field"] == "net_gain"


# -- row-aware visual grammar ------------------------------------------

@pytest.mark.parametrize("hint,dimensions,row", [
    (None, [DimensionRef(field="region")],
     {"region": "North", "net_gain": 42_000_000}),
    ("line", [DimensionRef(field="intervention_date", grain="month")],
     {"intervention_date": "2026-01-01", "net_gain": 1250}),
    ("pie", [DimensionRef(field="region")],
     {"region": "North", "net_gain": 1250}),
])
def test_a_single_non_spatial_result_is_a_contextual_kpi(
        lyr, hint, dimensions, row):
    r, _ = spec_for(lyr, hint=hint, rows=[row], dimensions=dimensions)

    assert r.chart_type == "big_number"
    assert r.spec["usermeta"]["presentation"] == "kpi"
    assert r.spec["layer"][0]["encoding"]["text"]["field"] == "net_gain"
    assert r.spec["layer"][1]["encoding"]["text"]["field"] == dimensions[0].field
    assert r.hint_rejected is (hint is not None)


def test_one_row_with_several_measures_shows_every_kpi(lyr):
    r, _ = spec_for(
        lyr,
        rows=[{"net_gain": 1200, "cost": 45_000}],
        measures=["net_gain", "cost"],
    )

    assert r.chart_type == "big_number"
    assert [unit["layer"][0]["encoding"]["text"]["field"]
            for unit in r.spec["hconcat"]] == ["net_gain", "cost"]


def test_a_constant_heatmap_axis_collapses_to_a_ranked_bar(lyr):
    r, _ = spec_for(
        lyr,
        rows=[
            {"region": "North", "status": "COMPLETED", "net_gain": 10},
            {"region": "South", "status": "COMPLETED", "net_gain": 20},
        ],
        dimensions=[DimensionRef(field="region"), DimensionRef(field="status")],
    )

    assert r.chart_type == "bar"
    assert r.spec["encoding"]["y"]["field"] == "region"


def test_a_two_point_scatter_falls_back_to_separate_comparisons(lyr):
    r, _ = spec_for(
        lyr,
        hint="scatter",
        rows=[
            {"region": "North", "net_gain": 10, "cost": 100},
            {"region": "South", "net_gain": 20, "cost": 300},
        ],
        measures=["net_gain", "cost"],
        dimensions=[DimensionRef(field="region")],
    )

    assert r.chart_type == "faceted_bar"
    assert r.hint_rejected


@pytest.mark.parametrize("rows", [
    [
        {"region": "North", "net_gain": 10, "cost": 100},
        {"region": "South", "net_gain": 10, "cost": 100},
        {"region": "East", "net_gain": 10, "cost": 100},
    ],
    [
        {"region": "North", "net_gain": 10, "cost": 100},
        {"region": "South", "net_gain": None, "cost": 200},
        {"region": "East", "net_gain": 30, "cost": float("nan")},
    ],
])
def test_scatter_requires_three_distinct_complete_plotted_points(lyr, rows):
    result, _ = spec_for(
        lyr,
        hint="scatter",
        rows=rows,
        measures=["net_gain", "cost"],
        dimensions=[DimensionRef(field="region")],
    )

    assert result.chart_type == "faceted_bar"
    assert result.hint_rejected


def test_bubble_requires_three_distinct_xy_positions_not_only_different_sizes(lyr):
    rows = [
        {"region": region, "net_gain": 10, "cost": 100, "n_jobs": size}
        for region, size in (("North", 1), ("South", 2), ("East", 3))
    ]
    result, _ = spec_for(
        lyr,
        hint="bubble",
        rows=rows,
        measures=["net_gain", "cost", "n_jobs"],
        dimensions=[DimensionRef(field="region")],
    )

    assert result.chart_type == "faceted_bar"
    assert result.hint_rejected


@pytest.mark.parametrize("hint,measures", [
    ("scatter", ["net_gain"]),
    ("bubble", ["net_gain", "cost"]),
])
def test_relationship_hints_still_require_the_right_measure_arity(
        lyr, hint, measures):
    rows = [
        {"region": region, "net_gain": index, "cost": index * 10}
        for index, region in enumerate(("North", "South", "East"), start=1)
    ]
    result, _ = spec_for(
        lyr,
        hint=hint,
        rows=rows,
        measures=measures,
        dimensions=[DimensionRef(field="region")],
    )

    assert result.hint_rejected
    assert result.chart_type != hint


def test_mixed_raw_and_percentage_facets_are_refused_instead_of_mislabelled(lyr):
    result, _ = spec_for(
        lyr,
        rows=[
            {"intervention_date": "2026-01-01", "net_gain": 10,
             "cost_percent_of_total": 0.25},
            {"intervention_date": "2026-02-01", "net_gain": 20,
             "cost_percent_of_total": 0.75},
        ],
        measures=["net_gain", {"name": "cost", "transform": "percent_of_total"}],
        dimensions=[DimensionRef(field="intervention_date", grain="month")],
    )

    assert result.chart_type == "unplottable"
    assert "incompatible units" in result.error


def test_multi_measure_percentage_facets_keep_percentage_axes_and_tooltips(lyr):
    result, _ = spec_for(
        lyr,
        rows=[
            {"intervention_date": "2026-01-01",
             "net_gain_percent_of_total": 0.25,
             "cost_percent_of_total": 0.4},
            {"intervention_date": "2026-02-01",
             "net_gain_percent_of_total": 0.75,
             "cost_percent_of_total": 0.6},
        ],
        measures=[
            {"name": "net_gain", "transform": "percent_of_total"},
            {"name": "cost", "transform": "percent_of_total"},
        ],
        dimensions=[DimensionRef(field="intervention_date", grain="month")],
    )

    assert result.spec["spec"]["layer"][0]["encoding"]["y"]["axis"]["format"] == ".1%"
    tooltip = result.spec["spec"]["layer"][-1]["encoding"]["tooltip"]
    assert next(item for item in tooltip if item["type"] == "quantitative")["format"] == ".1%"


def test_a_single_component_normalised_bar_does_not_draw_meaningless_100_percent_bars(lyr):
    r, _ = spec_for(
        lyr,
        hint="normalised_bar",
        rows=[
            {"intervention_date": "2026-01-01", "region": "North", "net_gain": 10},
            {"intervention_date": "2026-02-01", "region": "North", "net_gain": 20},
        ],
        dimensions=[DimensionRef(field="intervention_date", grain="month"),
                    DimensionRef(field="region")],
    )

    assert r.chart_type == "line"
    assert r.hint_rejected


def test_too_many_ranked_categories_are_refused_instead_of_crushed(lyr):
    rows = [{"region": f"Region {i:02}", "net_gain": i} for i in range(25)]
    r, _ = spec_for(lyr, rows=rows, dimensions=[DimensionRef(field="region")])

    assert r.chart_type == "unplottable"
    assert "25 categories" in r.error


def test_ranked_bars_leave_a_readable_gap_between_categories(lyr):
    rows = [
        {"region": region, "net_gain": value}
        for region, value in (
            ("North", 50), ("South", 40), ("East", 30),
            ("West", 20), ("Central", 10),
        )
    ]
    result, _ = spec_for(
        lyr,
        rows=rows,
        dimensions=[DimensionRef(field="region")],
    )

    assert result.spec["usermeta"]["idealHeight"] >= 200
    assert result.spec["encoding"]["y"]["scale"] == {
        "paddingInner": 0.38,
        "paddingOuter": 0.18,
    }
    assert result.spec["mark"]["size"] <= 22


def test_an_oversized_heatmap_is_refused_instead_of_becoming_a_pixel_wall(lyr):
    rows = [
        {"region": f"Region {i}", "contractor": f"Contractor {j}", "net_gain": i + j}
        for i in range(13) for j in range(13)
    ]
    r, _ = spec_for(
        lyr,
        rows=rows,
        dimensions=[DimensionRef(field="region"), DimensionRef(field="contractor")],
    )

    assert r.chart_type == "unplottable"
    assert "169 cells" in r.error


@pytest.mark.parametrize("hint", ["bar", "point"])
def test_two_nominal_comparisons_refuse_too_many_categories(lyr, hint):
    rows = [
        {"region": f"Region {index}", "contractor": contractor, "net_gain": index}
        for index in range(25)
        for contractor in ("A", "B")
    ]
    result, _ = spec_for(
        lyr,
        hint=hint,
        rows=rows,
        dimensions=[DimensionRef(field="region"), DimensionRef(field="contractor")],
    )

    assert result.chart_type == "unplottable"
    assert "25 categories" in result.error


@pytest.mark.parametrize("hint", ["bar", "point"])
def test_two_nominal_comparisons_refuse_too_many_colour_series(lyr, hint):
    rows = [
        {"region": region, "contractor": f"Contractor {index}", "net_gain": index}
        for region in ("North", "South")
        for index in range(9)
    ]
    result, _ = spec_for(
        lyr,
        hint=hint,
        rows=rows,
        dimensions=[DimensionRef(field="region"), DimensionRef(field="contractor")],
    )

    assert result.chart_type == "unplottable"
    assert "9 series" in result.error


def test_stacked_bars_refuse_too_many_x_categories(lyr):
    rows = [
        {"region": f"Region {index}", "contractor": contractor, "net_gain": index}
        for index in range(25)
        for contractor in ("A", "B")
    ]
    result, _ = spec_for(
        lyr,
        hint="stacked_bar",
        rows=rows,
        dimensions=[DimensionRef(field="region"), DimensionRef(field="contractor")],
    )

    assert result.chart_type == "unplottable"
    assert "25 categories" in result.error


def test_a_multi_measure_matrix_does_not_silently_drop_measures(lyr):
    r, _ = spec_for(
        lyr,
        rows=[
            {"region": "North", "contractor": "A", "net_gain": 10, "cost": 100},
            {"region": "South", "contractor": "B", "net_gain": 20, "cost": 300},
        ],
        measures=["net_gain", "cost"],
        dimensions=[DimensionRef(field="region"), DimensionRef(field="contractor")],
    )

    assert r.spec["transform"][0]["fold"] == ["net_gain", "cost"]
    assert r.spec["facet"]["row"]["field"] == "measure"
    assert r.spec["spec"]["encoding"]["color"]["field"] == "value"
    assert r.spec["resolve"]["scale"]["color"] == "independent"


def test_a_map_uses_one_magnitude_channel_and_keeps_its_size_legend_on_the_right(lyr):
    compiled = compile_query(
        SemanticQuery(
            entity="production",
            measures=["oil"],
            dimensions=[DimensionRef(field="well_name")],
        ),
        lyr,
    )
    result = build_spec(
        compiled,
        [{
            "well_name": "Well 1",
            "oil": 100,
            "latitude": 47.1,
            "longitude": 51.9,
        }],
        "map",
    )

    point = result.spec["layer"][1]
    assert point["encoding"]["size"]["legend"]["orient"] == "right"
    assert point["encoding"]["size"]["legend"]["direction"] == "vertical"
    assert "color" not in point["encoding"]
    assert point["mark"]["color"] == "#1f6f63"


def test_a_multi_measure_map_hint_falls_back_without_dropping_a_measure(lyr):
    compiled = compile_query(
        SemanticQuery(
            entity="production",
            measures=["oil", "gas"],
            dimensions=[DimensionRef(field="well_name")],
        ),
        lyr,
    )
    rows = [
        {
            "well_name": f"Well {index}",
            "oil": 100 + index,
            "gas": 200 + index,
            "latitude": 47.1 + index / 10,
            "longitude": 51.9 + index / 10,
        }
        for index in range(3)
    ]

    result = build_spec(compiled, rows, "map")

    assert result.hint_rejected
    assert result.chart_type == "faceted_bar"
    assert result.spec["transform"][0]["fold"] == ["oil", "gas"]


@pytest.mark.parametrize("hint", ["stacked_bar", "normalised_bar"])
def test_a_multi_measure_stack_hint_falls_back_to_independent_facets(lyr, hint):
    rows = [
        {
            "intervention_date": date_,
            "region": region,
            "net_gain": 10,
            "cost": 100,
        }
        for date_ in ("2026-01-01", "2026-02-01")
        for region in ("North", "South")
    ]
    result, _ = spec_for(
        lyr,
        hint=hint,
        rows=rows,
        measures=["net_gain", "cost"],
        dimensions=[
            DimensionRef(field="intervention_date", grain="month"),
            DimensionRef(field="region"),
        ],
    )

    assert result.hint_rejected
    assert result.chart_type == "faceted_line"
    assert result.spec["transform"][0]["fold"] == ["net_gain", "cost"]


def test_a_four_measure_bubble_hint_falls_back_without_dropping_the_fourth(lyr):
    rows = [
        {
            "region": region,
            "net_gain": 10,
            "cost": 100,
            "n_jobs": 2,
            "avg_net_gain": 5,
        }
        for region in ("North", "South", "East")
    ]
    result, _ = spec_for(
        lyr,
        hint="bubble",
        rows=rows,
        measures=["net_gain", "cost", "n_jobs", "avg_net_gain"],
        dimensions=[DimensionRef(field="region")],
    )

    assert result.hint_rejected
    assert result.chart_type == "faceted_bar"
    assert result.spec["transform"][0]["fold"] == [
        "net_gain", "cost", "n_jobs", "avg_net_gain"]


def test_three_dimensions_with_multiple_measures_are_refused_instead_of_dropping_measures(lyr):
    rows = [
        {
            "intervention_date": date_,
            "region": region,
            "contractor": contractor,
            "net_gain": 10,
            "cost": 100,
        }
        for date_, region, contractor in (
            ("2026-01-01", "North", "A"),
            ("2026-02-01", "South", "B"),
        )
    ]
    result, _ = spec_for(
        lyr,
        rows=rows,
        measures=["net_gain", "cost"],
        dimensions=[
            DimensionRef(field="intervention_date", grain="month"),
            DimensionRef(field="region"),
            DimensionRef(field="contractor"),
        ],
    )

    assert result.chart_type == "unplottable"
    assert "multiple measures" in result.error


def test_three_nominal_facets_refuse_an_oversized_x_axis(lyr):
    rows = [
        {
            "region": region,
            "contractor": f"Contractor {index}",
            "intervention_type": intervention_type,
            "net_gain": index,
        }
        for index in range(25)
        for region in ("North", "South")
        for intervention_type in ("Repair", "Inspection")
    ]
    result, _ = spec_for(
        lyr,
        rows=rows,
        dimensions=[
            DimensionRef(field="region"),
            DimensionRef(field="contractor"),
            DimensionRef(field="intervention_type"),
        ],
    )

    assert result.chart_type == "unplottable"
    assert "25 categories" in result.error


# -- the invariants that make a model-authored spec unnecessary ----------

@pytest.mark.parametrize("kw", [
    {},
    {"dimensions": [DimensionRef(field="region")]},
    {"dimensions": [DimensionRef(field="intervention_date", grain="month")]},
    {"measures": ["net_gain", "cost"], "dimensions": [DimensionRef(field="region")]},
    {"measures": ["net_gain", "cost"],
     "dimensions": [DimensionRef(field="intervention_date", grain="day"),
                    DimensionRef(field="region")]},
    {"dimensions": [DimensionRef(field="region"), DimensionRef(field="contractor")]},
])
def test_no_spec_ever_carries_a_reaggregating_transform(lyr, kw):
    """The failure a field-membership cross-check cannot catch: Vega-Lite
    re-aggregating rows the compiler already grouped, so the chart
    contradicts the header above it."""
    r, _ = spec_for(lyr, **kw)
    assert not any("aggregate" in node or "joinaggregate" in node or "pivot" in node
                   for node in walk(r.spec))


@pytest.mark.parametrize("kw", [
    {},
    {"dimensions": [DimensionRef(field="region")]},
    {"measures": ["net_gain", "cost"],
     "dimensions": [DimensionRef(field="intervention_date", grain="month"),
                    DimensionRef(field="region")]},
    {"dimensions": [DimensionRef(field="region"), DimensionRef(field="contractor")]},
])
def test_every_spec_field_is_an_output_column(lyr, kw):
    """The design's cross-check. True by construction here; asserted anyway."""
    r, compiled = spec_for(lyr, **kw)
    folded = {"measure", "value"}
    fields = {n["field"] for n in walk(r.spec) if "field" in n}
    assert fields - folded <= set(compiled.columns)


def test_encoding_types_come_from_the_compiler_not_the_data(lyr):
    r, compiled = spec_for(lyr, dimensions=[DimensionRef(field="intervention_date",
                                                          grain="month")])
    assert r.spec["encoding"]["x"]["type"] == compiled.column_kinds["intervention_date"]


# -- chart_hint ----------------------------------------------------------

def test_a_fitting_hint_is_honoured(lyr):
    r, _ = spec_for(lyr, hint="area",
                    dimensions=[DimensionRef(field="intervention_date", grain="month")])
    assert r.chart_type == "area"
    assert not r.hint_rejected


def test_a_hint_that_does_not_fit_falls_back_and_says_so(lyr):
    """'line' over a nominal dimension is meaningless; the UI is told
    rather than silently disagreeing with the manager."""
    r, _ = spec_for(lyr, hint="line", dimensions=[DimensionRef(field="region")])
    assert r.chart_type == "bar"
    assert r.hint_rejected


def test_big_number_hint_is_rejected_when_dimensions_exist(lyr):
    r, _ = spec_for(lyr, hint="big_number", dimensions=[DimensionRef(field="region")])
    assert r.hint_rejected


def test_heatmap_hint_is_rejected_without_two_nominals(lyr):
    r, _ = spec_for(lyr, hint="heatmap", dimensions=[DimensionRef(field="region")])
    assert r.hint_rejected
