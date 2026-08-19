"""Validation runs before any database contact. Every refusal names what
is undefined -- a confident wrong chart is the one failure the design
cannot recover from."""

import copy

import pytest

from app.semantic.query import DimensionRef, Filter, OrderBy, SemanticQuery
from app.semantic.validate import QueryValidationError, validate_query


@pytest.fixture
def verified_layer(layer):
    """The real layer with the confidence gate lifted on well_interventions,
    so the other rules can be exercised. test_confidence_gate_* below use
    the real, gated layer."""
    lyr = copy.deepcopy(layer)
    lyr["well_interventions"].dimensions["status"].confidence = "high"
    return lyr


def q(**kw) -> SemanticQuery:
    base = {"entity": "well_interventions", "measures": ["net_gain"]}
    return SemanticQuery(**{**base, **kw})


# -- happy path ----------------------------------------------------------

def test_valid_query_returns_the_entity(verified_layer):
    entity = validate_query(
        q(
            dimensions=[DimensionRef(field="intervention_date", grain="month"),
                        DimensionRef(field="region")],
            filters=[Filter(field="intervention_date", op="in_year", value=2026),
                     Filter(field="status", op="=", value="COMPLETED")],
            order_by=[OrderBy(field="net_gain", dir="desc")],
        ),
        verified_layer,
    )
    assert entity.name == "well_interventions"


# -- 1. entity -----------------------------------------------------------

def test_unknown_entity_is_rejected(verified_layer):
    with pytest.raises(QueryValidationError) as e:
        validate_query(q(entity="revenue"), verified_layer)
    assert e.value.reason == "unknown_entity"
    assert "well_interventions" in e.value.detail   # names what is available


# -- 2. confidence gate (entity-level, on the real layer) ----------------

def test_confidence_gate_refuses_the_whole_entity(layer):
    """A query touching none of the unverified fields is still refused.
    That harshness is the forcing function for layer review."""
    with pytest.raises(QueryValidationError) as e:
        validate_query(q(measures=["n_jobs"]), layer)
    assert e.value.reason == "unverified_layer"
    assert "dimension status" in e.value.detail


def test_confidence_gate_does_not_block_a_verified_entity(layer):
    validate_query(SemanticQuery(entity="production", measures=["oil"]), layer)


# -- 3. measures ---------------------------------------------------------

def test_unknown_measure_is_rejected(verified_layer):
    with pytest.raises(QueryValidationError) as e:
        validate_query(q(measures=["revenue"]), verified_layer)
    assert e.value.reason == "unknown_measure"


# -- 4. dimensions and grains -------------------------------------------

def test_unknown_dimension_is_rejected(verified_layer):
    with pytest.raises(QueryValidationError) as e:
        validate_query(q(dimensions=[DimensionRef(field="rig")]), verified_layer)
    assert e.value.reason == "unknown_dimension"


def test_grain_on_a_string_dimension_is_rejected(verified_layer):
    with pytest.raises(QueryValidationError) as e:
        validate_query(q(dimensions=[DimensionRef(field="region", grain="month")]),
                       verified_layer)
    assert e.value.reason == "grain_on_non_date"


def test_undeclared_grain_is_rejected(verified_layer):
    lyr = copy.deepcopy(verified_layer)
    lyr["well_interventions"].dimensions["intervention_date"].grains = ["day", "month"]
    with pytest.raises(QueryValidationError) as e:
        validate_query(
            q(dimensions=[DimensionRef(field="intervention_date", grain="quarter")]), lyr)
    assert e.value.reason == "unsupported_grain"


def test_three_dimensions_are_representable():
    """The third dimension becomes a facet. Whether it *should* is a
    question about how many distinct values it has, which only the result
    set can answer -- so it is asked in the chart builder, not here."""
    q(dimensions=[DimensionRef(field="region"),
                  DimensionRef(field="contractor"),
                  DimensionRef(field="field_name")])


def test_more_than_three_dimensions_cannot_be_constructed():
    """The ceiling is in the grammar, not in validation: four dimensions is
    not a rejected query, it is an unrepresentable one. There is no channel
    left after x, y, colour and facet."""
    with pytest.raises(ValueError):
        q(dimensions=[DimensionRef(field="region"),
                      DimensionRef(field="contractor"),
                      DimensionRef(field="field_name"),
                      DimensionRef(field="well_name")])


def test_duplicate_dimension_is_rejected(verified_layer):
    with pytest.raises(QueryValidationError) as e:
        validate_query(q(dimensions=[DimensionRef(field="region"),
                                     DimensionRef(field="region")]), verified_layer)
    assert e.value.reason == "duplicate_dimension"


# -- 5. filters ----------------------------------------------------------

def test_unknown_filter_field_is_rejected(verified_layer):
    with pytest.raises(QueryValidationError) as e:
        validate_query(q(filters=[Filter(field="rig", op="=", value="x")]), verified_layer)
    assert e.value.reason == "unknown_filter_field"


def test_in_year_on_a_string_field_is_rejected(verified_layer):
    with pytest.raises(QueryValidationError) as e:
        validate_query(q(filters=[Filter(field="region", op="in_year", value=2026)]),
                       verified_layer)
    assert e.value.reason == "op_type_mismatch"


@pytest.mark.parametrize("f", [
    Filter(field="status", op="in", value="COMPLETED"),          # not a list
    Filter(field="intervention_date", op="between", value=[1]),  # needs two
    Filter(field="intervention_date", op="in_year", value="2026"),
    Filter(field="intervention_date", op="last_n_days", value=-3),
    Filter(field="status", op="=", value=["COMPLETED"]),         # needs a scalar
])
def test_bad_filter_value_shapes_are_rejected(verified_layer, f):
    with pytest.raises(QueryValidationError) as e:
        validate_query(q(filters=[f]), verified_layer)
    assert e.value.reason == "bad_filter_value"


# -- 6. declared value domains ------------------------------------------

def test_value_outside_the_declared_domain_is_rejected(verified_layer):
    """Catching this here is the difference between naming the mistake and
    rendering a silently empty chart."""
    with pytest.raises(QueryValidationError) as e:
        validate_query(q(filters=[Filter(field="status", op="=", value="DONE")]),
                       verified_layer)
    assert e.value.reason == "value_not_in_domain"
    assert "COMPLETED" in e.value.detail


def test_value_inside_the_declared_domain_is_accepted(verified_layer):
    validate_query(q(filters=[Filter(field="intervention_type", op="in",
                                     value=["FRAC", "WORKOVER"])]), verified_layer)


# -- 7. ordering ---------------------------------------------------------

def test_order_by_an_unselected_field_is_rejected(verified_layer):
    with pytest.raises(QueryValidationError) as e:
        validate_query(q(order_by=[OrderBy(field="cost")]), verified_layer)
    assert e.value.reason == "order_by_unselected"


def test_order_by_a_selected_dimension_is_accepted(verified_layer):
    validate_query(q(dimensions=[DimensionRef(field="region")],
                     order_by=[OrderBy(field="region", dir="asc")]), verified_layer)


# -- the grammar's structural guarantees --------------------------------

def test_an_invented_key_is_a_hard_failure():
    """extra=forbid is what makes a hallucinated field an error rather than
    a silently ignored key."""
    with pytest.raises(ValueError):
        SemanticQuery(entity="well_interventions", measures=["net_gain"],
                      table="ddh.secret")


def test_there_is_no_free_text_table_slot():
    assert "table" not in SemanticQuery.model_fields
    assert "sql" not in SemanticQuery.model_fields


def test_the_same_measure_twice_is_rejected(verified_layer):
    """Found by a model, not by inspection: gpt-5-mini emitted `oil` twice
    for "oil by well on a map". The query validated, executed, and drew a
    heatmap -- because the measure count is what picks the chart, and two
    identical columns is two measures."""
    with pytest.raises(QueryValidationError) as e:
        validate_query(
            SemanticQuery.model_validate(
                {"entity": "production", "measures": ["oil", "oil"],
                 "dimensions": [{"field": "well_name"}]}),
            verified_layer)
    assert e.value.reason == "duplicate_measure"


def test_the_same_measure_under_different_transforms_is_fine(verified_layer):
    """`oil` and its running total are different columns and belong
    together -- the check is on output names, not base names."""
    validate_query(
        SemanticQuery.model_validate(
            {"entity": "production",
             "measures": ["oil", {"name": "oil", "transform": "cumulative"}],
             "dimensions": [{"field": "reading_date", "grain": "month"}]}),
        verified_layer)
