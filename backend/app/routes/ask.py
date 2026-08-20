"""Query endpoints.

/query takes an explicit semantic query and renders it. /ask takes natural
language and is wired to the model in the LLM phase.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import Provider, settings
from ..deps import LAYER, SYNONYMS, example_questions
from ..llm.client import (
    LLMError,
    LLMRateLimited,
    configured_providers,
    make_client,
)
from ..llm.query_step import ask as ask_model
from ..render import render, to_payload
from ..semantic.diff import diff_queries
from ..semantic.query import ChartHint, SemanticQuery
from ..store import cards as store

router = APIRouter(tags=["query"])


class AskIn(BaseModel):
    question: str
    card_id: uuid.UUID | None = None
    # The asker says the question is hard; the server decides what that
    # costs. Exposing a model id here would let any caller spend the
    # expensive one, and would ask a manager to reason about model names.
    hard: bool = False
    # Which API answers. Unlike a model id this is not an escalator -- it
    # chooses which account pays, and one of the two is free -- so it is
    # safe to expose. `hard` remains the only way to spend more.
    provider: Provider | None = None


class QueryIn(BaseModel):
    semantic_query: SemanticQuery
    chart_hint: ChartHint | None = None
    title: str = ""
    card_id: uuid.UUID | None = None      # when set, the result is saved


@router.get("/layer")
def get_layer():
    """What the manager is allowed to ask about. Also what the empty card
    shows as examples."""
    return {
        "entities": [
            {
                "name": e.name,
                "label": e.label,
                "description": e.description,
                "unverified": e.low_confidence_fields(),
                "dimensions": [
                    {"name": k, "label": d.label, "type": d.type,
                     "grains": d.grains, "values": d.values}
                    for k, d in e.dimensions.items()
                ],
                "measures": [
                    {"name": k, "label": m.label, "agg": m.agg,
                     "description": m.description}
                    for k, m in e.measures.items()
                ],
            }
            for e in LAYER.values()
        ],
        "examples": example_questions(),
        "providers": {
            "default": settings.llm_provider,
            "available": configured_providers(),
        },
    }


@router.post("/query")
def run_query(body: QueryIn):
    r = render(body.semantic_query, LAYER, chart_hint=body.chart_hint,
               title=body.title)

    if body.card_id is not None and r.state == "ready":
        _save(body, r)

    return to_payload(r)


def _save(body: QueryIn, r) -> None:
    existing = store.get_card(body.card_id)
    if existing is None:
        raise HTTPException(404, "no such card")
    previous = (
        {"semantic_query": existing["semantic_query"],
         "chart_hint": existing["chart_hint"],
         "vega_spec": existing["vega_spec"]}
        if existing.get("semantic_query") else None
    )
    store.update_card(
        body.card_id,
        semantic_query=r.semantic_query.model_dump(mode="json"),
        chart_hint=r.chart_hint,
        vega_spec=r.vega_spec,
        title=body.title or existing["title"],
        state="ready",
        cache=r.cache,
        previous=previous,
    )


@router.post("/ask")
def ask(body: AskIn):
    """Natural language in, a card out -- or one clarifying question, or a
    refusal naming what is undefined. Never a confidently wrong chart."""
    card = store.get_card(body.card_id) if body.card_id else None
    current = (SemanticQuery.model_validate(card["semantic_query"])
               if card and card.get("semantic_query") else None)

    try:
        client = make_client(body.provider, hard=body.hard)
    except LLMError as exc:
        raise HTTPException(400, str(exc)) from exc
    who = {"provider": client.provider, "model": client.model}

    try:
        outcome = ask_model(body.question, LAYER, client,
                            synonyms=SYNONYMS, current=current)
    except LLMRateLimited as exc:
        # A person waiting on a card wants to be told, not held. The eval
        # makes the other choice and waits.
        return {"state": "refused", "message": str(exc), **who}

    if outcome.refusal:
        return {"state": "refused", "message": outcome.refusal, **who}
    if outcome.clarify:
        return {"state": "clarify", "message": outcome.clarify, **who}

    r = render(outcome.query, LAYER, chart_hint=outcome.chart_hint,
               title=(card["title"] if current is not None and card.get("title")
                      else outcome.title))

    # What moved, stated deterministically. An edit that silently changes
    # more than was asked for is the same failure as a chart that silently
    # means something else.
    changed = (diff_queries(current, outcome.query, LAYER[outcome.query.entity])
               if current is not None and outcome.query.entity in LAYER else [])

    if body.card_id is not None and r.state == "ready":
        # An edit keeps the card's name. "Break this down by well type" is
        # an instruction, not a title, and letting it become one renames the
        # card to the last thing anybody typed at it. The restatement
        # already carries the full meaning.
        title = card["title"] if current is not None and card.get("title") \
            else outcome.title
        _save(QueryIn(semantic_query=outcome.query, chart_hint=outcome.chart_hint,
                      title=title, card_id=body.card_id), r)
        store.update_card(body.card_id, prompt=body.question)

    return {"state": r.state, **who, "changed": changed, **to_payload(r)}
