from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..store import cards as store

router = APIRouter(prefix="/boards", tags=["boards"])


class BoardIn(BaseModel):
    title: str = "Untitled board"


class LayoutIn(BaseModel):
    layouts: dict[str, dict]


@router.get("")
def list_boards():
    return store.list_boards()


@router.post("")
def create_board(body: BoardIn):
    return store.create_board(body.title)


@router.get("/{board_id}")
def get_board(board_id: uuid.UUID):
    board = store.get_board(board_id)
    if board is None:
        raise HTTPException(404, "no such board")
    return {**board, "cards": store.list_cards(board_id)}


@router.delete("/{board_id}", status_code=204)
def delete_board(board_id: uuid.UUID):
    store.delete_board(board_id)


@router.post("/{board_id}/cards")
def create_card(board_id: uuid.UUID):
    if store.get_board(board_id) is None:
        raise HTTPException(404, "no such board")
    return store.create_card(board_id)


@router.patch("/{board_id}/layout", status_code=204)
def save_layout(board_id: uuid.UUID, body: LayoutIn):
    store.save_layouts(body.layouts)
