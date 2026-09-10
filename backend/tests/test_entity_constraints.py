"""An entity may be a slice of its table, not the whole of it.

The case this exists for is a stacked fact table: production and delivery
of the same oil, in one table, told apart by a `type` column. One entity
summing the whole table answers "how much oil in August" with roughly
double the real figure and looks completely normal doing it -- the chart
draws, the number is plausible, and nothing anywhere says it is wrong.

So the discriminator becomes part of the entity's identity. What is
tested here is that it cannot be escaped: not by a model that filters on
the column itself, not by one that filters it the other way, and not by
one that simply never mentions it.
"""

import pytest
from pydantic import ValidationError

from app.layer.models import Constraint, Entity
from app.semantic.compile import compile_query
from app.semantic.query import SemanticQuery

MINING = {
    "entity": "oil_mined",
    "label": "Oil Mined",
    "table": "ops.daily_summary",
    "filters": [{"field": "type", "value": "Mining"}],
    "dimensions": {"company": {"label": "company", "type": "string"}},
    "measures": {"fact": {"label": "actual", "agg": "sum",
                          "column": "day_actual"}},
}


def _compile(entity: Entity, **query):
    return compile_query(
        SemanticQuery.model_validate({"entity": entity.name, **query}),
        {entity.name: entity})


def _sql(entity: Entity, **query) -> str:
    return _compile(entity, **query).sql


def test_the_constraint_is_in_every_query_whether_or_not_it_was_asked_for():
    entity = Entity.model_validate(MINING)
    sql = _sql(entity, measures=["fact"], dimensions=[{"field": "company"}])
    assert '"type" = %s' in sql


def test_a_query_that_mentions_nothing_at_all_is_still_constrained():
    entity = Entity.model_validate(MINING)
    assert '"type" = %s' in _sql(entity, measures=["fact"], dimensions=[])


def test_the_constrained_value_is_bound_not_interpolated():
    """Layer-authored, but still a parameter. A layer file is not a place
    where quoting rules should start mattering."""
    entity = Entity.model_validate(MINING)
    compiled = _compile(entity, measures=["fact"], dimensions=[])
    assert "Mining" in compiled.params
    assert "Mining" not in compiled.sql


def test_a_model_cannot_filter_its_way_out_of_the_slice():
    """The clause it did not write is ANDed with the one it did, so
    asking for Delivery inside the Mining entity returns nothing rather
    than the other half of the table."""
    entity = Entity.model_validate(MINING | {
        "dimensions": {"company": {"label": "company", "type": "string"},
                       "type": {"label": "type", "type": "string"}}})
    sql = _sql(entity, measures=["fact"], dimensions=[],
               filters=[{"field": "type", "op": "=", "value": "Delivery"}])
    assert sql.count('"type" = %s') == 2      # both, ANDed. Never one.


def test_two_entities_over_one_table_stay_disjoint():
    mined = Entity.model_validate(MINING)
    delivered = Entity.model_validate(
        MINING | {"entity": "oil_delivered", "label": "Oil Delivered",
                  "filters": [{"field": "type", "value": "Delivery"}]})
    assert mined.table == delivered.table
    assert mined.filters[0].value != delivered.filters[0].value


def test_the_prompt_says_the_entity_is_already_scoped():
    """Otherwise the model sees a number lower than it expects and helps
    by inventing a reason for it."""
    from app.llm.prompt import build_system_prompt
    text = build_system_prompt({"oil_mined": Entity.model_validate(MINING)})
    assert "every row has type = Mining" in text
    assert "do not filter on it" in text


# -- what the grammar refuses ---------------------------------------------

def test_a_constraint_field_must_be_an_identifier():
    with pytest.raises(ValidationError):
        Constraint.model_validate({"field": "type; DROP TABLE x",
                                   "value": "Mining"})


def test_in_needs_a_list_and_equals_does_not_take_one():
    with pytest.raises(ValidationError):
        Constraint.model_validate({"field": "type", "op": "in",
                                   "value": "Mining"})
    with pytest.raises(ValidationError):
        Constraint.model_validate({"field": "type", "op": "=",
                                   "value": ["Mining", "Delivery"]})


def test_an_entity_without_constraints_is_unchanged():
    """The whole existing layer declares none, so the default has to be
    the empty list and the SQL has to be what it always was."""
    plain = Entity.model_validate({k: v for k, v in MINING.items()
                                   if k != "filters"})
    assert plain.filters == []
    assert "WHERE" not in _sql(plain, measures=["fact"], dimensions=[])


# -- the pair in the real layer -------------------------------------------

def test_the_two_halves_of_the_daily_report_are_separate_entities():
    """dm_upstream.daily_production_summary holds production
    and delivery of the same oil. Nothing may offer them as one number."""
    from app.deps import LAYER

    mined, delivered = LAYER["oil_mined"], LAYER["oil_delivered"]
    assert mined.table == delivered.table
    assert [(c.field, c.value) for c in mined.filters] == [("type", "Mining")]
    assert [(c.field, c.value) for c in delivered.filters] == [
        ("type", "Delivery")]


def test_every_query_on_either_half_carries_its_constraint():
    from app.deps import LAYER
    from app.semantic.compile import compile_query

    for name, expected in (("oil_mined", "Mining"),
                           ("oil_delivered", "Delivery")):
        compiled = compile_query(
            SemanticQuery.model_validate(
                {"entity": name, "measures": ["actual"], "dimensions": []}),
            LAYER)
        assert '"type" = %s' in compiled.sql
        assert expected in compiled.params


def test_a_new_schema_does_not_steal_another_schema_s_vocabulary():
    """Learned by breaking it. The cross-schema guard reads measures, so a
    synonym like a bare "production" declared in dm_upstream makes the word
    belong there -- and a ddh dashboard asking "show me production" is
    told its own subject lives somewhere else. Phrases are safe; lone
    common nouns another schema might want are not."""
    from app.deps import LAYER, SYNONYMS
    from app.layer.scope import elsewhere

    for question in ("show me production", "oil production by region",
                     "downtime by well", "variance to plan"):
        assert elsewhere(question, LAYER, SYNONYMS, "ddh") is None, question


def test_the_daily_report_exposes_no_running_totals():
    """dateplanamount runs about thirty times day_plan -- a month of
    accumulation. Summed across days it gives some fifteen times the
    truth, and draws a normal-looking chart doing it. Out until somebody
    confirms what those columns mean."""
    from app.deps import LAYER

    for name in ("oil_mined", "oil_delivered"):
        columns = {m.column for m in LAYER[name].measures.values()}
        assert not columns & {"dateplanamount", "datefactamount",
                              "datedeviation", "monthplanamount",
                              "stokofgoods"}
