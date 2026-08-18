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


def spec_for(lyr, hint=None, **kw):
    base = {"entity": "well_interventions", "measures": ["net_gain"]}
    compiled = compile_query(SemanticQuery(**{**base, **kw}), lyr)
    return build_spec(compiled, hint), compiled


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
    assert r.spec["mark"]["type"] == "text"


def test_one_temporal_dimension_is_a_line(lyr):
    r, _ = spec_for(lyr, dimensions=[DimensionRef(field="intervention_date", grain="month")])
    assert r.chart_type == "line"
    assert r.spec["encoding"]["x"]["type"] == "temporal"
    assert r.spec["encoding"]["y"]["field"] == "net_gain"


def test_one_nominal_dimension_is_a_sorted_bar(lyr):
    r, _ = spec_for(lyr, dimensions=[DimensionRef(field="region")])
    assert r.chart_type == "bar"
    assert r.spec["encoding"]["x"]["type"] == "nominal"
    assert r.spec["encoding"]["x"]["sort"] == "-y"


def test_ascending_order_by_flips_the_sort(lyr):
    r, _ = spec_for(lyr, dimensions=[DimensionRef(field="region")],
                    order_by=[OrderBy(field="net_gain", dir="asc")])
    assert r.spec["encoding"]["x"]["sort"] == "y"


def test_several_measures_over_a_temporal_dimension_fold_into_a_series(lyr):
    r, _ = spec_for(lyr, measures=["net_gain", "cost"],
                    dimensions=[DimensionRef(field="intervention_date", grain="month")])
    assert r.spec["transform"][0]["fold"] == ["net_gain", "cost"]
    assert r.spec["encoding"]["color"]["field"] == "measure"


def test_several_measures_over_a_nominal_dimension_group_the_bars(lyr):
    r, _ = spec_for(lyr, measures=["net_gain", "cost"],
                    dimensions=[DimensionRef(field="region")])
    assert r.spec["encoding"]["xOffset"]["field"] == "measure"


def test_temporal_plus_nominal_is_a_multi_series_line(lyr):
    r, _ = spec_for(lyr, dimensions=[DimensionRef(field="intervention_date", grain="month"),
                                     DimensionRef(field="region")])
    assert r.chart_type == "line"
    assert r.spec["encoding"]["color"]["field"] == "region"


def test_temporal_plus_nominal_with_two_measures_facets_by_row(lyr):
    """A requested measure is never silently dropped."""
    r, _ = spec_for(lyr, measures=["net_gain", "cost"],
                    dimensions=[DimensionRef(field="intervention_date", grain="month"),
                                DimensionRef(field="region")])
    assert r.spec["encoding"]["row"]["field"] == "measure"
    assert r.spec["transform"][0]["fold"] == ["net_gain", "cost"]


def test_two_nominal_dimensions_are_a_heatmap(lyr):
    r, _ = spec_for(lyr, dimensions=[DimensionRef(field="region"),
                                     DimensionRef(field="intervention_type")])
    assert r.chart_type == "heatmap"
    assert r.spec["mark"]["type"] == "rect"
    assert r.spec["encoding"]["color"]["field"] == "net_gain"


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
def test_no_spec_ever_carries_an_aggregate_key(lyr, kw):
    """The failure a field-membership cross-check cannot catch: Vega-Lite
    re-aggregating rows the compiler already grouped, so the chart
    contradicts the header above it."""
    r, _ = spec_for(lyr, **kw)
    assert not any("aggregate" in node for node in walk(r.spec))


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
