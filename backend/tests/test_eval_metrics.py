"""The relaxed metric decides the headline number, so it is tested like
code. It must forgive a harmless default and must not forgive a dropped
ranking."""

import pytest

from app.semantic.query import SemanticQuery

from eval.run_eval import relaxed_match


def q(**kw):
    return SemanticQuery(**{"entity": "production", "measures": ["oil"], **kw})


def test_an_added_order_by_is_forgiven_when_the_fixture_had_no_opinion():
    expected = q()
    got = q(order_by=[{"field": "oil", "dir": "desc"}])
    assert relaxed_match(got, expected, {"entity": "production", "measures": ["oil"]})


def test_a_dropped_ranking_is_not_forgiven_when_the_fixture_asked_for_one():
    """'top 5 fields by oil' means the ordering is the question."""
    raw = {"entity": "production", "measures": ["oil"],
           "dimensions": [{"field": "field_name"}],
           "order_by": [{"field": "oil", "dir": "desc"}], "limit": 5}
    expected = SemanticQuery.model_validate(raw)
    got = q(dimensions=[{"field": "field_name"}], limit=5)
    assert not relaxed_match(got, expected, raw)


def test_a_wrong_limit_is_not_forgiven_when_specified():
    raw = {"entity": "production", "measures": ["oil"], "limit": 5}
    expected = SemanticQuery.model_validate(raw)
    assert not relaxed_match(q(limit=100), expected, raw)


def test_measure_order_does_not_matter():
    raw = {"entity": "production", "measures": ["oil", "gas"]}
    expected = SemanticQuery.model_validate(raw)
    assert relaxed_match(q(measures=["gas", "oil"]), expected, raw)


@pytest.mark.parametrize("wrong", [
    {"entity": "well_interventions", "measures": ["net_gain"]},
    {"measures": ["gas"]},
    {"dimensions": [{"field": "region"}]},
    {"filters": [{"field": "reading_date", "op": "in_year", "value": 2026}]},
    {"dimensions": [{"field": "reading_date", "grain": "day"}]},
])
def test_real_differences_are_never_forgiven(wrong):
    raw = {"entity": "production", "measures": ["oil"],
           "dimensions": [{"field": "reading_date", "grain": "month"}]}
    expected = SemanticQuery.model_validate(raw)
    got = q(**{"dimensions": [{"field": "reading_date", "grain": "month"}], **wrong})
    assert not relaxed_match(got, expected, raw)
