"""Query endpoints.

/query takes an explicit semantic query and renders it. /ask takes natural
language and is wired to the model in the LLM phase.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..deps import LAYER, SYNONYMS, example_questions
from ..llm.client import AnthropicClient
from ..llm.query_step import ask as ask_model
from ..render import render, to_payload
from ..semantic.query import ChartHint, SemanticQuery
from ..store import cards as store

router = APIRouter(tags=["query"])


class AskIn(BaseModel):
    question: str
    card_id: uuid.UUID | None = None
    model: str | None = None


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

    outcome = ask_model(body.question, LAYER, AnthropicClient(body.model),
                        synonyms=SYNONYMS, current=current)

    if outcome.refusal:
        return {"state": "refused", "message": outcome.refusal}
    if outcome.clarify:
        return {"state": "clarify", "message": outcome.clarify}

    r = render(outcome.query, LAYER, chart_hint=outcome.chart_hint,
               title=outcome.title)

    if body.card_id is not None and r.state == "ready":
        _save(QueryIn(semantic_query=outcome.query, chart_hint=outcome.chart_hint,
                      title=outcome.title, card_id=body.card_id), r)
        store.update_card(body.card_id, prompt=body.question)

    return {"state": r.state, **to_payload(r)}
