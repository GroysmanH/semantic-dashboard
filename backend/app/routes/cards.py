from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..deps import LAYER
from ..render import render, to_payload
from ..semantic.query import ChartHint, SemanticQuery
from ..store import cards as store

router = APIRouter(prefix="/cards", tags=["cards"])


class CardPatch(BaseModel):
    title: str | None = None
    ttl_seconds: int | None = None


def _render_card(card: dict, *, force: bool = False) -> dict:
    """A card with no query yet is genuinely empty, not broken."""
    if not card.get("semantic_query"):
        return {**card, "render": {"state": "empty"}}

    q = SemanticQuery.model_validate(card["semantic_query"])
    r = render(q, LAYER, chart_hint=card.get("chart_hint"),
               title=card.get("title") or "", cache=card.get("cache"),
               ttl_seconds=card["ttl_seconds"], force=force)

    # Persist a freshly fetched result, and the state the layer says it is in.
    if r.state == "ready" and not r.from_cache:
        store.update_card(card["id"], cache=r.cache, state="ready",
                          vega_spec=r.vega_spec)
    elif r.state != card["state"]:
        store.update_card(card["id"], state=r.state)

    return {**card, "state": r.state, "render": to_payload(r)}


@router.get("/{card_id}")
def get_card(card_id: uuid.UUID):
    card = store.get_card(card_id)
    if card is None:
        raise HTTPException(404, "no such card")
    return _render_card(card)


@router.post("/{card_id}/refresh")
def refresh_card(card_id: uuid.UUID):
    card = store.get_card(card_id)
    if card is None:
        raise HTTPException(404, "no such card")
    return _render_card(card, force=True)


@router.patch("/{card_id}")
def patch_card(card_id: uuid.UUID, body: CardPatch):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    card = store.update_card(card_id, **fields)
    if card is None:
        raise HTTPException(404, "no such card")
    return _render_card(card)


@router.delete("/{card_id}", status_code=204)
def delete_card(card_id: uuid.UUID):
    store.delete_card(card_id)
