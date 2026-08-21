from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..store import cards as store

router = APIRouter(prefix="/boards", tags=["boards"])


class BoardIn(BaseModel):
    title: str = "Untitled board"


class BoardPatch(BaseModel):
    title: str | None = None
    position: int | None = None


class ReorderIn(BaseModel):
    order: list[uuid.UUID]


class LayoutIn(BaseModel):
    layouts: dict[str, dict]


@router.get("")
def list_boards():
    return store.list_boards()


@router.post("")
def create_board(body: BoardIn):
    return store.create_board(body.title)


@router.post("/reorder", status_code=204)
def reorder_boards(body: ReorderIn):
    # Declared before /{board_id} so "reorder" is not captured as a uuid.
    store.reorder_boards(body.order)


@router.get("/{board_id}")
def get_board(board_id: uuid.UUID):
    board = store.get_board(board_id)
    if board is None:
        raise HTTPException(404, "no such board")
    return {**board, "cards": store.list_cards(board_id)}


@router.patch("/{board_id}")
def update_board(board_id: uuid.UUID, body: BoardPatch):
    if store.get_board(board_id) is None:
        raise HTTPException(404, "no such board")
    fields = body.model_dump(exclude_none=True)
    return store.update_board(board_id, **fields)


@router.delete("/{board_id}", status_code=204)
def delete_board(board_id: uuid.UUID):
    try:
        store.hard_delete_board(board_id)
    except store.LastVisibleBoardError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{board_id}/cards")
def create_card(board_id: uuid.UUID):
    if store.get_board(board_id) is None:
        raise HTTPException(404, "no such board")
    return store.create_card(board_id)


@router.patch("/{board_id}/layout", status_code=204)
def save_layout(board_id: uuid.UUID, body: LayoutIn):
    store.save_layouts(board_id, body.layouts)
