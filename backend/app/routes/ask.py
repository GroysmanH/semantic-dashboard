"""Query endpoints.

/query takes an explicit semantic query and renders it. /ask takes natural
language and is wired to the model in the LLM phase.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..deps import LAYER, example_questions
from ..render import render, to_payload
from ..semantic.query import ChartHint, SemanticQuery
from ..store import cards as store

router = APIRouter(tags=["query"])


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
