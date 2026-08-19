"""The chart types added with the analytical grammar.

These run against real rows because three of the new rules cannot be
decided without them -- slice counts, negative values, and how many
distinct values a dimension actually has.
"""

import pytest

from app.db import warehouse_pool
from app.render import render
from app.semantic.chart import MAX_FACETS, MAX_SLICES, OTHER, build_spec
from app.semantic.compile import compile_query
from app.semantic.query import SemanticQuery


def q(**kw):
    return SemanticQuery.model_validate({"entity": "production", **kw})


def drawn(layer, hint=None, **kw):
    return render(q(**kw), layer, chart_hint=hint)


def walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


# -- the shape table -----------------------------------------------------

CASES = [
    ("pie", "pie", dict(measures=["oil"], dimensions=[{"field": "region"}])),
    ("donut", "donut", dict(measures=["oil"], dimensions=[{"field": "region"}])),
    ("stacked_bar", "stacked_bar",
     dict(measures=["oil"], dimensions=[{"field": "reading_date", "grain": "month"},
                                        {"field": "region"}])),
    ("normalised_bar", "normalised_bar",
     dict(measures=["oil"], dimensions=[{"field": "reading_date", "grain": "month"},
                                        {"field": "region"}])),
    ("scatter", "scatter",
     dict(measures=["oil", "gas"], dimensions=[{"field": "region"}])),
    ("bubble", "bubble",
     dict(measures=["oil", "gas", "water"], dimensions=[{"field": "field_name"}])),
    ("map", "map", dict(measures=["oil"], dimensions=[{"field": "well_name"}])),
]


@pytest.mark.parametrize("hint,expected,body", CASES,
                         ids=[c[0] for c in CASES])
def test_each_new_hint_produces_its_chart(layer, hint, expected, body):
    r = drawn(layer, hint, **body)
    assert r.chart_type == expected
    assert not r.hint_rejected


def test_scatter_takes_over_when_the_bars_would_be_unreadable(layer):
    """Two measures over 200 wells is 400 bars. No hint: the shape rule
    alone has to choose."""
    r = drawn(layer, measures=["oil", "gas"],
              dimensions=[{"field": "well_name"}], limit=300)
    assert r.chart_type == "scatter"


def test_a_grouped_bar_survives_at_small_cardinality(layer):
    """The other side of the same threshold: five regions still read best
    as bars, so scatter must not take over."""
    r = drawn(layer, measures=["oil", "gas"], dimensions=[{"field": "region"}])
    assert r.chart_type == "bar"


# -- pie guards ----------------------------------------------------------

def test_pie_is_refused_on_an_average(layer):
    """Means do not add up to a whole. The sentence that produces this is
    entirely natural, which is what makes the guard worth having."""
    r = drawn(layer, "pie", measures=["avg_oil"], dimensions=[{"field": "region"}])
    assert r.chart_type == "bar"
    assert r.hint_rejected


def test_pie_is_refused_when_the_result_was_truncated(layer):
    """A pie is a claim about a whole. Two hundred wells against a limit of
    a hundred means "Other" would stand for rows never fetched."""
    r = drawn(layer, "pie", measures=["oil"],
              dimensions=[{"field": "well_name"}], limit=100)
    assert r.chart_type == "bar"
    assert r.hint_rejected


def test_pie_is_refused_on_negative_values(layer):
    """A negative slice has no angle."""
    r = drawn(layer, "pie", measures=[{"name": "oil", "transform": "period_change"}],
              dimensions=[{"field": "reading_date", "grain": "month"}])
    assert r.chart_type != "pie"


def test_a_long_tail_collapses_and_says_so(layer):
    r = drawn(layer, "pie", measures=["oil"], dimensions=[{"field": "well_name"}],
              filters=[{"field": "region", "op": "=", "value": "Atyrau"}])
    assert r.chart_type == "pie"
    assert r.chart_rows is not None
    assert len(r.chart_rows) == MAX_SLICES
    assert r.chart_rows[-1]["well_name"] == OTHER
    # The row count and the SQL still describe the full result.
    assert r.row_count > MAX_SLICES
    assert f"grouped as {OTHER}" in r.restatement


def test_the_other_bucket_sums_the_tail_it_replaces(layer):
    r = drawn(layer, "pie", measures=["oil"], dimensions=[{"field": "well_name"}],
              filters=[{"field": "region", "op": "=", "value": "Atyrau"}])
    total = sum(float(row["oil"]) for row in r.rows)
    shown = sum(float(row["oil"]) for row in r.chart_rows)
    assert shown == pytest.approx(total)


def test_a_collapse_never_happens_silently(layer):
    """The chart shows eight of twenty-four categories, which means
    something different from showing all of them. If chart_rows differ from
    rows, the sentence has to say why."""
    for body in [dict(measures=["oil"], dimensions=[{"field": "region"}]),
                 dict(measures=["oil"], dimensions=[{"field": "well_name"}],
                      filters=[{"field": "region", "op": "=", "value": "Atyrau"}])]:
        r = drawn(layer, "pie", **body)
        if r.chart_rows is not None:
            assert "grouped as" in r.restatement


# -- faceting ------------------------------------------------------------

def test_a_third_dimension_becomes_a_facet(layer):
    r = drawn(layer, measures=["gas"],
              dimensions=[{"field": "reading_date", "grain": "quarter"},
                          {"field": "region"}, {"field": "well_type"}], limit=500)
    assert r.chart_type == "faceted_line"
    assert "facet" in r.vega_spec


def test_three_nominal_dimensions_facet_a_bar(layer):
    r = drawn(layer, measures=["oil"],
              dimensions=[{"field": "region"}, {"field": "well_type"},
                          {"field": "field_name"}], limit=500)
    assert r.chart_type == "faceted_bar"


def test_a_dense_third_dimension_breaks_the_card_by_name(layer):
    """Checking only the facet channel would move the problem to colour
    rather than catch it: the sparsest dimension faces, and the dense one
    would land on a two-hundred-entry legend."""
    r = drawn(layer, measures=["oil"],
              dimensions=[{"field": "reading_date", "grain": "month"},
                          {"field": "region"}, {"field": "well_name"}], limit=5000)
    assert r.state == "broken"
    assert r.error_reason == "unplottable"
    assert "well" in r.error
    assert r.vega_spec is None


# -- transform-aware encoding --------------------------------------------

def test_a_share_is_drawn_as_a_percentage(layer):
    r = drawn(layer, measures=[{"name": "oil", "transform": "percent_of_total"}],
              dimensions=[{"field": "region"}])
    assert r.vega_spec["encoding"]["y"]["axis"]["format"] == ".1%"


def test_a_running_total_fills(layer):
    r = drawn(layer, measures=[{"name": "oil", "transform": "cumulative"}],
              dimensions=[{"field": "reading_date", "grain": "month"}])
    assert r.chart_type == "area"


def test_a_signed_change_gets_a_zero_baseline_and_reads_as_signed(layer):
    """Drawn as a line in one colour, a month of decline looks exactly like
    a month of growth. Correct, and backwards."""
    r = drawn(layer, measures=[{"name": "oil", "transform": "period_change"}],
              dimensions=[{"field": "reading_date", "grain": "month"}])
    assert r.chart_type == "bar"
    assert r.vega_spec["encoding"]["y"]["scale"]["zero"] is True
    assert "condition" in r.vega_spec["encoding"]["color"]


# -- the invariants, across every type -----------------------------------

ALL_SHAPES = [
    (None, dict(measures=["oil"])),
    (None, dict(measures=["oil"], dimensions=[{"field": "region"}])),
    ("pie", dict(measures=["oil"], dimensions=[{"field": "region"}])),
    ("donut", dict(measures=["oil"], dimensions=[{"field": "region"}])),
    ("map", dict(measures=["oil"], dimensions=[{"field": "well_name"}])),
    ("scatter", dict(measures=["oil", "gas"], dimensions=[{"field": "region"}])),
    ("bubble", dict(measures=["oil", "gas", "water"],
                    dimensions=[{"field": "field_name"}])),
    ("stacked_bar", dict(measures=["oil"],
                         dimensions=[{"field": "reading_date", "grain": "month"},
                                     {"field": "region"}])),
    ("normalised_bar", dict(measures=["oil"],
                            dimensions=[{"field": "reading_date", "grain": "month"},
                                        {"field": "region"}])),
    ("heatmap", dict(measures=["oil"], dimensions=[{"field": "region"},
                                                   {"field": "well_type"}])),
    (None, dict(measures=["gas"],
                dimensions=[{"field": "reading_date", "grain": "quarter"},
                            {"field": "region"}, {"field": "well_type"}], limit=500)),
]


@pytest.mark.parametrize("hint,body", ALL_SHAPES)
def test_no_spec_ever_carries_an_aggregate_key(layer, hint, body):
    """Vega-Lite's own aggregation would compute means over rows the
    compiler already summed -- a chart that quietly contradicts the header
    while every field in it is real."""
    r = drawn(layer, hint, **body)
    assert all("aggregate" not in node for node in walk(r.vega_spec))


@pytest.mark.parametrize("hint,body", ALL_SHAPES)
def test_every_spec_field_is_an_output_column(layer, hint, body):
    compiled = compile_query(q(**body), layer)
    allowed = set(compiled.columns) | {"measure", "value"}
    if compiled.entity.geo:
        allowed |= {compiled.entity.geo.lat.split(".")[-1],
                    compiled.entity.geo.lon.split(".")[-1]}
    r = drawn(layer, hint, **body)
    for node in walk(r.vega_spec):
        if "field" in node and isinstance(node["field"], str):
            assert node["field"] in allowed, node
