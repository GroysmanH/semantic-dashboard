from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..config import settings
from ..deps import LAYER
from ..layer.scope import schemas
from ..layout import LayoutConflict, LayoutInput, LayoutResult
from ..store import cards as store

router = APIRouter(prefix="/boards", tags=["boards"])


def _known(schema_name: str) -> None:
    """Refuse a schema the layer cannot answer from.

    The picker only offers schemas with entities behind them, so reaching
    this is either a stale browser or a hand-made request. Either way a
    board pointed at a schema with nothing modelled in it is a board where
    every question fails, and failing here says why once instead.
    """
    allowed = settings.schema_allowlist
    # Two gates, not one. "Nobody modelled it" keeps the HR schemas out
    # today; the allowlist keeps them out on the day somebody does.
    available = [s for s in schemas(LAYER) if not allowed or s in allowed]
    if schema_name not in available:
        raise HTTPException(
            422,
            f"No entities are modelled in {schema_name!r}. "
            f"Available: {', '.join(available)}.")


class BoardIn(BaseModel):
    title: str = "Untitled board"
    # Inherited from the board the person was looking at, so a second
    # dashboard on the same schema costs no extra step. Absent means the
    # configured default, which is what a first-ever board gets.
    schema_name: str | None = None


class BoardPatch(BaseModel):
    title: str | None = None
    position: int | None = None
    layout_mode: Literal["free", "auto_pack"] | None = None
    schema_name: str | None = None


class ReorderIn(BaseModel):
    order: list[uuid.UUID]


class LayoutIn(BaseModel):
    layouts: dict[uuid.UUID, LayoutInput]
    manually_resized: list[uuid.UUID] = Field(default_factory=list)
    expected_revision: int = Field(ge=0)


@router.get("")
def list_boards():
    return store.list_boards()


@router.post("")
def create_board(body: BoardIn):
    if body.schema_name is not None:
        _known(body.schema_name)
    return store.create_board(body.title, schema_name=body.schema_name)


@router.post("/reorder", status_code=204)
def reorder_boards(body: ReorderIn):
    # Declared before /{board_id} so "reorder" is not captured as a uuid.
    try:
        store.reorder_boards(body.order)
    except store.BoardOrderError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{board_id}/duplicate")
def duplicate_board(board_id: uuid.UUID, body: BoardIn | None = None):
    """A copy, with its own cards. Nothing is shared with the original --
    editing a card on one does not touch the other."""
    source = store.get_board(board_id)
    if source is None:
        raise HTTPException(404, "no such board")
    title = (body.title if body and body.title != BoardIn().title
             else f"{source['title']} copy")
    copy = store.duplicate_board(board_id, title)
    if copy is None:
        raise HTTPException(404, "no such board")
    return copy


@router.get("/{board_id}")
def get_board(board_id: uuid.UUID):
    board = store.get_board(board_id)
    if board is None:
        raise HTTPException(404, "no such board")
    return {**board, "cards": store.list_cards(board_id)}


@router.patch("/{board_id}")
def update_board(board_id: uuid.UUID, body: BoardPatch):
    fields = body.model_dump(exclude_none=True)
    if "schema_name" in fields:
        # Switching leaves every existing card exactly where it is. Their
        # queries are already frozen and still render; what changes is only
        # what the next question may be about. See layer/scope.py.
        _known(fields["schema_name"])
    board = store.update_board(board_id, **fields)
    if board is None:
        raise HTTPException(404, "no such board")
    return board


@router.delete("/{board_id}", status_code=204)
def delete_board(board_id: uuid.UUID):
    try:
        store.hard_delete_board(board_id)
    except store.LastVisibleBoardError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{board_id}/cards")
def create_card(board_id: uuid.UUID):
    card = store.create_card(board_id, auto_size_pending=True)
    if card is None:
        raise HTTPException(404, "no such board")
    return card


@router.patch(
    "/{board_id}/layout",
    response_model=LayoutResult,
    responses={409: {"model": LayoutConflict}},
)
def save_layout(board_id: uuid.UUID, body: LayoutIn):
    try:
        result = store.save_layouts(
            board_id,
            {str(card_id): layout.model_dump()
             for card_id, layout in body.layouts.items()},
            manually_resized=body.manually_resized,
            require_complete=True,
            expected_revision=body.expected_revision,
        )
    except store.BoardLayoutConflict as exc:
        conflict = LayoutConflict(layouts=exc.layouts, revision=exc.revision)
        return JSONResponse(status_code=409, content=conflict.model_dump(mode="json"))
    except store.BoardLayoutError as exc:
        raise HTTPException(409, str(exc)) from exc
    if result is None:
        raise HTTPException(404, "no such board")
    return result
