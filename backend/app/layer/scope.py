"""Which schema a dashboard asks its questions of.

A real warehouse has many schemas and each answers its own kind of
question. Handing a model every entity in all of them makes the menu
longer without making any single dashboard more useful, and a longer menu
is a wider target for the wrong choice. So a dashboard names one schema and
sees only that.

The narrowing has a cost that has to be paid for deliberately. A model
shown only the planning mart, asked about oil production, no longer has a
right answer available -- and a model with no right answer available does
not stop, it picks the closest wrong one. `elsewhere` exists to catch that
before the model is ever asked.

Nothing here is stored twice. An entity's schema is the prefix of the table
it already declares, so there is no `schema:` key to disagree with the
`table:` key underneath it.
"""

from __future__ import annotations

import re

from .models import Entity, Layer


def schema_of(entity: Entity) -> str:
    return entity.table.split(".", 1)[0]


def schemas(layer: Layer) -> list[str]:
    """Every schema the layer can answer from, in a stable order."""
    return sorted({schema_of(entity) for entity in layer.values()})


def layer_for(layer: Layer, schema: str) -> Layer:
    """The part of the layer a dashboard on `schema` may ask about.

    Used for *writing* a query -- the prompt, the examples, the model's
    choice of entity. Never for rendering: a card built before the
    dashboard was switched still has to draw, and its entity is somewhere
    this filter deliberately excludes. Reading what is already on screen
    goes through the whole layer.
    """
    return {name: entity for name, entity in layer.items()
            if schema_of(entity) == schema}


def _mentions(term: str, text: str) -> bool:
    # Word boundaries, so 'gas' does not fire inside 'gasket' and a
    # multi-word term like 'planned production' matches only as a phrase.
    return re.search(rf"\b{re.escape(term)}\b", text) is not None


def elsewhere(question: str, layer: Layer,
              synonyms: dict[str, dict[str, list[str]]],
              schema: str) -> str | None:
    """The schema this question's subject actually lives in, if not this one.

    Judged on measures alone, and that is the whole subtlety. Dimensions
    are shared vocabulary -- 'region' means something in operations and in
    planning both -- so a question that mentions one proves nothing about
    where it belongs. A measure is what a question is *about*; a dimension
    is only how it is cut. "oil production by region" asked of the planning
    mart matches 'region' in scope and would sail through on any rule that
    counted dimensions, straight into a model with no oil measure to offer.

    Returns None when the question's subject is in scope, and also when it
    is nowhere at all -- an unanswerable question is the ordinary refusal
    path's business, not this one's.
    """
    text = question.lower()
    in_scope = False
    others: set[str] = set()

    for term, per_entity in synonyms.items():
        if not _mentions(term, text):
            continue
        for entity_name, fields in per_entity.items():
            entity = layer.get(entity_name)
            if entity is None:
                continue
            if not any(entity.measure(field) is not None for field in fields):
                continue
            if schema_of(entity) == schema:
                in_scope = True
            else:
                others.add(schema_of(entity))

    if in_scope or not others:
        return None
    return sorted(others)[0]
