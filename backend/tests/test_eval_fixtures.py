"""The fixtures are the yardstick, so they get checked like code.

A wrong expected-query silently caps the score and looks like a model
failure, which is the worst possible bug in an eval.
"""

from pathlib import Path

import pytest
import yaml

from app.semantic.compile import compile_query
from app.semantic.query import SemanticQuery
from app.semantic.validate import QueryValidationError, validate_query

FIXTURES = yaml.safe_load(
    (Path(__file__).resolve().parents[1] / "eval" / "fixtures.yaml").read_text())

ANSWERABLE = [f for f in FIXTURES if f.get("expect") != "refused"]
REFUSALS = [f for f in FIXTURES if f.get("expect") == "refused"]


def test_there_are_thirty_fixtures():
    assert len(FIXTURES) == 30


def test_ids_are_unique():
    ids = [f["id"] for f in FIXTURES]
    assert len(ids) == len(set(ids))


def test_a_meaningful_share_must_be_refused():
    """A harness that only scores successes overstates the system."""
    assert len(REFUSALS) >= 5


def test_every_fixture_has_a_question_and_tags():
    for f in FIXTURES:
        assert f["question"].strip()
        assert f["tags"]


def test_every_refusal_explains_itself():
    for f in REFUSALS:
        assert f.get("because"), f["id"]


@pytest.mark.parametrize("fx", ANSWERABLE, ids=[f["id"] for f in ANSWERABLE])
def test_expected_queries_are_valid_against_the_layer(fx, layer):
    q = SemanticQuery.model_validate(fx["expected"])
    validate_query(q, layer)


@pytest.mark.parametrize("fx", ANSWERABLE, ids=[f["id"] for f in ANSWERABLE])
def test_expected_queries_compile_and_execute(fx, layer, warehouse_conn):
    q = SemanticQuery.model_validate(fx["expected"])
    compiled = compile_query(q, layer)
    with warehouse_conn.cursor() as cur:
        cur.execute(compiled.sql, compiled.params)
        cur.fetchall()


@pytest.mark.parametrize("fx", ANSWERABLE, ids=[f["id"] for f in ANSWERABLE])
def test_canonical_and_relaxed_forms_round_trip(fx):
    q = SemanticQuery.model_validate(fx["expected"])
    assert SemanticQuery.model_validate(q.canonical()).canonical() == q.canonical()
    assert q.relaxed() == SemanticQuery.model_validate(q.canonical()).relaxed()


def test_relaxed_matching_ignores_ordering_but_exact_does_not():
    """The two metrics have to actually differ, or reporting both is theatre."""
    a = SemanticQuery(entity="production", measures=["oil", "gas"])
    b = SemanticQuery(entity="production", measures=["gas", "oil"])
    assert a.canonical() != b.canonical()
    assert a.relaxed() == b.relaxed()


@pytest.mark.parametrize("fx", REFUSALS, ids=[f["id"] for f in REFUSALS])
def test_refusal_fixtures_are_genuinely_unanswerable(fx, layer):
    """Either the layer gates the entity, or the grammar cannot express the
    question at all. Neither is a matter of the model trying harder."""
    expected = fx.get("expected")
    if expected is None:
        return                       # not expressible; nothing to construct
    with pytest.raises((QueryValidationError, ValueError)):
        validate_query(SemanticQuery.model_validate(expected), layer)
