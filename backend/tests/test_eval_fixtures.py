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


# -- the visualisation suite ---------------------------------------------

def test_the_viz_suite_is_thirty_fixtures_with_unique_ids():
    import yaml

    from eval.run_eval import SUITES

    viz = yaml.safe_load(SUITES["viz"].read_text())
    assert len(viz) == 30
    assert len({f["id"] for f in viz}) == 30


def test_no_question_appears_in_both_suites():
    """The same question scored twice is the same evidence counted twice."""
    import yaml

    from eval.run_eval import SUITES

    a = {f["question"] for f in yaml.safe_load(SUITES["queries"].read_text())}
    b = {f["question"] for f in yaml.safe_load(SUITES["viz"].read_text())}
    assert not (a & b)


def test_every_answerable_viz_fixture_declares_a_chart():
    """A fixture with no expected_chart contributes nothing to the metric
    the suite exists for."""
    import yaml

    from eval.run_eval import SUITES

    for fx in yaml.safe_load(SUITES["viz"].read_text()):
        if fx.get("expect") == "refused":
            assert "expected_chart" not in fx
        else:
            assert "expected_chart" in fx, fx["id"]


def test_every_expected_viz_query_is_valid_and_runs(layer, warehouse_conn):
    """A fixture the compiler rejects measures the fixture, not the model."""
    import yaml

    from app.semantic.compile import compile_query
    from app.semantic.query import SemanticQuery
    from eval.run_eval import SUITES

    for fx in yaml.safe_load(SUITES["viz"].read_text()):
        if fx.get("expect") == "refused":
            continue
        compiled = compile_query(SemanticQuery.model_validate(fx["expected"]),
                                 layer)
        with warehouse_conn.cursor() as cur:
            cur.execute(compiled.sql, compiled.params)
            cur.fetchall()


def test_the_new_chart_vocabulary_is_covered(layer):
    """Each type added with this work should be reachable from at least one
    fixture, or the suite is not exercising what it was written for."""
    import yaml

    from eval.run_eval import SUITES

    charts = {fx["expected_chart"]
              for fx in yaml.safe_load(SUITES["viz"].read_text())
              if "expected_chart" in fx}
    assert {"pie", "donut", "stacked_bar", "normalised_bar", "scatter",
            "bubble", "map", "faceted_line", "faceted_bar",
            "unplottable"} <= charts


def test_the_builder_agrees_with_what_each_question_deserves(layer):
    """The reconciliation, run offline and for free.

    Comparing intent against the builder needs only the *expected* query,
    so it costs no model call at all -- the model is what the other three
    metrics measure, not this one.

    On the strength of a clean result: the ordering is verifiable in git
    (the fixtures were committed before any of the builder existed, so no
    expectation could have been read off it), but the author is not
    independent, since the same session wrote both. Treat agreement here as
    a regression net rather than as outside validation. A disagreement is
    the interesting event, and it is a finding about the shape rules, not a
    fixture to quietly correct.
    """
    import yaml

    from app.render import render
    from app.semantic.query import SemanticQuery
    from eval.run_eval import SUITES

    disagreements = []
    for fx in yaml.safe_load(SUITES["viz"].read_text()):
        if fx.get("expect") == "refused":
            continue
        drawn = render(SemanticQuery.model_validate(fx["expected"]), layer,
                       chart_hint=fx.get("hint"))
        built = drawn.chart_type or "unplottable"
        if built != fx["expected_chart"]:
            disagreements.append(
                f"{fx['id']}: wanted {fx['expected_chart']}, drew {built}")
        if "expected_hint_rejected" in fx:
            assert drawn.hint_rejected == fx["expected_hint_rejected"], fx["id"]

    assert not disagreements, "\n".join(disagreements)
