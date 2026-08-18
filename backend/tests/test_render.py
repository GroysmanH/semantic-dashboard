"""The render pipeline, including the two states the design cares about
most: served-from-cache, and broken-because-the-layer-moved."""

import copy

import pytest

from app.cache import cache_key, envelope, is_fresh, now
from app.render import render
from app.semantic.query import DimensionRef, Filter, SemanticQuery


@pytest.fixture
def lyr(layer):
    l = copy.deepcopy(layer)
    l["well_interventions"].dimensions["status"].confidence = "high"
    return l


def q(**kw):
    base = {"entity": "production", "measures": ["oil"]}
    return SemanticQuery(**{**base, **kw})


# -- happy path ----------------------------------------------------------

def test_render_produces_everything_a_card_needs(lyr):
    r = render(q(dimensions=[DimensionRef(field="reading_date", grain="month")]), lyr)
    assert r.state == "ready"
    assert r.row_count > 0
    assert r.vega_spec["encoding"]["x"]["type"] == "temporal"
    assert r.restatement.startswith("Sum of oil production")
    assert r.data_max_ts is not None
    assert not r.from_cache


# -- cache ---------------------------------------------------------------

def test_a_fresh_cache_is_reused_without_touching_the_database(lyr):
    query = q()
    first = render(query, lyr)
    second = render(query, lyr, cache=first.cache, ttl_seconds=900)
    assert second.from_cache
    assert second.rows == first.rows
    assert second.fetched_at == first.fetched_at


def test_force_bypasses_a_fresh_cache(lyr):
    query = q()
    first = render(query, lyr)
    second = render(query, lyr, cache=first.cache, ttl_seconds=900, force=True)
    assert not second.from_cache


def test_an_expired_cache_is_refetched(lyr):
    query = q()
    first = render(query, lyr)
    second = render(query, lyr, cache=first.cache, ttl_seconds=0)
    assert not second.from_cache


def test_a_cache_from_a_different_query_is_not_reused(lyr):
    """Changing the question must not be answered with the old numbers."""
    first = render(q(), lyr)
    second = render(q(measures=["gas"]), lyr, cache=first.cache, ttl_seconds=900)
    assert not second.from_cache
    assert "gas" in second.rows[0]


def test_cache_key_changes_with_the_query(lyr):
    a = render(q(), lyr)
    b = render(q(measures=["gas"]), lyr)
    assert a.cache["key"] != b.cache["key"]


def test_is_fresh_rejects_an_empty_cache():
    assert not is_fresh(None, 900)
    assert not is_fresh({}, 900)


# -- broken cards --------------------------------------------------------

def test_a_query_whose_field_vanished_renders_broken_naming_the_field(lyr):
    """Not stale-but-pretty cached numbers, and not a model quietly
    rewriting what the chart means."""
    query = q(dimensions=[DimensionRef(field="region")])
    good = render(query, lyr)
    assert good.state == "ready"

    moved = copy.deepcopy(lyr)
    del moved["production"].dimensions["region"]

    broken = render(query, moved, cache=good.cache)
    assert broken.state == "broken"
    assert broken.error_reason == "unknown_dimension"
    assert "region" in broken.error
    assert broken.rows == []          # no stale numbers leak through


def test_a_broken_card_does_not_serve_its_cache(lyr):
    query = q()
    good = render(query, lyr)
    moved = copy.deepcopy(lyr)
    del moved["production"].measures["oil"]
    broken = render(query, moved, cache=good.cache, ttl_seconds=900)
    assert broken.state == "broken"
    assert broken.row_count == 0


def test_the_confidence_gate_renders_as_broken_naming_the_field(layer):
    r = render(SemanticQuery(entity="well_interventions", measures=["n_jobs"]), layer)
    assert r.state == "broken"
    assert r.error_reason == "unverified_layer"
    assert "dimension status" in r.error


# -- chart hint routing (the basis for refinement without a re-query) ----

def test_the_same_query_with_a_different_hint_reuses_the_cache(lyr):
    query = q(dimensions=[DimensionRef(field="reading_date", grain="month")])
    first = render(query, lyr)
    second = render(query, lyr, chart_hint="area", cache=first.cache, ttl_seconds=900)
    assert second.from_cache            # no warehouse scan for a chart flip
    assert second.chart_type == "area"
    assert second.vega_spec != first.vega_spec


def test_the_restatement_states_meaning_and_not_provenance(lyr):
    """Row count and freshness travel in their own fields. Putting them in
    the sentence too made the card print both twice."""
    r = render(q(), lyr)
    assert "rows" not in r.restatement
    assert "data through" not in r.restatement
    assert r.row_count > 0 and r.data_max_ts is not None
