from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..deps import LAYER
from ..render import is_persistable, render, to_payload
from ..layout import LayoutResult, visualization_size
from ..semantic.query import ChartHint, SemanticQuery
from ..store import cards as store

router = APIRouter(prefix="/cards", tags=["cards"])


class CardPatch(BaseModel):
    title: str | None = None
    ttl_seconds: int | None = None


class DuplicateCardOut(LayoutResult):
    card: dict


def _render_card(card: dict, *, force: bool = False) -> dict:
    """A card with no query yet is genuinely empty, not broken."""
    if not card.get("semantic_query"):
        return {**card, "can_undo": False, "render": {"state": "empty"}}

    q = SemanticQuery.model_validate(card["semantic_query"])
    r = render(q, LAYER, chart_hint=card.get("chart_hint"),
               title=card.get("title") or "", cache=card.get("cache"),
               ttl_seconds=card["ttl_seconds"], force=force)

    saved = card
    # A first successful render consumes one-time sizing regardless of
    # whether it came from a forced refresh or an already-populated cache.
    if is_persistable(r) and (
        not r.from_cache or (r.state == "ready" and card["auto_size_pending"])
    ):
        size = (
            visualization_size(
                r.chart_type,
                r.vega_spec,
                r.chart_rows if r.chart_rows is not None else r.rows,
            )
            if r.state == "ready"
            else None
        )
        saved = store.save_rendered_card(
            card["id"], size=size, cache=r.cache, state=r.state,
            vega_spec=r.vega_spec,
        ) or card
    elif r.error_reason == "result_verification":
        # A malformed cache is not merely stale. Clear it once so reloads
        # cannot keep hydrating the same rejected envelope.
        saved = store.update_card(card["id"], cache=None) or {**card, "cache": None}
    elif r.state != card["state"]:
        saved = store.update_card(card["id"], state=r.state) or card

    # A boolean rather than the previous query itself: the frontend needs
    # to know whether a button belongs on screen, not what is behind it.
    return {**saved, "state": r.state, "can_undo": bool(saved.get("previous")),
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


@router.post("/{card_id}/duplicate", response_model=DuplicateCardOut)
def duplicate_card(card_id: uuid.UUID):
    copied = store.duplicate_card(card_id)
    if copied is None:
        raise HTTPException(404, "no such card")
    return copied


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
        # Any open exchange is about the outgoing edit. Carrying it across
        # undo would attach an old question to the restored query.
        pending_clarification=None,
    )
    return _render_card(restored)


@router.post("/{card_id}/dismiss-exchange", status_code=204)
def dismiss_exchange(card_id: uuid.UUID):
    """Leave a clarification/refusal without letting it return on reload."""
    card = store.update_card(card_id, pending_clarification=None)
    if card is None:
        raise HTTPException(404, "no such card")


@router.patch("/{card_id}")
def patch_card(card_id: uuid.UUID, body: CardPatch):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    card = store.update_card(card_id, **fields)
    if card is None:
        raise HTTPException(404, "no such card")
    return _render_card(card)


@router.delete("/{card_id}", status_code=204)
def delete_card(card_id: uuid.UUID):
    store.hard_delete_card(card_id)
