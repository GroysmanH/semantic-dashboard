from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..deps import LAYER
from ..render import is_persistable, render, to_payload
from ..semantic.query import ChartHint, SemanticQuery
from ..store import cards as store

router = APIRouter(prefix="/cards", tags=["cards"])


class CardPatch(BaseModel):
    title: str | None = None
    ttl_seconds: int | None = None


def _render_card(card: dict, *, force: bool = False) -> dict:
    """A card with no query yet is genuinely empty, not broken."""
    if not card.get("semantic_query"):
        return {**card, "can_undo": False, "render": {"state": "empty"}}

    q = SemanticQuery.model_validate(card["semantic_query"])
    r = render(q, LAYER, chart_hint=card.get("chart_hint"),
               title=card.get("title") or "", cache=card.get("cache"),
               ttl_seconds=card["ttl_seconds"], force=force)

    # Persist a freshly fetched result, and the state the layer says it is in.
    if is_persistable(r) and not r.from_cache:
        store.update_card(card["id"], cache=r.cache, state=r.state,
                          vega_spec=r.vega_spec)
    elif r.state != card["state"]:
        store.update_card(card["id"], state=r.state)

    # A boolean rather than the previous query itself: the frontend needs
    # to know whether a button belongs on screen, not what is behind it.
    return {**card, "state": r.state, "can_undo": bool(card.get("previous")),
            "render": to_payload(r)}


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


@router.post("/{card_id}/undo")
def undo_card(card_id: uuid.UUID):
    """One step back, per design section 9.

    Refinement occasionally makes a card worse, and without this the only
    recourse is rebuilding it -- the moment someone stops trusting the edit
    box. One step and no more: `previous` is cleared on the way out, so the
    button disappears rather than quietly restoring something older still.
    """
    card = store.get_card(card_id)
    if card is None:
        raise HTTPException(404, "no such card")

    previous = card.get("previous")
    if not previous:
        raise HTTPException(409, "This card has nothing to undo.")

    restored = store.update_card(
        card_id,
        semantic_query=previous.get("semantic_query"),
        chart_hint=previous.get("chart_hint"),
        vega_spec=previous.get("vega_spec"),
        # Cleared, not carried: update_card writes NULL for None, which is
        # what makes this one step rather than an undo stack nobody asked
        # for.
        previous=None,
        # The restored query may hash to a different cache key, so let the
        # render decide freshness rather than trusting the outgoing card's
        # envelope.
        cache=None,
        state="ready",
    )
    return _render_card(restored)


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
