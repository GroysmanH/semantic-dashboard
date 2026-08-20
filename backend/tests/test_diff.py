"""The diff is the trust surface for an edit: what moved, in words."""

import pytest

from app.semantic.diff import diff_queries
from app.semantic.query import SemanticQuery


@pytest.fixture
def entity(layer):
    return layer["production"]


def q(**kw):
    return SemanticQuery.model_validate({"entity": "production",
                                         "measures": ["oil"], **kw})


def test_an_unchanged_query_has_nothing_to_report(entity):
    """The path that matters most: identical query, different chart. It
    re-renders from cache without touching the warehouse, and saying
    "nothing changed" about the data is exactly right."""
    assert diff_queries(q(), q(), entity) == []


def test_a_new_breakdown_is_named_in_human_words(entity):
    assert diff_queries(q(), q(dimensions=[{"field": "region"}]), entity) \
        == ["broke down by region"]


def test_a_dropped_breakdown_says_so(entity):
    assert diff_queries(q(dimensions=[{"field": "region"}]), q(), entity) \
        == ["stopped breaking down by region"]


def test_a_grain_change_shows_both_sides(entity):
    out = diff_queries(
        q(dimensions=[{"field": "reading_date", "grain": "month"}]),
        q(dimensions=[{"field": "reading_date", "grain": "quarter"}]), entity)
    assert out == ["reading date month → quarter"]


def test_measures_use_their_labels(entity):
    assert diff_queries(q(), q(measures=["oil", "gas"]), entity) \
        == ["added gas production"]


def test_a_transformed_measure_is_a_different_measure(entity):
    """`oil` and its running total are separate columns, so swapping one
    for the other is an add and a remove rather than silence."""
    out = diff_queries(
        q(), q(measures=[{"name": "oil", "transform": "cumulative"}],
               dimensions=[{"field": "reading_date", "grain": "month"}]),
        entity)
    assert "added oil production" in out
    assert "removed oil production" in out


def test_filters_report_their_value(entity):
    out = diff_queries(
        q(), q(filters=[{"field": "region", "op": "=", "value": "Atyrau"}]),
        entity)
    assert out == ["filtered to region = Atyrau"]


def test_a_changed_filter_shows_the_move(entity):
    out = diff_queries(
        q(filters=[{"field": "reading_date", "op": "in_year", "value": 2025}]),
        q(filters=[{"field": "reading_date", "op": "in_year", "value": 2026}]),
        entity)
    assert out == ["reading date filter in_year 2025 → in_year 2026"]


def test_limit_and_ordering_are_reported(entity):
    out = diff_queries(
        q(dimensions=[{"field": "region"}]),
        q(dimensions=[{"field": "region"}], limit=5,
          order_by=[{"field": "oil", "dir": "desc"}]), entity)
    assert "ordered by oil production desc" in out
    assert "limit 100 → 5" in out


def test_switching_entity_stops_rather_than_producing_nonsense(entity):
    """Every field name downstream belongs to a different vocabulary, so
    comparing them phrase by phrase would read like gibberish."""
    out = diff_queries(q(), SemanticQuery.model_validate(
        {"entity": "well_interventions", "measures": ["net_gain"]}), entity)
    assert out == ["switched from production to well_interventions"]
