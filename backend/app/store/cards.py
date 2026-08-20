"""Boards and cards. An empty card is a real persisted row: a manager
sketching a layout with four blank cards must not lose them on reload."""

from __future__ import annotations

import json
import uuid
from typing import Any

from psycopg.rows import dict_row

from ..db import app_pool

CARD_COLUMNS = """id, board_id, title, semantic_query, chart_hint, vega_spec,
                  prompt, state, layout, cache, ttl_seconds, previous,
                  created_at, updated_at"""


def _q(sql: str, params: tuple = (), *, fetch: str | None = None):
    with app_pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        if fetch == "one":
            return cur.fetchone()
        if fetch == "all":
            return cur.fetchall()
        return None


# -- boards --------------------------------------------------------------

BOARD_COLUMNS = "id, title, position, created_at, updated_at"


def create_board(title: str) -> dict[str, Any]:
    """New boards land at the end. Computing the position here rather than
    defaulting to 0 keeps tab order stable as boards are added."""
    return _q(f"INSERT INTO app.board (id, title, position) "
              f"VALUES (%s, %s, (SELECT coalesce(max(position) + 1, 0) "
              f"FROM app.board)) RETURNING {BOARD_COLUMNS}",
              (uuid.uuid4(), title), fetch="one")


def list_boards() -> list[dict[str, Any]]:
    # created_at breaks ties so ordering is total: two boards sharing a
    # position must still come back in the same order every time.
    return _q(f"SELECT {BOARD_COLUMNS} FROM app.board "
              f"ORDER BY position, created_at", fetch="all")


def get_board(board_id: uuid.UUID) -> dict[str, Any] | None:
    return _q(f"SELECT {BOARD_COLUMNS} FROM app.board WHERE id = %s",
              (board_id,), fetch="one")


def update_board(board_id: uuid.UUID, **fields: Any) -> dict[str, Any] | None:
    """Mirrors update_card: only known columns are settable."""
    allowed = {"title", "position"}

    sets, params = [], []
    for k, v in fields.items():
        if k not in allowed:
            raise ValueError(f"not a settable board column: {k}")
        sets.append(f"{k} = %s")
        params.append(v)

    if not sets:
        return get_board(board_id)

    sets.append("updated_at = now()")
    params.append(board_id)
    return _q(f"UPDATE app.board SET {', '.join(sets)} WHERE id = %s "
              f"RETURNING {BOARD_COLUMNS}", tuple(params), fetch="one")


def reorder_boards(order: list[uuid.UUID]) -> None:
    """Positions are rewritten from the given sequence in one transaction, so
    a half-applied reorder cannot leave two tabs claiming the same slot."""
    with app_pool.connection() as conn, conn.cursor() as cur:
        for position, board_id in enumerate(order):
            cur.execute("UPDATE app.board SET position = %s, updated_at = now() "
                        "WHERE id = %s", (position, board_id))


def delete_board(board_id: uuid.UUID) -> None:
    _q("DELETE FROM app.board WHERE id = %s", (board_id,))


# -- cards ---------------------------------------------------------------

CARD_W, CARD_H, COLS = 6, 10, 12


def next_slot(board_id: uuid.UUID) -> dict[str, int]:
    """Place a new card beside the last one rather than under it. Every card
    defaulting to x=0 makes the grid resolve collisions vertically, so a
    board of four cards arrives as a single column."""
    n = len(list_cards(board_id))
    per_row = COLS // CARD_W
    return {"x": (n % per_row) * CARD_W, "y": (n // per_row) * CARD_H,
            "w": CARD_W, "h": CARD_H}


def create_card(board_id: uuid.UUID, layout: dict | None = None) -> dict[str, Any]:
    return _q(
        f"INSERT INTO app.card (id, board_id, layout) VALUES (%s, %s, %s) "
        f"RETURNING {CARD_COLUMNS}",
        (uuid.uuid4(), board_id, json.dumps(layout or next_slot(board_id))),
        fetch="one")


def list_cards(board_id: uuid.UUID) -> list[dict[str, Any]]:
    return _q(f"SELECT {CARD_COLUMNS} FROM app.card WHERE board_id = %s "
              f"ORDER BY created_at", (board_id,), fetch="all")


def get_card(card_id: uuid.UUID) -> dict[str, Any] | None:
    return _q(f"SELECT {CARD_COLUMNS} FROM app.card WHERE id = %s",
              (card_id,), fetch="one")


def delete_card(card_id: uuid.UUID) -> None:
    _q("DELETE FROM app.card WHERE id = %s", (card_id,))


def update_card(card_id: uuid.UUID, **fields: Any) -> dict[str, Any] | None:
    """Only known columns are settable, and jsonb columns are dumped here so
    callers never hand raw dicts to psycopg."""
    allowed = {"title", "semantic_query", "chart_hint", "vega_spec", "prompt",
               "state", "layout", "cache", "ttl_seconds", "previous"}
    json_cols = {"semantic_query", "vega_spec", "layout", "cache", "previous"}

    sets, params = [], []
    for k, v in fields.items():
        if k not in allowed:
            raise ValueError(f"not a settable card column: {k}")
        sets.append(f"{k} = %s")
        params.append(json.dumps(v) if k in json_cols and v is not None else v)

    if not sets:
        return get_card(card_id)

    sets.append("updated_at = now()")
    params.append(card_id)
    return _q(f"UPDATE app.card SET {', '.join(sets)} WHERE id = %s "
              f"RETURNING {CARD_COLUMNS}", tuple(params), fetch="one")


def save_layouts(board_id: uuid.UUID, layouts: dict[str, dict]) -> None:
    """Scoped to one board on purpose. Keyed by card id alone, a request
    naming board A could move a card belonging to board B; the id is a uuid
    the caller supplies, so that is a real reachable state, not a theoretical
    one."""
    with app_pool.connection() as conn, conn.cursor() as cur:
        for card_id, layout in layouts.items():
            cur.execute("UPDATE app.card SET layout = %s, updated_at = now() "
                        "WHERE id = %s AND board_id = %s",
                        (json.dumps(layout), card_id, board_id))
